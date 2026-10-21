"""Course boundaries, explicit outline confirmation and budgeted study plans."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from app.models import Chunk, Document
from app.models_learning import (
    Course,
    CourseUnit,
    KnowledgePoint,
    KnowledgePointChunk,
    LearningEvent,
    StudyPlan,
    StudyTask,
)
from app.services.access import require_kb
from app.services.corpus import CorpusScope


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def payload(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def event(db, course, name, entity_id=None, properties=None):
    db.add(
        LearningEvent(
            user_id=course.user_id,
            course_id=course.id,
            event_name=name,
            entity_id=entity_id,
            properties=properties or {},
        )
    )


def own_course(db, user, course_id, lock=False):
    if lock:
        db.execute(update(Course).where(Course.id == course_id, Course.user_id == user.id).values(status=Course.status))
    query = select(Course).where(Course.id == course_id, Course.user_id == user.id)
    course = db.scalar(query.with_for_update() if lock else query)
    if not course:
        raise HTTPException(404, "课程不存在")
    require_kb(db, user, course.knowledge_base_id)
    return course


def unit_in_course(db, course, unit_id):
    if unit_id and not db.scalar(
        select(CourseUnit.id).where(CourseUnit.id == unit_id, CourseUnit.course_id == course.id)
    ):
        raise HTTPException(404, "章节不属于当前课程")


def point_in_course(db, course, point_id):
    point = db.get(KnowledgePoint, point_id) if point_id else None
    if point_id and (not point or point.course_id != course.id):
        raise HTTPException(404, "知识点不属于当前课程")
    return point


def course_scope(db, user, course, unit_id=None, point_id=None):
    unit_in_course(db, course, unit_id)
    point = point_in_course(db, course, point_id)
    if point and unit_id and point.unit_id != unit_id:
        raise HTTPException(422, "知识点不属于所选章节")
    scope = CorpusScope(db, user, [course.knowledge_base_id])
    if point:
        scope.conditions.append(
            Chunk.id.in_(select(KnowledgePointChunk.chunk_id).where(KnowledgePointChunk.knowledge_point_id == point.id))
        )
    elif unit_id:
        point_ids = select(KnowledgePoint.id).where(
            KnowledgePoint.unit_id == unit_id, KnowledgePoint.confirmed_at.is_not(None)
        )
        from sqlalchemy import or_

        scope.conditions.append(
            or_(
                Chunk.id.in_(
                    select(KnowledgePointChunk.chunk_id).where(KnowledgePointChunk.knowledge_point_id.in_(point_ids))
                ),
                Document.material_metadata["unit_id"].as_string() == unit_id,
            )
        )
    return scope


def rebuild_plan(db, course):
    """Only pending tasks are replaced. Completed, skipped and blocked records survive."""
    plan = db.scalar(select(StudyPlan).where(StudyPlan.course_id == course.id))
    if not plan:
        plan = StudyPlan(course_id=course.id, exam_date=course.exam_date, weekly_minutes=course.weekly_minutes)
        db.add(plan)
        db.flush()
    else:
        plan.version += 1
        plan.exam_date, plan.weekly_minutes = course.exam_date, course.weekly_minutes
    db.execute(delete(StudyTask).where(StudyTask.plan_id == plan.id, StudyTask.status == "pending"))
    if course.exam_date < today() or course.status != "active":
        return plan
    points = db.scalars(
        select(KnowledgePoint)
        .where(
            KnowledgePoint.course_id == course.id,
            KnowledgePoint.confirmed_at.is_not(None),
            KnowledgePoint.mastery_state != "mastered",
        )
        .order_by(KnowledgePoint.importance.desc(), KnowledgePoint.title)
    ).all()
    units = db.scalars(select(CourseUnit).where(CourseUnit.course_id == course.id).order_by(CourseUnit.ordinal)).all()
    docs = db.scalars(
        select(Document).where(Document.knowledge_base_id == course.knowledge_base_id, Document.status == "ready")
    ).all()
    targets = [("knowledge_point", p.id, "学习与自测：" + p.title) for p in points]
    if not targets:
        targets = [("unit", u.id, "章节复习：" + u.title) for u in units]
    if not targets:
        targets = [("document", d.id, "阅读资料：" + d.name) for d in docs]
    if not targets:
        targets = [("setup", None, "上传课程资料并整理考试范围")]
    completed_targets = set(
        db.scalars(select(StudyTask.target_id).where(StudyTask.plan_id == plan.id, StudyTask.status == "completed"))
    )
    targets = [target for target in targets if target[1] is None or target[1] not in completed_targets]
    days = (course.exam_date - today()).days + 1
    daily = max(1, course.weekly_minutes // 7)
    # Keep each day's allocation within its budget, including at very low budgets.
    minutes = min(20, daily)
    per_day = max(1, daily // minutes)
    capacity = days * per_day
    targets = targets[: min(capacity, 500)]
    for i, (kind, target, title) in enumerate(targets):
        offset = min(days - 1, i // per_day)
        db.add(
            StudyTask(
                plan_id=plan.id,
                task_type=kind,
                target_id=target,
                title=title,
                scheduled_for=today() + timedelta(days=offset),
                minutes=minutes,
            )
        )
    event(db, course, "plan_rebuilt", properties={"version": plan.version, "task_count": len(targets)})
    return plan


def refresh_material_plan(db, kb_id):
    course = db.scalar(select(Course).where(Course.knowledge_base_id == kb_id))
    if course:
        rebuild_plan(db, course)
        event(db, course, "material_ready")


def validate_sources(db, user, course, citations):
    scope = course_scope(db, user, course)
    valid = scope.hydrate([c.get("chunk_id") for c in citations])
    return bool(citations) and all(
        c.get("chunk_id") in valid
        and (not c.get("revision_id") or c["revision_id"] == valid[c["chunk_id"]]["revision_id"])
        for c in citations
    )


def remaining_budget(db, course):
    """Count completed work on the user's current day; pending plans do not reserve time twice."""
    from datetime import UTC

    start = (
        datetime.combine(today(), datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    end = start + timedelta(days=1)
    completed = db.scalars(
        select(StudyTask)
        .join(StudyPlan)
        .where(
            StudyPlan.course_id == course.id,
            StudyTask.status == "completed",
            StudyTask.completed_at >= start,
            StudyTask.completed_at < end,
        )
    ).all()
    reviews = db.scalars(
        select(LearningEvent).where(
            LearningEvent.course_id == course.id,
            LearningEvent.event_name == "review_card_graded",
            LearningEvent.created_at >= start,
            LearningEvent.created_at < end,
        )
    ).all()
    return max(0, course.weekly_minutes // 7 - sum(t.minutes for t in completed) - 5 * len(reviews))
