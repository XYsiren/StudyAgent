"""Enforce the organization reference on the production PostgreSQL schema."""
import sqlalchemy as sa
from alembic import op

revision = "004_pg_org_fk"
down_revision = "003_kb_acl"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        foreign_keys = sa.inspect(bind).get_foreign_keys("knowledge_bases")
        if not any(key["constrained_columns"] == ["organization_id"] for key in foreign_keys):
            op.create_foreign_key("fk_knowledge_base_organization", "knowledge_bases", "organizations",
                ["organization_id"], ["id"], ondelete="RESTRICT")


def downgrade():
    raise RuntimeError("Restore a coordinated backup to roll back the schema")
