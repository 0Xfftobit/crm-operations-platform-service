"""Kafka event bus — topic constants, in-process transport, and optional real broker (§6, §9)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from app.core import metrics as core_metrics
from app.core.config import get_settings

# Consumed topics (architecture §6)
TOPIC_STAGE_CHANGED = "crm.deal.stage_changed"
TOPIC_LEAD_ASSIGNED = "crm.lead.assigned"
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
    "lead.assigned": TOPIC_LEAD_ASSIGNED,
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

_CANONICAL_TO_SHORT: dict[str, str] = {v: k for k, v in TOPIC_MAP.items()}

_kafka_log: list[dict[str, Any]] = []
_consumer_lag: int = 0
_real_producer: Any | None = None

_lock = RLock()
logger = logging.getLogger(__name__)


def reset_kafka_bus() -> None:
    """Clear the in-memory log and release any real producer (tests / shutdown)."""
    global _consumer_lag, _real_producer
    with _lock:
        _kafka_log.clear()
        _consumer_lag = 0
        if _real_producer is not None:
            try:
                _real_producer.close()
            except Exception:
                pass
            _real_producer = None


def canonical_topic(topic: str) -> str:
    """Return the canonical `crm.<short>` (or `compliance.<short>`) topic name."""
    if topic in TOPIC_MAP:
        return TOPIC_MAP[topic]
    if topic.startswith("crm.") or topic.startswith("compliance."):
        return topic
    return f"crm.{topic}"


def short_topic(canonical: str) -> str:
    """Reverse a canonical dotted topic name back to the short form."""
    if canonical in _CANONICAL_TO_SHORT:
        return _CANONICAL_TO_SHORT[canonical]
    if canonical.startswith("crm."):
        return canonical[4:]
    if canonical.startswith("compliance."):
        return canonical[11:]
    return canonical


def partition_key(payload: dict[str, Any]) -> str:
    """Per-deal ordering via ordered ID partition key (§9)."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("contact_id")
        or payload.get("entity_id")
        or payload.get("id")
        or "unknown"
    )


def _append_log(
    topic: str,
    payload: dict[str, Any],
    *,
    key: str,
    transport: str = "in_process_bus",
) -> dict[str, Any]:
    row = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "topic": topic,
        "source_topic": short_topic(topic),
        "key": key,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": transport,
    }
    with _lock:
        _kafka_log.append(row)
    return row


def _create_kafka_producer(bootstrap_servers: list[str]) -> Any:
    """Lazy factory for the threaded KafkaProducer."""
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
        retries=1,
        request_timeout_ms=5000,
    )


def _send_sync(producer: Any, topic: str, key: str, payload: dict[str, Any]) -> Any:
    """Send and await an ack synchronously; raises on send/timeout errors."""
    future = producer.send(topic, key=key.encode("utf-8"), value=payload)
    return future.get(timeout=5)


async def start_producer() -> bool:
    """Start the real Kafka producer when ``KAFKA_PRODUCE=true``.

    Returns ``True`` when a real producer is available, ``False`` otherwise.
    """
    global _real_producer
    settings = get_settings()
    if not settings.kafka_produce:
        logger.debug("kafka_producer_disabled")
        return False
    if _real_producer is not None:
        return True
    try:
        _real_producer = await asyncio.to_thread(
            _create_kafka_producer, settings.kafka_bootstrap_servers.split(",")
        )
    except Exception as exc:
        logger.warning("kafka_producer_start_failed", extra={"error": str(exc)})
        _real_producer = None
        return False
    logger.info("kafka_producer_started", extra={"bootstrap": settings.kafka_bootstrap_servers})
    return True


async def stop_producer() -> None:
    """Stop the real Kafka producer if running."""
    global _real_producer
    if _real_producer is None:
        return
    try:
        await asyncio.to_thread(_real_producer.close)
    except Exception as exc:
        logger.warning("kafka_producer_stop_failed", extra={"error": str(exc)})
    finally:
        _real_producer = None


async def produce(
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Publish a message to the canonical topic; falls back to the in-memory bus."""
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    if settings.kafka_produce and _real_producer is not None:
        try:
            await asyncio.to_thread(_send_sync, _real_producer, canonical, pk, payload)
            log = _append_log(canonical, payload, key=pk, transport="kafka")
            core_metrics.inc_kafka_produced(topic=canonical, transport="kafka")
            return log
        except Exception as exc:
            logger.warning("kafka_produce_failed", extra={"topic": canonical, "error": str(exc)})
    log = _append_log(canonical, payload, key=pk, transport="in_process_bus")
    core_metrics.inc_kafka_produced(topic=canonical, transport="in_process_bus")
    return log


def produce_sync(
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Synchronous variant of :func:`produce` for non-async call sites."""
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    if settings.kafka_produce and _real_producer is not None:
        try:
            _send_sync(_real_producer, canonical, pk, payload)
            log = _append_log(canonical, payload, key=pk, transport="kafka")
            core_metrics.inc_kafka_produced(topic=canonical, transport="kafka")
            return log
        except Exception as exc:
            logger.warning("kafka_produce_sync_failed", extra={"topic": canonical, "error": str(exc)})
    log = _append_log(canonical, payload, key=pk, transport="in_process_bus")
    core_metrics.inc_kafka_produced(topic=canonical, transport="in_process_bus")
    return log


def publish_kafka(
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Legacy sync publish — keeps the original name as an alias to :func:`produce_sync`."""
    return produce_sync(topic, payload, key=key)


def consume_kafka(*, topic: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        items = list(_kafka_log)
    if topic:
        items = [m for m in items if m["topic"] == topic or m.get("source_topic") == topic]
    return list(reversed(items[-limit:]))


def consumer_lag() -> int:
    with _lock:
        return _consumer_lag


def set_consumer_lag(lag: int) -> None:
    global _consumer_lag
    with _lock:
        _consumer_lag = max(0, lag)


def bus_stats() -> dict[str, Any]:
    with _lock:
        topics: dict[str, int] = {}
        for m in _kafka_log:
            topics[m["topic"]] = topics.get(m["topic"], 0) + 1
        transport = (
            "kafka"
            if (_real_producer is not None and get_settings().kafka_produce)
            else "in_process_bus"
        )
        return {
            "messages_total": len(_kafka_log),
            "consumer_lag": _consumer_lag,
            "topics": topics,
            "transport": transport,
        }
