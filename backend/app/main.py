import logging
import threading
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.db import Database
from app.worker import run_worker

logger = logging.getLogger(__name__)


class UploadLimitMiddleware:
    """Bound multipart input before Starlette can spool an oversized request."""

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/documents/upload":
            return await self.app(scope, receive, send)
        # 1 MB is reserved for multipart field names and boundaries; the file
        # itself has a separate exact size check in the upload handler.
        maximum = (self.settings.max_upload_mb + 1) * 1024 * 1024
        length = dict(scope.get("headers", [])).get(b"content-length", b"0")
        try:
            too_large = int(length) > maximum
        except ValueError:
            return await JSONResponse({"detail": "Content-Length 无效"}, status_code=400)(scope, receive, send)
        if too_large:
            return await JSONResponse({"detail": f"文档不能超过 {self.settings.max_upload_mb} MB"},
                                      status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise HTTPException(413, f"文档不能超过 {self.settings.max_upload_mb} MB")
            return message

        return await self.app(scope, bounded_receive, send)


def create_app(settings=None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.settings = settings
        database = Database(settings)
        app.state.database = database
        database.initialize()
        # Evaluation is synchronous. A process crash must leave an explicit
        # failed run, never a permanently running result or invented metrics.
        from sqlalchemy import update

        from app.models import EvaluationRun, utcnow
        with database.session() as db:
            db.execute(update(EvaluationRun).where(EvaluationRun.status == "running",
                       EvaluationRun.created_at < utcnow() - timedelta(hours=2)).values(
                status="failed", error="服务重启中断了评测，请重新运行"))
            db.commit()
        stop = threading.Event()
        worker = None
        if not settings.redis_url:
            worker = threading.Thread(target=run_worker, args=(database, settings, stop), daemon=True,
                                      name="rag-ingestion-worker")
            worker.start()
        try:
            yield
        finally:
            stop.set()
            if worker:
                worker.join(timeout=10)
            database.engine.dispose()

    app = FastAPI(title="知衡 · RAG Knowledge Assistant", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=False, allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type"])
    app.add_middleware(UploadLimitMiddleware, settings=settings)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    return app


app = create_app()
