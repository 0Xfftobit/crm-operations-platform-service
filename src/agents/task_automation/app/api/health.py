"""Liveness + readiness probes."""
from fastapi import APIRouter

from app.core.config import get_settings
from app.persistence import db, redis_store

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
        "engine_version": s.task_automation_engine_version,
        "storage": s.storage_backend,
        "scheduler_enabled": s.scheduler_enabled,
        "postgres": db.postgres_health(),
        "redis": redis_store.backend_info(),
    }
