import hashlib
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import __version__
from app.api.dependencies import DB, CurrentUser
from app.models import (
    Chunk,
    Conversation,
    Document,
    EvaluationRun,
    IngestionJob,
    KnowledgeBase,
    Message,
    User,
    new_id,
)
from app.schemas import (
    ChatRequest,
    ComparisonRequest,
    EvaluationRequest,
    KnowledgeBaseRequest,
    LoginRequest,
    RegisterRequest,
)
from app.security import hash_password, issue_token, verify_password
from app.worker import notify_job

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}


def user_payload(user):
    return {"id": user.id, "email": user.email, "name": user.name}


def auth_payload(user, settings):
    return {"access_token": issue_token(user.id, settings), "token_type": "bearer", "user": user_payload(user)}


def own_kb(db, user, kb_id):
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user.id))
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    return kb


def own_document(db, user, document_id):
    document = db.scalar(select(Document).join(KnowledgeBase).where(
        Document.id == document_id, KnowledgeBase.user_id == user.id))
    if document is None:
        raise HTTPException(404, "文档不存在")
    return document


def own_conversation(db, user, conversation_id):
    conversation = db.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.user_id == user.id))
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    return conversation


def document_payload(db, document):
    job_id = db.scalar(select(IngestionJob.id).where(IngestionJob.document_id == document.id)
                       .order_by(IngestionJob.created_at.desc()).limit(1))
    return {"id": document.id, "knowledge_base_id": document.knowledge_base_id, "name": document.name,
            "size_bytes": document.size_bytes, "status": document.status, "chunk_count": document.chunk_count,
            "error": document.error, "created_at": document.created_at, "job_id": job_id}


def evaluation_payload(run):
    return {"id": run.id, "name": run.name, "status": run.status, "metrics": run.metrics,
            "results": run.results, "created_at": run.created_at, "error": run.error,
            "cases": run.cases, "knowledge_base_ids": run.knowledge_base_ids}


def scoped_chunks(db, user, kb_ids):
    selected = list(dict.fromkeys(kb_ids))
    if selected:
        owned_ids = set(db.scalars(select(KnowledgeBase.id).where(
            KnowledgeBase.user_id == user.id, KnowledgeBase.id.in_(selected))).all())
        if set(selected) != owned_ids:
            raise HTTPException(404, "一个或多个知识库不存在")
    query = select(Chunk, Document).join(Document).join(KnowledgeBase).where(
        KnowledgeBase.user_id == user.id, Document.status == "ready")
    if selected:
        query = query.where(KnowledgeBase.id.in_(selected))
    return [{"id": chunk.id, "document_id": document.id, "document_name": document.name,
             "knowledge_base_id": document.knowledge_base_id, "text": chunk.text,
             "page": chunk.page, "ordinal": chunk.ordinal}
            for chunk, document in db.execute(query.order_by(Document.created_at, Chunk.ordinal)).all()]


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.post("/auth/register", status_code=201)
def register(body: RegisterRequest, request: Request, db: DB):
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此邮箱已注册")
    return auth_payload(user, request.app.state.settings)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, db: DB):
    user = db.scalar(select(User).where(User.email == body.email))
    encoded = user.password_hash if user else "pbkdf2_sha256$600000$00000000000000000000000000000000$" + "0" * 64
    valid = verify_password(body.password, encoded)
    if user is None or not valid:
        raise HTTPException(401, "邮箱或密码错误", headers={"WWW-Authenticate": "Bearer"})
    return auth_payload(user, request.app.state.settings)


@router.get("/auth/me")
def me(user: CurrentUser):
    return user_payload(user)


@router.get("/knowledge-bases")
def knowledge_bases(db: DB, user: CurrentUser):
    query = select(KnowledgeBase, func.count(Document.id)).outerjoin(Document).where(
        KnowledgeBase.user_id == user.id).group_by(KnowledgeBase.id).order_by(KnowledgeBase.created_at.desc())
    return [{"id": kb.id, "name": kb.name, "description": kb.description,
             "created_at": kb.created_at, "document_count": count} for kb, count in db.execute(query).all()]


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(body: KnowledgeBaseRequest, db: DB, user: CurrentUser):
    kb = KnowledgeBase(user_id=user.id, name=body.name, description=body.description)
    db.add(kb)
    db.commit()
    return {"id": kb.id, "name": kb.name, "description": kb.description,
            "created_at": kb.created_at, "document_count": 0}


