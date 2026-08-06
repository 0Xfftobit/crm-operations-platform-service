"""Kafka event bus — topic constants and in-process transport (§6)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

# Consumed topics (architecture §6)
TOPIC_STAGE_CHANGED = "crm.deal.stage_changed"
TOPIC_LEAD_SCORED = "crm.lead.scored"
TOPIC_NBA_RECOMMENDED = "crm.nba.recommended"
TOPIC_TASK_COMPLETED = "crm.task.completed"
TOPIC_DOCUMENT_RECEIVED = "crm.document.received"

# Produced topics
TOPIC_TASK_CREATED = "crm.task.created"
TOPIC_RECORD_UPDATED = "crm.record.updated"
TOPIC_DOCUMENT_REQUESTED = "crm.document.requested"
TOPIC_AUTOMATION_COMPLETED = "crm.automation.run.completed"
TOPIC_AUTOMATION_FAILED = "crm.automation.failed"

TOPIC_MAP: dict[str, str] = {
    "deal.stage_changed": TOPIC_STAGE_CHANGED,
    "lead.scored": TOPIC_LEAD_SCORED,
    "nba.recommended": TOPIC_NBA_RECOMMENDED,
    "task.completed": TOPIC_TASK_COMPLETED,
    "document.received": TOPIC_DOCUMENT_RECEIVED,
    "task.created": TOPIC_TASK_CREATED,
    "record.updated": TOPIC_RECORD_UPDATED,
    "document.requested": TOPIC_DOCUMENT_REQUESTED,
    "automation.run.completed": TOPIC_AUTOMATION_COMPLETED,
    "automation.failed": TOPIC_AUTOMATION_FAILED,
    "automation.loop_detected": "crm.automation.loop_detected",
    "automation.llm.feedback": "crm.automation.llm.feedback",
    "task.updated": "crm.task.updated",
    "workflow.started": "crm.workflow.started",
    "note.posted": "crm.note.posted",
}

_kafka_log: list[dict[str, Any]] = []
_consumer_lag: int = 0


def reset_kafka_bus() -> None:
    global _consumer_lag
    _kafka_log.clear()
    _consumer_lag = 0


def canonical_topic(topic: str) -> str:
    return TOPIC_MAP.get(topic, f"crm.{topic}")


def partition_key(payload: dict[str, Any]) -> str:
    """Per-deal ordering via deal_id partition key (§9)."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("opportunity_id")
        or payload.get("id")
        or "unknown"
    )


def publish_kafka(topic: str, payload: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    row = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "topic": canonical,
        "source_topic": topic,
        "key": pk,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": "in_process_bus",
    }
    _kafka_log.append(row)
    return row


def consume_kafka(*, topic: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    items = _kafka_log
    if topic:
        items = [m for m in _kafka_log if m["topic"] == topic or m.get("source_topic") == topic]
    return list(reversed(items[-limit:]))


def consumer_lag() -> int:
    return _consumer_lag


def set_consumer_lag(lag: int) -> None:
    global _consumer_lag
    _consumer_lag = max(0, lag)


def bus_stats() -> dict[str, Any]:
    topics: dict[str, int] = {}
    for m in _kafka_log:
        topics[m["topic"]] = topics.get(m["topic"], 0) + 1
    return {
        "messages_total": len(_kafka_log),
        "consumer_lag": _consumer_lag,
        "topics": topics,
        "transport": "in_process_bus",
    }
