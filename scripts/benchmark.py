"""Isolated business regressions and a persistent-BM25 capacity probe (no user data)."""
import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_job(client, headers, job_id):
    for _ in range(1800):
        result = client.get('/api/jobs/' + job_id, headers=headers).json()
        if result.get('status') in {'completed', 'failed'}:
            require(result['status'] == 'completed', 'Fixture ingestion failed: ' + str(result.get('error')))
            return
        time.sleep(0.1)
    raise TimeoutError('Fixture ingestion timed out')


def capacity_probe(database, user, settings, count, queries):
    from app.models import Chunk, ChunkTerm, Document, DocumentRevision, KnowledgeBase, new_id
    from app.services.corpus import CorpusScope, term_rows
    from app.services.retrieval import retrieve
    from sqlalchemy import insert
    with database.session() as db:
        kb = KnowledgeBase(user_id=user.id, name='Capacity fixture')
        db.add(kb)
        db.flush()
        doc = Document(knowledge_base_id=kb.id, name='synthetic.txt', stored_path='synthetic-only',
            sha256='capacity-fixture', size_bytes=count * 100, status='ready', chunk_count=count)
        db.add(doc)
        db.flush()
        rev = DocumentRevision(document_id=doc.id, name=doc.name, stored_path=doc.stored_path,
            sha256=doc.sha256, size_bytes=doc.size_bytes, status='ready', chunk_count=count)
        db.add(rev)
        db.flush()
        doc.active_revision_id = rev.id
        db.flush()
        for start in range(0, count, 200):
            chunks, postings = [], []
            for number in range(start, min(start + 200, count)):
                text = f'设备型号 GX{number} 的额定功率为 {100 + number % 600} 瓦，维护周期为 {10 + number % 20} 天。'
                cid = new_id()
                length, terms = term_rows(cid, text)
                chunks.append({'id': cid, 'document_id': doc.id, 'revision_id': rev.id, 'text': text,
                    'ordinal': number, 'token_count': length})
                postings.extend({'chunk_id': row.chunk_id, 'term': row.term, 'frequency': row.frequency} for row in terms)
            db.execute(insert(Chunk), chunks)
            db.execute(insert(ChunkTerm), postings)
        db.commit()
        kb_id = kb.id
    durations, correct = [], 0
    for index in range(queries):
        number = (index * 997) % count
        started = time.perf_counter()
        with database.session() as db:
            result = retrieve(f'GX{number} 额定功率', CorpusScope(db, user, [kb_id]), strategy='bm25')
        durations.append((time.perf_counter() - started) * 1000)
        correct += bool(result and f'GX{number} ' in result[0]['text'])
    return {'chunks': count, 'queries': queries, 'strategy': 'SQL persistent BM25 only', 'top1_hits': correct,
        'p50_ms': round(statistics.median(durations), 2), 'p95_ms': round(sorted(durations)[math.ceil(len(durations) * .95) - 1], 2),
        'note': 'Synthetic single-process retrieval probe; excludes embedding, reranking, LLM and network latency.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['demo', 'configured'], default='demo')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--case-indices', default='', help='Optional comma-separated zero-based subset indices')
    parser.add_argument('--load-chunks', type=int, default=5000)
    parser.add_argument('--queries', type=int, default=40)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'benchmark.json')
    args = parser.parse_args()
    require(1 <= args.load_chunks <= 100000 and 1 <= args.queries <= 1000, 'Invalid probe size')
    bundle = json.loads((ROOT / 'examples/benchmarks/business-v1.json').read_text(encoding='utf-8'))
    prefix = 'rag_bench_' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='rag-benchmark-') as temporary:
        os.environ.update(RAG_DATA_DIR=temporary, RAG_DATABASE_URL='sqlite:///' + Path(temporary, 'bench.db').as_posix(),
            RAG_REDIS_URL='', RAG_ENVIRONMENT='development', RAG_WORKER_POLL_SECONDS='0.05',
            RAG_QDRANT_COLLECTION_PREFIX=prefix, RAG_CHAT_RATE_PER_MINUTE='200', RAG_MAX_PENDING_JOBS='100')
        if args.mode == 'demo':
            os.environ.update(RAG_EMBEDDING_PROVIDER='hash', RAG_QDRANT_URL='', RAG_LLM_MODEL='',
                RAG_LLM_API_KEY='', RAG_RERANKER_MODEL='', RAG_CITATION_VERIFIER='numeric',
                RAG_EMBEDDING_MODEL='deterministic-hash-demo', RAG_EMBEDDING_DIMENSION='384')
        from app.config import get_settings
        from app.main import create_app
        from app.models import User
        from app.services import retrieval
        from fastapi.testclient import TestClient
        from sqlalchemy import select
        settings = get_settings()
        rows, document_ids = [], {}
        try:
            with TestClient(create_app()) as client:
                account = client.post('/api/auth/register', json={'email': 'benchmark@fixture.local',
                    'password': uuid.uuid4().hex, 'name': 'Synthetic benchmark'})
                require(account.status_code == 201, 'Registration failed')
                headers = {'Authorization': 'Bearer ' + account.json()['access_token']}
                kb = client.post('/api/knowledge-bases', headers=headers, json={'name': 'Business regressions'}).json()['id']
                for doc in bundle['documents']:
                    response = client.post('/api/documents/upload', headers=headers,
                        data={'knowledge_base_id': kb}, files={'file': (doc['name'], doc['text'].encode('utf-8'))})
                    require(response.status_code == 201, 'Upload failed')
                    result = response.json()
                    wait_job(client, headers, result['job_id'])
                    document_ids[doc['name']] = result['document']['id']
                    if doc.get('expires_at'):
                        require(client.patch('/api/documents/' + result['document']['id'] + '/metadata', headers=headers,
                            json={'expires_at': doc['expires_at'], 'source': 'benchmark:obsolete'}).status_code == 200, 'Expiry failed')
                cases = [bundle['cases'][int(index)] for index in args.case_indices.split(',')] if args.case_indices else (
                    bundle['cases'][:args.limit] if args.limit else bundle['cases'])
                for case in cases:
                    started = time.perf_counter()
                    response = client.post('/api/chat', headers=headers, json={'question': case['question'],
                        'knowledge_base_ids': [kb], 'mode': 'agent', 'request_id': uuid.uuid4().hex})
                    require(response.status_code == 200, f'Chat failed ({response.status_code})')
                    answer = response.json()
                    found = {citation['document_id'] for citation in answer['citations']}
                    expected = {document_ids[name] for name in case.get('expected_documents', [])}
                    rows.append({'question': case['question'], 'should_abstain': case.get('should_abstain', False),
                        'abstained': answer.get('abstained', not answer['citations']),
                        'hit_at_5': bool(found & expected) if expected else None,
                        'recall_at_5': len(found & expected) / len(expected) if expected else None,
                        'required_terms_present': all(term in answer['answer'] for term in case.get('required', [])),
                        'forbidden_terms_absent': all(term not in answer['answer'] for term in case.get('forbidden', [])),
                        'expired_source_absent': document_ids['旧住宿制度.md'] not in found,
                        'answer': answer['answer'], 'provider': answer['provider'], 'verification': answer.get('verification'),
                        'latency_ms': round((time.perf_counter() - started) * 1000, 2)})
                with client.app.state.database.session() as db:
                    user = db.scalar(select(User))
                load = capacity_probe(client.app.state.database, user, settings, args.load_chunks, args.queries)
            positives = [row for row in rows if row['hit_at_5'] is not None]
            negatives = [row for row in rows if row['should_abstain']]
            hit_rate = statistics.mean(row['hit_at_5'] for row in positives) if positives else None
            abstention = statistics.mean(row['abstained'] for row in negatives) if negatives else None
            gate = (hit_rate is None or hit_rate >= .85) and (abstention is None or abstention >= .9) and all(
                row['expired_source_absent'] for row in rows) and load['top1_hits'] == args.queries
            report = {'dataset': bundle['name'], 'provenance': bundle['provenance'], 'mode': args.mode,
                'generation_model': settings.llm_model or 'extractive', 'embedding_provider': settings.embedding_provider,
                'embedding_model': settings.embedding_model, 'citation_verifier': settings.citation_verifier,
                'cases': len(rows), 'positive_cases': len(positives), 'unanswerable_cases': len(negatives),
                'hit_at_5': hit_rate, 'recall_at_5': statistics.mean(row['recall_at_5'] for row in positives) if positives else None,
                'abstention_rate_on_unanswerable': statistics.mean(row['abstained'] for row in negatives) if negatives else None,
                'functional_gate_passed': gate, 'capacity_probe': load, 'results': rows,
                'limits': 'Fixture functional gate, not a production accuracy/SLA claim. String checks are lexical, not semantic grading.'}
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({key: value for key, value in report.items() if key != 'results'}, ensure_ascii=False, indent=2))
            require(gate, 'Business fixture functional gate failed; inspect the saved report')
        finally:
            if retrieval.client.cache_info().currsize:
                store = retrieval.client()
                if settings.qdrant_url:
                    for collection in store.get_collections().collections:
                        if collection.name.startswith(prefix + '_'):
                            store.delete_collection(collection.name)
                store.close()
                retrieval.client.cache_clear()


if __name__ == '__main__':
    main()
