import threading
from datetime import timedelta

from app.models import IngestionJob, utcnow
from test_api import create_kb, register, upload, wait_job


def test_platform_permissions_global_and_per_account_quotas_take_effect(client, monkeypatch):
    admin = register(client)
    member = register(client, "member@example.com")
    assert client.get("/api/platform/users", headers=admin).status_code == 403
    monkeypatch.setattr(client.app.state.settings, "platform_admin_emails", ["owner@example.com"])
    assert client.get("/api/auth/me", headers=admin).json()["is_platform_admin"]
    assert not client.get("/api/auth/me", headers=member).json()["is_platform_admin"]
    for path in ["users", "quotas", "jobs"]:
        assert client.get("/api/platform/" + path, headers=member).status_code == 403
    limits = client.get("/api/platform/quotas", headers=admin).json()["limits"]
    limits.update(user_storage_mb=2048, max_pending_jobs=3, global_active_chats=5)
    assert client.put("/api/platform/quotas", headers=member, json=limits).status_code == 403
    assert client.put("/api/platform/quotas", headers=admin, json=limits).status_code == 200
    assert client.get("/api/operations", headers=member).json()["limits"]["user_storage_mb"] == 2048
    actor = client.get("/api/auth/me", headers=member).json()["id"]
    limits.update(max_pending_jobs=1, user_daily_model_requests=1, global_active_chats=128)
    response = client.put("/api/platform/quotas?user_id=" + actor, headers=admin, json=limits)
    assert response.status_code == 200 and response.json()["limits"]["global_active_chats"] == 5
    kb = create_kb(client, member)
    first = upload(client, member, kb)
    wait_job(client, member, first["job_id"])
    with client.app.state.database.session() as db:
        db.add(IngestionJob(document_id=first["document"]["id"], available_at=utcnow()+timedelta(hours=1)))
        db.commit()
    assert client.post("/api/documents/upload", headers=member, data={"knowledge_base_id":kb},
                       files={"file":("extra.txt",b"extra")}).status_code == 429
    assert client.post("/api/chat", headers=member, json={"question":"第一次"}).status_code == 200
    assert client.post("/api/chat", headers=member, json={"question":"第二次"}).status_code == 429
    assert client.delete("/api/platform/quotas?user_id="+actor, headers=admin).status_code == 204
    assert client.get("/api/operations", headers=member).json()["limits"]["max_pending_jobs"] == 3
    assert client.put("/api/platform/quotas", headers=admin, json={**limits,"max_active_chats":999}).status_code == 422


def blocked_parser(monkeypatch):
    from app.services import parsing
    entered, resume = threading.Event(), threading.Event()
    real = parsing.parse_document
    def block(path, filename, **kwargs):
        entered.set()
        assert resume.wait(10)
        return real(path, filename, **kwargs)
    monkeypatch.setattr(parsing,"parse_document",block)
    return entered, resume


def test_admin_cancels_running_ingestion_and_retries_without_losing_file(client, monkeypatch):
    admin = register(client)
    monkeypatch.setattr(client.app.state.settings,"platform_admin_emails",["owner@example.com"])
    kb = create_kb(client,admin)
    entered,resume=blocked_parser(monkeypatch)
    try:
        result=upload(client,admin,kb)
        assert entered.wait(5)
        path="/api/platform/jobs/ingestion/"+result["job_id"]
        assert client.post(path+"/cancel",headers=admin).status_code==200
        job=client.get("/api/jobs/"+result["job_id"],headers=admin).json()
        assert job["status"]=="cancelled"
        resume.set()
        assert client.post(path+"/retry",headers=admin).status_code==200
        assert wait_job(client,admin,result["job_id"])["status"]=="completed"
        assert client.get("/api/documents/"+result["document"]["id"]+"/download",headers=admin).status_code==200
    finally:
        resume.set()


def test_delete_while_parser_is_running_returns_before_parse_completes(client, monkeypatch):
    owner=register(client)
    kb=create_kb(client,owner)
    entered,resume=blocked_parser(monkeypatch)
    try:
        result=upload(client,owner,kb)
        assert entered.wait(5)
        assert client.delete("/api/documents/"+result["document"]["id"],headers=owner).status_code==204
        assert client.get("/api/jobs/"+result["job_id"],headers=owner).status_code==404
        assert not client.get("/api/documents",headers=owner).json()
    finally:
        resume.set()