def remove_documents(db, documents, settings):
    from app.services.retrieval import delete_document
    paths = []
    for document in documents:
        # Serialize deletion with indexing; a worker whose document was deleted
        # cannot pass its subsequent atomic job update.
        db.execute(update(Document).where(Document.id == document.id).values(status="deleting"))
        try:
            delete_document(document.id)
        except Exception as exc:
            logger.exception("Vector deletion failed for %s", document.id)
            raise HTTPException(503, "向量索引暂时不可用，删除未完成，请稍后重试") from exc
        paths.append(document.stored_path)
        db.delete(document)
    db.flush()
    return paths


def unlink_uploads(paths, settings):
    root = (settings.data_dir / "uploads").resolve()
    for stored_path in paths:
        path = Path(stored_path).resolve()
        if path.is_relative_to(root):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove deleted upload %s", path.name)


@router.delete("/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str, request: Request, db: DB, user: CurrentUser):
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id,
                   KnowledgeBase.user_id == user.id).with_for_update())
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    # PostgreSQL's row lock prevents a new document FK from appearing during
    # cleanup; this write also provides the equivalent serialization in SQLite.
    db.execute(update(KnowledgeBase).where(KnowledgeBase.id == kb.id).values(name=kb.name))
    documents = db.scalars(select(Document).where(Document.knowledge_base_id == kb.id)).all()
    paths = remove_documents(db, documents, request.app.state.settings)
    db.delete(kb)
    db.commit()
    unlink_uploads(paths, request.app.state.settings)


@router.get("/documents")
def documents(db: DB, user: CurrentUser, knowledge_base_id: str | None = None):
    query = select(Document).join(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
    if knowledge_base_id:
        own_kb(db, user, knowledge_base_id)
        query = query.where(Document.knowledge_base_id == knowledge_base_id)
    return [document_payload(db, item) for item in db.scalars(query.order_by(Document.created_at.desc())).all()]


@router.post("/documents/upload", status_code=201)
async def upload_document(request: Request, db: DB, user: CurrentUser,
                          file: Annotated[UploadFile, File()], knowledge_base_id: Annotated[str, Form()]):
    own_kb(db, user, knowledge_base_id)
    filename = Path((file.filename or "").replace("\\", "/")).name[:255]
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "不支持此格式。支持 PDF、Word(docx)、文本、Markdown、CSV、JSON、HTML 和图片")
    settings = request.app.state.settings
    upload_root = (settings.data_dir / "uploads").resolve()
    upload_root.mkdir(parents=True, exist_ok=True)
    target = upload_root / f"{new_id()}{suffix}"
    digest, size, committed = hashlib.sha256(), 0, False
    try:
        with target.open("xb") as output:
            while data := await file.read(1024 * 1024):
                size += len(data)
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, f"文档不能超过 {settings.max_upload_mb} MB")
                digest.update(data)
                output.write(data)
        if size == 0:
            raise HTTPException(400, "不能上传空文件")
        existing = db.scalar(select(Document).where(Document.knowledge_base_id == knowledge_base_id,
                                                    Document.sha256 == digest.hexdigest()))
        if existing:
            target.unlink(missing_ok=True)
            payload = document_payload(db, existing)
            return {"document": payload, "job_id": payload["job_id"], "duplicate": True}
        document = Document(knowledge_base_id=knowledge_base_id, name=filename, stored_path=str(target),
                            sha256=digest.hexdigest(), size_bytes=size, status="queued")
        db.add(document)
        db.flush()
        job = IngestionJob(document_id=document.id)
        db.add(job)
        db.commit()
        committed = True
        notify_job(settings, job.id)
        return {"document": document_payload(db, document), "job_id": job.id, "duplicate": False}
    except IntegrityError:
        db.rollback()
        target.unlink(missing_ok=True)
        existing = db.scalar(select(Document).where(Document.knowledge_base_id == knowledge_base_id,
                                                    Document.sha256 == digest.hexdigest()))
        if existing:
            payload = document_payload(db, existing)
            return {"document": payload, "job_id": payload["job_id"], "duplicate": True}
        raise HTTPException(409, "文档或知识库状态已发生变化，请刷新后重试")
    except BaseException:
        if not committed:
            target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/documents/{document_id}/download")
