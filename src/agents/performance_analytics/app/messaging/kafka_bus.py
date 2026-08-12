"""Async Kafka bus with an in-process fallback for the Performance Analytics System.

Public contract (per ``.devin/kafka_contract.md``):

- ``start_producer`` / ``stop_producer``
- ``produce`` / ``produce_sync``
- ``consume_loop`` / ``start_consumer`` / ``stop_consumer``
- ``reset_kafka_bus`` / ``bus_stats``
- ``canonical_topic`` / ``partition_key``

Real Kafka is used only when ``KAFKA_PRODUCE=true`` or ``KAFKA_CONSUMER_ENABLED=true`` and a
broker is reachable. Otherwise the bus degrades to an in-memory log so tests and local runs stay
fast and broker-free.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog

from app.core import metrics as _metrics
from app.core.config import get_settings
from app.domain.models import OutcomeEvent, TouchEvent

logger = structlog.get_logger(__name__)

_lock = RLock()

_in_memory_log: list[dict[str, Any]] = []
_consumer_index: int = 0

_producer: Any | None = None
_loop: asyncio.AbstractEventLoop | None = None

_consumer_task: asyncio.Task[Any] | None = None
_consumer_stop: asyncio.Event | None = None
_consuming: contextvars.ContextVar[bool] = contextvars.ContextVar("kafka_consuming", default=False)

TOPIC_MAP: dict[str, str] = {
    "outreach.sent": "crm.outreach.sent",
    "outreach.delivered": "crm.outreach.delivered",
    "outreach.converted": "crm.outreach.converted",
    "outreach.bounced": "crm.outreach.bounced",
    "outreach.failed": "crm.outreach.failed",
    "outcome.funded": "crm.outcome.funded",
    "outcome.lost": "crm.outcome.lost",
    "outcome.progressed": "crm.outcome.progressed",
    "kpi.snapshot": "crm.kpi.snapshot",
}
_REVERSE_TOPIC_MAP = {v: k for k, v in TOPIC_MAP.items()}


def reset_kafka_bus() -> None:
    """Clear the in-memory bus and consumer pointer (used by tests and teardown)."""
    global _consumer_index, _producer, _loop, _consumer_task, _consumer_stop
    with _lock:
        _in_memory_log.clear()
        _consumer_index = 0
    _producer = None
    _loop = None
    _consumer_task = None
    _consumer_stop = None


def canonical_topic(topic: str) -> str:
    """Convert a short or canonical topic to its canonical form."""
    if topic in TOPIC_MAP:
        return TOPIC_MAP[topic]
    if topic.startswith(("crm.", "compliance.")):
        return topic
    return f"crm.{topic}"


def short_topic(canonical: str) -> str:
    """Convert a canonical topic back to its short form."""
    return _REVERSE_TOPIC_MAP.get(canonical, canonical.split(".", 1)[1] if "." in canonical else canonical)


def partition_key(payload: dict[str, Any]) -> str:
    """Return a per-deal/per-entity ordering key for a payload."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("contact_id")
        or payload.get("contact_pid")
        or payload.get("entity_id")
        or payload.get("id")
        or "unknown"
    )


def bus_stats() -> dict[str, Any]:
    """Return in-memory bus statistics."""
    with _lock:
        topics: dict[str, int] = {}
        for m in _in_memory_log:
            topics[m["topic"]] = topics.get(m["topic"], 0) + 1
        lag = max(0, len(_in_memory_log) - _consumer_index)
        transport = "kafka" if _producer is not None else "in_process_bus"
        return {
            "messages_total": len(_in_memory_log),
            "consumer_lag": lag,
            "topics": topics,
            "transport": transport,
        }


def _append_to_memory(canonical: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "topic": canonical,
        "key": key,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": "in_process_bus",
    }
    with _lock:
        _in_memory_log.append(row)
    _metrics.kafka_produced_total.inc({"topic": canonical})
    return row


async def _publish_real(canonical: str, payload: dict[str, Any], key: str) -> bool:
    producer = None
    with _lock:
        producer = _producer
    if producer is None:
        return False
    try:
        value = json.dumps(payload, default=str).encode("utf-8")
        k = key.encode("utf-8")
        await producer.send_and_wait(canonical, value=value, key=k)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_send_failed", topic=canonical, error=str(exc))
        return False


async def start_producer() -> bool:
    """Start the async Kafka producer; return ``False`` when unavailable/disabled."""
    global _producer, _loop
    settings = get_settings()
    if not settings.kafka_produce:
        logger.info("kafka_producer_disabled")
        return False
    with _lock:
        if _producer is not None:
            return True
    try:
        from aiokafka import AIOKafkaProducer
    except Exception as exc:  # noqa: BLE001
        logger.warning("aiokafka_not_available", error=str(exc))
        return False
    try:
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await producer.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_producer_start_failed", error=str(exc))
        return False
    loop = asyncio.get_running_loop()
    with _lock:
        _producer = producer
        _loop = loop
    logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)
    return True


async def stop_producer() -> None:
    """Stop the async Kafka producer."""
    global _producer, _loop
    producer = None
    with _lock:
        producer = _producer
        _producer = None
        _loop = None
    if producer is not None:
        try:
            await producer.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_producer_stop_failed", error=str(exc))


