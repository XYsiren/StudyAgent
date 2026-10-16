"""Explicit member grants for restricted organization knowledge bases."""
import sqlalchemy as sa
from alembic import op

revision = "003_kb_acl"
down_revision = "002_enterprise"


def upgrade():
    op.add_column("knowledge_bases", sa.Column("visibility", sa.String(20), nullable=False, server_default="organization"))
    op.create_table("knowledge_base_grants",
        sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False))


def downgrade():
    raise RuntimeError("Restore the matching backup to roll back an authorization schema")
