"""Platform event bus — local + Kafka fan-out."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from app.messaging.kafka_bus import publish_kafka

_events: list[dict[str, Any]] = []

_lock = RLock()


def publish(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        "event_id": f"evt_{uuid4().hex[:12]}",
        "topic": topic,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
    }
    with _lock:
        _events.append(row)
    publish_kafka(topic, payload)
    return row


def consume(*, limit: int = 50, topic: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_events)
    if topic:
        rows = [e for e in rows if e.get("topic") == topic]
    return list(reversed(rows[-limit:]))


def reset_events() -> None:
    with _lock:
        _events.clear()
