"""FastAPI application factory for the Task Automation engine."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agents import router as agents_router
from app.api.architecture import router as architecture_router
from app.api.audit import router as audit_router
from app.api.automation import router as automation_router
from app.api.dashboard import router as dashboard_router
from app.api.design import router as design_router
from app.api.events import router as events_router
from app.api.functional import router as functional_router
from app.api.health import router as health_router
from app.api.requirements import router as requirements_router
from app.api.rules import router as rules_router
from app.api.tasks import router as tasks_router
from app.api.triggers import router as triggers_router
from app.api.vision import router as vision_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.messaging.kafka_bus import start_producer, stop_producer
from app.pipeline import init_pipeline
from app.scheduler import start_scheduler, stop_scheduler

try:
    from app.kafka_consumer_group import start_kafka_consumer_async, stop_kafka_consumer_async
except ImportError:
    async def start_kafka_consumer_async() -> dict:  # type: ignore[misc]
        return {"started": False}

    async def stop_kafka_consumer_async() -> None:
        return None

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "application_starting",
        app_name=settings.app_name,
        env=settings.env,
        engine_version=settings.task_automation_engine_version,
        storage=settings.storage_backend,
        scheduler=settings.scheduler_enabled,
    )
    producer_started = await start_producer()
    if producer_started:
        logger.info("kafka_producer_started", started=producer_started)
    await start_scheduler()
    init_pipeline()
    kafka = await start_kafka_consumer_async()
    if kafka.get("started"):
        logger.info("kafka_consumer_started", **kafka)
    yield
    await stop_kafka_consumer_async()
    await stop_producer()
    await stop_scheduler()
    logger.info("application_stopping", app_name=settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Task Automation Engine — converts CRM events and decisions into automated, "
            "auditable tasks, record updates, and document requests. Rules engine + "
            "Temporal orchestration + LLM task agents under strict guardrails."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    app.include_router(health_router, prefix=prefix)
    app.include_router(rules_router, prefix=prefix)
    app.include_router(tasks_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(events_router, prefix=prefix)
    app.include_router(audit_router, prefix=prefix)
    app.include_router(vision_router, prefix=prefix)
    app.include_router(requirements_router, prefix=prefix)
    app.include_router(triggers_router, prefix=prefix)
    app.include_router(agents_router, prefix=prefix)
    app.include_router(automation_router)
    app.include_router(functional_router, prefix=prefix)
    app.include_router(architecture_router, prefix=prefix)
    app.include_router(design_router, prefix=prefix)

    @app.get("/health", tags=["health"], summary="Liveness (root)")
    def _root_health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
