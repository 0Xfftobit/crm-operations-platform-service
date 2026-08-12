"""Liveness + readiness probes with dependency checks."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path

from fastapi import APIRouter

from app.core.config import Settings, get_settings
from app.db import engine as db_engine
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


async def _postgres_check() -> dict[str, str]:
    pg = await asyncio.to_thread(db_engine.postgres_health)
    if pg["status"] == "ok":
        return {"status": "ok"}
    if pg["status"] == "disabled":
        return {"status": "not_configured"}
    return {"status": "degraded", "error": pg.get("error", "unreachable")}


async def _redis_status(url: str | None) -> dict[str, str]:
    if (
        not url
        and os.environ.get("CACHE_GATEWAY") != "redis"
        and os.environ.get("IDEMPOTENCY_BACKEND") != "redis"
    ):
        return {"status": "not_configured"}
    if not url:
        return {"status": "degraded", "error": "REDIS_URL not set"}

    try:
        if importlib.util.find_spec("redis"):
            from redis.asyncio import from_url

            client = from_url(url, socket_connect_timeout=2)
            await client.ping()
            await client.close()
            return {"status": "ok"}
        if importlib.util.find_spec("aioredis"):
            import aioredis

            client = aioredis.from_url(url, socket_connect_timeout=2)
            await client.ping()
            await client.close()
            return {"status": "ok"}
        return {"status": "degraded", "error": "redis driver not installed"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": str(exc)}


async def _kafka_status(settings: Settings) -> dict[str, str]:
    if (
        not settings.kafka_produce
        and not settings.kafka_consumer_enabled
        and os.environ.get("EVENT_SOURCE_GATEWAY") != "kafka"
    ):
        return {"status": "not_configured"}

    try:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "error": f"aiokafka not installed: {exc}"}

    ready = kafka_bus.is_ready()
    if settings.kafka_produce and not ready["producer_ready"]:
        return {"status": "degraded", "error": "kafka producer not connected", "transport": ready["transport"]}
    if settings.kafka_consumer_enabled and not ready["consumer_ready"]:
        return {"status": "degraded", "error": "kafka consumer not running", "transport": ready["transport"]}
    return {"status": "ok", "transport": ready["transport"]}


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
async def ready() -> dict:
    s = get_settings()
    checks = {
        "data": _storage_health(s.data_path),
        "models": _storage_health(s.model_path),
        "postgres": await _postgres_check(),
        "redis": await _redis_status(s.redis_url),
        "kafka": await _kafka_status(s),
    }
    overall = "degraded" if any(c["status"] == "degraded" for c in checks.values()) else "ready"
    return {
        "status": overall,
        "app": s.app_name,
        "env": s.env,
        "storage_backend": s.storage_backend,
        "storage_gateway": s.storage_gateway,
        "uses_sql": s.uses_sql,
        "checks": checks,
    }