def download_document(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id)
    path = Path(document.stored_path).resolve()
    if not path.is_relative_to((request.app.state.settings.data_dir / "uploads").resolve()) or not path.is_file():
        raise HTTPException(404, "原文件不存在")
    return FileResponse(path, filename=document.name, media_type="application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/documents/{document_id}", status_code=204)
def delete_document_api(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id)
    paths = remove_documents(db, [document], request.app.state.settings)
    db.commit()
    unlink_uploads(paths, request.app.state.settings)


@router.post("/documents/{document_id}/retry")
def retry_document(document_id: str, request: Request, db: DB, user: CurrentUser):
    document = own_document(db, user, document_id)
    if document.status not in {"failed", "ready"}:
        raise HTTPException(409, "仅失败或已就绪的文档可以重新索引")
    changed = db.execute(update(Document).where(Document.id == document_id, Document.status.in_(["failed", "ready"]))
                         .values(status="queued", error=None, chunk_count=0))
    if changed.rowcount != 1:
        raise HTTPException(409, "任务状态已变化，请刷新后重试")
    job = IngestionJob(document_id=document_id)
    db.add(job)
    db.commit()
    notify_job(request.app.state.settings, job.id)
    return {"job_id": job.id}


@router.get("/documents/{document_id}/chunks")
def document_chunks(document_id: str, db: DB, user: CurrentUser):
    own_document(db, user, document_id)
    return [{"id": chunk.id, "text": chunk.text, "page": chunk.page, "ordinal": chunk.ordinal}
            for chunk in db.scalars(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)).all()]


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: DB, user: CurrentUser):
    job = db.scalar(select(IngestionJob).join(Document).join(KnowledgeBase).where(
        IngestionJob.id == job_id, KnowledgeBase.user_id == user.id))
    if job is None:
        raise HTTPException(404, "任务不存在")
    return {"id": job.id, "status": job.status, "progress": job.progress,
            "error": job.error, "document_id": job.document_id}


def perform_chat(body, db, user):
    from app.services.answering import answer_question
    conversation = own_conversation(db, user, body.conversation_id) if body.conversation_id else None
    chunks = scoped_chunks(db, user, body.knowledge_base_ids)
    history = []
    if conversation:
        previous_messages = db.scalars(select(Message).where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(6)).all()
        history = [{"role": item.role, "content": item.content} for item in reversed(previous_messages)]
    try:
        result = answer_question(body.question, chunks, top_k=body.top_k, mode=body.mode, history=history)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Answer provider failed")
        raise HTTPException(503, "问答服务暂时不可用，请检查模型配置或稍后重试") from exc
    # Defense in depth: service citations must refer to this request's candidates.
    allowed_ids = {chunk["id"] for chunk in chunks}
    result["citations"] = [citation for citation in result.get("citations", [])
                           if citation.get("chunk_id", citation.get("id")) in allowed_ids]
    result.setdefault("trace", [])
    result.setdefault("cached", False)
    result.setdefault("provider", "unknown")
    result.setdefault("usage", {})
    if conversation is None:
        conversation = Conversation(user_id=user.id, title=body.question[:100])
        db.add(conversation)
        db.flush()
    db.add(Message(conversation_id=conversation.id, role="user", content=body.question))
    db.flush()
    assistant = Message(conversation_id=conversation.id, role="assistant", content=result["answer"],
                        citations=result["citations"], trace=result["trace"])
    db.add(assistant)
    db.commit()
    return {**result, "conversation_id": conversation.id, "message_id": assistant.id}


@router.post("/chat")
def chat(body: ChatRequest, db: DB, user: CurrentUser):
    return perform_chat(body, db, user)


@router.post("/chat/stream")
def stream_chat(body: ChatRequest, db: DB, user: CurrentUser):
    # Events are replayed after a complete, persisted answer; this endpoint does
    # not claim that the configured provider supports token streaming.
    result = perform_chat(body, db, user)
    def events():
        yield "event: sources\ndata: " + json.dumps(result["citations"], ensure_ascii=False) + "\n\n"
        for trace in result["trace"]:
            yield "event: trace\ndata: " + json.dumps(trace, ensure_ascii=False) + "\n\n"
        for start in range(0, len(result["answer"]), 32):
            yield "event: token\ndata: " + json.dumps({"text": result["answer"][start:start + 32]}, ensure_ascii=False) + "\n\n"
        yield "event: done\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations")
