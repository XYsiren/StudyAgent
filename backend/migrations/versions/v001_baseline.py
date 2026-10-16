"""Adopt the original schema without changing existing data."""
from alembic import op

from migrations.schema_v1 import Base

revision = "001_baseline"
down_revision = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")
