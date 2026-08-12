"""FastAPI application factory for the Feedback Loop System."""

from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
import structlog.contextvars
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.metrics import MetricMiddleware
from app.messaging import kafka_bus

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "application_starting",
        app_name=settings.app_name,
        env=settings.env,
        storage=settings.storage_backend,
        storage_gateway=settings.storage_gateway,
        uses_sql=settings.uses_sql,
    )
    if settings.uses_sql and settings.db_auto_create:
        from app.db import engine

        engine.create_all()
    producer_started = await kafka_bus.start_producer()
    consumer_info = await kafka_bus.start_consumer()
    logger.info(
        "kafka_ready",
        producer_started=producer_started,
        consumer_status=consumer_info.get("status"),
    )
    yield
    await kafka_bus.stop_consumer()
    await kafka_bus.stop_producer()
    logger.info("application_stopping", app_name=settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Feedback Loop System — capture, label, monitor, retrain.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(MetricMiddleware)

    @app.middleware("http")
    async def add_request_id_header(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    prefix = settings.api_prefix
    app.include_router(health_router, prefix=prefix)
    app.include_router(feedback_router, prefix=prefix)

    @app.get("/health", tags=["health"], summary="Liveness (root)")
    def _root_health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
