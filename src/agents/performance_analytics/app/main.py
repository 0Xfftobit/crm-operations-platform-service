"""FastAPI application factory for the Performance Analytics System."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analytics import router as analytics_router
from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.service import seed

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "application_starting",
        app_name=settings.app_name,
        env=settings.env,
        storage=settings.storage_backend,
    )
    seed()
    yield
    logger.info("application_stopping", app_name=settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Performance Analytics System — governed KPIs, attribution, and dashboards.",
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
    app.include_router(analytics_router, prefix=prefix)

    @app.get("/health", tags=["health"], summary="Liveness (root)")
    def _root_health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
