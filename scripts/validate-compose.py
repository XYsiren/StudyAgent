"""Exercise the isolated Compose stack through its real HTTP entrypoints."""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('artifacts/compose-acceptance.env'))
    parser.add_argument('--output', type=Path, default=Path('docs/artifacts/enterprise-compose.json'))
    args = parser.parse_args()
    settings = dotenv_values(args.env_file)
    api = 'http://127.0.0.1:' + settings.get('RAG_API_PORT', '18010') + '/api/'
    web = 'http://127.0.0.1:' + settings.get('RAG_WEB_PORT', '18080')
    report = {'mode': 'isolated Docker Compose', 'model_mode': 'hash embeddings / extractive answers', 'checks': {}}
    with httpx.Client(base_url=api, timeout=30, headers={'X-RAG-Request': '1'}, trust_env=False) as client:
        readiness = client.get('ready')
        assert readiness.status_code == 200, 'Compose readiness failed'
        report['checks']['dependency_readiness'] = readiness.json()['checks']
        assert client.get(web).status_code == 200
        report['checks']['frontend_proxy'] = 'Knowledge OS' in client.get(web).text
        email, password = 'compose-' + uuid.uuid4().hex[:12] + '@fixture.local', uuid.uuid4().hex
        response = client.post('auth/register', json={'email': email, 'password': password, 'name': 'Compose acceptance'})
        assert response.status_code == 201
        assert client.cookies.get('rag_session')
        kb = client.post('knowledge-bases', json={'name': 'Compose fixtures'}).json()['id']
        report['checks']['cookie_auth_and_csrf_header'] = True
        upload = client.post('documents/upload', data={'knowledge_base_id': kb},
            files={'file': ('policy.txt', '住宿报销上限每晚500元，申请在30天内提交。'.encode())})
        assert upload.status_code == 201
        data = upload.json()
        for _ in range(120):
            job = client.get('jobs/' + data['job_id']).json()
            if job['status'] in {'completed', 'failed'}:
                break
            time.sleep(.5)
        assert job['status'] == 'completed', 'Compose ingestion failed'
        report['checks']['standalone_ingestion_worker'] = True
        response = client.post('chat/stream', json={'question': '住宿报销上限是多少',
            'knowledge_base_ids': [kb], 'request_id': uuid.uuid4().hex})
        assert response.status_code == 200 and 'event: done' in response.text
        result = json.loads(response.text.split('event: done\ndata: ')[1].split('\n\n')[0])
        assert '500' in result['answer'] and result['citations']
        report['checks']['chat_and_citations'] = True
        submitted = client.post('evaluations', json={'name': 'Compose evaluation', 'knowledge_base_ids': [kb],
            'cases': [{'question': '住宿报销上限', 'reference': '500元', 'expected_document_ids': [data['document']['id']]}]})
        assert submitted.status_code == 202
        run_id = submitted.json()['id']
        for _ in range(120):
            run = client.get('evaluations/' + run_id).json()
            if run['status'] in {'completed', 'failed'}:
                break
            time.sleep(.5)
        assert run['status'] == 'completed' and run['metrics']['hit_at_5'] == 1
        report['checks']['standalone_evaluation_worker'] = True
        token = settings['RAG_METRICS_TOKEN']
        metric_response = client.get('metrics', headers={'Authorization': 'Bearer ' + token})
        assert metric_response.status_code == 200 and 'rag_workers_alive' in metric_response.text
        report['checks']['authenticated_metrics'] = True
        assert client.post('auth/logout').status_code == 204
        assert client.get('auth/me').status_code == 401
        report['checks']['session_revocation'] = True
    report['passed'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
