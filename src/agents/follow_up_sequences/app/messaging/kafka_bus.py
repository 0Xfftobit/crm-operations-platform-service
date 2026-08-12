"""Kafka cross-agent bus for FUS.

Lazily uses ``aiokafka`` for real production/consumption when ``KAFKA_PRODUCE`` /
``KAFKA_CONSUMER_ENABLED`` are true. In ``local``/``test``, or when the broker is
down, the bus transparently falls back to an in-process log so the agent keeps
working and tests stay broker-free.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog

from app.core import metrics
from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_lock = RLock()
_producer: Any | None = None
_loop: asyncio.AbstractEventLoop | None = None

_consumer_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None

_kafka_log: list[dict[str, Any]] = []
_consumed_index = 0


def canonical_topic(short: str) -> str:
    """Return the canonical cross-agent topic for a short event type."""
    if short.startswith("crm.") or short.startswith("compliance."):
        return short
    return f"crm.{short}"


def short_topic(canonical: str) -> str:
    """Reverse of :func:`canonical_topic`."""
    for prefix in ("crm.", "compliance."):
        if canonical.startswith(prefix):
            return canonical[len(prefix) :]
    return canonical


def partition_key(payload: dict[str, Any]) -> str:
    """Per-deal / per-lead ordering key."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("contact_id")
        or payload.get("entity_id")
        or payload.get("id")
        or "unknown"
    )


def reset_kafka_bus() -> None:
    """Clear in-memory bus state and producer/consumer references (test helper)."""
    global _producer, _loop, _consumer_task, _stop_event, _consumed_index
    with _lock:
        _kafka_log.clear()
        _consumed_index = 0
        _producer = None
        _loop = None
        _consumer_task = None
        _stop_event = None


def bus_stats() -> dict[str, Any]:
    """Return in-memory bus telemetry."""
    with _lock:
        topics: dict[str, int] = {}
        for row in _kafka_log:
            topics[row["topic"]] = topics.get(row["topic"], 0) + 1
        transport = "in_process_bus" if _producer is None else "kafka"
        return {
            "messages_total": len(_kafka_log),
            "in_memory_lag": max(0, len(_kafka_log) - _consumed_index),
            "topics": topics,
            "transport": transport,
        }


def is_healthy() -> bool:
    """Return True when the configured Kafka transport is ready."""
    settings = get_settings()
    with _lock:
        if settings.kafka_produce and _producer is not None:
            return True
        if (
            settings.kafka_consumer_enabled
            and _consumer_task is not None
            and not _consumer_task.done()
        ):
            return True
    return False


def _append_log(
    topic: str, payload: dict[str, Any], key: str, transport: str
) -> dict[str, Any]:
    row = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "topic": topic,
        "key": key,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": transport,
    }
    with _lock:
        _kafka_log.append(row)
    return row


def _update_transport(message_id: str, transport: str) -> None:
    with _lock:
        for row in _kafka_log:
            if row["message_id"] == message_id:
                row["transport"] = transport
                break


async def start_producer() -> bool:
    """Start the shared aiokafka producer; return False if disabled or unavailable."""
    global _producer, _loop
    settings = get_settings()
    if not settings.kafka_produce:
        return False
    with _lock:
        if _producer is not None:
            return True
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await producer.start()
        loop = asyncio.get_running_loop()
        with _lock:
            _producer = producer
            _loop = loop
        logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_producer_unavailable", error=str(exc))
        with _lock:
            _producer = None
        return False


async def stop_producer() -> None:
    """Stop the shared producer and release the captured event loop."""
    global _producer, _loop
    with _lock:
        producer = _producer
        _producer = None
        _loop = None
    if producer is not None:
        try:
            await producer.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_producer_stop_failed", error=str(exc))


