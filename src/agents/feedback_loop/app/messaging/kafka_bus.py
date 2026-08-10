"""Kafka bus with in-memory fallback for the Feedback Loop System.

The bus is designed to degrade gracefully: when ``KAFKA_PRODUCE`` /
``KAFKA_CONSUMER_ENABLED`` are disabled, or when ``aiokafka`` / the broker is
unavailable, all operations fall back to an in-process log. This keeps local
and CI test suites fast while allowing the same code paths to drive a real
Kafka cluster in production.

Public API:
    start_producer, stop_producer, produce, produce_sync,
    consume_loop, start_consumer, stop_consumer,
    reset_kafka_bus, bus_stats, canonical_topic, partition_key
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_lock = RLock()

# Real Kafka state
_producer: Any = None
_loop: asyncio.AbstractEventLoop | None = None

# Consumer state
_consumer_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None

# In-process bus state
_consumer_offset: int = 0
_kafka_log: list[dict[str, Any]] = []
_dead_letter: list[dict[str, Any]] = []

# Known FLS topics. Canonical form is ``crm.<short>``.
TOPIC_MAP: dict[str, str] = {
    "outcome.funded": "crm.outcome.funded",
    "outcome.lost": "crm.outcome.lost",
    "outreach.converted": "crm.outreach.converted",
    "model.retrained": "crm.model.retrained",
    "champion.promoted": "crm.champion.promoted",
}

_SHORT_MAP: dict[str, str] = {canonical: short for short, canonical in TOPIC_MAP.items()}


def canonical_topic(short: str) -> str:
    """Return the canonical ``crm.<short>`` topic; idempotent for canonical names."""
    if short.startswith(("crm.", "compliance.")):
        return short
    return TOPIC_MAP.get(short, f"crm.{short}")


def short_topic(canonical: str) -> str:
    """Return the short form of a canonical topic (``crm.outcome.funded`` -> ``outcome.funded``)."""
    return _SHORT_MAP.get(canonical, canonical.split(".", 1)[1] if "." in canonical else canonical)


def partition_key(payload: dict[str, Any]) -> str:
    """Compute a partition key from a payload for per-entity ordering."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("contact_id")
        or payload.get("entity_id")
        or payload.get("id")
        or "unknown"
    )


def _record_message(canonical: str, payload: dict[str, Any], key: str, transport: str) -> dict[str, Any]:
    row = {
        "message_id": f"msg_{uuid4().hex[:12]}",
        "topic": canonical,
        "key": key,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": transport,
    }
    with _lock:
        _kafka_log.append(row)
    return row


async def _ensure_real_producer() -> Any:
    """Start the real AIOKafkaProducer when configured; return None on failure."""
    global _producer
    settings = get_settings()
    if not settings.kafka_produce:
        return None
    with _lock:
        producer = _producer
    if producer is not None:
        return producer
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await producer.start()
        with _lock:
            _producer = producer
        logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)
        return producer
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_producer_unavailable", error=str(exc))
        with _lock:
            _producer = None
        return None


async def start_producer() -> bool:
    """Start the shared producer; capture the running loop for sync producers.

    Returns ``True`` only when a real broker producer is active. A return value
    of ``False`` is not an error — it means the bus will use the in-process log.
    """
    global _loop
    _loop = asyncio.get_running_loop()
    producer = await _ensure_real_producer()
    return producer is not None


async def stop_producer() -> None:
    """Stop the shared producer and release the captured loop."""
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


async def produce(topic: str, payload: dict[str, Any], *, key: str | None = None) -> dict[str, Any] | None:
    """Publish one message to the bus."""
    settings = get_settings()
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)
    transport = "in_process_bus"

    if settings.kafka_produce:
        producer = await _ensure_real_producer()
        if producer is not None:
            try:
                await producer.send_and_wait(
                    canonical,
                    json.dumps(payload).encode("utf-8"),
                    key=pk.encode("utf-8"),
                )
                transport = "kafka"
            except Exception as exc:  # noqa: BLE001
                logger.warning("kafka_produce_failed", topic=canonical, error=str(exc))

    return _record_message(canonical, payload, key=pk, transport=transport)


def produce_sync(topic: str, payload: dict[str, Any], *, key: str | None = None) -> None:
    """Fire-and-forget producer for synchronous call sites.

    Schedules the real async :func:`produce` when a running loop is available,
    otherwise writes directly to the in-process log (useful in tests and
    worker threads before the app lifespan has started).
    """
    canonical = canonical_topic(topic)
    pk = key or partition_key(payload)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(produce(canonical, payload, key=pk))
        return
    except RuntimeError:
        pass

    with _lock:
        main_loop = _loop
    if main_loop is not None and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(produce(canonical, payload, key=pk), main_loop)
        return

    # No usable loop: record in-process immediately.
    _record_message(canonical, payload, key=pk, transport="in_process_bus")


