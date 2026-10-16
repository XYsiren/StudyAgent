"""Transactional outbox: SQL visibility commits before external cleanup."""
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, or_, select, update

from app.models import Chunk, CleanupTask, Document, IngestionJob, KnowledgeBase, new_id, utcnow

logger = logging.getLogger(__name__)


def enqueue_delete(db, document):
    from app.models import DocumentRevision
    revisions = db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == document.id)).all()
    paths = list({r.stored_path for r in revisions} | {document.stored_path})
    sizes = {document.stored_path: document.size_bytes}
    for revision in revisions:
        sizes[revision.stored_path] = max(sizes.get(revision.stored_path, 0), revision.size_bytes)
    owner = db.scalar(select(KnowledgeBase.user_id).where(KnowledgeBase.id == document.knowledge_base_id))
    task = CleanupTask(document_id=document.id, paths=paths, owner_id=owner, retained_bytes=sum(sizes.values()))
    db.add(task)
    db.delete(document)
    return task


def process_cleanup(database, settings, task_id):
    from app.services.retrieval import delete_document, delete_revision
    lease, now = new_id(), utcnow()
    with database.session() as db:
        changed = db.execute(update(CleanupTask).where(CleanupTask.id == task_id,
            CleanupTask.status.in_(["queued", "processing"]), CleanupTask.available_at <= now,
            or_(CleanupTask.lease_expires_at.is_(None), CleanupTask.lease_expires_at < now)).values(
            status="processing", lease_token=lease, lease_expires_at=now + timedelta(minutes=10),
            attempts=CleanupTask.attempts + 1))
        db.commit()
        if not changed.rowcount:
            return
        task = db.get(CleanupTask, task_id)
        document_id, revision_id, paths = task.document_id, task.revision_id, task.paths
        if revision_id:
            doc = db.get(Document, document_id)
            active_job = db.scalar(select(IngestionJob.id).where(IngestionJob.document_id == document_id,
                IngestionJob.revision_id == revision_id, IngestionJob.status.in_(["queued", "processing"])))
            if (doc and doc.active_revision_id == revision_id) or active_job:
                task.status, task.lease_token, task.lease_expires_at = "queued", None, None
                task.available_at = now + timedelta(minutes=10)
                db.commit()
                return
    try:
        if revision_id:
            delete_revision(document_id, revision_id)
        else:
            delete_document(document_id)
        root = (settings.data_dir / "uploads").resolve()
        for value in paths:
            path = Path(value).resolve()
            if not path.is_relative_to(root):
                raise ValueError("cleanup path is outside uploads")
            path.unlink(missing_ok=True)
        with database.session() as db:
            if revision_id:
                db.execute(delete(Chunk).where(Chunk.document_id == document_id, Chunk.revision_id == revision_id))
            db.execute(update(CleanupTask).where(CleanupTask.id == task_id, CleanupTask.lease_token == lease)
                       .values(status="completed", error=None, retained_bytes=0, lease_token=None, lease_expires_at=None))
            db.commit()
    except Exception as exc:
        logger.exception("Durable cleanup failed: %s", task_id)
        with database.session() as db:
            task = db.get(CleanupTask, task_id)
            if task and task.lease_token == lease:
                task.status, task.error = "queued", type(exc).__name__
                task.available_at = utcnow() + timedelta(seconds=min(3600, 2 ** min(task.attempts, 10)))
                task.lease_token = task.lease_expires_at = None
                db.commit()
