"""Liveness + readiness probes."""

from pathlib import Path

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


def _storage_health(path: Path) -> dict[str, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".ready_probe"
        test.write_text("")
        test.unlink()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


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
        "data": _storage_health(s.data_path),
        "models": _storage_health(s.model_path),
    }
