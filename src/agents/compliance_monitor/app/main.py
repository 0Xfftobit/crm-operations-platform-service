"""FastAPI application factory.

Wires config, structured logging, health/readiness, CORS, and the Compliance Monitoring domain:
pre-send hook + consent + activity ingest, fair-lending + disclosures, the immutable hash-chained
audit trail, and the rule-change (two-step approval) + regulation-tracker routers.
"""
import asyncio
import contextlib
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.audit import router as audit_router
from app.api.compliance import router as compliance_router
from app.api.health import router as health_router
from app.api.rules import router as rules_router
from app.core.config import get_settings
from app.core.logging import configure_logging

logger = structlog.get_logger(__name__)


async def _chain_verify_loop(interval_min: int) -> None:
    """Periodically verify audit-chain integrity (FR-018); a break raises a critical alert."""
    from app import service
    while True:
        await asyncio.sleep(interval_min * 60)
        result = service.integrity_check()
        if not result["verified"]:
            logger.error("audit_chain_break", broken_at=result["broken_at"])


async def _anchor_loop(interval_min: int) -> None:
    """Periodically anchor a Merkle root over the ledger for compact tamper-evidence."""
    from app import service
    while True:
        await asyncio.sleep(interval_min * 60)
        result = service.anchor_ledger(external_ref="periodic")
        if result is not None:
            logger.info("merkle_anchor", seq_to=result["seq_to"], root=result["merkle_root"][:16])


async def _coverage_loop(interval_min: int) -> None:
    """Periodically flag sources that have gone silent (coverage blind spots, BR-3)."""
    from app import service
    while True:
        await asyncio.sleep(interval_min * 60)
        service.sweep_coverage()


async def _lifecycle_loop(interval_min: int) -> None:
    """Periodically tier aged audit entries to cold storage and purge past-retention ranges."""
    from app import service
    while True:
        await asyncio.sleep(interval_min * 60)
        service.audit_cold_tier()
        service.audit_purge()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "application_starting", app_name=settings.app_name, env=settings.env,
        storage=settings.storage_gateway, cache=settings.cache_gateway,
        event_source=settings.event_source_gateway, worm=settings.worm_gateway,
    )
    # Durable schema on startup (dev convenience; prod runs `alembic upgrade head`).
    if settings.uses_sql and settings.db_auto_create:
        from app.db import engine
        engine.create_all()

    from app import kafka_bus
    await kafka_bus.start_producer()
    if settings.kafka_consumer_enabled or settings.kafka_ingest:
        await kafka_bus.start_consumer()

    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if settings.chain_verify_interval_min > 0:
        tasks.append(asyncio.create_task(_chain_verify_loop(settings.chain_verify_interval_min)))
    if settings.merkle_anchor_interval_min > 0:
        tasks.append(asyncio.create_task(_anchor_loop(settings.merkle_anchor_interval_min)))
    if settings.event_source_gateway == "http":
        from app import http_source
        tasks.append(asyncio.create_task(http_source.poll_loop(stop)))
    if settings.coverage_heartbeat_max_min > 0 and settings.coverage_sweep_interval_min > 0:
        tasks.append(asyncio.create_task(_coverage_loop(settings.coverage_sweep_interval_min)))
    if settings.audit_lifecycle_interval_min > 0:
        tasks.append(asyncio.create_task(_lifecycle_loop(settings.audit_lifecycle_interval_min)))
    yield
    stop.set()
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await kafka_bus.stop_consumer()
    await kafka_bus.stop_producer()
    logger.info("application_stopping", app_name=settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # /api/v1/health + /api/v1/ready (matches the Docker/ECS health check path)
    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(compliance_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    app.include_router(rules_router, prefix=settings.api_prefix)

    # bare /health for simple load-balancer / uptime probes
    @app.get("/health", tags=["health"], summary="Liveness (root)")
    def _root_health() -> dict:
        return {"status": "ok"}

    # OpenTelemetry auto-instrumentation (no-op unless TRACING_ENABLED + otel libs present)
    from app.core.tracing import configure_tracing
    configure_tracing(app)

    return app


app = create_app()
