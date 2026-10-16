"""Check the project's actual local services without printing secret values."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from dotenv import dotenv_values
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def check_postgres(settings):
    database_url = settings.get("RAG_DATABASE_URL", "")
    parsed = make_url(database_url)
    if parsed.database != "rag_knowledge_assistant":
        raise ValueError("数据库必须是独立命名空间 rag_knowledge_assistant")
    engine = create_engine(database_url, connect_args={"connect_timeout": 5}, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT current_database(), 1")).one()
        return {"ok": row[1] == 1, "database": row[0]}
    finally:
        engine.dispose()


def check_redis(settings):
    with Redis.from_url(settings["RAG_REDIS_URL"], socket_connect_timeout=5, socket_timeout=5) as client:
        if client.connection_pool.connection_kwargs.get("db") != 6:
            raise ValueError("Redis 必须使用独立 DB6")
        return {"ok": bool(client.ping()), "database": 6}


def check_qdrant(settings):
    headers = {"api-key": settings.get("RAG_QDRANT_API_KEY", "")} if settings.get("RAG_QDRANT_API_KEY") else {}
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(settings["RAG_QDRANT_URL"].rstrip("/") + "/collections", headers=headers)
        response.raise_for_status()
    prefix = settings.get("RAG_QDRANT_COLLECTION_PREFIX", "")
    if prefix != "rag_assistant":
        raise ValueError("Qdrant 必须使用独立 rag_assistant 前缀")
    collections = response.json()["result"]["collections"]
    return {"ok": True, "collection_prefix": prefix, "project_collections":
            sum(item["name"].startswith(prefix + "_") for item in collections)}


def check_ollama(settings):
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(settings["RAG_LLM_BASE_URL"].rstrip("/") + "/api/tags")
        response.raise_for_status()
    available = {model["name"] for model in response.json()["models"]}
    required = {settings["RAG_LLM_MODEL"], settings["RAG_EMBEDDING_MODEL"]}
    missing = sorted(required - available)
    if missing:
        raise ValueError("缺少已配置的本地模型：" + ", ".join(missing))
    return {"ok": True, "models": sorted(required), "embedding_dimension": int(settings["RAG_EMBEDDING_DIMENSION"])}


def main():
    config = ROOT / ".env"
    if not config.is_file():
        raise SystemExit("请先运行 scripts/configure-local.py 生成本地配置")
    settings = dotenv_values(config)
    checks = {"postgres": check_postgres, "redis": check_redis, "qdrant": check_qdrant, "ollama": check_ollama}
    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(check, settings): name for name, check in checks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:
                # Exception strings can contain connection URLs/passwords.
                results[name] = {"ok": False, "error_type": type(error).__name__,
                                 "hint": "检查本机服务、认证配置和端口映射；未输出连接凭证"}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(result["ok"] for result in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
