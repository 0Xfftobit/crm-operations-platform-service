"""FastAPI application factory.

Wires config, structured logging, health/readiness, CORS, and the Follow-up Sequences domain:
enroll contacts into versioned cadences → on each scheduler tick run the fail-closed guardrail
(consent/DNC/quiet-hours/frequency-cap) → personalize → send via channel provider → advance
(branch on engagement / linear / stop on reply/apply/opt-out) → append to the outreach ledger,
plus per-step analytics. Temporal/Kafka/real-providers are deferred behind the gateways.
"""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.sequences import router as sequences_router
from app.core.config import get_settings
from app.core.logging import configure_logging

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "application_starting", app_name=settings.app_name, env=settings.env,
        storage=settings.storage_gateway, cache=settings.cache_gateway,
        event_source=settings.event_source_gateway,
    )
    yield
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
    app.include_router(sequences_router, prefix=settings.api_prefix)

    # bare /health for simple load-balancer / uptime probes
    @app.get("/health", tags=["health"], summary="Liveness (root)")
    def _root_health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
