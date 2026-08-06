"""Liveness + readiness probes for the Performance Analytics System."""

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
        "storage_backend": s.storage_backend,
    }
