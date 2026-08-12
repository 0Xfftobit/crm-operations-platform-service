"""Kafka bus — async producer for produced events + a consumer loop for activity ingest.

- **Producer** (``KAFKA_PRODUCE=true``): mirrors ``events.publish`` onto the bus so Engine 8 and the
  warehouse receive ``compliance.alert / case.* / signal`` in real time (topic = event type).
- **Consumer** (``KAFKA_INGEST=true`` or ``KAFKA_CONSUMER_ENABLED=true``): subscribes to
  ``KAFKA_TOPICS`` and feeds each activity event to ``service.record_activity`` — the streaming
  surveillance path (design §6).

Uses ``aiokafka`` (async, fits the FastAPI event loop), lazily imported. If the lib or broker is
unavailable, producing degrades to the in-memory bus and the consumer loop falls back to the same
in-memory log — the in-process paths keep working, honouring the platform's graceful-degradation
convention.

The public API exposes:

- ``start_producer`` / ``stop_producer``
- ``produce`` / ``produce_sync`` (keyed by ``partition_key`` by default)
- ``consume_loop`` / ``start_consumer`` / ``stop_consumer``
- ``reset_kafka_bus`` / ``bus_stats``
- ``canonical_topic`` / ``short_topic`` / ``partition_key``
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from threading import RLock
from typing import Any

import structlog

from app.core import metrics
from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_lock = RLock()
_producer: Any | None = None
_loop: asyncio.AbstractEventLoop | None = None

# In-memory bus state (queue + log). Protected by _lock.
_memory_log: list[dict] = []
_consumed_index: int = 0
_memory_new: asyncio.Event | None = None

# Consumer task/controls.
_consumer_stop: asyncio.Event | None = None
_consumer_task: asyncio.Task | None = None


def _build_record(topic: str, payload: dict, key: str, transport: str = "memory") -> dict:
    """Build a canonical in-memory bus row."""
    return {
        "message_id": uuid.uuid4().hex,
        "topic": topic,
        "key": key,
        "payload": payload,
        "published_at": datetime.now(UTC).isoformat(),
        "transport": transport,
    }


def canonical_topic(short_or_topic: str, *, domain: str = "compliance") -> str:
    """Return a canonical topic name.

    Names that already start with a known domain (``compliance.`` / ``crm.``) are canonical.
    Legacy hyphenated names (e.g. ``compliance-audit``) are preserved. Bare short names get
    ``{domain}.{short}``.
    """
    if "-" in short_or_topic:
        return short_or_topic
    if "." in short_or_topic:
        head = short_or_topic.split(".", 1)[0]
        if head in ("compliance", "crm"):
            return short_or_topic
    return f"{domain}.{short_or_topic}"


def short_topic(canonical: str) -> str:
    """Return the short name from a canonical topic (``compliance.case.opened`` → ``case.opened``).

    Legacy hyphenated names and bare names are returned unchanged.
    """
    if "." in canonical:
        return canonical.split(".", 1)[1]
    return canonical


def partition_key(payload: dict) -> str:
    """Compute a partition key from the payload to keep per-deal / per-entity ordering."""
    return str(
        payload.get("deal_id")
        or payload.get("lead_id")
        or payload.get("contact_id")
        or payload.get("entity_id")
        or payload.get("id")
        or "unknown"
    )


def _notify_memory_new() -> None:
    """Signal the in-memory consumer that a new record is available."""
    if _memory_new is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop and _memory_new is not None:
        _memory_new.set()
    elif _loop is not None and _loop.is_running():
        asyncio.run_coroutine_threadsafe(_set_event(_memory_new), _loop)


async def _set_event(event: asyncio.Event) -> None:
    event.set()


def _store_in_memory(record: dict) -> dict:
    """Append ``record`` to the in-memory bus and wake any active in-memory consumer."""
    with _lock:
        _memory_log.append(record)
    logger.info("kafka_produced_to_memory", topic=record["topic"], key=record["key"])
    _notify_memory_new()
    metrics.KAFKA_PRODUCED_TOTAL.inc(
        {"topic": record["topic"], "transport": record.get("transport", "memory")}, 1
    )
    return record


async def start_producer() -> bool:
    """Start the shared async producer; return False if disabled or unavailable."""
    global _producer, _loop
    settings = get_settings()
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    with _lock:
        if _producer is not None:
            return True
    if not settings.kafka_produce:
        logger.info("kafka_producer_disabled", produce=settings.kafka_produce)
        return False
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await producer.start()
        with _lock:
            _producer = producer
        logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)
        return True
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("kafka_producer_unavailable", error=str(exc))
        with _lock:
            _producer = None
        return False


async def stop_producer() -> None:
    """Stop the shared async producer."""
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
    logger.info("kafka_producer_stopped")


async def produce(topic: str, payload: dict, *, key: str | None = None) -> dict | None:
    """Publish one message.

    When ``KAFKA_PRODUCE`` is true and a real producer is running, the message is sent to the
    broker. In all other cases it is written to the in-memory bus (graceful degradation).
    """
    topic = canonical_topic(topic)
    key = key or partition_key(payload)
    record = _build_record(topic, payload, key, transport="memory")
    settings = get_settings()

    if not settings.kafka_produce:
        return _store_in_memory(record)

    producer = _producer
    if producer is None:
        logger.warning("kafka_producer_not_ready_falling_back", topic=topic)
        return _store_in_memory(record)

    value = json.dumps(payload).encode("utf-8")
    key_bytes = key.encode("utf-8")
    try:
        await producer.send_and_wait(topic, value, key=key_bytes)
        record["transport"] = "kafka"
        logger.info("kafka_produced", topic=topic, key=key)
        metrics.KAFKA_PRODUCED_TOTAL.inc({"topic": topic, "transport": "kafka"}, 1)
        return record
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_produce_failed", topic=topic, key=key, error=str(exc))
        record["transport"] = "memory"
        return _store_in_memory(record)


def produce_sync(topic: str, payload: dict, *, key: str | None = None) -> None:
    """Fire-and-forget from sync code.

    FastAPI runs ``def`` handlers in a threadpool with no running loop, so we schedule onto the
    captured main loop via ``run_coroutine_threadsafe``. In an async context (e.g. the consumer
    loop) we schedule on the current loop. If there is no loop at all we write to the in-memory bus
    synchronously.
    """
    topic = canonical_topic(topic)
    key = key or partition_key(payload)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # In an async context: schedule the async produce. It will route to real or memory.
        loop.create_task(produce(topic, payload, key=key))
        return

    main_loop = _loop
    if main_loop is not None and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(produce(topic, payload, key=key), main_loop)
        return

    # Synchronous, no event loop — store in memory directly.
    _store_in_memory(_build_record(topic, payload, key, transport="memory"))


def _glob_to_regex(pattern: str) -> str:
    """Convert a shell-style topic glob to a regex for aiokafka pattern subscription."""
    out: list[str] = []
    meta = re.compile(r"[.*+?^${}()|[\]\\]")
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(meta.sub(r"\\\g<0>", ch))
    return "".join(out)


def _subscription_pattern(topics: list[str]) -> tuple[list[str] | None, str | None]:
    """Decide whether to use ``topics=`` or ``pattern=`` for aiokafka subscribe."""
    if not any("*" in t for t in topics):
        return topics, None
    return None, "|".join(_glob_to_regex(t) for t in topics)


def _topic_matches(topic: str, patterns: list[str]) -> bool:
    """Check ``topic`` against the configured topic globs (e.g. ``crm.*``)."""
    return any(fnmatchcase(topic, pat) for pat in patterns)


async def _consume_real(consumer: Any, stop: asyncio.Event, patterns: list[str]) -> None:
    """Consume messages from a real Kafka/Redpanda consumer."""
    logger.info("kafka_real_consumer_running", topics=patterns)
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=200, max_records=100)
            for _tp, messages in batch.items():
                for msg in messages:
                    if _topic_matches(msg.topic, patterns):
                        key = (msg.key or b"").decode("utf-8")
                        _handle(msg.topic, msg.value, key, transport="kafka")
    finally:
        with contextlib.suppress(Exception):
            await consumer.stop()
        logger.info("kafka_real_consumer_stopped")


async def _wait_for_memory_or_stop(stop: asyncio.Event, timeout: float = 0.2) -> None:
    if _memory_new is None:
        await asyncio.sleep(timeout)
        return
    try:
        await asyncio.wait_for(_memory_new.wait(), timeout=timeout)
    except TimeoutError:
        return
    _memory_new.clear()


async def _consume_memory(stop: asyncio.Event, patterns: list[str]) -> None:
    """Consume messages from the in-memory bus (fallback / broker-free tests)."""
    global _consumed_index
    logger.info("kafka_memory_consumer_running", topics=patterns)
    while not stop.is_set():
        records: list[dict] = []
        with _lock:
            while _consumed_index < len(_memory_log):
                record = _memory_log[_consumed_index]
                _consumed_index += 1
                if _topic_matches(record["topic"], patterns):
                    records.append(record)
        for record in records:
            _handle(
                record["topic"],
                json.dumps(record["payload"]).encode("utf-8"),
                record.get("key", ""),
                transport=record.get("transport", "memory"),
            )
        if stop.is_set():
            break
        await _wait_for_memory_or_stop(stop)
    logger.info("kafka_memory_consumer_stopped")


async def consume_loop(stop: asyncio.Event) -> None:
    """Consume activity events → record_activity, until ``stop`` is set."""
    settings = get_settings()
    if not (settings.kafka_consumer_enabled or settings.kafka_ingest):
        return

    patterns = list(settings.kafka_topics)
    if not patterns:
        logger.warning("kafka_consumer_no_topics")
        return

    # Try a real consumer first, then fall back to the in-memory log.
    consumer = None
    try:
        from aiokafka import AIOKafkaConsumer

        topics, pat = _subscription_pattern(patterns)
        group = settings.kafka_consumer_group or settings.kafka_group_id
        consumer = AIOKafkaConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=group,
            enable_auto_commit=True,
            auto_offset_reset="latest",
        )
        if pat is not None:
            consumer.subscribe(pattern=pat)
        else:
            consumer.subscribe(topics=topics or [])
        await consumer.start()
        logger.info("kafka_consumer_started", topics=patterns)
        await _consume_real(consumer, stop, patterns)
        return
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_consumer_unavailable", error=str(exc))
        if consumer is not None:
            with contextlib.suppress(Exception):
                await consumer.stop()

    # In-memory fallback.
    logger.info("kafka_consumer_falling_back_to_memory")
    await _consume_memory(stop, patterns)


async def start_consumer() -> dict[str, Any]:
    """Convenience: start ``consume_loop`` in a background ``asyncio.Task``.

    Returns a status dict with ``started``, ``task``, and ``reason`` keys.
    """
    global _consumer_stop, _consumer_task, _memory_new
    settings = get_settings()
    if not (settings.kafka_consumer_enabled or settings.kafka_ingest):
        return {"started": False, "reason": "disabled"}
    if _consumer_task is not None and not _consumer_task.done():
        return {"started": True, "task": _consumer_task, "reason": "already_running"}

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"started": False, "reason": "no_event_loop"}

    _consumer_stop = asyncio.Event()
    _memory_new = asyncio.Event()
    _consumer_task = loop.create_task(consume_loop(_consumer_stop))
    return {"started": True, "task": _consumer_task}


async def stop_consumer() -> None:
    """Stop the background consumer task created by ``start_consumer``."""
    global _consumer_task
    if _consumer_stop is not None:
        _consumer_stop.set()
    if _consumer_task is not None:
        _consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _consumer_task
    _consumer_task = None


def _handle(
    topic: str | bytes,
    raw: bytes | str = b"",
    key: str = "",
    transport: str = "memory",
) -> None:
    """Dispatch one consumed message to ``service.record_activity``."""
    from app import service
    from app.domain.models import ActivityEvent

    # Backward-compatible call signature used by older tests: _handle(bytes)
    if isinstance(topic, bytes):
        raw = topic
        topic = "compliance-audit"
    elif isinstance(raw, str):
        raw = raw.encode("utf-8")

    try:
        payload = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_ingest_bad_message", topic=topic, error=str(exc))
        service.note_dead_letter()
        return

    try:
        service.record_activity(ActivityEvent(**payload))
        logger.info("kafka_ingest_recorded", topic=topic, key=key)
        metrics.KAFKA_CONSUMED_TOTAL.inc({"topic": topic, "transport": transport}, 1)
    except Exception as exc:  # noqa: BLE001 — a poison message must not kill the loop
        logger.warning("kafka_ingest_bad_message", topic=topic, error=str(exc))
        service.note_dead_letter()


def reset_kafka_bus() -> None:
    """Reset the bus state. For tests and graceful restarts."""
    global _producer, _loop, _consumer_stop, _consumer_task
    global _memory_log, _consumed_index, _memory_new
    with _lock:
        if _consumer_task is not None and not _consumer_task.done():
            _consumer_task.cancel()
        _producer = None
        _loop = None
        _consumer_stop = None
        _consumer_task = None
        _memory_log.clear()
        _consumed_index = 0
        _memory_new = None


def bus_stats() -> dict[str, Any]:
    """Return in-memory bus statistics."""
    with _lock:
        total = len(_memory_log)
        lag = max(0, total - _consumed_index)
        topics = sorted({row["topic"] for row in _memory_log})
        transport: dict[str, int] = {}
        for row in _memory_log:
            transport[row["transport"]] = transport.get(row["transport"], 0) + 1
        return {
            "total": total,
            "in_memory_lag": lag,
            "topics": topics,
            "transport": transport,
            "producer_running": _producer is not None,
            "consumer_running": _consumer_task is not None and not _consumer_task.done(),
        }