async def produce(
    topic: str, payload: dict[str, Any], *, key: str | None = None
) -> dict[str, Any] | None:
    """Publish a single message; fall back to in-memory on broker or config failure."""
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)

    metrics.KAFKA_PRODUCED_TOTAL.labels(topic=canonical).inc()

    if not settings.kafka_produce:
        return _append_log(canonical, payload, pk, "in_process_bus")

    with _lock:
        producer = _producer

    if producer is None:
        logger.warning("kafka_producer_not_started", topic=canonical)
        return _append_log(canonical, payload, pk, "in_process_bus")

    row = _append_log(canonical, payload, pk, "kafka")
    try:
        await producer.send_and_wait(
            canonical,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            key=pk.encode("utf-8"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_produce_failed", topic=canonical, error=str(exc))
        _update_transport(row["message_id"], "in_process_bus")
    return row


def produce_sync(
    topic: str, payload: dict[str, Any], *, key: str | None = None
) -> dict[str, Any] | None:
    """Sync wrapper for :func:`produce`.

    In ``local``/``test`` (``KAFKA_PRODUCE=false``) the row is written to the
    in-memory bus immediately. In real mode the coroutine is scheduled on the
    running application loop; if the producer is not running, we fall back to
    the in-memory bus instead of blocking the caller.
    """
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)

    metrics.KAFKA_PRODUCED_TOTAL.labels(topic=canonical).inc()

    if not settings.kafka_produce:
        return _append_log(canonical, payload, pk, "in_process_bus")

    with _lock:
        producer = _producer
        main_loop = _loop

    if producer is None or main_loop is None or not main_loop.is_running():
        logger.warning("kafka_producer_sync_not_ready", topic=canonical)
        return _append_log(canonical, payload, pk, "in_process_bus")

    try:
        loop = asyncio.get_running_loop()
        if loop is main_loop:
            loop.create_task(produce(canonical, payload, key=pk))
        else:
            asyncio.run_coroutine_threadsafe(produce(canonical, payload, key=pk), main_loop)
    except RuntimeError:
        asyncio.run_coroutine_threadsafe(produce(canonical, payload, key=pk), main_loop)
    return None


async def _handle(record: dict[str, Any]) -> None:
    """Dispatch a single consumed record to the appropriate service handler."""
    from app import service

    topic = record.get("topic", "")
    payload = record.get("payload", {})
    short = short_topic(topic)

    metrics.KAFKA_CONSUMED_TOTAL.labels(topic=topic).inc()

    try:
        if short == "deal.stage_changed":
            contact_id = (
                payload.get("contact_id")
                or payload.get("lead_id")
                or payload.get("id")
                or "unknown"
            )
            stage = payload.get("stage") or payload.get("deal_stage") or ""
            tenant = payload.get("tenant", "default")
            await asyncio.to_thread(service.record_stage_change, contact_id, stage, tenant=tenant)

        elif short == "lead.scored":
            contact_id = (
                payload.get("contact_id")
                or payload.get("lead_id")
                or payload.get("id")
                or "unknown"
            )
            score = float(payload.get("score", 0))
            sequence_id = payload.get("sequence_id", "")
            tenant = payload.get("tenant", "default")
            context = payload.get("context")
            await asyncio.to_thread(
                service.handle_lead_scored,
                contact_id,
                score,
                sequence_id,
                tenant=tenant,
                context=context,
            )

        elif short == "nba.recommended":
            logger.info("kafka_nba_recommended_ignored", topic=topic)

        else:
            logger.warning("kafka_unknown_topic", topic=topic)

    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_handle_record_failed", topic=topic, error=str(exc))


async def consume_loop(stop: asyncio.Event) -> None:
    """Consume configured topics until ``stop`` is set.

    Falls back to an in-memory poll when aiokafka is unavailable or the broker
    cannot be reached, so local/test and degraded environments keep dispatching.
    """
    global _consumed_index
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        return

    consumer: Any | None = None
    real = False
    AIOKafkaConsumer = None

    try:
        from aiokafka import AIOKafkaConsumer
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_consumer_unavailable", error=str(exc))

    if AIOKafkaConsumer is not None:
        try:
            consumer = AIOKafkaConsumer(
                *settings.kafka_topics,
                bootstrap_servers=settings.kafka_bootstrap_servers,
                group_id=settings.kafka_consumer_group,
                enable_auto_commit=True,
                auto_offset_reset="latest",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            )
            await consumer.start()
            real = True
            logger.info("kafka_consumer_started", topics=settings.kafka_topics)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_consumer_start_failed", error=str(exc))

    try:
        if real and consumer is not None:
            while not stop.is_set():
                try:
                    batch = await consumer.getmany(timeout_ms=500, max_records=100)
                except asyncio.CancelledError:
                    break
                for _tp, messages in batch.items():
                    for msg in messages:
                        if stop.is_set():
                            break
                        record = {
                            "topic": msg.topic,
                            "key": (msg.key or b"").decode("utf-8"),
                            "payload": msg.value,
                        }
                        await _handle(record)

        else:
            logger.info("kafka_consumer_in_memory_fallback")
            while not stop.is_set():
                rows: list[dict[str, Any]] = []
                with _lock:
                    rows = list(_kafka_log[_consumed_index:])
                    _consumed_index = len(_kafka_log)
                for row in rows:
                    if stop.is_set():
                        break
                    if row["topic"] in settings.kafka_topics:
                        await _handle(row)
                if not rows:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=0.05)
                    except TimeoutError:
                        pass

    except asyncio.CancelledError:
        logger.info("kafka_consumer_cancelled")
        raise
    finally:
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("kafka_consumer_stop_failed", error=str(exc))
        logger.info("kafka_consumer_stopped")


async def start_consumer() -> dict[str, Any]:
    """Start the consumer loop as a background task (called from the app lifespan)."""
    global _consumer_task, _stop_event
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        return {"started": False, "reason": "disabled"}
    if _consumer_task is not None and not _consumer_task.done():
        return {"started": True, "running": True}
    _stop_event = asyncio.Event()
    _consumer_task = asyncio.create_task(consume_loop(_stop_event))
    return {"started": True, "topics": settings.kafka_topics}


async def stop_consumer() -> None:
    """Cancel the consumer loop and wait for it to finish."""
    global _consumer_task, _stop_event
    if _consumer_task is None:
        return
    if _stop_event is not None:
        _stop_event.set()
    _consumer_task.cancel()
    try:
        await _consumer_task
    except asyncio.CancelledError:
        pass
    _consumer_task = None
    _stop_event = None
