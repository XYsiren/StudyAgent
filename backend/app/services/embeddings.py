"""Explicit demo embeddings and optional semantic model adapters."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np

from app.config import get_settings


def tokenize(text: str) -> list[str]:
    """Chinese word segmentation plus ASCII words, shared by BM25 and hash vectors."""
    import jieba

    return [t.lower() for t in jieba.cut(text) if re.search(r"[\w\u4e00-\u9fff]", t)]


@lru_cache(maxsize=2)
def semantic_model(name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name, trust_remote_code=False)


def dimension() -> int:
    s = get_settings()
    if s.embedding_provider == "sentence-transformers":
        return int(semantic_model(s.embedding_model).get_sentence_embedding_dimension())
    return s.embedding_dimension


def embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    if not texts:
        return []
    if s.embedding_provider == "hash":
        vectors = np.zeros((len(texts), s.embedding_dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
                vectors[row, int.from_bytes(digest[:4], "little") % s.embedding_dimension] += 1 if digest[4] % 2 else -1
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return (vectors / np.maximum(norms, 1e-9)).tolist()
    if s.embedding_provider == "sentence-transformers":
        return semantic_model(s.embedding_model).encode(texts, normalize_embeddings=True).tolist()
    if s.embedding_provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        model = OllamaEmbeddings(model=s.embedding_model,
            base_url=(s.embedding_base_url or s.llm_base_url).removesuffix("/v1").rstrip("/"),
            num_ctx=s.embedding_num_ctx,
            client_kwargs={"timeout": s.model_timeout})
        vectors = []
        for offset in range(0, len(texts), s.embedding_batch_size):
            vectors.extend(model.embed_documents(texts[offset:offset + s.embedding_batch_size]))
        if vectors and len(vectors[0]) != s.embedding_dimension:
            raise ValueError(f"实际 embedding 为 {len(vectors[0])} 维，RAG_EMBEDDING_DIMENSION={s.embedding_dimension}；请修正后重试索引")
        return vectors
    if s.embedding_provider == "openai":
        key = s.embedding_api_key or s.llm_api_key
        if not key:
            raise ValueError("OpenAI embedding 需要设置 RAG_LLM_API_KEY")
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=s.embedding_model, api_key=key, base_url=s.embedding_base_url or s.llm_base_url,
            dimensions=s.embedding_dimension, request_timeout=60, max_retries=2,
            check_embedding_ctx_length=False,
        ).embed_documents(texts)
    raise ValueError(f"未知 embedding_provider: {s.embedding_provider}")