def _dispatch(topic: str, payload: Any) -> None:
    """Route a consumed message to the appropriate FLS service handler."""
    from app import service
    from app.domain.models import Outcome

    short = short_topic(topic)
    try:
        data = dict(payload) if isinstance(payload, dict) else {}
        if short in ("outcome.funded", "outcome.lost", "outreach.converted"):
            # Use the topic suffix as a default event_type so the Outcome model
            # validates even when the upstream payload omits it.
            data.setdefault("event_type", short.split(".")[-1])
            service.ingest_outcome(Outcome(**data))
        else:
            raise ValueError(f"unknown consumer topic {topic}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_dispatch_failed", topic=topic, error=str(exc))
        with _lock:
            _dead_letter.append(
                {
                    "topic": topic,
                    "payload": payload,
                    "error": str(exc),
                    "at": datetime.now(UTC).isoformat(),
                }
            )


async def consume_loop(stop: asyncio.Event) -> None:
    """Consume from Kafka (or the in-process log) and dispatch records."""
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        logger.info("kafka_consumer_disabled")
        return

    logger.info(
        "kafka_consumer_starting",
        topics=settings.kafka_topics,
        group=settings.kafka_consumer_group,
    )

    consumer: Any = None
    try:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            *settings.kafka_topics,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=True,
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()
        logger.info("kafka_consumer_started", transport="kafka")
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_consumer_unavailable", error=str(exc))
        consumer = None

    if consumer is not None:
        try:
            while not stop.is_set():
                batch = await consumer.getmany(timeout_ms=500, max_records=100)
                for _tp, messages in batch.items():
                    for msg in messages:
                        _dispatch(msg.topic, msg.value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kafka_consumer_loop_failed", error=str(exc))
        finally:
            try:
                await consumer.stop()
            except Exception:  # noqa: BLE001
                pass
            logger.info("kafka_consumer_stopped")
    else:
        # In-process fallback: poll the shared log for newly produced messages.
        logger.info("kafka_consumer_in_memory_poll_started")
        global _consumer_offset
        while not stop.is_set():
            with _lock:
                log = list(_kafka_log)
                offset = _consumer_offset

            new_offset = offset
            while new_offset < len(log) and not stop.is_set():
                msg = log[new_offset]
                new_offset += 1
                if msg["topic"] in settings.kafka_topics:
                    _dispatch(msg["topic"], msg["payload"])

            with _lock:
                _consumer_offset = max(_consumer_offset, new_offset)

            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.05)
                except TimeoutError:
                    pass
        logger.info("kafka_consumer_in_memory_poll_stopped")


async def start_consumer() -> dict[str, Any]:
    """Start the consumer loop in a background task when enabled."""
    global _consumer_task, _stop_event
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        return {"status": "disabled"}
    if _consumer_task is not None and not _consumer_task.done():
        return {"status": "already_started", "task": _consumer_task}

    _stop_event = asyncio.Event()
    _consumer_task = asyncio.create_task(consume_loop(_stop_event))
    return {
        "status": "started",
        "topics": settings.kafka_topics,
        "group": settings.kafka_consumer_group,
        "task": _consumer_task,
    }


async def stop_consumer() -> None:
    """Cancel and await the consumer loop."""
    global _consumer_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    task = _consumer_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    with _lock:
        _consumer_task = None
        _stop_event = None


def reset_kafka_bus() -> None:
    """Clear all bus state (used by tests and local resets)."""
    global _producer, _loop, _consumer_task, _stop_event, _consumer_offset, _kafka_log, _dead_letter
    with _lock:
        _producer = None
        _loop = None
        _consumer_task = None
        _stop_event = None
        _consumer_offset = 0
        _kafka_log.clear()
        _dead_letter.clear()


def bus_stats() -> dict[str, Any]:
    """Return current bus statistics."""
    with _lock:
        topics: dict[str, int] = {}
        for m in _kafka_log:
            topics[m["topic"]] = topics.get(m["topic"], 0) + 1
        lag = max(0, len(_kafka_log) - _consumer_offset)
        return {
            "messages_total": len(_kafka_log),
            "consumer_lag": lag,
            "topics": topics,
            "dead_letters": len(_dead_letter),
            "transport": "kafka" if _producer is not None else "in_process_bus",
        }


def produce_model_retrained(
    engine_name: str,
    model_id: str,
    metrics: dict[str, Any],
    dataset_id: str | None = None,
) -> None:
    """Emit ``crm.model.retrained`` and ``crm.champion.promoted`` after a successful retrain."""
    now = datetime.now(UTC).isoformat()
    payload = {
        "id": model_id,
        "engine_name": engine_name,
        "model_id": model_id,
        "dataset_id": dataset_id,
        "metrics": metrics,
        "retrained_at": now,
    }
    produce_sync("model.retrained", payload, key=model_id)
    produce_sync("champion.promoted", payload, key=model_id)
