"""Keep pending file deletion charged to its owner until cleanup succeeds."""
import sqlalchemy as sa
from alembic import op

revision = "005_cleanup_quota"
down_revision = "004_pg_org_fk"


def upgrade():
    op.add_column("cleanup_tasks", sa.Column("owner_id", sa.String(36), nullable=True))
    op.add_column("cleanup_tasks", sa.Column("retained_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.create_index("ix_cleanup_tasks_owner_id", "cleanup_tasks", ["owner_id"])


def downgrade():
    raise RuntimeError("Restore the matching backup to roll back accounting state")