async def produce(topic: str, payload: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    """Publish one message. Always appends to the in-memory bus; sends to Kafka when enabled."""
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    row = _append_to_memory(canonical, payload, pk)
    if not settings.kafka_produce:
        return row
    if _producer is None:
        await start_producer()
    with _lock:
        producer = _producer
    if producer is not None:
        if await _publish_real(canonical, payload, pk):
            row["transport"] = "kafka"
    return row


def produce_sync(topic: str, payload: dict[str, Any], *, key: str | None = None) -> dict[str, Any]:
    """Sync-friendly ``produce`` that schedules the real send on the captured event loop."""
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    row = _append_to_memory(canonical, payload, pk)
    main_loop = None
    with _lock:
        main_loop = _loop
    if _producer is None or main_loop is None or not main_loop.is_running():
        return row
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_real(canonical, payload, pk))
    except RuntimeError:
        if main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_publish_real(canonical, payload, pk), main_loop)
    return row


def produce_touch(touch: TouchEvent) -> dict[str, Any] | None:
    """Emit a ``TouchEvent`` to the bus when ``KAFKA_PRODUCE=true``."""
    settings = get_settings()
    if not settings.kafka_produce or _consuming.get():
        return None
    payload: dict[str, Any] = touch.model_dump(mode="json")
    payload["contact_id"] = payload.get("contact_pid") or payload.get("contact_id")
    if touch.engaged or touch.clicked or touch.replied:
        short = "outreach.converted"
    else:
        short = "outreach.sent"
    topic = canonical_topic(short)
    key = partition_key(payload)
    return produce_sync(topic, payload, key=key)


def produce_outcome(outcome: OutcomeEvent) -> dict[str, Any] | None:
    """Emit an ``OutcomeEvent`` to the bus when ``KAFKA_PRODUCE=true``."""
    settings = get_settings()
    if not settings.kafka_produce or _consuming.get():
        return None
    payload: dict[str, Any] = outcome.model_dump(mode="json")
    payload["contact_id"] = payload.get("contact_pid") or payload.get("contact_id")
    stage = payload.get("stage")
    short = f"outcome.{stage}" if stage else "outcome.progressed"
    topic = canonical_topic(short)
    key = partition_key(payload)
    return produce_sync(topic, payload, key=key)


def _topic_matches(topic: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == topic:
            return True
        if pattern.endswith(".*") and topic.startswith(pattern[:-1]):
            return True
        if pattern.endswith(".") and topic.startswith(pattern):
            return True
    return False


def _dispatch(raw: bytes | str | dict[str, Any], topic: str | None = None) -> None:
    """Route one message to ``service.ingest_touch`` or ``service.ingest_outcome``.

    Poison messages and unknown topics are logged as dead-letters and do not crash the loop.
    Messages processed by the consumer do not get re-emitted to avoid an infinite echo loop.
    """
    from app import service

    token = _consuming.set(True)
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            payload = json.loads(raw)
        else:
            payload = raw
        if not isinstance(payload, dict):
            raise ValueError("payload is not a dict")

        canonical = canonical_topic(topic or payload.get("topic", ""))

        if canonical.startswith("crm.outreach."):
            service.ingest_touch(TouchEvent(**payload))
        elif canonical.startswith("crm.outcome."):
            service.ingest_outcome(OutcomeEvent(**payload))
        else:
            logger.warning("kafka_unrouted_message", topic=canonical)
        _metrics.kafka_consumed_total.inc({"topic": canonical})
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_dead_letter", topic=topic, error=str(exc))
    finally:
        _consuming.reset(token)


async def consume_loop(stop: asyncio.Event) -> None:
    """Consume Kafka or in-memory messages until ``stop`` is set."""
    global _consumer_index
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        logger.info("kafka_consumer_disabled")
        return

    topics = list(settings.kafka_topics or [])
    if not topics:
        logger.warning("kafka_consumer_no_topics")
        return

    consumer = None
    try:
        from aiokafka import AIOKafkaConsumer
    except Exception as exc:  # noqa: BLE001
        logger.warning("aiokafka_not_available", error=str(exc))
        AIOKafkaConsumer = None  # type: ignore[misc]

    if AIOKafkaConsumer is not None:
        try:
            consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_consumer_group,
                enable_auto_commit=True,
                auto_offset_reset="latest",
            )
            await consumer.start()
            logger.info("kafka_consumer_started", topics=topics)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_consumer_start_failed", error=str(exc))
            consumer = None

    if consumer is None:
        logger.info("kafka_consumer_in_memory_fallback", topics=topics)
        try:
            while not stop.is_set():
                idx = 0
                with _lock:
                    log = list(_in_memory_log)
                    idx = _consumer_index
                while idx < len(log) and not stop.is_set():
                    row = log[idx]
                    if _topic_matches(row["topic"], topics):
                        _dispatch(row["payload"], topic=row["topic"])
                    idx += 1
                with _lock:
                    _consumer_index = idx
                await asyncio.sleep(0.05)
        finally:
            logger.info("kafka_consumer_stopped")
        return

    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=500, max_records=100)
            for _tp, messages in batch.items():
                for msg in messages:
                    _dispatch(msg.value, topic=msg.topic)
    finally:
        await consumer.stop()
        logger.info("kafka_consumer_stopped")


async def start_consumer() -> dict[str, Any]:
    """Launch ``consume_loop`` in a background task."""
    global _consumer_task, _consumer_stop
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        return {"started": False, "reason": "disabled"}
    stop = asyncio.Event()
    _consumer_stop = stop
    _consumer_task = asyncio.create_task(consume_loop(stop))
    return {
        "started": True,
        "topics": list(settings.kafka_topics),
        "group": settings.kafka_consumer_group,
    }


async def stop_consumer() -> None:
    """Stop the consumer loop and cancel its task."""
    global _consumer_task, _consumer_stop
    if _consumer_stop is not None:
        _consumer_stop.set()
    if _consumer_task is not None and not _consumer_task.done():
        _consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _consumer_task
    _consumer_task = None
    _consumer_stop = None
