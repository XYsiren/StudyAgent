"""Learning records remain separate from the existing knowledge containers."""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, new_id, utcnow


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    exam_name: Mapped[str] = mapped_column(String(120))
    exam_date: Mapped[date] = mapped_column(Date)
    target_score: Mapped[float | None] = mapped_column(Float)
    syllabus: Mapped[str] = mapped_column(Text, default="")
    weekly_minutes: Mapped[int] = mapped_column(Integer, default=300)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CourseUnit(Base):
    __tablename__ = "course_units"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("course_units.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    weight: Mapped[float] = mapped_column(Float, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("course_units.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=1)
    mastery_state: Mapped[str] = mapped_column(String(20), default="unknown")
    mastery_score: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class KnowledgePointChunk(Base):
    __tablename__ = "knowledge_point_chunks"
    knowledge_point_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True)


class QuizItem(Base):
    __tablename__ = "quiz_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("course_units.id", ondelete="SET NULL"))
    knowledge_point_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_points.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(20))
    difficulty: Mapped[str] = mapped_column(String(20), default="basic")
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    answer: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    model_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_learning_attempt_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    quiz_item_id: Mapped[str] = mapped_column(ForeignKey("quiz_items.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    assessment_phase: Mapped[str] = mapped_column(String(20), default="practice")
    answer: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool | None]
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[int | None] = mapped_column(Integer)
    error_tags: Mapped[list] = mapped_column(JSON, default=list)
    suggested_error_tags: Mapped[list] = mapped_column(JSON, default=list)
    feedback: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReviewCard(Base):
    __tablename__ = "review_cards"
    __table_args__ = (UniqueConstraint("user_id", "quiz_item_id", name="uq_review_user_quiz"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    knowledge_point_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_points.id", ondelete="SET NULL"))
    quiz_item_id: Mapped[str | None] = mapped_column(ForeignKey("quiz_items.id", ondelete="CASCADE"))
    level: Mapped[int] = mapped_column(Integer, default=1)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)


class StudyPlan(Base):
    __tablename__ = "study_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), unique=True)
    exam_date: Mapped[date] = mapped_column(Date)
    weekly_minutes: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)


class StudyTask(Base):
    __tablename__ = "study_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str] = mapped_column(ForeignKey("study_plans.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(250))
    scheduled_for: Mapped[date] = mapped_column(Date)
    minutes: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    event_name: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40), default="course")
    entity_id: Mapped[str | None] = mapped_column(String(36))
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningJob(Base):
    __tablename__ = "learning_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_learning_job_request"),)
    parameters: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
