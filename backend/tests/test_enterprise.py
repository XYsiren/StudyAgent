"""Enterprise regressions: real DB transactions, ACL revocation, failures and versions."""
import json
import threading
from datetime import timedelta
from types import SimpleNamespace

from app.models import AuthSession, CleanupTask, Document, utcnow
from sqlalchemy import select, update
from test_api import create_kb, register, upload, wait_job


def shared_space(client, owner, reader):
    org = client.post('/api/organizations', headers=owner, json={'name': '测试团队'}).json()['id']
    response = client.put(f'/api/organizations/{org}/members', headers=owner,
                          json={'email': 'reader@example.com', 'role': 'reader'})
    assert response.status_code == 200, response.text
    kb = client.post('/api/knowledge-bases', headers=owner,
                     json={'name': '组织制度', 'organization_id': org}).json()['id']
    return org, kb


def ask(client, headers, kb, **kwargs):
    response = client.post('/api/chat', headers=headers,
        json={'question': '住宿报销上限是多少', 'knowledge_base_ids': [kb], **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_organization_roles_and_history_revocation(client):
    owner, reader = register(client), register(client, 'reader@example.com')
    org, kb = shared_space(client, owner, reader)
    result = upload(client, owner, kb, '住宿报销上限500元。'.encode())
    assert wait_job(client, owner, result['job_id'])['status'] == 'completed'
    doc = result['document']['id']
    assert client.get('/api/knowledge-bases', headers=reader).json()[0]['role'] == 'reader'
    assert client.get(f'/api/documents/{doc}/download', headers=reader).status_code == 200
    assert client.post('/api/documents/upload', headers=reader, data={'knowledge_base_id': kb},
        files={'file': ('x.txt', b'private')}).status_code == 403
    assert client.post(f'/api/documents/{doc}/retry', headers=reader).status_code == 403
    assert client.patch(f'/api/documents/{doc}/metadata', headers=reader, json={'source': 'x'}).status_code == 403
    assert client.delete(f'/api/documents/{doc}', headers=reader).status_code == 403
    answer = ask(client, reader, kb, request_id='shared-request-1')
    assert '500' in answer['answer']
    reader_id = client.get('/api/auth/me', headers=reader).json()['id']
    assert client.delete(f'/api/organizations/{org}/members/{reader_id}', headers=owner).status_code == 204
    assert client.get('/api/auth/me', headers=reader).status_code == 401
    response = client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': 'correct horse 47'})
    reader = {'Authorization': 'Bearer ' + response.json()['access_token']}
    assert client.get('/api/knowledge-bases', headers=reader).json() == []
    assert client.get(f'/api/documents/{doc}/versions', headers=reader).status_code == 404
    history = client.get('/api/conversations/' + answer['conversation_id'], headers=reader).json()
    assert history['messages'][1]['citations'] == [] and '500' not in history['messages'][1]['content']
    assert client.post('/api/chat', headers=reader, json={'question': '住宿报销上限是多少',
        'knowledge_base_ids': [kb], 'request_id': 'shared-request-1'}).status_code == 404


def test_last_admin_protection_and_audit_visibility(client):
    owner, reader = register(client), register(client, 'reader@example.com')
    org, kb = shared_space(client, owner, reader)
    owner_id = client.get('/api/auth/me', headers=owner).json()['id']
    assert client.delete(f'/api/organizations/{org}/members/{owner_id}', headers=owner).status_code == 409
    assert client.put(f'/api/organizations/{org}/members', headers=owner,
        json={'email': 'owner@example.com', 'role': 'reader'}).status_code == 409
    assert client.get('/api/audit-events', headers=reader, params={'organization_id': org}).status_code == 403
    uploaded = upload(client, owner, kb, '住宿报销上限500元。'.encode())
    wait_job(client, owner, uploaded['job_id'])
    doc = uploaded['document']['id']
    assert client.patch(f'/api/documents/{doc}/metadata', headers=owner, json={'source': 'audit-fixture'}).status_code == 200
    revision = client.get(f'/api/documents/{doc}/versions', headers=owner).json()[0]['id']
    restored = client.post(f'/api/documents/{doc}/versions/{revision}/restore', headers=owner)
    assert restored.status_code == 202
    wait_job(client, owner, restored.json()['job_id'])
    events = client.get('/api/audit-events', headers=owner, params={'organization_id': org}).json()
    assert any(row['action'] == 'membership.set_role.reader' for row in events)
    assert {row['action'] for row in events if row['resource_id'] == doc} >= {'document.metadata', 'document.restore'}
    assert 'correct horse' not in json.dumps(events)


def test_session_revocation_and_cookie_csrf(client):
    headers = register(client)
    token = headers['Authorization'].split()[1]
    assert client.cookies.get('rag_session') == token
    assert client.post('/api/knowledge-bases', json={'name': 'csrf'}).status_code == 403
    assert client.post('/api/knowledge-bases', headers={'X-RAG-Request': '1', 'Origin': 'https://evil.test'},
                       json={'name': 'csrf'}).status_code == 403
    assert client.post('/api/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    with client.app.state.database.session() as db:
        assert db.scalar(select(AuthSession)).revoked


def test_login_rate_limits_before_password_work(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'auth_rate_per_minute', 2)
    body = {'email': 'missing@example.com', 'password': 'incorrect password'}
    assert client.post('/api/auth/login', json=body).status_code == 401
    assert client.post('/api/auth/login', json=body).status_code == 401
    response = client.post('/api/auth/login', json=body)
    assert response.status_code == 429 and response.headers['Retry-After']


def test_storage_and_pending_task_quotas(client, monkeypatch):
    owner = register(client)
    kb = create_kb(client, owner)
    result = upload(client, owner, kb)
    wait_job(client, owner, result['job_id'])
    monkeypatch.setattr(client.app.state.settings, 'user_storage_mb', 1)
    from app.models import DocumentRevision
    with client.app.state.database.session() as db:
        db.execute(update(DocumentRevision).values(size_bytes=1024 * 1024))
        db.commit()
    response = client.post('/api/documents/upload', headers=owner, data={'knowledge_base_id': kb},
                           files={'file': ('second.txt', b'new content')})
    assert response.status_code == 429
    assert len(client.get('/api/documents', headers=owner).json()) == 1


def test_idempotency_and_conversation_busy_guard(client):
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    first = ask(client, headers, kb, request_id='idempotent-1')
    second = ask(client, headers, kb, request_id='idempotent-1')
    assert second['idempotent_replay'] and first['message_id'] == second['message_id']
    assert client.post('/api/chat', headers=headers, json={'question': 'different', 'request_id': 'idempotent-1'}).status_code == 409
    from app.models import Conversation
    with client.app.state.database.session() as db:
        db.execute(update(Conversation).where(Conversation.id == first['conversation_id']).values(
            lease_token='another-request', lease_expires_at=utcnow() + timedelta(minutes=5)))
        db.commit()
    response = client.post('/api/chat', headers=headers, json={'question': '追问', 'conversation_id': first['conversation_id']})
    assert response.status_code == 409


def test_durable_delete_during_qdrant_outage(client, monkeypatch):
    from app.services import retrieval
    from app.services.cleanup import process_cleanup
    headers = register(client)
    kb = create_kb(client, headers)
    uploads = [upload(client, headers, kb, f'制度{i}：住宿报销500元。'.encode(), f'{i}.txt') for i in range(2)]
    for item in uploads:
        wait_job(client, headers, item['job_id'])
    # Drive cleanup manually so assertions cannot race an independently claimed lease.
    client.app.state.worker_stop.set()
    client.app.state.worker_thread.join(timeout=10)
    real_delete = retrieval.delete_document
    monkeypatch.setattr(retrieval, 'delete_document', lambda doc: (_ for _ in ()).throw(ConnectionError('outage')))
    assert client.delete('/api/knowledge-bases/' + kb, headers=headers).status_code == 204
    assert client.get('/api/documents', headers=headers).json() == []
    with client.app.state.database.session() as db:
        assert db.scalar(select(Document)) is None
        tasks = db.scalars(select(CleanupTask).where(CleanupTask.revision_id.is_(None))).all()
        assert len(tasks) == 2
        ids = [task.id for task in tasks]
    for task_id in ids:
        process_cleanup(client.app.state.database, client.app.state.settings, task_id)
    with client.app.state.database.session() as db:
        assert all(db.get(CleanupTask, task_id).status == 'queued' for task_id in ids)
        db.execute(update(CleanupTask).where(CleanupTask.id.in_(ids)).values(available_at=utcnow()))
        db.commit()
    monkeypatch.setattr(retrieval, 'delete_document', real_delete)
    for task_id in ids:
        process_cleanup(client.app.state.database, client.app.state.settings, task_id)
    with client.app.state.database.session() as db:
        assert all(db.get(CleanupTask, task_id).status == 'completed' for task_id in ids)


def test_version_rebuild_keeps_old_until_atomic_switch_and_can_restore(client, monkeypatch):
    from app.services import parsing
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    doc = uploaded['document']['id']
    original = client.get(f'/api/documents/{doc}/versions', headers=headers).json()[0]['id']
    entered, resume = threading.Event(), threading.Event()
    real_parse = parsing.parse_document
    def blocking_parse(path, filename, **kwargs):
        if b'600' in path.read_bytes():
            entered.set()
            assert resume.wait(10)
        return real_parse(path, filename, **kwargs)
    monkeypatch.setattr(parsing, 'parse_document', blocking_parse)
    try:
        response = client.post('/api/documents/upload', headers=headers,
            data={'knowledge_base_id': kb, 'document_id': doc}, files={'file': ('updated.txt', '住宿报销上限600元。'.encode())})
        assert response.status_code == 201, response.text
        assert entered.wait(5)
        assert '500' in ask(client, headers, kb)['answer']
        resume.set()
        assert wait_job(client, headers, response.json()['job_id'])['status'] == 'completed'
        answer = ask(client, headers, kb)
        assert '600' in answer['answer'] and answer['citations'][0]['revision_id'] != original
        restored = client.post(f'/api/documents/{doc}/versions/{original}/restore', headers=headers)
        assert restored.status_code == 202, restored.text
        wait_job(client, headers, restored.json()['job_id'])
        assert '500' in ask(client, headers, kb)['answer']
        assert '500' in client.get(f'/api/documents/{doc}/versions/{original}/download', headers=headers).text
    finally:
        resume.set()


def test_expiry_invalidates_cached_answer(client):
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    assert '500' in ask(client, headers, kb)['answer']
    doc = uploaded['document']['id']
    expired = client.patch(f'/api/documents/{doc}/metadata', headers=headers,
        json={'expires_at': '2020-01-01T00:00:00Z', 'source': 'policy:v1'})
    assert expired.status_code == 200
    answer = ask(client, headers, kb)
    assert answer['citations'] == [] and answer['abstained']


def test_persistent_bm25_loads_only_candidates(client, monkeypatch):
    from app.services import corpus, evidence
    from app.services.retrieval import retrieve
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    from app.models import User
    with client.app.state.database.session() as db:
        user = db.scalar(select(User))
        scope = corpus.CorpusScope(db, user, [kb])
        real_tokenize = evidence.tokenize
        calls = []
        def track(text):
            calls.append(text)
            return real_tokenize(text)
        monkeypatch.setattr(evidence, 'tokenize', track)
        hits = retrieve('住宿报销', scope, strategy='bm25')
        assert hits and hits[0]['document_id'] == uploaded['document']['id']
        assert calls == ['住宿报销']


def test_numeric_verification_and_semantic_fail_closed(monkeypatch):
    from app.config import Settings
    from app.services import answering
    from app.services.verification import verify_answer
    settings = Settings(_env_file=None)
    sources = [{'text': '住宿报销每晚不超过500元。申请在30天内提交。'}]
    assert verify_answer('限额500元，30天内提交 [1]', sources, settings)['passed']
    assert not verify_answer('限额5000元 [1]', sources, settings)['passed']
    assert not verify_answer('1. 5000元 [1]', sources, settings)['passed']
    settings.citation_verifier = 'llm'
    monkeypatch.setattr(answering, 'configured_llm', lambda *a: SimpleNamespace(invoke=lambda _: SimpleNamespace(content='not json')))
    result = verify_answer('限额500元 [1]', sources, settings)
    assert not result['passed'] and result['issues'] == ['verifier_unavailable']


def test_true_provider_stream_and_verified_final_answer(client, monkeypatch):
    from app.config import get_settings
    from app.services import answering
    from langchain_core.messages import AIMessageChunk
    headers = register(client)
    kb = create_kb(client, headers)
    uploaded = upload(client, headers, kb, '住宿报销上限500元。'.encode())
    wait_job(client, headers, uploaded['job_id'])
    monkeypatch.setattr(get_settings(), 'llm_model', 'stub-stream')
    monkeypatch.setattr(get_settings(), 'llm_provider', 'ollama')
    class Provider:
        def stream(self, _):
            yield AIMessageChunk(content='住宿报销上限')
            yield AIMessageChunk(content='5000元 [1]')
        def invoke(self, _):
            # The initial generation streams; one bounded verification repair may invoke.
            from langchain_core.messages import AIMessage
            return AIMessage(content='住宿报销上限5000元 [1]')
    monkeypatch.setattr(answering, 'configured_llm', lambda: Provider())
    response = client.post('/api/chat/stream', headers=headers,
        json={'question': '住宿报销上限是多少', 'knowledge_base_ids': [kb], 'request_id': 'stream-test-1'})
    assert response.status_code == 200
    assert response.text.count('event: draft') == 2
    assert response.text.index('event: draft') < response.text.index('event: done')
    final = json.loads(response.text.split('event: done\ndata: ')[1].split('\n\n')[0])
    assert '5000' not in final['answer'] and '500' in final['answer']
    assert not final['verification']['passed']


def test_readiness_and_protected_metrics(client, monkeypatch):
    monkeypatch.setattr(client.app.state.settings, 'metrics_token', 'test-metrics-secret')
    assert client.get('/api/metrics').status_code == 401
    result = client.get('/api/metrics', headers={'Authorization': 'Bearer test-metrics-secret'})
    assert result.status_code == 200 and 'rag_http_requests_total' in result.text
    response = client.get('/api/ready')
    assert response.status_code in {200, 503} and response.json()['checks']['database']


def test_incremental_folder_sync_and_path_boundary(client, tmp_path, monkeypatch):
    headers = register(client)
    kb = create_kb(client, headers)
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'policy.txt').write_text('住宿报销上限500元。', encoding='utf-8')
    monkeypatch.setattr(client.app.state.settings, 'source_sync_root', root)
    body = {'knowledge_base_id': kb, 'relative_path': '.'}
    response = client.post('/api/sources/sync', headers=headers, json=body)
    assert response.status_code == 200, response.text
    row = response.json()['files'][0]
    wait_job(client, headers, row['job_id'])
    assert client.post('/api/sources/sync', headers=headers, json=body).json()['files'][0]['unchanged']
    assert client.post('/api/sources/sync', headers=headers,
        json={**body, 'relative_path': '../outside'}).status_code == 422


def test_legacy_database_migrates_without_losing_content(tmp_path):
    from app.config import Settings
    from app.db import Database
    from migrations.schema_v1 import Base
    settings = Settings(_env_file=None, database_url='sqlite:///' + (tmp_path / 'legacy.db').as_posix(),
                        redis_url='', qdrant_url='', environment='development')
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    tables = Base.metadata.tables
    with database.engine.begin() as connection:
        connection.execute(tables['users'].insert().values(id='u', email='legacy@example.com', name='legacy', password_hash='x'))
        connection.execute(tables['knowledge_bases'].insert().values(id='k', user_id='u', name='legacy'))
        connection.execute(tables['documents'].insert().values(id='d', knowledge_base_id='k', name='policy.txt',
            stored_path='unused', sha256='hash', size_bytes=20, status='ready', chunk_count=1))
        connection.execute(tables['chunks'].insert().values(id='c', document_id='d', text='报销500元', ordinal=0))
    database.initialize()
    database.initialize()
    from app.models import Chunk, ChunkTerm
    with database.session() as db:
        doc, chunk = db.get(Document, 'd'), db.get(Chunk, 'c')
        assert doc.active_revision_id == chunk.revision_id and chunk.text == '报销500元'
        assert db.scalar(select(ChunkTerm).where(ChunkTerm.chunk_id == 'c'))
    database.engine.dispose()
