"""Shared admission policy for upload, reindex and version restore."""
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.models import CleanupTask, Document, DocumentRevision, IngestionJob, KnowledgeBase, User
from app.services.access import require_kb


def reserve_ingestion(db, user, kb_id, settings, extra_bytes=0):
    kb = require_kb(db, user, kb_id, "write")
    owner_id = db.scalar(select(User.id).where(User.id == kb.user_id).with_for_update())
    from app.services.operations import effective_settings
    settings = effective_settings(db, settings, owner_id)
    db.execute(update(User).where(User.id == owner_id).values(name=User.name))
    db.expire_all()
    require_kb(db, user, kb_id, "write")
    # Reindex/restore may share immutable source files; charge each stored file once.
    files = select(DocumentRevision.stored_path, func.max(DocumentRevision.size_bytes).label("bytes"))
    files = files.join(Document).join(KnowledgeBase).where(KnowledgeBase.user_id == owner_id)
    files = files.group_by(DocumentRevision.stored_path).subquery()
    used = db.scalar(select(func.coalesce(func.sum(files.c.bytes), 0)))
    used += db.scalar(select(func.coalesce(func.sum(CleanupTask.retained_bytes), 0)).where(
        CleanupTask.owner_id == owner_id, CleanupTask.status != "completed"))
    pending = db.scalar(select(func.count(IngestionJob.id)).join(Document).join(KnowledgeBase).where(
        KnowledgeBase.user_id == owner_id, IngestionJob.status.in_(["queued", "processing"])))
    if used + extra_bytes > settings.user_storage_mb * 1024 * 1024 or pending >= settings.max_pending_jobs:
        raise HTTPException(429, "存储容量或待处理任务配额已达上限")
