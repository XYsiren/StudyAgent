"""Run real infrastructure regressions and a guarded PG/files/Qdrant restore drill."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    from app.config import get_settings
    from app.main import create_app
    from app.maintenance import backup, restore, verify_backup
    from app.models import Document
    from app.services import retrieval
    from benchmark import wait_job
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-container')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/infrastructure-validation.json')
    args, original = parser.parse_args(), get_settings()
    url = make_url(original.database_url)
    if not url.drivername.startswith('postgresql') or not original.redis_url or not original.qdrant_url:
        parser.error('Configure PostgreSQL, Redis and Qdrant in the project .env first')
    suffix = uuid.uuid4().hex[:12]
    db_names = ['rag_accept_' + suffix, 'rag_restore_' + suffix]
    prefixes = ['rag_accept_' + suffix, 'rag_restore_' + suffix]
    admin = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    report = {'storage': ['PostgreSQL', 'Redis', 'Qdrant server'], 'isolated': True}
    journal = ROOT / 'artifacts/infrastructure-resources.json'
    journal.parent.mkdir(exist_ok=True)
    journal.write_text(json.dumps({'databases': db_names, 'prefixes': prefixes}), encoding='utf-8')
    try:
        with admin.connect() as connection:
            for name in db_names:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
        with tempfile.TemporaryDirectory(prefix='rag-infrastructure-') as temporary:
            temporary = Path(temporary)
            environment = {**os.environ, 'RAG_TEST_DATABASE_URL': url.set(database=db_names[0]).render_as_string(hide_password=False),
                'RAG_TEST_REDIS_URL': original.redis_url, 'RAG_TEST_QDRANT_URL': original.qdrant_url,
                'RAG_QDRANT_API_KEY': original.qdrant_api_key}
            junit = temporary / 'junit.xml'
            completed = subprocess.run([sys.executable, '-m', 'pytest', '-vv', '-o', 'faulthandler_timeout=60', '--junitxml=' + str(junit)],
                cwd=ROOT / 'backend', env={**environment, 'PYTHONUNBUFFERED': '1'}, check=False, timeout=600)
            tree = ET.parse(junit).getroot()
            report['tests'] = {key: sum(int(suite.attrib.get(key, 0)) for suite in tree.iter('testsuite'))
                for key in ['tests', 'failures', 'errors', 'skipped']}
            if completed.returncode:
                raise RuntimeError('Infrastructure regressions failed')
            os.environ.update(RAG_DATABASE_URL=url.set(database=db_names[0]).render_as_string(hide_password=False),
                RAG_DATA_DIR=str(temporary / 'source-data'), RAG_REDIS_URL='', RAG_QDRANT_URL=original.qdrant_url,
                RAG_QDRANT_API_KEY=original.qdrant_api_key, RAG_QDRANT_COLLECTION_PREFIX=prefixes[0],
                RAG_EMBEDDING_PROVIDER='hash', RAG_EMBEDDING_MODEL='deterministic-hash-demo', RAG_EMBEDDING_DIMENSION='384',
                RAG_LLM_MODEL='', RAG_LLM_API_KEY='', RAG_RERANKER_MODEL='', RAG_ENVIRONMENT='development',
                RAG_WORKER_POLL_SECONDS='0.05')
            get_settings.cache_clear()
            source = get_settings()
            with TestClient(create_app()) as client:
                account = client.post('/api/auth/register', json={'email': 'recovery@fixture.local',
                    'password': 'Restore-drill-fixture-47', 'name': 'Recovery drill'}).json()
                headers = {'Authorization': 'Bearer ' + account['access_token']}
                kb = client.post('/api/knowledge-bases', headers=headers, json={'name': 'Recovery fixtures'}).json()['id']
                uploaded = client.post('/api/documents/upload', headers=headers, data={'knowledge_base_id': kb},
                    files={'file': ('policy.txt', '住宿报销上限500元，30天内提交。'.encode())}).json()
                wait_job(client, headers, uploaded['job_id'])
                answer = client.post('/api/chat', headers=headers, json={'question': '住宿报销上限', 'knowledge_base_ids': [kb]})
                assert answer.status_code == 200 and '500' in answer.json()['answer']
            snapshot = temporary / 'backup'
            report['backup'] = backup(source, snapshot, quiesced=True, postgres_container=args.postgres_container)
            report['backup'].pop('backup')
            report['backup']['integrity_checked'] = bool(verify_backup(snapshot)['files'])
            target = source.model_copy(update={'database_url': url.set(database=db_names[1]).render_as_string(hide_password=False),
                'data_dir': temporary / 'target-data', 'qdrant_collection_prefix': prefixes[1]})
            report['restore'] = restore(target, snapshot, quiesced=True, postgres_container=args.postgres_container)
            if retrieval.client.cache_info().currsize:
                retrieval.client().close()
                retrieval.client.cache_clear()
            os.environ.update(RAG_DATABASE_URL=target.database_url, RAG_DATA_DIR=str(target.data_dir),
                              RAG_QDRANT_COLLECTION_PREFIX=prefixes[1])
            get_settings.cache_clear()
            with TestClient(create_app()) as client:
                logged = client.post('/api/auth/login', json={'email': 'recovery@fixture.local',
                    'password': 'Restore-drill-fixture-47'}).json()
                headers = {'Authorization': 'Bearer ' + logged['access_token']}
                with client.app.state.database.session() as db:
                    doc = db.scalar(select(Document))
                    assert Path(doc.stored_path).is_relative_to(target.data_dir)
                response = client.post('/api/chat', headers=headers, json={'question': '住宿报销上限', 'knowledge_base_ids': [kb]})
                assert response.status_code == 200 and '500' in response.json()['answer'] and response.json()['citations']
                report['restore']['retrieval_after_restore'] = True
                report['restore']['original_file_download'] = client.get(
                    '/api/documents/' + uploaded['document']['id'] + '/download', headers=headers).status_code == 200
            report['passed'] = True
    finally:
        if retrieval.client.cache_info().currsize:
            retrieval.client().close()
            retrieval.client.cache_clear()
        from qdrant_client import QdrantClient
        with closing(QdrantClient(url=original.qdrant_url, api_key=original.qdrant_api_key or None)) as store:
            for collection in store.get_collections().collections:
                if any(collection.name.startswith(prefix + '_') for prefix in prefixes):
                    store.delete_collection(collection.name)
        with admin.connect() as connection:
            for name in db_names:
                connection.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:database AND pid<>pg_backend_pid()'), {'database': name})
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
        journal.unlink(missing_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
