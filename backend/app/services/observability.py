"""Low-cardinality metrics; no document text, emails or access tokens in telemetry."""
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select, text

from app.models import AdmissionSlot, CleanupTask, EvaluationRun, IngestionJob, utcnow
from app.services.admission import _insert

REGISTRY = CollectorRegistry()
REQUESTS = Counter("rag_http_requests_total", "HTTP responses", ["method", "route", "status"], registry=REGISTRY)
LATENCY = Histogram("rag_http_duration_seconds", "HTTP request duration", ["route"], registry=REGISTRY)
STAGES = Histogram("rag_stage_duration_seconds", "RAG stage duration", ["stage"], registry=REGISTRY)
WORKER_KEY = f"worker:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"


@contextmanager
def stage(name, timings):
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        timings[name] = round(elapsed * 1000, 2)
        STAGES.labels(name).observe(elapsed)


def worker_heartbeat(database):
    expires = utcnow() + timedelta(seconds=90)
    with database.session() as db:
        _insert(db, {"key": WORKER_KEY, "count": 1, "expires_at": expires}, {"expires_at": expires})
        db.commit()


def readiness(database, settings):
    checks = {}
    try:
        with database.session() as db:
            db.execute(text("SELECT 1"))
            checks["database"] = True
            checks["worker"] = bool(db.scalar(select(AdmissionSlot.key).where(
                AdmissionSlot.key.like("worker:%"), AdmissionSlot.expires_at > utcnow()).limit(1)))
    except Exception:
        checks.update(database=False, worker=False)
    if settings.redis_url:
        try:
            from redis import Redis
            with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
                checks["redis"] = bool(client.ping())
        except Exception:
            checks["redis"] = False
    try:
        from app.services.retrieval import client
        client().get_collections()
        checks["qdrant"] = True
    except Exception:
        checks["qdrant"] = False
    # Generation availability is a separate probe, avoiding costly model inference per health poll.
    return checks


def metrics(database):
    registry = CollectorRegistry()
    for collector in REGISTRY._collector_to_names:
        registry.register(collector)
    with database.session() as db:
        for model, name in [(IngestionJob, "ingestion"), (CleanupTask, "cleanup"), (EvaluationRun, "evaluation")]:
            gauge = Gauge(f"rag_{name}_jobs", "Persisted jobs by status", ["status"], registry=registry)
            for status, count in db.execute(select(model.status, func.count()).group_by(model.status)):
                gauge.labels(status).set(count)
        workers = Gauge("rag_workers_alive", "Workers with recent SQL heartbeat", registry=registry)
        workers.set(db.scalar(select(func.count()).select_from(AdmissionSlot).where(
            AdmissionSlot.key.like("worker:%"), AdmissionSlot.expires_at > utcnow())))
        oldest = db.scalar(select(func.min(IngestionJob.created_at)).where(IngestionJob.status == "queued"))
        Gauge("rag_ingestion_oldest_queued_seconds", "Oldest queued ingestion age", registry=registry).set(
            max(0, (utcnow() - oldest).total_seconds()) if oldest else 0)
        Gauge("rag_cleanup_retry_tasks", "Cleanup tasks needing repeated recovery", registry=registry).set(
            db.scalar(select(func.count()).select_from(CleanupTask).where(CleanupTask.status != "completed", CleanupTask.attempts >= 3)))
    return generate_latest(registry), CONTENT_TYPE_LATEST
