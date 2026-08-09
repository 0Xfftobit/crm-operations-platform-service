"""Kafka consumer group — optional background ingestion."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from threading import RLock
from typing import Any

from app.core.config import get_settings
from app.service import process_event

logger = logging.getLogger(__name__)

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()
_stats: dict[str, Any] = {"running": False, "messages_processed": 0, "errors": 0}

_lock = RLock()


def reset_kafka_consumer() -> None:
    stop_kafka_consumer()
    with _lock:
        _stats.update({"running": False, "messages_processed": 0, "errors": 0})


def consumer_stats() -> dict[str, Any]:
    with _lock:
        return dict(_stats)


def _process_record(topic: str, payload: dict[str, Any], *, offset: int, partition: int) -> None:
    trigger_id = payload.get("trigger_id") or f"kafka:{topic}:{partition}:{offset}"
    payload = {**payload, "trigger_id": trigger_id}
    process_event(
        topic=topic,
        payload=payload,
        idempotency_key=f"kafka:{topic}:{offset}:{partition}:{trigger_id}",
        trigger_id=trigger_id,
        source="kafka",
    )
    with _lock:
        _stats["messages_processed"] += 1


def _consumer_loop() -> None:
    settings = get_settings()
    try:
        from kafka import KafkaConsumer
    except ImportError:
        logger.warning("kafka_python_not_installed")
        return

    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=True,
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    consumer.subscribe(list(settings.kafka_topics))
    with _lock:
        _stats["running"] = True
    logger.info(
        "kafka_consumer_started",
        extra={"bootstrap": settings.kafka_bootstrap_servers, "group": settings.kafka_consumer_group},
    )
    try:
        while not _stop_event.is_set():
            polled = consumer.poll(timeout_ms=1000, max_records=50)
            for _tp, records in polled.items():
                for record in records:
                    if _stop_event.is_set():
                        break
                    try:
                        payload = record.value if isinstance(record.value, dict) else {}
                        _process_record(
                            record.topic,
                            payload,
                            offset=record.offset,
                            partition=record.partition,
                        )
                    except Exception as exc:
                        with _lock:
                            _stats["errors"] += 1
                        logger.exception("kafka_message_failed", extra={"error": str(exc)})
    finally:
        consumer.close()
        with _lock:
            _stats["running"] = False


def start_kafka_consumer() -> dict[str, Any]:
    global _consumer_thread
    settings = get_settings()
    if not settings.kafka_consumer_enabled:
        return {"started": False, "reason": "disabled"}
    if _consumer_thread and _consumer_thread.is_alive():
        return {"started": True, "already_running": True}
    _stop_event.clear()
    _consumer_thread = threading.Thread(target=_consumer_loop, name="kafka-consumer", daemon=True)
    _consumer_thread.start()
    return {"started": True}


def stop_kafka_consumer() -> None:
    global _consumer_thread
    _stop_event.set()
    if _consumer_thread and _consumer_thread.is_alive():
        _consumer_thread.join(timeout=5)
    _consumer_thread = None


async def start_kafka_consumer_async() -> dict[str, Any]:
    return await asyncio.to_thread(start_kafka_consumer)


async def stop_kafka_consumer_async() -> None:
    await asyncio.to_thread(stop_kafka_consumer)
