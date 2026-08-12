"""Liveness + readiness probes and Prometheus metrics endpoint."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import Settings, get_settings
from app.core.metrics import generate_metrics
from app.kafka_consumer_group import consumer_stats
from app.messaging import kafka_bus
from app.persistence import redis_store

router = APIRouter(tags=["health"])


def _check_storage(settings: Settings) -> dict[str, Any]:
    backend = settings.storage_backend
    if backend == "memory":
        return {"status": "ok", "backend": "memory"}
    if backend == "sqlite":
        path = Path(settings.database_path)
        if path.parent.exists() and os.access(path.parent, os.W_OK):
            return {"status": "ok", "backend": "sqlite", "path": str(path)}
        return {"status": "degraded", "error": f"sqlite path not writable: {path.parent}"}
    if backend == "postgres":
        if not settings.database_url:
            return {"status": "degraded", "error": "STORAGE_BACKEND=postgres but DATABASE_URL not set"}
        try:
            import psycopg

            with psycopg.connect(settings.database_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return {"status": "ok", "backend": "postgres", "url": settings.database_url.split("@")[-1]}
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}
    return {"status": "not_configured", "backend": backend}


def _check_postgres(settings: Settings) -> dict[str, Any]:
    url = settings.database_url
    if not url:
        return {"status": "not_configured"}
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"status": "ok", "url": url.split("@")[-1]}
    except Exception as exc:
        return {"status": "degraded", "error": str(exc)}


def _check_redis(settings: Settings) -> dict[str, Any]:
    if settings.idempotency_backend != "redis":
        return {"status": "not_configured"}
    info = redis_store.backend_info()
    backend = info.get("backend")
    if backend == "redis":
        return {"status": "ok", "info": info}
    if backend == "memory_fallback":
        return {"status": "degraded", "error": "redis configured but unreachable; using memory fallback"}
    return {"status": "degraded", "error": "redis not reachable"}


def _check_kafka(settings: Settings) -> dict[str, Any]:
    if not settings.kafka_produce and not settings.kafka_consumer_enabled:
        return {"status": "not_configured"}
    problems: list[str] = []
    if settings.kafka_produce and kafka_bus._real_producer is None:
        problems.append("producer not connected")
    if settings.kafka_consumer_enabled and not consumer_stats()["running"]:
        problems.append("consumer not running")
    if problems:
        return {"status": "degraded", "error": "; ".join(problems)}
    return {
        "status": "ok",
        "producer": settings.kafka_produce,
        "consumer": settings.kafka_consumer_enabled,
    }


@router.get("/health", summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness")
def ready() -> dict:
    s = get_settings()
    checks = {
        "storage": _check_storage(s),
        "postgres": _check_postgres(s),
        "redis": _check_redis(s),
        "kafka": _check_kafka(s),
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
        "engine_version": s.task_automation_engine_version,
        "storage": s.storage_backend,
        "scheduler_enabled": s.scheduler_enabled,
        "checks": checks,
    }


@router.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        generate_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