def conversations(db: DB, user: CurrentUser):
    return [{"id": item.id, "title": item.title, "created_at": item.created_at}
            for item in db.scalars(select(Conversation).where(Conversation.user_id == user.id)
                                   .order_by(Conversation.created_at.desc())).all()]


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: str, db: DB, user: CurrentUser):
    item = own_conversation(db, user, conversation_id)
    messages = db.scalars(select(Message).where(Message.conversation_id == item.id)
                          .order_by(Message.created_at, Message.id)).all()
    return {"id": item.id, "title": item.title, "messages": [
        {"id": message.id, "role": message.role, "content": message.content,
         "citations": message.citations, "trace": message.trace, "created_at": message.created_at}
        for message in messages]}


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: DB, user: CurrentUser):
    item = own_conversation(db, user, conversation_id)
    db.delete(item)
    db.commit()


def validate_expected_documents(db, user, cases, kb_ids):
    expected = {doc for case in cases for doc in case.expected_document_ids}
    if not expected:
        return
    query = select(Document.id).join(KnowledgeBase).where(
        KnowledgeBase.user_id == user.id, Document.id.in_(expected))
    if kb_ids:
        query = query.where(Document.knowledge_base_id.in_(kb_ids))
    if set(db.scalars(query).all()) != expected:
        raise HTTPException(422, "预期文档必须属于当前用户所选知识库")


@router.post("/evaluations", status_code=201)
def create_evaluation(body: EvaluationRequest, db: DB, user: CurrentUser):
    from app.services.evaluation import evaluate_cases
    chunks = scoped_chunks(db, user, body.knowledge_base_ids)
    validate_expected_documents(db, user, body.cases, body.knowledge_base_ids)
    cases = [case.model_dump() for case in body.cases]
    for case in cases:
        if case.get("message_id"):
            recorded = db.scalar(select(Message).join(Conversation).where(
                Message.id == case["message_id"], Message.role == "assistant", Conversation.user_id == user.id))
            if recorded is None:
                raise HTTPException(404, "历史答案不存在或无权访问")
            question_message = db.scalar(select(Message).where(Message.conversation_id == recorded.conversation_id,
                Message.role == "user", Message.created_at <= recorded.created_at)
                .order_by(Message.created_at.desc(), Message.id.desc()).limit(1))
            if question_message is None:
                raise HTTPException(400, "历史答案缺少对应问题")
            case.update(question=question_message.content, recorded_answer=recorded.content,
                recorded_citations=recorded.citations or [])
    run = EvaluationRun(user_id=user.id, name=body.name, status="running", cases=cases,
                        knowledge_base_ids=body.knowledge_base_ids)
    db.add(run)
    db.commit()
    try:
        result = evaluate_cases(cases, chunks, use_ragas=body.use_ragas)
        run.status, run.metrics, run.results = "completed", result["metrics"], result["results"]
        db.commit()
    except Exception as exc:
        logger.exception("Evaluation failed")
        db.rollback()
        run = db.get(EvaluationRun, run.id)
        run.status, run.error = "failed", str(exc)[:2000]
        db.commit()
        raise HTTPException(400 if isinstance(exc, ValueError) else 503,
                            f"评测失败：{str(exc)[:500]}") from exc
    return evaluation_payload(run)


@router.post("/evaluations/compare")
def compare_evaluation(body: ComparisonRequest, db: DB, user: CurrentUser):
    from app.services.evaluation import compare_retrieval
    chunks = scoped_chunks(db, user, body.knowledge_base_ids)
    validate_expected_documents(db, user, body.cases, body.knowledge_base_ids)
    try:
        return compare_retrieval([case.model_dump() for case in body.cases], chunks)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Retrieval comparison failed")
        raise HTTPException(503, "检索对比暂时不可用") from exc


@router.get("/evaluations")
def evaluations(db: DB, user: CurrentUser):
    return [evaluation_payload(run) for run in db.scalars(select(EvaluationRun)
            .where(EvaluationRun.user_id == user.id).order_by(EvaluationRun.created_at.desc())).all()]


@router.get("/evaluations/{evaluation_id}")
def evaluation_detail(evaluation_id: str, db: DB, user: CurrentUser):
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == evaluation_id, EvaluationRun.user_id == user.id))
    if run is None:
        raise HTTPException(404, "评测不存在")
    return evaluation_payload(run)


@router.get("/system")
def system_info(user: CurrentUser):
    from app.services.runtime import capabilities
    return capabilities()
