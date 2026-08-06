"""Kafka bus — async producer for produced events + a consumer loop for activity ingest.

- **Producer** (``KAFKA_PRODUCE=true``): mirrors ``events.publish`` onto the bus so Engine 8 and the
  warehouse receive ``compliance.alert / case.* / signal`` in real time (topic = event type).
- **Consumer** (``KAFKA_INGEST=true``): subscribes to ``KAFKA_TOPIC`` and feeds each activity event
  to ``service.record_activity`` — the streaming surveillance path (design §6, keyed by entity_id).

Uses ``aiokafka`` (async, fits the FastAPI event loop), lazily imported. If the lib or broker is
unavailable, producing degrades to a no-op and the consumer loop exits — the in-process paths keep
working, honouring the platform's graceful-degradation convention.
"""
import asyncio
import json

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_producer = None
_loop = None          # the app's main event loop (for producing from worker threads)


async def start_producer() -> bool:
    """Start the shared async producer; return False if unavailable (degrade to no-op)."""
    global _producer, _loop
    settings = get_settings()
    if not settings.kafka_produce:
        return False
    try:
        from aiokafka import AIOKafkaProducer
        _producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await _producer.start()
        _loop = asyncio.get_running_loop()
        logger.info("kafka_producer_started", servers=settings.kafka_bootstrap_servers)
        return True
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("kafka_producer_unavailable", error=str(exc))
        _producer = None
        return False


async def stop_producer() -> None:
    global _producer, _loop
    if _producer is not None:
        await _producer.stop()
        _producer = None
    _loop = None


async def produce(topic: str, payload: dict) -> None:
    """Publish one message (no-op if the producer isn't running)."""
    if _producer is None:
        return
    try:
        await _producer.send_and_wait(topic, json.dumps(payload).encode())
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_produce_failed", topic=topic, error=str(exc))


def produce_sync(topic: str, payload: dict) -> None:
    """Fire-and-forget from sync code.

    FastAPI runs ``def`` handlers in a threadpool with no running loop, so we can't ``create_task``
    there — schedule onto the captured main loop via ``run_coroutine_threadsafe`` instead. In an
    async context (e.g. the consumer loop) we schedule on the current loop directly.
    """
    if _producer is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(produce(topic, payload))
    except RuntimeError:                       # called from a worker thread → use the main loop
        if _loop is not None and _loop.is_running():
            asyncio.run_coroutine_threadsafe(produce(topic, payload), _loop)


async def consume_loop(stop: asyncio.Event) -> None:
    """Consume activity events → record_activity, until ``stop`` is set (design streaming path)."""
    settings = get_settings()
    if not settings.kafka_ingest:
        return
    try:
        from aiokafka import AIOKafkaConsumer
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_consumer_unavailable", error=str(exc))
        return
    consumer = AIOKafkaConsumer(
        settings.kafka_topic, bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_group_id, enable_auto_commit=True,
        auto_offset_reset="latest")
    try:
        await consumer.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kafka_consumer_start_failed", error=str(exc))
        return
    logger.info("kafka_consumer_started", topic=settings.kafka_topic)
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=500, max_records=100)
            for _tp, messages in batch.items():
                for msg in messages:
                    _handle(msg.value)
    finally:
        await consumer.stop()
        logger.info("kafka_consumer_stopped")


def _handle(raw: bytes) -> None:
    from app import service
    from app.domain.models import ActivityEvent
    try:
        service.record_activity(ActivityEvent(**json.loads(raw)))
    except Exception as exc:  # noqa: BLE001 — a poison message must not kill the loop
        logger.warning("kafka_ingest_bad_message", error=str(exc))
        service.note_dead_letter()      # feed the dead-letter monitor (coverage guarantee)
