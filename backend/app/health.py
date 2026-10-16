"""Worker heartbeat check and lightweight model API probes, without inference."""
import json
import socket
import sys

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import Database
from app.models import AdmissionSlot, utcnow


def model_checks(settings):
    checks = {}
    for kind, provider, model, base, key in [
        ("generation", settings.llm_provider, settings.llm_model, settings.llm_base_url, settings.llm_api_key),
        ("embedding", settings.embedding_provider, settings.embedding_model,
         settings.embedding_base_url or settings.llm_base_url, settings.embedding_api_key or settings.llm_api_key),
    ]:
        if (kind == "generation" and not model) or provider == "hash":
            checks[kind] = {"ready": True, "mode": "extractive" if kind == "generation" else "demo"}
            continue
        if provider == "sentence-transformers":
            from app.services.embeddings import semantic_model
            checks[kind] = {"ready": bool(semantic_model.cache_info().currsize), "mode": "local-weights",
                            "note": "Weights are loaded lazily by ingestion/query"}
            continue
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                if provider == "ollama":
                    response = client.get(base.rstrip("/").removesuffix("/v1") + "/api/tags")
                    response.raise_for_status()
                    names = {entry["name"] for entry in response.json().get("models", [])}
                    ready = model in names or model + ":latest" in names
                else:
                    response = client.get(base.rstrip("/") + "/models", headers={"Authorization": "Bearer " + key})
                    response.raise_for_status()
                    ready = any(entry.get("id") == model for entry in response.json().get("data", []))
                checks[kind] = {"ready": ready, "mode": provider}
        except Exception:
            checks[kind] = {"ready": False, "mode": provider, "note": "Model API unavailable or model not listed"}
    return checks


def main():
    settings = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "models":
        checks = model_checks(settings)
        print(json.dumps(checks))
        raise SystemExit(0 if all(value["ready"] for value in checks.values()) else 1)
    database = Database(settings)
    with database.session() as db:
        alive = db.scalar(select(AdmissionSlot.key).where(AdmissionSlot.key.like(
            f"worker:{socket.gethostname()}:%"), AdmissionSlot.expires_at > utcnow()).limit(1))
    database.engine.dispose()
    raise SystemExit(0 if alive else 1)


if __name__ == '__main__':
    main()
