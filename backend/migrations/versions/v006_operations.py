"""Persist operational quotas and page-level ingestion progress."""
import sqlalchemy as sa
from alembic import op

revision = "006_operations"
down_revision = "005_cleanup_quota"


def upgrade():
    op.create_table("operational_policies", sa.Column("scope", sa.String(80), primary_key=True),
        sa.Column("limits", sa.JSON(), nullable=False), sa.Column("updated_by", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.add_column("ingestion_jobs", sa.Column("stage", sa.String(120), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("processed_pages", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ingestion_jobs", sa.Column("total_pages", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
