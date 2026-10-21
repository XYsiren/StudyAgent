"""Authenticated personal courses and an evidence-bound practice/review loop."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DB, CurrentUser
from app.models import Chunk, Document, KnowledgeBase, User, utcnow
from app.models_learning import (
    Course,
    CourseUnit,
    KnowledgePoint,
    KnowledgePointChunk,
    LearningEvent,
    LearningJob,
    QuizAttempt,
    QuizItem,
    ReviewCard,
    StudyPlan,
    StudyTask,
)
from app.schemas_learning import (
    ERROR_TAGS,
    AttemptRequest,
    CardGrade,
    CourseCreate,
    CoursePatch,
    ErrorTags,
    MaterialPatch,
    PointMerge,
    PointRequest,
    PointSplit,
    QuizFeedback,
    QuizGenerate,
    QuizStatus,
    TaskPatch,
    TutorRequest,
    UnitRequest,
)
from app.services.learning import (
    course_scope,
    event,
    own_course,
    payload,
    point_in_course,
    rebuild_plan,
    remaining_budget,
    today,
    unit_in_course,
    validate_sources,
)

router = APIRouter(prefix="/api", tags=["learning"])


def course_payload(db, course):
    kb = db.get(KnowledgeBase, course.knowledge_base_id)
    return {**payload(course), "generation": kb.generation, "exam_passed": course.exam_date < today()}


@router.get("/courses")
def courses(db: DB, user: CurrentUser):
    return [
        course_payload(db, c)
        for c in db.scalars(select(Course).where(Course.user_id == user.id).order_by(Course.created_at.desc()))
    ]


@router.post("/courses", status_code=201)
def create_course(body: CourseCreate, db: DB, user: CurrentUser):
    if body.knowledge_base_id:
        from app.services.access import require_kb

        kb = require_kb(db, user, body.knowledge_base_id, "admin")
        if kb.user_id != user.id or kb.organization_id:
            raise HTTPException(422, "第一版课程只能关联自己创建的个人知识库")
        if db.scalar(select(Course.id).where(Course.knowledge_base_id == kb.id)):
            raise HTTPException(409, "该知识库已关联课程")
    else:
        kb = KnowledgeBase(user_id=user.id, name=body.name, description=body.syllabus or "", visibility="restricted")
        db.add(kb)
        db.flush()
    course = Course(
        user_id=user.id,
        knowledge_base_id=kb.id,
        name=body.name,
        exam_name=body.exam_name or body.name,
        exam_date=body.exam_date,
        target_score=body.target_score,
        syllabus=body.syllabus or "",
        weekly_minutes=body.weekly_minutes or 300,
    )
    db.add(course)
    db.flush()
    rebuild_plan(db, course)
    event(db, course, "course_created")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该知识库已关联课程")
    return course_payload(db, course)


@router.get("/courses/{course_id}")
def course_detail(course_id: str, db: DB, user: CurrentUser):
    return course_payload(db, own_course(db, user, course_id))


@router.patch("/courses/{course_id}")
def patch_course(course_id: str, body: CoursePatch, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    for key in ("name", "exam_date", "status"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} 不能为空")
    for key, value in changes.items():
        if key == "weekly_minutes" and value is None:
            value = 300
        if key == "syllabus" and value is None:
            value = ""
        if key == "exam_name" and not value:
            value = changes.get("name") or course.name
        setattr(course, key, value)
    if "exam_date" in changes or "weekly_minutes" in changes or "status" in changes:
        rebuild_plan(db, course)
    db.commit()
    return course_payload(db, course)


@router.delete("/courses/{course_id}", status_code=204)
def delete_course(course_id: str, request: Request, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    kb = db.get(KnowledgeBase, course.knowledge_base_id)
    # Reuse the durable cleanup outbox; never leave searchable vectors after SQL deletion.
    from app.api.routes import remove_documents

    docs = db.scalars(select(Document).where(Document.knowledge_base_id == kb.id)).all()
    remove_documents(db, docs, request.app.state.settings)
    db.delete(course)
    db.flush()
    db.delete(kb)
    db.commit()


@router.get("/courses/{course_id}/materials")
def course_materials(course_id: str, db: DB, user: CurrentUser):
    from app.api.routes import document_payload

    course = own_course(db, user, course_id)
    return [
        document_payload(db, d)
        for d in db.scalars(
            select(Document)
            .where(Document.knowledge_base_id == course.knowledge_base_id)
            .order_by(Document.created_at.desc())
        )
    ]


@router.post("/courses/{course_id}/materials/upload", status_code=201)
async def upload_material(
    course_id: str,
    request: Request,
    db: DB,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    material_type: Annotated[str, Form()] = "lecture",
):
    from app.api.routes import upload_document

    course = own_course(db, user, course_id)
    if material_type not in {"textbook", "lecture", "exam", "notes", "answers", "presentation"}:
        raise HTTPException(422, "资料类型无效")
    result = await upload_document(request, db, user, file, course.knowledge_base_id)
    doc = db.get(Document, result["document"]["id"])
    doc.material_metadata = {**doc.material_metadata, "type": material_type}
    event(
        db, course, "material_uploaded", doc.id, {"file_type": Path(doc.name).suffix, "duplicate": result["duplicate"]}
    )
    db.commit()
    return result


@router.patch("/courses/{course_id}/materials/{document_id}")
def patch_material(course_id: str, document_id: str, body: MaterialPatch, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    unit_in_course(db, course, body.unit_id)
    doc = db.get(Document, document_id)
    if not doc or doc.knowledge_base_id != course.knowledge_base_id:
        raise HTTPException(404, "课程资料不存在")
    doc.material_metadata = body.model_dump()
    db.commit()
    return payload(doc)


def material_course(db, user, document_id):
    doc = db.get(Document, document_id)
    course = (
        db.scalar(select(Course).where(Course.knowledge_base_id == doc.knowledge_base_id, Course.user_id == user.id))
        if doc
        else None
    )
    if not course:
        raise HTTPException(404, "课程资料不存在")
    return own_course(db, user, course.id), doc


@router.get("/materials/{document_id}/parse-status")
def parse_status(document_id: str, db: DB, user: CurrentUser):
    from app.api.routes import document_payload

    course, doc = material_course(db, user, document_id)
    result = document_payload(db, doc)
    result["parse_metadata"] = doc.parse_metadata
    if result["job_id"]:
        from app.models import IngestionJob

        job = db.get(IngestionJob, result["job_id"])
        stages = {
            "queued": "uploaded",
            "processing": "extracting",
            "completed": "ready",
            "failed": "failed",
            "vision_pending": "vision_pending",
        }
        result.update(
            parse_status="embedding"
            if job.stage == "生成向量"
            else "chunking"
            if job.stage == "切分知识片段"
            else stages.get(job.status, job.status),
            processed_slides=job.processed_pages if result["file_type"] == "pptx" else 0,
            total_pages=job.total_pages,
            stage=job.stage,
            progress=job.progress,
        )
    return result


@router.post("/materials/{document_id}/retry", status_code=202)
def retry_material(document_id: str, request: Request, db: DB, user: CurrentUser):
    from app.api.routes import retry_document

    material_course(db, user, document_id)
    return retry_document(document_id, request, db, user)


@router.get("/materials/{document_id}/slides/{slide_number}")
def slide_source(document_id: str, slide_number: int, db: DB, user: CurrentUser, revision_id: str | None = None):
    _, doc = material_course(db, user, document_id)
    if not doc.name.lower().endswith(".pptx"):
        raise HTTPException(422, "资料不是 PPTX")
    metadata = doc.parse_metadata
    if revision_id:
        from app.models import DocumentRevision

        revision = db.get(DocumentRevision, revision_id)
        if not revision or revision.document_id != doc.id:
            raise HTTPException(404, "来源版本不存在")
        metadata = revision.parse_metadata
    pages = metadata.get("pages", [])
    row = next((p for p in pages if p.get("slide_number") == slide_number), None)
    if not row:
        raise HTTPException(404, "幻灯片不存在")
    return {
        **row,
        "document_id": doc.id,
        "document_name": doc.name,
        "revision_id": revision_id or metadata.get("revision_id") or doc.active_revision_id,
    }


@router.get("/courses/{course_id}/units")
def units(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    return [
        payload(u)
        for u in db.scalars(select(CourseUnit).where(CourseUnit.course_id == course.id).order_by(CourseUnit.ordinal))
    ]


@router.post("/courses/{course_id}/units", status_code=201)
def create_unit(course_id: str, body: UnitRequest, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    unit_in_course(db, course, body.parent_id)
    unit = CourseUnit(course_id=course.id, **body.model_dump())
    db.add(unit)
    db.flush()
    rebuild_plan(db, course)
    db.commit()
    return payload(unit)


@router.patch("/course-units/{unit_id}")
def patch_unit(unit_id: str, body: UnitRequest, db: DB, user: CurrentUser):
    unit = db.get(CourseUnit, unit_id)
    if not unit:
        raise HTTPException(404, "章节不存在")
    course = own_course(db, user, unit.course_id, lock=True)
    unit_in_course(db, course, body.parent_id)
    parent, visited = body.parent_id, {unit.id}
    while parent:
        if parent in visited:
            raise HTTPException(422, "章节树不能形成循环")
        visited.add(parent)
        parent = db.get(CourseUnit, parent).parent_id
    for k, v in body.model_dump().items():
        setattr(unit, k, v)
    rebuild_plan(db, course)
    db.commit()
    return payload(unit)


@router.get("/courses/{course_id}/knowledge-points")
def points(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    return [
        payload(p)
        for p in db.scalars(
            select(KnowledgePoint).where(KnowledgePoint.course_id == course.id).order_by(KnowledgePoint.title)
        )
    ]


def link_chunks(db, user, course, point, ids):
    valid = course_scope(db, user, course).hydrate(ids)
    if len(set(ids)) != len(valid):
        raise HTTPException(422, "来源必须属于当前课程的有效资料版本")
    db.execute(delete(KnowledgePointChunk).where(KnowledgePointChunk.knowledge_point_id == point.id))
    db.add_all(KnowledgePointChunk(knowledge_point_id=point.id, chunk_id=i) for i in set(ids))


@router.post("/courses/{course_id}/knowledge-points", status_code=201)
def create_point(course_id: str, body: PointRequest, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    unit_in_course(db, course, body.unit_id)
    point = KnowledgePoint(
        course_id=course.id, **body.model_dump(exclude={"chunk_ids"}), source="manual", confirmed_at=utcnow()
    )
    db.add(point)
    db.flush()
    link_chunks(db, user, course, point, body.chunk_ids)
    rebuild_plan(db, course)
    event(db, course, "knowledge_point_confirmed", point.id)
    db.commit()
    return payload(point)


@router.post("/courses/{course_id}/knowledge-points/suggest")
def suggest_points(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    scope = course_scope(db, user, course)
    rows = db.execute(
        select(Chunk, Document)
        .join(Document)
        .where(*scope.conditions)
        .order_by(Document.name, Chunk.ordinal)
        .limit(200)
    ).all()
    existing = set(db.scalars(select(KnowledgePoint.title).where(KnowledgePoint.course_id == course.id)))
    result = []
    # Deterministic title suggestions avoid turning model guesses into an exam syllabus.
    for chunk, doc in rows:
        title = (chunk.source_metadata or {}).get("slide_title") or next(
            (s.strip(" #\t") for s in chunk.text.splitlines() if len(s.strip()) >= 4), ""
        )[:120]
        if not title or title in existing:
            continue
        point = KnowledgePoint(
            course_id=course.id,
            title=title,
            source="suggested",
            confirmed_at=None,
            description="由资料标题建议，请确认是否属于考试范围",
        )
        db.add(point)
        db.flush()
        db.add(KnowledgePointChunk(knowledge_point_id=point.id, chunk_id=chunk.id))
        existing.add(title)
        result.append(payload(point))
        if len(result) >= 20:
            break
    db.commit()
    return result


@router.patch("/knowledge-points/{point_id}")
def patch_point(point_id: str, body: PointRequest, db: DB, user: CurrentUser):
    point = db.get(KnowledgePoint, point_id)
    if not point:
        raise HTTPException(404, "知识点不存在")
    course = own_course(db, user, point.course_id, lock=True)
    unit_in_course(db, course, body.unit_id)
    for k, v in body.model_dump(exclude={"chunk_ids"}).items():
        setattr(point, k, v)
    link_chunks(db, user, course, point, body.chunk_ids)
    rebuild_plan(db, course)
    db.commit()
    return payload(point)


@router.post("/knowledge-points/{point_id}/confirm")
def confirm_point(point_id: str, db: DB, user: CurrentUser):
    point = db.get(KnowledgePoint, point_id)
    if not point:
        raise HTTPException(404, "知识点不存在")
    course = own_course(db, user, point.course_id, lock=True)
    point.confirmed_at = utcnow()
    rebuild_plan(db, course)
    event(db, course, "knowledge_point_confirmed", point.id)
    db.commit()
    return payload(point)


@router.delete("/knowledge-points/{point_id}", status_code=204)
def delete_point(point_id: str, db: DB, user: CurrentUser):
    point = db.get(KnowledgePoint, point_id)
    if not point:
        raise HTTPException(404, "知识点不存在")
    course = own_course(db, user, point.course_id, lock=True)
    db.delete(point)
    db.flush()
    rebuild_plan(db, course)
    db.commit()


@router.post("/courses/{course_id}/knowledge-points/merge")
def merge_points(course_id: str, body: PointMerge, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    ids = set(body.point_ids)
    if len(ids) < 2:
        raise HTTPException(422, "请至少选择两个不同知识点")
    rows = [point_in_course(db, course, point_id) for point_id in ids]
    linked = db.scalars(
        select(KnowledgePointChunk.chunk_id).where(KnowledgePointChunk.knowledge_point_id.in_(ids))
    ).all()
    valid = course_scope(db, user, course).hydrate(linked)
    merged = KnowledgePoint(
        course_id=course.id,
        title=body.title,
        description="\n".join(p.description for p in rows if p.description),
        unit_id=rows[0].unit_id if all(p.unit_id == rows[0].unit_id for p in rows) else None,
        importance=max(p.importance for p in rows),
        source="manual",
        confirmed_at=utcnow() if all(p.confirmed_at for p in rows) else None,
    )
    db.add(merged)
    db.flush()
    for cid in valid:
        db.add(KnowledgePointChunk(knowledge_point_id=merged.id, chunk_id=cid))
    for model in (QuizItem, ReviewCard):
        db.execute(update(model).where(model.knowledge_point_id.in_(ids)).values(knowledge_point_id=merged.id))
    for row in rows:
        db.delete(row)
    db.flush()
    rebuild_plan(db, course)
    event(db, course, "knowledge_points_merged", merged.id, {"count": len(rows)})
    db.commit()
    return payload(merged)


@router.post("/knowledge-points/{point_id}/split")
def split_point(point_id: str, body: PointSplit, db: DB, user: CurrentUser):
    point = db.get(KnowledgePoint, point_id)
    if not point:
        raise HTTPException(404, "知识点不存在")
    titles = [t.strip() for t in body.titles]
    if any(not t or len(t) > 200 for t in titles) or len(set(titles)) != len(titles):
        raise HTTPException(422, "新知识点名称不能为空或重复")
    course = own_course(db, user, point.course_id, lock=True)
    ids = db.scalars(
        select(KnowledgePointChunk.chunk_id).where(KnowledgePointChunk.knowledge_point_id == point.id)
    ).all()
    valid = course_scope(db, user, course).hydrate(ids)
    rows = []
    for title in titles:
        row = KnowledgePoint(
            course_id=course.id,
            unit_id=point.unit_id,
            title=title,
            description=point.description,
            importance=point.importance,
            source="manual",
            confirmed_at=point.confirmed_at,
        )
        db.add(row)
        db.flush()
        for cid in valid:
            db.add(KnowledgePointChunk(knowledge_point_id=row.id, chunk_id=cid))
        rows.append(row)
    db.delete(point)
    db.flush()
    rebuild_plan(db, course)
    event(db, course, "knowledge_point_split", properties={"count": len(rows)})
    db.commit()
    return [payload(r) for r in rows]


@router.get("/knowledge-points/{point_id}/sources")
def point_sources(point_id: str, db: DB, user: CurrentUser):
    point = db.get(KnowledgePoint, point_id)
    if not point:
        raise HTTPException(404, "知识点不存在")
    course = own_course(db, user, point.course_id)
    ids = db.scalars(
        select(KnowledgePointChunk.chunk_id).where(KnowledgePointChunk.knowledge_point_id == point.id)
    ).all()
    return list(course_scope(db, user, course).hydrate(ids).values())


def tutor_result(course_id, body, request, db, user, emit=None, cancelled=None):
    from app.schemas import ChatRequest
    from app.services.chat import run_chat

    course = own_course(db, user, course_id)
    course_scope(db, user, course, body.unit_id, body.knowledge_point_id)
    if body.conversation_id and not db.scalar(
        select(LearningEvent.id).where(
            LearningEvent.course_id == course.id,
            LearningEvent.event_name == "tutor_conversation",
            LearningEvent.entity_id == body.conversation_id,
        )
    ):
        raise HTTPException(404, "该会话不属于当前课程")
    modes = {"explain": "讲解", "example": "举例说明", "compare": "对比解释", "practice": "用练习引导理解"}
    question = f"请{modes[body.mode]}：{body.question}"
    chat = ChatRequest(
        question=question,
        knowledge_base_ids=[course.knowledge_base_id],
        mode="agent",
        request_id=body.request_id,
        conversation_id=body.conversation_id,
    )

    def scope_factory(session, actor):
        c = own_course(session, actor, course_id)
        return course_scope(session, actor, c, body.unit_id, body.knowledge_point_id)

    result = run_chat(
        chat,
        db,
        user,
        request,
        emit,
        cancelled,
        scope_factory=scope_factory,
        scope_key={"course": course_id, "unit": body.unit_id, "point": body.knowledge_point_id},
    )
    result.update(
        course_id=course_id,
        scope={
            "knowledge_base_ids": [course.knowledge_base_id],
            "unit_id": body.unit_id,
            "knowledge_point_id": body.knowledge_point_id,
        },
        evidence_status="unsupported"
        if result.get("abstained")
        else "cited"
        if result.get("citations")
        else "uncovered",
    )
    if result["evidence_status"] != "cited":
        result["answer"] = "当前资料未覆盖。" + result["answer"]
    if not result.get("idempotent_replay"):
        event(db, course, "tutor_conversation", result["conversation_id"])
        event(
            db,
            course,
            "tutor_answer_cited",
            properties={"cited": bool(result.get("citations")), "abstained": bool(result.get("abstained"))},
        )
        db.commit()
    return result


@router.post("/courses/{course_id}/tutor/ask")
@router.post("/courses/{course_id}/tutor/explain")
def tutor(course_id: str, body: TutorRequest, request: Request, db: DB, user: CurrentUser):
    return tutor_result(course_id, body, request, db, user)


@router.post("/courses/{course_id}/tutor/stream")
async def tutor_stream(course_id: str, body: TutorRequest, request: Request, db: DB, user: CurrentUser):
    import asyncio
    import queue
    import threading

    from fastapi.responses import StreamingResponse

    own_course(db, user, course_id)
    events, cancelled = queue.Queue(maxsize=128), threading.Event()
    user_id = user.id

    def emit(kind, value):
        while not cancelled.is_set():
            try:
                events.put((kind, value), timeout=0.2)
                return
            except queue.Full:
                continue
        raise HTTPException(499, "请求已取消")

    def produce():
        try:
            with request.app.state.database.session() as session:
                actor = session.get(User, user_id)
                if not actor or actor.disabled:
                    raise HTTPException(401, "登录状态无效")
                result = tutor_result(course_id, body, request, session, actor, emit, cancelled.is_set)
                emit("done", result)
        except Exception as exc:
            if not cancelled.is_set():
                emit(
                    "error",
                    {
                        "status": exc.status_code if isinstance(exc, HTTPException) else 503,
                        "detail": exc.detail if isinstance(exc, HTTPException) else "生成失败，请稍后重试",
                    },
                )

    threading.Thread(target=produce, daemon=True).start()

    async def stream():
        try:
            while not cancelled.is_set():
                if await request.is_disconnected():
                    break
                try:
                    kind, value = await asyncio.to_thread(events.get, True, 0.5)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield "event: " + kind + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"
                if kind in {"done", "error"}:
                    break
        finally:
            cancelled.set()

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/courses/{course_id}/quiz/generate", status_code=202)
@router.post("/courses/{course_id}/tutor/generate-practice", status_code=202)
def generate_quiz(course_id: str, body: QuizGenerate, request: Request, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    course_scope(db, user, course, body.unit_id, body.knowledge_point_id)
    existing = db.scalar(
        select(LearningJob).where(LearningJob.user_id == user.id, LearningJob.request_key == body.request_id)
    )
    params = body.model_dump(exclude={"request_id"})
    if existing:
        if existing.course_id != course.id or existing.parameters != params:
            raise HTTPException(409, "请求 ID 已用于其他出题任务")
        return payload(existing)
    from app.services.admission import consume

    settings = request.app.state.settings
    consume(
        request.app.state.database,
        f"model-budget:{user.id}",
        settings.user_daily_model_requests,
        86400,
        cost=body.count,
    )
    job = LearningJob(course_id=course.id, user_id=user.id, request_key=body.request_id, parameters=params)
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一出题请求正在创建，请重试")
    return payload(job)


@router.get("/learning-jobs/{job_id}")
def learning_job(job_id: str, db: DB, user: CurrentUser):
    job = db.get(LearningJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    own_course(db, user, job.course_id)
    return payload(job)


@router.post("/learning-jobs/{job_id}/{action}")
def change_job(job_id: str, action: str, db: DB, user: CurrentUser):
    job = db.scalar(select(LearningJob).where(LearningJob.id == job_id).with_for_update())
    if not job:
        raise HTTPException(404, "任务不存在")
    own_course(db, user, job.course_id)
    if action == "cancel" and job.status in {"queued", "running"}:
        job.status = "cancelled"
        job.lease_token = None
    elif action == "retry" and job.status in {"failed", "cancelled"}:
        job.status = "queued"
        job.error = None
        job.lease_token = None
    else:
        raise HTTPException(409, "任务状态不允许该操作")
    db.commit()
    return payload(job)


def own_quiz(db, user, quiz_id, require_valid=True):
    quiz = db.get(QuizItem, quiz_id)
    if not quiz:
        raise HTTPException(404, "题目不存在")
    course = own_course(db, user, quiz.course_id)
    if require_valid and (
        quiz.status in {"retired", "rejected"} or not validate_sources(db, user, course, quiz.citations)
    ):
        raise HTTPException(409, "题目已停用或来源已过期/变更，请重新生成")
    return course, quiz


@router.get("/courses/{course_id}/quiz")
def quiz_list(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    return [
        payload(q)
        for q in db.scalars(
            select(QuizItem).where(QuizItem.course_id == course.id).order_by(QuizItem.created_at.desc())
        )
        if validate_sources(db, user, course, q.citations)
    ]


@router.get("/quiz/{quiz_id}")
def quiz_detail(quiz_id: str, db: DB, user: CurrentUser):
    return payload(own_quiz(db, user, quiz_id)[1])


@router.patch("/quiz/{quiz_id}/review-status")
def quiz_status(quiz_id: str, body: QuizStatus, db: DB, user: CurrentUser):
    course, quiz = own_quiz(db, user, quiz_id, False)
    if body.status == "approved" and not validate_sources(db, user, course, quiz.citations):
        raise HTTPException(409, "来源已失效")
    quiz.status = body.status
    db.commit()
    return payload(quiz)


@router.post("/quiz/{quiz_id}/feedback")
def quiz_feedback(quiz_id: str, body: QuizFeedback, db: DB, user: CurrentUser):
    course, quiz = own_quiz(db, user, quiz_id)
    event(db, course, "quiz_feedback_reported", quiz.id, body.model_dump())
    db.commit()
    return {"recorded": True}


@router.post("/quiz/{quiz_id}/attempt", status_code=201)
def attempt_quiz(quiz_id: str, body: AttemptRequest, db: DB, user: CurrentUser):
    course, quiz = own_quiz(db, user, quiz_id)
    # Lock the course as a portable serialization boundary for attempts/cards.
    course = own_course(db, user, course.id, lock=True)
    if set(body.error_tags) - ERROR_TAGS:
        raise HTTPException(422, "错因标签无效")
    existing = db.scalar(
        select(QuizAttempt).where(QuizAttempt.user_id == user.id, QuizAttempt.request_key == body.request_id)
    )
    if existing:
        if (
            existing.quiz_item_id != quiz.id
            or existing.answer != body.answer
            or existing.assessment_phase != body.assessment_phase
        ):
            raise HTTPException(409, "幂等键已用于其他作答")
        return {
            **payload(existing),
            "standard_answer": quiz.answer,
            "explanation": quiz.explanation,
            "citations": quiz.citations,
            "idempotent_replay": True,
        }
    if body.assessment_phase != "practice":
        if not quiz.knowledge_point_id:
            raise HTTPException(422, "前测/后测题目必须关联同一知识点")
        opposite = "post" if body.assessment_phase == "pre" else "pre"
        if db.scalar(
            select(QuizAttempt.id).where(
                QuizAttempt.user_id == user.id,
                QuizAttempt.quiz_item_id == quiz.id,
                QuizAttempt.assessment_phase == opposite,
            )
        ):
            raise HTTPException(422, "前测和后测必须使用不同题目，不能重复同一题")
    correct = (body.answer.strip() == quiz.answer.strip()) if quiz.type != "short" else None
    row = QuizAttempt(
        user_id=user.id,
        quiz_item_id=quiz.id,
        request_key=body.request_id,
        answer=body.answer,
        assessment_phase=body.assessment_phase,
        is_correct=correct,
        duration_ms=body.duration_ms,
        confidence=body.confidence,
        error_tags=body.error_tags,
        suggested_error_tags=["记忆不牢"] if correct is False else [],
        feedback="简答题需对照来源自查，未自动计入正确率" if correct is None else "规则比对结果；题目和解析仍需核对",
    )
    db.add(row)
    db.flush()
    card = db.scalar(select(ReviewCard).where(ReviewCard.user_id == user.id, ReviewCard.quiz_item_id == quiz.id))
    if not card:
        card = ReviewCard(
            user_id=user.id,
            course_id=course.id,
            quiz_item_id=quiz.id,
            knowledge_point_id=quiz.knowledge_point_id,
            level=1,
            due_at=utcnow() + timedelta(days=1),
        )
        db.add(card)
    else:
        from app.services.review import schedule

        if correct is not None:
            schedule(card, "remember" if correct else "forgot")
    if quiz.knowledge_point_id and correct is not None:
        point = db.get(KnowledgePoint, quiz.knowledge_point_id)
        if point:
            point.mastery_state = "learning"
            point.mastery_score = max(0, min(1, point.mastery_score + (0.1 if correct else -0.2)))
    event(
        db,
        course,
        "quiz_attempt_submitted",
        row.id,
        {"correct": correct, "duration_ms": body.duration_ms, "assessment_phase": body.assessment_phase},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "重复作答请求，请重试同一请求 ID")
    return {
        **payload(row),
        "standard_answer": quiz.answer,
        "explanation": quiz.explanation,
        "citations": quiz.citations,
    }


@router.get("/courses/{course_id}/mistakes")
def mistakes(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    rows = db.execute(
        select(QuizAttempt, QuizItem)
        .join(QuizItem)
        .where(QuizItem.course_id == course.id, QuizAttempt.user_id == user.id, QuizAttempt.is_correct.is_(False))
        .order_by(QuizAttempt.created_at.desc())
    ).all()
    return [{**payload(a), "quiz": payload(q)} for a, q in rows if validate_sources(db, user, course, q.citations)]


@router.patch("/attempts/{attempt_id}/error-tags")
def error_tags(attempt_id: str, body: ErrorTags, db: DB, user: CurrentUser):
    row = db.get(QuizAttempt, attempt_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "作答不存在")
    own_quiz(db, user, row.quiz_item_id)
    if set(body.error_tags) - ERROR_TAGS:
        raise HTTPException(422, "错因标签无效")
    row.error_tags = body.error_tags
    db.commit()
    return payload(row)


@router.post("/attempts/{attempt_id}/retry")
def retry_attempt(attempt_id: str, db: DB, user: CurrentUser):
    row = db.get(QuizAttempt, attempt_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "作答不存在")
    return payload(own_quiz(db, user, row.quiz_item_id)[1])


@router.get("/courses/{course_id}/review/today")
def reviews_today(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    cards = db.scalars(
        select(ReviewCard)
        .where(ReviewCard.course_id == course.id, ReviewCard.user_id == user.id, ReviewCard.due_at <= utcnow())
        .order_by(ReviewCard.due_at)
    ).all()
    budget = max(1, course.weekly_minutes // 7)
    remaining = remaining_budget(db, course)
    result = []
    for c in cards:
        q = db.get(QuizItem, c.quiz_item_id) if c.quiz_item_id else None
        if q and q.status not in {"retired", "rejected"} and validate_sources(db, user, course, q.citations):
            result.append({**payload(c), "quiz": payload(q)})
    return {
        "cards": result[: remaining // 5],
        "due_count": len(result),
        "daily_budget_minutes": budget,
        "remaining_minutes": remaining,
        "estimated_card_minutes": 5,
        "interval_days": [1, 2, 4, 7, 14, 30],
    }


@router.post("/review-cards/{card_id}/grade")
def grade_card(card_id: str, body: CardGrade, db: DB, user: CurrentUser):
    card = db.get(ReviewCard, card_id)
    if not card or card.user_id != user.id:
        raise HTTPException(404, "复习卡不存在")
    course = own_course(db, user, card.course_id, lock=True)
    own_quiz(db, user, card.quiz_item_id)
    existing = db.scalar(
        select(LearningEvent).where(
            LearningEvent.course_id == course.id,
            LearningEvent.event_name == "review_card_graded",
            LearningEvent.entity_id == body.request_id,
        )
    )
    if existing:
        if existing.properties.get("card_id") != card_id or existing.properties.get("grade") != body.grade:
            raise HTTPException(409, "幂等键已用于其他复习")
        return existing.properties["result"]
    if remaining_budget(db, course) < 5:
        raise HTTPException(429, "今日学习时间预算已用完，请调整每周预算或明日继续")
    from app.services.review import schedule

    schedule(card, body.grade)
    result = payload(card)
    result = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in result.items()}
    event(
        db, course, "review_card_graded", body.request_id, {"card_id": card.id, "grade": body.grade, "result": result}
    )
    db.commit()
    return result


@router.get("/courses/{course_id}/study-plan")
def study_plan(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    plan = db.scalar(select(StudyPlan).where(StudyPlan.course_id == course.id))
    tasks = (
        db.scalars(
            select(StudyTask).where(StudyTask.plan_id == plan.id).order_by(StudyTask.scheduled_for, StudyTask.title)
        ).all()
        if plan
        else []
    )
    return {
        "plan": payload(plan) if plan else None,
        "tasks": [payload(t) for t in tasks],
        "today": today(),
        "exam_passed": course.exam_date < today(),
        "daily_budget_minutes": max(1, course.weekly_minutes // 7),
    }


@router.post("/courses/{course_id}/study-plan/rebuild")
def rebuild(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id, lock=True)
    rebuild_plan(db, course)
    db.commit()
    return study_plan(course_id, db, user)


@router.post("/study-tasks/{task_id}/complete")
def task_complete(task_id: str, body: TaskPatch, db: DB, user: CurrentUser):
    task = db.get(StudyTask, task_id)
    if not task:
        raise HTTPException(404, "学习任务不存在")
    plan = db.get(StudyPlan, task.plan_id)
    course = own_course(db, user, plan.course_id, lock=True)
    if body.minutes is not None:
        others = db.scalars(
            select(StudyTask).where(
                StudyTask.plan_id == plan.id,
                StudyTask.scheduled_for == task.scheduled_for,
                StudyTask.id != task.id,
                StudyTask.status.not_in(["skipped", "blocked"]),
            )
        ).all()
        if body.minutes + sum(t.minutes for t in others) > max(1, course.weekly_minutes // 7):
            raise HTTPException(422, "超过当日学习时间预算")
        task.minutes = body.minutes
    if task.status != "completed" and body.status == "completed" and task.minutes > remaining_budget(db, course):
        raise HTTPException(422, "超过今日学习预算，可调整时长、跳过任务或修改课程预算")
    old = task.status
    task.status = body.status
    task.completed_at = utcnow() if body.status == "completed" else None
    if old != "completed" and body.status == "completed":
        event(db, course, "study_task_completed", task.id, {"minutes": task.minutes})
    db.commit()
    return payload(task)


@router.get("/courses/{course_id}/metrics/overview")
@router.get("/courses/{course_id}/metrics/mastery")
@router.get("/courses/{course_id}/metrics/quality")
def course_metrics(course_id: str, db: DB, user: CurrentUser):
    from app.services.learning_metrics import metrics

    return metrics(db, user, own_course(db, user, course_id))


@router.get("/courses/{course_id}/metrics/pre-post")
def pre_post_metrics(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    results = []
    for point in db.scalars(
        select(KnowledgePoint).where(KnowledgePoint.course_id == course.id, KnowledgePoint.confirmed_at.is_not(None))
    ):
        rows = db.execute(
            select(QuizAttempt, QuizItem)
            .join(QuizItem)
            .where(
                QuizAttempt.user_id == user.id,
                QuizItem.knowledge_point_id == point.id,
                QuizAttempt.assessment_phase.in_(["pre", "post"]),
                QuizAttempt.is_correct.is_not(None),
            )
        ).all()
        rows = [(a, q) for a, q in rows if validate_sources(db, user, course, q.citations)]
        phases = {phase: {} for phase in ["pre", "post"]}
        for attempt, quiz in sorted(rows, key=lambda row: row[0].created_at):
            phases[attempt.assessment_phase][quiz.id] = attempt.is_correct
        pre, post = phases["pre"], phases["post"]
        difference = (sum(post.values()) / len(post) - sum(pre.values()) / len(pre)) if pre and post else None
        results.append(
            {
                "knowledge_point_id": point.id,
                "title": point.title,
                "value": difference,
                "sample_size": len(pre) + len(post),
                "pre_sample_size": len(pre),
                "post_sample_size": len(post),
                "period": {"from": course.created_at.date().isoformat(), "to": today().isoformat()},
                "definition_version": "pre-post-v1-independent-items",
                "definition": "同一知识点、不同题目的后测正确率减前测正确率；不代表考试成绩提升",
                "insufficient_sample": min(len(pre), len(post)) < 5,
            }
        )
    return results


@router.get("/courses/{course_id}/metrics/export")
def export_metrics(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    from fastapi.encoders import jsonable_encoder

    result = {
        "course": course_payload(db, course),
        "metrics": course_metrics(course_id, db, user),
        "pre_post": pre_post_metrics(course_id, db, user),
        "outline": points(course_id, db, user),
        "study_plan": study_plan(course_id, db, user),
        "quizzes": quiz_list(course_id, db, user),
        "mistakes": mistakes(course_id, db, user),
        "attempts": [
            payload(a)
            for a in db.scalars(
                select(QuizAttempt)
                .join(QuizItem)
                .where(QuizItem.course_id == course.id, QuizAttempt.user_id == user.id)
            )
        ],
        "review_cards": [
            payload(c)
            for c in db.scalars(
                select(ReviewCard).where(ReviewCard.course_id == course.id, ReviewCard.user_id == user.id)
            )
        ],
    }
    return Response(
        json.dumps(jsonable_encoder(result), ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="learning-export.json"'},
    )


@router.get("/courses/{course_id}/overview")
def overview(course_id: str, db: DB, user: CurrentUser):
    course = own_course(db, user, course_id)
    plan = study_plan(course_id, db, user)
    status_counts = dict(
        db.execute(
            select(Document.status, func.count())
            .where(Document.knowledge_base_id == course.knowledge_base_id)
            .group_by(Document.status)
        ).all()
    )
    return {
        "course": course_payload(db, course),
        "metrics": course_metrics(course_id, db, user),
        "today_tasks": [t for t in plan["tasks"] if t["scheduled_for"] <= today() and t["status"] == "pending"],
        "material_status": {
            status: status_counts.get(status, 0)
            for status in ["ready", "queued", "processing", "vision_pending", "failed"]
        },
        "exam_notice": "考试日期已过，请修改日期或进入考后复盘" if course.exam_date < today() else None,
    }
