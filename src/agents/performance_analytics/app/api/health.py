"""Liveness + readiness probes and Prometheus /metrics endpoint."""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.metrics import generate_metrics
from app.messaging import kafka_bus

router = APIRouter(tags=["health"])


def _storage_health(path: Path) -> dict[str, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".ready_probe"
        test.write_text("")
        test.unlink()
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}


async def _check_postgres() -> dict[str, str]:
    from app.db.engine import postgres_health

    return await asyncio.to_thread(postgres_health)


async def _check_redis() -> dict[str, str]:
    redis_url = os.environ.get("REDIS_URL")
    cache_gateway = os.environ.get("CACHE_GATEWAY")
    idempotency_backend = os.environ.get("IDEMPOTENCY_BACKEND")
    if not redis_url and cache_gateway != "redis" and idempotency_backend != "redis":
        return {"status": "not_configured"}

    try:
        redis = importlib.import_module("redis")
    except Exception:  # noqa: BLE001
        return {"status": "degraded", "error": "redis client not installed"}

    url = redis_url or "redis://localhost:6379/0"

    def _sync_ping() -> bool:
        client = redis.from_url(url, socket_connect_timeout=2)
        return bool(client.ping())

    try:
        result = await asyncio.to_thread(_sync_ping)
        if result:
            return {"status": "ok"}
        return {"status": "degraded", "error": "redis ping returned false"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}


def _check_kafka() -> dict[str, str]:
    settings = get_settings()
    if (
        not settings.kafka_produce
        and not settings.kafka_consumer_enabled
        and os.environ.get("EVENT_SOURCE_GATEWAY") != "kafka"
    ):
        return {"status": "not_configured"}

    try:
        importlib.import_module("aiokafka")
    except Exception:  # noqa: BLE001
        return {"status": "degraded", "error": "aiokafka not installed"}

    if settings.kafka_produce and kafka_bus._producer is None:
        return {"status": "degraded", "error": "kafka producer not initialized"}
    if settings.kafka_consumer_enabled and kafka_bus._consumer_task is None:
        return {"status": "degraded", "error": "kafka consumer not initialized"}

    return {"status": "ok"}


@router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
async def ready() -> dict:
    s = get_settings()
    checks = {
        "storage": _storage_health(s.data_path),
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "kafka": _check_kafka(),
    }
    overall = "ready" if all(c["status"] != "degraded" for c in checks.values()) else "degraded"
    return {
        "status": overall,
        "app": s.app_name,
        "env": s.env,
        "storage_backend": s.storage_backend,
        "outcome_fanout_enabled": s.outcome_fanout_enabled,
        "checks": checks,
    }


@router.get("/metrics", summary="Prometheus metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        generate_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
