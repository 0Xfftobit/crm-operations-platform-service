"""Liveness + readiness probes (used by the Docker HEALTHCHECK and the ECS health check)."""
from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
def ready() -> dict:
    s = get_settings()
    return {
        "status": "ready",
        "app": s.app_name,
        "env": s.env,
        "storage": s.storage_gateway,
        "uses_sql": s.uses_sql,
    }
