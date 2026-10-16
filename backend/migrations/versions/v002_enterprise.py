"""Organizations, sessions, durable cleanup and immutable document revisions."""
import uuid
from collections import Counter

import sqlalchemy as sa
from alembic import op

from migrations.schema_v2 import Base

revision = "002_enterprise"
down_revision = "001_baseline"


def upgrade():
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    defaults = {"disabled": sa.false(), "generation": "0", "source": "'upload'", "token_count": "0",
                "run_metadata": "'{}'", "use_ragas": sa.false()}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            table.create(bind)
            continue
        columns = {c["name"] for c in sa.inspect(bind).get_columns(table.name)}
        for column in table.columns:
            if column.name not in columns:
                # Frozen v2 schema, with explicit server defaults for non-null legacy rows.
                cloned = sa.Column(column.name, column.type, nullable=column.nullable,
                                   server_default=sa.text(str(defaults[column.name])) if column.name in defaults else None)
                op.add_column(table.name, cloned)
        for index in table.indexes:
            index.create(bind, checkfirst=True)
    # Preserve original vectors/chunk IDs and attach a real source revision.
    docs, revisions, chunks, terms = [Base.metadata.tables[name] for name in (
        "documents", "document_revisions", "chunks", "chunk_terms")]
    for doc in bind.execute(sa.select(docs)).mappings().all():
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL, "legacy-revision:" + doc["id"]))
        bind.execute(revisions.insert().values(id=rid, document_id=doc["id"], name=doc["name"],
            stored_path=doc["stored_path"], sha256=doc["sha256"], size_bytes=doc["size_bytes"],
            status="ready" if doc["status"] == "ready" else "failed", chunk_count=doc["chunk_count"],
            created_at=doc["created_at"]))
        bind.execute(docs.update().where(docs.c.id == doc["id"]).values(
            active_revision_id=rid if doc["status"] == "ready" else None))
        bind.execute(chunks.update().where(chunks.c.document_id == doc["id"]).values(revision_id=rid))
    from app.services.embeddings import tokenize
    last = ""
    while True:
        batch = bind.execute(sa.select(chunks.c.id, chunks.c.text).where(chunks.c.id > last)
                             .order_by(chunks.c.id).limit(200)).all()
        if not batch:
            break
        for cid, text in batch:
            tokens = [t for t in tokenize(text) if len(t) <= 128]
            bind.execute(chunks.update().where(chunks.c.id == cid).values(token_count=len(tokens)))
            rows = [{"chunk_id": cid, "term": term, "frequency": count} for term, count in Counter(tokens).items()]
            if rows:
                bind.execute(terms.insert(), rows)
        last = batch[-1][0]


def downgrade():
    raise RuntimeError("Restore the matching database/files/vector backup; in-place destructive rollback is disabled")
