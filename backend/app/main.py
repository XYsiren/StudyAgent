import logging
import threading
import time
import uuid
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
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        # 1 MB is reserved for multipart field names and boundaries; the file
        # itself has a separate exact size check in the upload handler.
        upload = scope.get("path") == "/api/documents/upload"
        maximum = ((self.settings.max_upload_mb + 1) if upload else 4) * 1024 * 1024
        length = dict(scope.get("headers", [])).get(b"content-length", b"0")
        try:
            too_large = int(length) > maximum
        except ValueError:
            return await JSONResponse({"detail": "Content-Length 无效"}, status_code=400)(scope, receive, send)
        if too_large:
            return await JSONResponse({"detail": "上传文件或请求体超过大小限制"},
                                      status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise HTTPException(413, "上传文件或请求体超过大小限制")
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
        # Recover legacy synchronous runs; new queued evaluations carry a lease.
        from sqlalchemy import update

        from app.models import EvaluationRun, utcnow
        with database.session() as db:
            db.execute(update(EvaluationRun).where(EvaluationRun.status == "running",
                       EvaluationRun.lease_token.is_(None),
                       EvaluationRun.created_at < utcnow() - timedelta(hours=2)).values(
                status="failed", error="服务重启中断了评测，请重新运行"))
            db.commit()
        stop = threading.Event()
        app.state.worker_stop = stop
        worker = None
        if not settings.redis_url:
            worker = threading.Thread(target=run_worker, args=(database, settings, stop), daemon=True,
                                      name="rag-ingestion-worker")
            worker.start()
        app.state.worker_thread = worker
        try:
            yield
        finally:
            stop.set()
            if worker:
                worker.join(timeout=10)
            database.engine.dispose()

    app = FastAPI(title="知衡 · RAG Knowledge Assistant", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=True, allow_methods=["GET", "POST", "DELETE", "PUT", "PATCH", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "X-RAG-Request"])
    app.add_middleware(UploadLimitMiddleware, settings=settings)

    @app.middleware("http")
    async def security_headers(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.cookies.get("rag_session") and not request.headers.get("Authorization"):
            origin = request.headers.get("Origin")
            if (origin and origin not in [*settings.cors_origins, settings.public_base_url.rstrip("/")]) or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
            if not request.url.path.startswith("/api/auth/") and request.headers.get("X-RAG-Request") != "1":
                return JSONResponse({"detail": "请求缺少防跨站标记"}, status_code=403)
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        from app.services.observability import LATENCY, REQUESTS
        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(route).observe(time.perf_counter() - started)
        import json
        logger.info(json.dumps({"event": "http", "request_id": request.state.request_id,
            "method": request.method, "route": route, "status": response.status_code,
            "headers_latency_ms": round((time.perf_counter() - started) * 1000, 2)}))
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    from app.api.enterprise import router as enterprise_router
    app.include_router(enterprise_router)
    from app.api.oidc import router as oidc_router
    app.include_router(oidc_router)
    from app.api.platform import router as platform_router
    app.include_router(platform_router)
    return app


app = create_app()
