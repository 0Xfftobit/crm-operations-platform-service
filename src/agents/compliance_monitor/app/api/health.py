"""Liveness + readiness probes (used by the Docker HEALTHCHECK and the ECS health check)."""
from fastapi import APIRouter
from sqlalchemy import text

from app import kafka_bus
from app.cache import redis_health
from app.core.config import get_settings
from app.db.engine import get_engine, postgres_health

router = APIRouter(tags=["health"])


def _storage_check() -> dict[str, str]:
    settings = get_settings()
    if settings.storage_gateway == "memory":
        return {"status": "ok"}
    if settings.storage_gateway == "postgres":
        if not settings.database_url:
            return {
                "status": "not_configured",
                "error": "STORAGE_GATEWAY=postgres but DATABASE_URL not set",
            }
        pg = postgres_health()
        if pg["status"] == "ok":
            return {"status": "ok"}
        return {"status": "degraded", "error": pg.get("error", "unreachable")}
    return {"status": "not_configured", "error": f"unknown storage: {settings.storage_gateway}"}


def _postgres_check() -> dict[str, str]:
    settings = get_settings()
    if not settings.database_url:
        return {"status": "not_configured"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}


def _redis_check() -> dict[str, str]:
    settings = get_settings()
    if not settings.uses_cache:
        return {"status": "not_configured"}
    health = redis_health()
    if health["status"] == "ok":
        return {"status": "ok"}
    return {"status": "degraded", "error": health.get("error", "unavailable")}


def _kafka_check() -> dict[str, str]:
    settings = get_settings()
    configured = (
        settings.kafka_produce
        or settings.kafka_consumer_enabled
        or settings.kafka_ingest
        or settings.event_source_gateway == "kafka"
    )
    if not configured:
        return {"status": "not_configured"}
    try:
        import aiokafka  # noqa: F401
    except ImportError:
        return {"status": "degraded", "error": "aiokafka not installed"}
    stats = kafka_bus.bus_stats()
    if stats.get("producer_running") or stats.get("consumer_running"):
        return {"status": "ok"}
    return {"status": "degraded", "error": "Kafka bus not connected"}


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
def ready() -> dict:
    s = get_settings()
    checks = {
        "storage": _storage_check(),
        "postgres": _postgres_check(),
        "redis": _redis_check(),
        "kafka": _kafka_check(),
    }
    overall = "ready"
    for check in checks.values():
        if check["status"] == "degraded":
            overall = "degraded"
            break

    return {
        "status": overall,
        "app": s.app_name,
        "env": s.env,
        "storage": s.storage_gateway,
        "uses_sql": s.uses_sql,
        "postgres": checks["postgres"],
        "redis": checks["redis"],
        "checks": checks,
    }
