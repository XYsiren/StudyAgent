from datetime import timedelta

from test_api import create_kb, register, upload, wait_job


def test_pending_quota_covers_upload_reindex_and_restore(client, monkeypatch):
    from app.models import IngestionJob, utcnow
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb)
    assert wait_job(client, headers, uploaded['job_id'])['status'] == 'completed'
    doc_id = uploaded['document']['id']
    revision = client.get(f'/api/documents/{doc_id}/versions', headers=headers).json()[0]['id']
    monkeypatch.setattr(client.app.state.settings, 'max_pending_jobs', 1)
    with client.app.state.database.session() as db:
        db.add(IngestionJob(document_id=doc_id, status='queued', available_at=utcnow() + timedelta(hours=1)))
        db.commit()
    assert client.post(f'/api/documents/{doc_id}/retry', headers=headers).status_code == 429
    assert client.post(f'/api/documents/{doc_id}/versions/{revision}/restore', headers=headers).status_code == 429
    assert client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
        files={'file': ('another.txt', b'another source')}).status_code == 429


def test_public_registration_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'allow_registration', False)
    assert client.post('/api/auth/register', json={'email': 'x@example.com', 'password': 'test-password-47', 'name': 'x'}).status_code == 403


def test_pending_deletion_does_not_release_storage_quota(client, monkeypatch):
    from app.models import CleanupTask, KnowledgeBase, utcnow
    from sqlalchemy import select
    headers = register(client)
    kb = create_kb(client, headers)
    monkeypatch.setattr(client.app.state.settings, 'user_storage_mb', 1)
    with client.app.state.database.session() as db:
        owner = db.get(KnowledgeBase, kb).user_id
        task = CleanupTask(document_id='deleted-fixture', owner_id=owner, retained_bytes=1024 * 1024,
            paths=[], available_at=utcnow() + timedelta(hours=1))
        db.add(task)
        db.commit()
    response = client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
        files={'file': ('new.txt', b'new source')})
    assert response.status_code == 429
    with client.app.state.database.session() as db:
        task = db.scalar(select(CleanupTask).where(CleanupTask.owner_id == owner))
        task.status, task.retained_bytes = 'completed', 0
        db.commit()
    assert client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
        files={'file': ('new.txt', b'new source')}).status_code == 201
