"""Learning workspace and source metadata; schema snapshot independent of runtime models."""

import sqlalchemy as sa
from alembic import op

revision = "007_learning"
down_revision = "006_operations"


def upgrade():
    op.add_column("document_revisions", sa.Column("parse_metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("documents", sa.Column("material_metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("documents", sa.Column("parse_metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("chunks", sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("exam_name", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("exam_date", sa.Date(), primary_key=False, nullable=False),
        sa.Column("target_score", sa.Float(), primary_key=False, nullable=True),
        sa.Column("syllabus", sa.Text(), primary_key=False, nullable=False),
        sa.Column("weekly_minutes", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), primary_key=False, nullable=False),
        sa.UniqueConstraint("knowledge_base_id", name=None),
    )
    op.create_index("ix_courses_user_id", "courses", ["user_id"])
    op.create_table(
        "course_units",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(length=36),
            sa.ForeignKey("course_units.id", ondelete="SET NULL"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), primary_key=False, nullable=False),
        sa.Column("ordinal", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("weight", sa.Float(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=20), primary_key=False, nullable=False),
    )
    op.create_index("ix_course_units_course_id", "course_units", ["course_id"])
    op.create_table(
        "knowledge_points",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "unit_id",
            sa.String(length=36),
            sa.ForeignKey("course_units.id", ondelete="SET NULL"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), primary_key=False, nullable=False),
        sa.Column("description", sa.Text(), primary_key=False, nullable=False),
        sa.Column("importance", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("mastery_state", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("mastery_score", sa.Float(), primary_key=False, nullable=False),
        sa.Column("source", sa.String(length=30), primary_key=False, nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), primary_key=False, nullable=True),
    )
    op.create_index("ix_knowledge_points_course_id", "knowledge_points", ["course_id"])
    op.create_table(
        "knowledge_point_chunks",
        sa.Column(
            "knowledge_point_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            sa.String(length=36),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_table(
        "quiz_items",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "unit_id",
            sa.String(length=36),
            sa.ForeignKey("course_units.id", ondelete="SET NULL"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "knowledge_point_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_points.id", ondelete="SET NULL"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("type", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("difficulty", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("stem", sa.Text(), primary_key=False, nullable=False),
        sa.Column("options", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("answer", sa.Text(), primary_key=False, nullable=False),
        sa.Column("explanation", sa.Text(), primary_key=False, nullable=False),
        sa.Column("citations", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("model_metadata", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), primary_key=False, nullable=False),
    )
    op.create_index("ix_quiz_items_course_id", "quiz_items", ["course_id"])
    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "quiz_item_id",
            sa.String(length=36),
            sa.ForeignKey("quiz_items.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("request_key", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("assessment_phase", sa.String(20), nullable=False, server_default="practice"),
        sa.Column("answer", sa.Text(), primary_key=False, nullable=False),
        sa.Column("is_correct", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column("duration_ms", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("confidence", sa.Integer(), primary_key=False, nullable=True),
        sa.Column("error_tags", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("suggested_error_tags", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("feedback", sa.Text(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), primary_key=False, nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_learning_attempt_request"),
    )
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"])
    op.create_index("ix_quiz_attempts_quiz_item_id", "quiz_attempts", ["quiz_item_id"])
    op.create_table(
        "review_cards",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "knowledge_point_id",
            sa.String(length=36),
            sa.ForeignKey("knowledge_points.id", ondelete="SET NULL"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "quiz_item_id",
            sa.String(length=36),
            sa.ForeignKey("quiz_items.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("level", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("due_at", sa.DateTime(), primary_key=False, nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(), primary_key=False, nullable=True),
        sa.Column("review_count", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("failure_count", sa.Integer(), primary_key=False, nullable=False),
        sa.UniqueConstraint("user_id", "quiz_item_id", name="uq_review_user_quiz"),
    )
    op.create_index("ix_review_cards_course_id", "review_cards", ["course_id"])
    op.create_index("ix_review_cards_user_id", "review_cards", ["user_id"])
    op.create_table(
        "study_plans",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("exam_date", sa.Date(), primary_key=False, nullable=False),
        sa.Column("weekly_minutes", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("version", sa.Integer(), primary_key=False, nullable=False),
        sa.UniqueConstraint("course_id", name=None),
    )
    op.create_table(
        "study_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("task_type", sa.String(length=30), primary_key=False, nullable=False),
        sa.Column("target_id", sa.String(length=36), primary_key=False, nullable=True),
        sa.Column("title", sa.String(length=250), primary_key=False, nullable=False),
        sa.Column("scheduled_for", sa.Date(), primary_key=False, nullable=False),
        sa.Column("minutes", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("completed_at", sa.DateTime(), primary_key=False, nullable=True),
    )
    op.create_index("ix_study_tasks_plan_id", "study_tasks", ["plan_id"])
    op.create_table(
        "learning_events",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("event_name", sa.String(length=80), primary_key=False, nullable=False),
        sa.Column("entity_type", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("entity_id", sa.String(length=36), primary_key=False, nullable=True),
        sa.Column("properties", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.DateTime(), primary_key=False, nullable=False),
    )
    op.create_index("ix_learning_events_user_id", "learning_events", ["user_id"])
    op.create_index("ix_learning_events_course_id", "learning_events", ["course_id"])
    op.create_table(
        "learning_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "course_id",
            sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("request_key", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("parameters", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("attempts", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("lease_token", sa.String(length=36), primary_key=False, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), primary_key=False, nullable=True),
        sa.Column("result", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("error", sa.Text(), primary_key=False, nullable=True),
        sa.Column("created_at", sa.DateTime(), primary_key=False, nullable=False),
        sa.UniqueConstraint("user_id", "request_key", name="uq_learning_job_request"),
    )
    op.create_index("ix_learning_jobs_course_id", "learning_jobs", ["course_id"])


def downgrade():
    raise RuntimeError("Restore a verified backup for downgrade")
