"""Reuse an existing cook_rag local deployment without sharing its data namespace."""
import argparse
import json
import secrets
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def expose_postgres(original, container: str):
    """Expose an existing container without recreating or changing its database."""
    host, port = original.host or "127.0.0.1", original.port or 5432
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("--expose-postgres 仅允许本机 PostgreSQL")
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError:
        pass
    result = subprocess.run(["docker", "inspect", container, "--format", "{{json .NetworkSettings.Networks}}"],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SystemExit("无法检查来源 PostgreSQL 容器，请确认 Docker 已运行")
    networks = json.loads(result.stdout)
    if not networks:
        raise SystemExit("来源 PostgreSQL 容器未连接 Docker 网络")
    network = next(iter(networks))
    proxy_name = "rag-assistant-postgres-proxy"
    config = ROOT / "backend" / "data" / "postgres-proxy.conf"
    config.parent.mkdir(parents=True, exist_ok=True)
    # Container and network names come from a local operator argument/Docker;
    # reject config syntax so they can never inject an nginx directive.
    if not all(char.isalnum() or char in "_.-" for char in container):
        raise SystemExit("PostgreSQL 容器名称无效")
    config.write_text("events {}\nstream { server { listen 5432; "
                      f"proxy_connect_timeout 5s; proxy_timeout 1h; proxy_pass {container}:5432; "
                      "} }\n", encoding="utf-8")
    existing = subprocess.run(["docker", "inspect", proxy_name, "--format", "{{.State.Running}}"],
                              capture_output=True, text=True, timeout=15)
    if existing.returncode == 0:
        command = ["docker", "start", proxy_name]
    else:
        command = ["docker", "run", "-d", "--name", proxy_name, "--restart", "unless-stopped",
                   "--network", network, "-p", f"127.0.0.1:{port}:5432", "--mount",
                   f"type=bind,source={config},target=/etc/nginx/nginx.conf,readonly", "nginx:latest"]
    created = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if created.returncode:
        raise SystemExit("无法启动本机 PostgreSQL TCP 代理；未修改原 PostgreSQL 容器")
    print("已提供仅绑定 127.0.0.1 的 PostgreSQL TCP 代理；原容器及原数据库保持原状。")


def create_database(original, container: str):
    """Use container-local psql; credentials never enter command lines/logs."""
    query = "SELECT 1 FROM pg_database WHERE datname = 'rag_knowledge_assistant';"
    command = ["docker", "exec", "-i", "-u", "postgres", container, "psql", "-U", original.username or "postgres",
               "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At"]
    checked = subprocess.run(command, input=query, capture_output=True, text=True, timeout=20)
    if checked.returncode:
        raise SystemExit("无法通过容器 psql 检查独立数据库；未写入 .env")
    if "1" not in checked.stdout.splitlines():
        owner = (original.username or "postgres").replace('"', '""')
        created = subprocess.run(command, input=f'CREATE DATABASE rag_knowledge_assistant OWNER "{owner}";',
                                 capture_output=True, text=True, timeout=20)
        if created.returncode:
            raise SystemExit("无法创建独立数据库 rag_knowledge_assistant；未写入 .env")
    print("独立 PostgreSQL 数据库 rag_knowledge_assistant 已就绪；未更改 cookrag 数据。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT.parent / "code_c8" / ".env")
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument("--expose-postgres", action="store_true",
                        help="当已有容器未映射端口时，添加仅本机可用的 TCP 代理")
    parser.add_argument("--postgres-container", default="cook-rag-postgres")
    parser.add_argument("--postgres-port", type=int,
                        help="覆盖 PostgreSQL 主机端口，例如 Windows 保留 5432 时使用 15432")
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit("找不到来源配置；请通过 --source 指定 cook_rag .env")
    target = ROOT / ".env"
    if target.exists():
        raise SystemExit("目标 .env 已存在，为避免覆盖请先手动备份或编辑")
    source = dotenv_values(args.source)
    if not source.get("DATABASE_URL") or not source.get("REDIS_URL"):
        raise SystemExit("来源配置必须包含 DATABASE_URL 和 REDIS_URL；未写入目标配置")
    settings = {
        "RAG_ENVIRONMENT": "development",
        "RAG_DATA_DIR": (ROOT / "backend" / "data").as_posix(),
        "RAG_SECRET_KEY": secrets.token_urlsafe(48),
        "RAG_LLM_PROVIDER": "ollama",
        "RAG_LLM_BASE_URL": (source.get("LLM_BASE_URL") or "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1"),
        "RAG_LLM_MODEL": source.get("LLM_MODEL_ID") or "qwen3.5:9b",
        "RAG_LLM_API_KEY": source.get("LLM_API_KEY") or "ollama",
        "RAG_EMBEDDING_PROVIDER": "ollama",
        "RAG_EMBEDDING_BASE_URL": (source.get("EMBED_BASE_URL") or "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1"),
        "RAG_EMBEDDING_MODEL": source.get("EMBED_MODEL_NAME") or "qwen3-embedding:8b",
        "RAG_EMBEDDING_DIMENSION": "4096",
        "RAG_EMBEDDING_BATCH_SIZE": source.get("EMBED_BATCH_SIZE") or "8",
        "RAG_QDRANT_URL": source.get("QDRANT_URL") or "http://127.0.0.1:6335",
        "RAG_QDRANT_API_KEY": source.get("QDRANT_API_KEY") or "",
        "RAG_QDRANT_COLLECTION_PREFIX": "rag_assistant",
        "RAG_RERANKER_MODEL": (source.get("RERANK_MODEL") or "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
            if (source.get("RERANK_ENABLED") or "").lower() in {"true", "1", "yes"} else "",
        "RAG_VISION_MODEL": source.get("LLM_MODEL_ID") or "qwen3.5:9b",
        "RAG_OCR_ENABLED": "false",
        "RAG_OLLAMA_NUM_CTX": "16384",
        "RAG_EMBEDDING_NUM_CTX": "4096",
        "RAG_MODEL_TIMEOUT": "240",
        "RAG_MAX_OUTPUT_TOKENS": "2048",
    }
    if source.get("REDIS_URL"):
        redis = urlsplit(source["REDIS_URL"])
        settings["RAG_REDIS_URL"] = urlunsplit(redis._replace(path="/6"))
    if source.get("DATABASE_URL"):
        original = make_url(source["DATABASE_URL"])
        if args.postgres_port:
            if not 1 <= args.postgres_port <= 65535:
                raise SystemExit("PostgreSQL 端口必须在 1–65535 之间")
            original = original.set(port=args.postgres_port)
        database_name = "rag_knowledge_assistant"
        if args.expose_postgres:
            expose_postgres(original, args.postgres_container)
        if args.create_database:
            create_database(original, args.postgres_container)
        settings["RAG_DATABASE_URL"] = original.set(drivername="postgresql+psycopg", database=database_name,
            query={**original.query, "connect_timeout": "10"}).render_as_string(hide_password=False)
    with target.open("x", encoding="utf-8") as output:
        output.write("# Local cook_rag service profile. Generated; never commit.\n" + "\n".join(
            f"{key}='{value.replace(chr(39), chr(92) + chr(39))}'" for key, value in settings.items()) + "\n")
    print("已写入本地 .env；已复用来源模型配置；独立数据库 rag_knowledge_assistant、Redis DB6、Qdrant rag_assistant_*。未输出密钥。")


if __name__ == "__main__":
    main()
