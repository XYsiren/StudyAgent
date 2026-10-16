"""Qdrant dense search + BM25, reciprocal rank fusion and optional reranking."""
from __future__ import annotations

import hashlib
import threading
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient, models
from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.services.embeddings import dimension, embed, tokenize

_lock = threading.RLock()


@lru_cache(maxsize=1)
def client() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url:
        return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=60)
    path = s.data_dir / "qdrant"
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def collection() -> str:
    s = get_settings()
    identity = f"{s.embedding_provider}:{s.embedding_model}:{getattr(s, 'embedding_base_url', '')}:{dimension()}:jieba-v1"
    return s.qdrant_collection_prefix + "_" + hashlib.sha256(identity.encode()).hexdigest()[:12]


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def index_chunks(chunks: list[dict]) -> None:
    if not chunks:
        return
    with _lock:
        store, name = client(), collection()
        if not store.collection_exists(name):
            store.create_collection(name, vectors_config=models.VectorParams(size=dimension(), distance=models.Distance.COSINE))
            if get_settings().qdrant_url:
                for field in ("knowledge_base_id", "document_id", "chunk_id"):
                    store.create_payload_index(name, field, field_schema=models.PayloadSchemaType.KEYWORD)
        for offset in range(0, len(chunks), 64):
            batch = chunks[offset:offset + 64]
            vectors = embed([c["text"] for c in batch])
            points = [models.PointStruct(id=point_id(c["id"]), vector=v, payload={
                "chunk_id": c["id"], "document_id": c["document_id"], "knowledge_base_id": c["knowledge_base_id"],
            }) for c, v in zip(batch, vectors, strict=True)]
            store.upsert(name, points, wait=True)


def delete_document(document_id: str) -> None:
    with _lock:
        store = client()
        # Clean old model collections too, so switching embeddings never resurrects deleted content.
        for item in store.get_collections().collections:
            if item.name.startswith(get_settings().qdrant_collection_prefix + "_"):
                store.delete(item.name, models.FilterSelector(filter=models.Filter(must=[
                    models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
                ])), wait=True)


@lru_cache(maxsize=1)
def reranker(name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name, trust_remote_code=False)


def rerank_candidates(question: str, candidates: list[dict], top_k: int) -> list[dict]:
    model = get_settings().reranker_model
    if model and candidates:
        values = reranker(model).predict([(question, c["text"]) for c in candidates])
        for item, value in zip(candidates, values, strict=True):
            item["rerank_score"] = float(value)
        candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
    return candidates[:top_k]


def retrieve(question: str, chunks: list[dict], top_k: int = 5, strategy: str = "hybrid", rerank_results: bool = True) -> list[dict]:
    if not chunks:
        return []
    if strategy not in {"hybrid", "dense", "bm25"}:
        raise ValueError("strategy must be hybrid, dense or bm25")
    top_k = max(1, min(top_k, 20))
    by_id = {c["id"]: c for c in chunks}
    limit = min(len(chunks), max(20, top_k * 4))
    dense, sparse = [], []
    dense_scores, sparse_scores = {}, {}
    if strategy != "bm25":
        with _lock:
            store, name = client(), collection()
            if not store.collection_exists(name):
                raise ValueError("当前 embedding 配置尚无索引，请重新索引文档（失败/就绪文档均可重试）")
            # Authorization is applied BEFORE vector ranking, down to the exact allowed chunk IDs.
            scope = models.Filter(must=[models.HasIdCondition(has_id=[point_id(i) for i in by_id])])
            hits = store.query_points(name, query=embed([question])[0], query_filter=scope, limit=limit).points
        for hit in hits:
            cid = (hit.payload or {}).get("chunk_id")
            if cid in by_id and hit.score > 0:
                dense.append(cid)
                dense_scores[cid] = float(hit.score)
    if strategy != "dense":
        corpus = [tokenize(c["text"]) or ["__empty__"] for c in chunks]
        # BM25 can return negative IDF for tiny corpora. Positive floor keeps useful common terms.
        bm25 = BM25Okapi(corpus)
        for word, value in bm25.idf.items():
            if value <= 0:
                bm25.idf[word] = 0.1
        scores = bm25.get_scores(tokenize(question))
        ordered = sorted(zip(chunks, scores, strict=True), key=lambda x: float(x[1]), reverse=True)
        for c, score in ordered[:limit]:
            if score > 0:
                sparse.append(c["id"])
                sparse_scores[c["id"]] = float(score)
    fused = {}
    for ranking in (dense, sparse):
        for rank, cid in enumerate(ranking, 1):
            fused[cid] = fused.get(cid, 0.0) + 1 / (60 + rank)
    ids = sorted(fused, key=lambda cid: fused[cid], reverse=True)[:limit]
    result = [{**by_id[cid], "chunk_id": cid, "score": fused[cid], "retrieval_score": fused[cid],
               "dense_score": dense_scores.get(cid), "bm25_score": sparse_scores.get(cid)} for cid in ids]
    return rerank_candidates(question, result, top_k) if rerank_results and strategy == "hybrid" else result[:top_k]
