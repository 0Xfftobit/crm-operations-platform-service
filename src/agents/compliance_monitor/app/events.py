"""Event contract — consumed activity events + produced compliance signals (FR-013).

Produced ``compliance.alert / case.opened / signal`` events are published back to the bus (captured
in an in-process audit-visible log here) for Engine-8 correlation + analytics.
"""
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)

EVENT_ALERT = "compliance.alert"
EVENT_CASE_OPENED = "compliance.case.opened"
EVENT_CASE_UPDATED = "compliance.case.updated"
EVENT_SIGNAL = "compliance.signal"          # to Engine 8

_events: list[dict] = []


def publish(event_type: str, payload: dict) -> None:
    _events.append({"type": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()})
    logger.info("event", event_type=event_type)
    # Mirror onto the Kafka bus when the producer is running (no-op otherwise).
    from app import kafka_bus
    kafka_bus.produce_sync(event_type, payload)


def get_events(*, event_type: str | None = None, limit: int = 200) -> list[dict]:
    items = [e for e in _events if event_type is None or e["type"] == event_type]
    return list(reversed(items[-limit:]))


def reset_events() -> None:
    _events.clear()
