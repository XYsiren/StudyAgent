from datetime import date
from typing import Literal

from pydantic import Field

from app.schemas import StrictRequest


class CourseCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=120)
    exam_date: date
    exam_name: str | None = Field(default=None, max_length=120)
    target_score: float | None = Field(default=None, ge=0, le=1000)
    syllabus: str | None = Field(default=None, max_length=10000)
    weekly_minutes: int | None = Field(default=None, ge=7, le=5040)
    knowledge_base_id: str | None = None


class CoursePatch(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    exam_date: date | None = None
    exam_name: str | None = Field(default=None, max_length=120)
    target_score: float | None = Field(default=None, ge=0, le=1000)
    syllabus: str | None = Field(default=None, max_length=10000)
    weekly_minutes: int | None = Field(default=None, ge=7, le=5040)
    status: Literal["active", "archived"] | None = None


class UnitRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None
    ordinal: int = Field(default=0, ge=0)
    weight: float = Field(default=1, gt=0, le=100)


class PointRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    unit_id: str | None = None
    importance: int = Field(default=1, ge=1, le=5)
    mastery_state: Literal["unknown", "learning", "mastered"] = "unknown"
    chunk_ids: list[str] = Field(default_factory=list, max_length=50)


class TutorRequest(StrictRequest):
    question: str = Field(min_length=1, max_length=7000)
    unit_id: str | None = None
    knowledge_point_id: str | None = None
    mode: Literal["explain", "example", "compare", "practice"] = "explain"
    request_id: str = Field(min_length=8, max_length=64)
    conversation_id: str | None = None


class QuizGenerate(StrictRequest):
    unit_id: str | None = None
    knowledge_point_id: str | None = None
    type: Literal["single", "boolean", "short"] = "single"
    difficulty: Literal["basic", "applied", "comprehensive"] = "basic"
    count: int = Field(default=3, ge=1, le=20)
    include_explanation: bool = True
    multiple_sources: bool = False
    request_id: str = Field(min_length=8, max_length=64)


ERROR_TAGS = {"概念混淆", "公式/步骤错误", "关键词遗漏", "审题错误", "记忆不牢", "资料理解错误", "题目或解析可能有问题"}


class AttemptRequest(StrictRequest):
    assessment_phase: Literal["practice", "pre", "post"] = "practice"
    answer: str = Field(min_length=1, max_length=8000)
    request_id: str = Field(min_length=8, max_length=64)
    duration_ms: int = Field(default=0, ge=0, le=86400000)
    confidence: int | None = Field(default=None, ge=1, le=5)
    error_tags: list[str] = Field(default_factory=list, max_length=7)


class ErrorTags(StrictRequest):
    error_tags: list[str] = Field(max_length=7)


class QuizStatus(StrictRequest):
    status: Literal["draft", "approved", "rejected", "retired"]


class QuizFeedback(StrictRequest):
    reason: Literal["题目有问题", "答案有问题", "解析有帮助", "解析无帮助"]
    rating: int | None = Field(default=None, ge=1, le=5)


class CardGrade(StrictRequest):
    grade: Literal["easy", "remember", "fuzzy", "forgot"]
    request_id: str = Field(min_length=8, max_length=64)


class TaskPatch(StrictRequest):
    status: Literal["pending", "completed", "skipped", "blocked"] = "completed"
    minutes: int | None = Field(default=None, ge=1, le=720)


class MaterialPatch(StrictRequest):
    type: Literal["textbook", "lecture", "exam", "notes", "answers", "presentation"] = "lecture"
    unit_id: str | None = None
    trust_note: str = Field(default="", max_length=1000)


class PointMerge(StrictRequest):
    point_ids: list[str] = Field(min_length=2, max_length=20)
    title: str = Field(min_length=1, max_length=200)


class PointSplit(StrictRequest):
    titles: list[str] = Field(min_length=2, max_length=20)
