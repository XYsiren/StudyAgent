"""SQL-atomic rate limits and expiring concurrency slots, shared by all replicas."""
import hashlib
from contextlib import contextmanager
from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, update

from app.models import AdmissionSlot, utcnow


def _insert(db, values, update_values=None, where=None, returning=False):
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(AdmissionSlot).values(**values)
    if update_values:
        statement = statement.on_conflict_do_update(index_elements=[AdmissionSlot.key], set_=update_values, where=where)
    else:
        statement = statement.on_conflict_do_nothing(index_elements=[AdmissionSlot.key])
    if returning:
        statement = statement.returning(AdmissionSlot.key)
    return db.execute(statement)


def consume(database, key, limit, seconds=60, cost=1):
    now = utcnow()
    window = int(now.replace(tzinfo=UTC).timestamp()) // seconds
    name = f"rate:{hashlib.sha256(key.encode()).hexdigest()}:{window}"
    with database.session() as db:
        _insert(db, {"key": name, "count": 0, "expires_at": now + timedelta(seconds=seconds * 2)})
        accepted = db.execute(update(AdmissionSlot).where(AdmissionSlot.key == name,
            AdmissionSlot.count + cost <= limit).values(count=AdmissionSlot.count + cost)
            .returning(AdmissionSlot.key)).scalar_one_or_none()
        db.commit()
    if accepted is None:
        raise HTTPException(429, "请求频率或每日模型请求预算已达上限，请稍后重试", headers={"Retry-After": str(seconds)})


@contextmanager
def capacity(database, user_id, limit, ttl):
    now, acquired = utcnow(), None
    for number in range(limit):
        key = f"active:{user_id}:{number}"
        with database.session() as db:
            accepted = _insert(db, {"key": key, "count": 1, "expires_at": now + timedelta(seconds=ttl)},
                {"expires_at": now + timedelta(seconds=ttl)}, AdmissionSlot.expires_at < now,
                returning=True).scalar_one_or_none()
            db.commit()
            # INSERT rowcount can be -1 with psycopg; RETURNING proves acquisition.
            if accepted is not None:
                acquired = key
                break
    if acquired is None:
        raise HTTPException(429, "同时运行的请求过多，请等待当前任务完成", headers={"Retry-After": "5"})
    try:
        yield
    finally:
        with database.session() as db:
            # Compare the lease timestamp: an expired holder cannot release its successor.
            db.execute(delete(AdmissionSlot).where(AdmissionSlot.key == acquired,
                       AdmissionSlot.expires_at == now + timedelta(seconds=ttl)))
            db.commit()
