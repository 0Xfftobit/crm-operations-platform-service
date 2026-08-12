"""Liveness + readiness probes (used by the Docker HEALTHCHECK and the ECS health check)."""
from __future__ import annotations

import importlib

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.metrics import generate_metrics
from app.db.engine import postgres_health
from app.frequency import redis_health
from app.messaging import kafka_bus

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


def _storage_health() -> dict:
    s = get_settings()
    if s.storage_gateway == "memory":
        return {"status": "ok"}
    if s.storage_gateway == "postgres":
        if not s.uses_sql:
            return {
                "status": "degraded",
                "error": "STORAGE_GATEWAY=postgres but DATABASE_URL is empty",
            }
        return _postgres_health()
    return {"status": "ok"}


def _postgres_health() -> dict:
    return postgres_health()


def _kafka_health() -> dict:
    s = get_settings()
    if not (s.kafka_produce or s.kafka_consumer_enabled or s.event_source_gateway == "kafka"):
        return {"status": "not_configured"}
    try:
        importlib.import_module("aiokafka")
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": f"aiokafka unavailable: {exc}"}

    if kafka_bus.is_healthy():
        return {"status": "ok"}
    return {"status": "degraded", "error": "Kafka bus is not initialized"}


@router.get("/ready", summary="Readiness")
def ready() -> dict:
    s = get_settings()
    checks = {
        "storage": _storage_health(),
        "postgres": _postgres_health(),
        "redis": redis_health(),
        "kafka": _kafka_health(),
    }
    overall = "degraded" if any(c["status"] == "degraded" for c in checks.values()) else "ready"
    return {
        "status": overall,
        "app": s.app_name,
        "env": s.env,
        "storage": s.storage_gateway,
        "uses_sql": s.uses_sql,
        "redis": checks["redis"],
        "precheck_enabled": s.precheck_enabled,
        "checks": checks,
    }


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=PlainTextResponse,
)
def metrics_endpoint() -> PlainTextResponse:
    return PlainTextResponse(
        generate_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
