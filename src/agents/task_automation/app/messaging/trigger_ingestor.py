"""Trigger ingestor — canonical envelope + durable event-id dedupe (§1)."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from app.messaging.kafka_bus import canonical_topic, partition_key
from app.persistence import redis_store

_seen_event_ids: set[str] = set()
_ingestor_lock = RLock()


def reset_trigger_ingestor() -> None:
    with _ingestor_lock:
        _seen_event_ids.clear()


def is_duplicate_event(event_id: str | None) -> bool:
    if not event_id:
        return False
    eid = str(event_id)
    with _ingestor_lock:
        if eid in _seen_event_ids:
            return True
    newly_seen = redis_store.mark_event_seen(eid)
    with _ingestor_lock:
        _seen_event_ids.add(eid)
        return not newly_seen


def mark_event_seen(event_id: str) -> None:
    with _ingestor_lock:
        _seen_event_ids.add(event_id)
    redis_store.mark_event_seen(event_id)


def normalize_trigger(
    *,
    topic: str,
    payload: dict[str, Any],
    source: str = "api",
    offset: int | None = None,
    partition: int | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Canonical trigger envelope: {trigger_id, type, deal_id, payload, occurred_at, source}."""
    event_id = payload.get("event_id")
    if event_id and is_duplicate_event(str(event_id)):
        return {"duplicate": True, "event_id": event_id}

    trigger_id = payload.get("trigger_id") or f"trg_{uuid4().hex[:14]}"
    assert trigger_id is not None
    trigger_id = str(trigger_id)
    deal_id = partition_key(payload)
    ts = occurred_at or payload.get("occurred_at") or datetime.now(UTC).isoformat()

    envelope = {
        "envelope_version": "2.0",
        "trigger_id": trigger_id,
        "type": topic,
        "deal_id": deal_id,
        "payload": {**payload, "trigger_id": trigger_id, "deal_id": deal_id},
        "occurred_at": ts,
        "source": source,
        "topic": topic,
        "canonical_topic": canonical_topic(topic),
        "lead_id": payload.get("lead_id") or deal_id,
        "event_id": event_id,
        "kafka": {"offset": offset, "partition": partition},
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    return envelope
