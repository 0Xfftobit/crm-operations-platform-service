"""Kafka trigger ingestion stub — platform event bus adapter."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.service import process_event

_consumed: list[dict[str, Any]] = []

_lock = RLock()


def reset_kafka() -> None:
    with _lock:
        _consumed.clear()


def consume_kafka_message(
    *,
    topic: str,
    payload: dict[str, Any],
    offset: int | None = None,
    partition: int | None = None,
) -> dict[str, Any]:
    """Process a Kafka message (HTTP stub for local/dev)."""
    settings = get_settings()
    trigger_id = payload.get("trigger_id") or f"kafka_{uuid4().hex[:12]}"
    payload = {**payload, "trigger_id": trigger_id}
    record = {
        "source": "kafka",
        "topic": topic,
        "payload": payload,
        "offset": offset,
        "partition": partition,
        "consumed_at": datetime.now(UTC).isoformat(),
        "bootstrap_servers": settings.kafka_bootstrap_servers,
    }
    try:
        result = process_event(
            topic=topic,
            payload=payload,
            idempotency_key=f"kafka:{topic}:{offset}:{partition}:{trigger_id}",
            trigger_id=trigger_id,
        )
        record["status"] = "processed"
        record["result"] = {"actions": len(result.get("created") or []), "latency_ms": result.get("latency_ms")}
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        raise
    finally:
        with _lock:
            _consumed.append(record)
    return record


def list_consumed(*, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        return list(reversed(_consumed[-limit:]))
