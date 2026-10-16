"""Durable ingestion worker: SQL leases are authoritative; Redis wakes consumers."""
import logging
import threading
from datetime import timedelta
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, update

from app.config import get_settings
from app.db import Database
from app.models import Chunk, Document, IngestionJob, new_id, utcnow

logger = logging.getLogger(__name__)
QUEUE_KEY = "rag:ingestion:pending"


def notify_job(settings, job_id: str):
    if settings.redis_url:
        try:
            with Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2) as client:
                client.lpush(QUEUE_KEY, job_id)
        except (RedisError, ValueError):
            # The SQL record survives a Redis outage and is found by periodic scans.
            logger.warning("Redis notification unavailable; durable SQL job %s remains queued", job_id)


def _claim(database, settings, job_id: str) -> str | None:
    now, lease = utcnow(), new_id()
    with database.session() as db:
        result = db.execute(update(IngestionJob).where(
            IngestionJob.id == job_id,
            or_(IngestionJob.status == "queued",
                (IngestionJob.status == "processing") &
                or_(IngestionJob.lease_expires_at < now, IngestionJob.lease_expires_at.is_(None))),
        ).values(status="processing", progress=1, error=None, lease_token=lease,
                 lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
                 attempts=IngestionJob.attempts + 1, updated_at=now))
        db.commit()
        return lease if result.rowcount == 1 else None


def _heartbeat(database, settings, job_id, lease, done):
    while not done.wait(min(15, settings.job_lease_seconds / 3)):
        try:
            with database.session() as db:
                db.execute(update(IngestionJob).where(
                    IngestionJob.id == job_id, IngestionJob.lease_token == lease,
                    IngestionJob.status == "processing",
                ).values(lease_expires_at=utcnow() + timedelta(seconds=settings.job_lease_seconds),
                         updated_at=utcnow()))
                db.commit()
        except Exception:
            logger.exception("Job heartbeat failed for %s", job_id)


def process_job(database: Database, settings, job_id: str):
    from app.services.parsing import chunk_pages, parse_document
    from app.services.retrieval import delete_document, index_chunks

    lease = _claim(database, settings, job_id)
    if lease is None:
        return
    done = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat, args=(database, settings, job_id, lease, done), daemon=True)
    heartbeat.start()
    document_id = None
    try:
        with database.session() as db:
            job = db.get(IngestionJob, job_id)
            document = db.get(Document, job.document_id) if job else None
            if document is None:
                return
            document_id, filename, stored_path, kb_id = document.id, document.name, document.stored_path, document.knowledge_base_id
            document.status, document.error = "processing", None
            db.commit()
        upload_root = (settings.data_dir / "uploads").resolve()
        path = Path(stored_path).resolve()
        if not path.is_relative_to(upload_root):
            raise ValueError("文件存储路径不在 uploads 目录内")
        pages = parse_document(path, filename)
        parsed_chunks = chunk_pages(pages)
        if not parsed_chunks:
            raise ValueError("未提取到可索引的文本；扫描件请启用 OCR 或配置视觉模型")
        indexed = [{"id": new_id(), "document_id": document_id, "document_name": filename,
                    "knowledge_base_id": kb_id, "text": item["text"], "page": item.get("page"),
                    "ordinal": item.get("ordinal", ordinal)} for ordinal, item in enumerate(parsed_chunks)]
        with database.session() as db:
            # Acquire a write lock before vector indexing. Deletion uses the same
            # ordering, so no late vector write can survive a committed delete.
            document_lock = db.execute(update(Document).where(Document.id == document_id)
                                       .values(status="processing"))
            if document_lock.rowcount != 1:
                db.rollback()
                return
            owned = db.execute(update(IngestionJob).where(
                IngestionJob.id == job_id, IngestionJob.lease_token == lease,
                IngestionJob.status == "processing",
            ).values(progress=70, updated_at=utcnow()))
            document = db.get(Document, document_id)
            if owned.rowcount != 1 or document is None:
                db.rollback()
                return
            db.execute(delete(Chunk).where(Chunk.document_id == document_id))
            delete_document(document_id)
            for item in indexed:
                db.add(Chunk(id=item["id"], document_id=document_id, text=item["text"],
                             page=item["page"], ordinal=item["ordinal"]))
            db.flush()
            index_chunks(indexed)
            document.status, document.chunk_count, document.error = "ready", len(indexed), None
            db.execute(update(IngestionJob).where(IngestionJob.id == job_id,
                       IngestionJob.lease_token == lease).values(status="completed", progress=100,
                       error=None, lease_token=None, lease_expires_at=None, updated_at=utcnow()))
            db.commit()
    except Exception as exc:
        logger.exception("Ingestion failed: %s", job_id)
        # Keep failure state durable and clear both partial SQL and vector data.
        with database.session() as db:
            if document_id:
                db.execute(update(Document).where(Document.id == document_id).values(status="processing"))
            owned = db.execute(update(IngestionJob).where(
                IngestionJob.id == job_id, IngestionJob.lease_token == lease,
            ).values(status="failed", progress=0, error=str(exc)[:2000],
                     lease_token=None, lease_expires_at=None, updated_at=utcnow()))
            if owned.rowcount:
                document = db.get(Document, document_id) if document_id else None
                if document:
                    document.status, document.error, document.chunk_count = "failed", str(exc)[:2000], 0
                    db.execute(delete(Chunk).where(Chunk.document_id == document_id))
                    try:
                        delete_document(document_id)
                    except Exception:
                        logger.exception("Partial vector cleanup failed for document %s", document_id)
                db.commit()
            else:
                db.rollback()
    finally:
        done.set()
        heartbeat.join(timeout=2)


def run_worker(database: Database, settings, stop: threading.Event):
    client = None
    if settings.redis_url:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=3)
    try:
        while not stop.is_set():
            try:
                with database.session() as db:
                    pending = db.scalars(select(IngestionJob.id).where(or_(
                        IngestionJob.status == "queued",
                        (IngestionJob.status == "processing") & or_(
                            IngestionJob.lease_expires_at < utcnow(), IngestionJob.lease_expires_at.is_(None)),
                    )).order_by(IngestionJob.created_at).limit(20)).all()
                if pending:
                    for job_id in pending:
                        if stop.is_set():
                            break
                        process_job(database, settings, job_id)
                    continue
                if client:
                    try:
                        item = client.brpop(QUEUE_KEY, timeout=max(1, min(2, int(settings.worker_poll_seconds))))
                        if item:
                            process_job(database, settings, item[1].decode())
                    except RedisError:
                        logger.warning("Redis unavailable; continuing SQL recovery scan")
                        stop.wait(settings.worker_poll_seconds)
                else:
                    stop.wait(settings.worker_poll_seconds)
            except Exception:
                logger.exception("Worker loop failed; retrying durable jobs")
                stop.wait(settings.worker_poll_seconds)
    finally:
        if client:
            client.close()


def main():
    import signal
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    database = Database(settings)
    database.initialize()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        run_worker(database, settings, stop)
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
