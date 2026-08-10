"""Kafka integration contract tests for the Performance Analytics System (broker-free).

These tests exercise the public API required by the cross-agent Kafka integration
contract: canonical/short topics, partition-key ordering, producer/consumer graceful
degradation, in-memory fallback, and sync-friendly publish paths.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import service
from app.core.config import get_settings
from app.domain.models import OutcomeEvent, TouchEvent
from app.messaging import kafka_bus

from .helpers import outcome_payload, touch_payload

# ── Helpers / fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def enable_kafka_produce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable real Kafka producer mode for a single test."""
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()


@pytest.fixture
def enable_kafka_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the Kafka consumer for a single test."""
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()


@pytest.fixture
def fake_producer(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``AIOKafkaProducer`` creation to return a controllable MagicMock."""
    import aiokafka

    producer = MagicMock()
    producer.start = AsyncMock(return_value=None)
    producer.send_and_wait = AsyncMock(return_value=None)
    producer.stop = AsyncMock(return_value=None)

    fake_cls = MagicMock(return_value=producer)
    monkeypatch.setattr(aiokafka, "AIOKafkaProducer", fake_cls)
    return producer


class _BoomConsumer:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def start(self) -> None:
        raise OSError("no broker")

    async def stop(self) -> None:
        pass


class _BoomProducer:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def start(self) -> None:
        raise OSError("no broker")


# ── Canonical / short topic helpers ─────────────────────────────────────────


def test_canonical_and_short_topic_helpers() -> None:
    # Known short topics round-trip through canonical/short helpers.
    for short, canonical in kafka_bus.TOPIC_MAP.items():
        assert kafka_bus.canonical_topic(short) == canonical
        assert kafka_bus.short_topic(canonical) == short

    # Already-canonical and unknown names are handled consistently.
    assert kafka_bus.canonical_topic("crm.outreach.sent") == "crm.outreach.sent"
    assert kafka_bus.canonical_topic("unknown.thing") == "crm.unknown.thing"

    assert kafka_bus.short_topic("crm.unknown.thing") == "unknown.thing"


# ── Partition key precedence ────────────────────────────────────────────────


def test_partition_key_precedence() -> None:
    assert kafka_bus.partition_key({"deal_id": "D1", "lead_id": "L1"}) == "D1"
    assert kafka_bus.partition_key({"lead_id": "L1", "contact_id": "C1"}) == "L1"
    assert kafka_bus.partition_key({"contact_id": "C1", "contact_pid": "CP1"}) == "C1"
    assert kafka_bus.partition_key({"contact_pid": "CP1", "entity_id": "E1"}) == "CP1"
    assert kafka_bus.partition_key({"entity_id": "E1", "id": "I1"}) == "E1"
    assert kafka_bus.partition_key({"id": "I1"}) == "I1"
    assert kafka_bus.partition_key({}) == "unknown"


# ── Bus stats / reset ───────────────────────────────────────────────────────


def test_bus_stats_and_reset() -> None:
    kafka_bus.reset_kafka_bus()
    assert kafka_bus.bus_stats() == {
        "messages_total": 0,
        "consumer_lag": 0,
        "topics": {},
        "transport": "in_process_bus",
    }

    kafka_bus.produce_sync("outreach.sent", touch_payload())
    kafka_bus.produce_sync("outcome.funded", outcome_payload(stage="funded"))

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 2
    assert stats["consumer_lag"] == 2
    assert stats["topics"] == {
        "crm.outreach.sent": 1,
        "crm.outcome.funded": 1,
    }
    assert stats["transport"] == "in_process_bus"

    kafka_bus.reset_kafka_bus()
    reset = kafka_bus.bus_stats()
    assert reset["messages_total"] == 0
    assert reset["consumer_lag"] == 0
    assert reset["topics"] == {}
    assert reset["transport"] == "in_process_bus"


# ── Producer defaults to in-memory bus ──────────────────────────────────────


def test_kafka_producer_defaults_to_in_process_bus() -> None:
    kafka_bus.reset_kafka_bus()
    assert kafka_bus._producer is None

    row_async = asyncio.run(kafka_bus.produce("outreach.sent", touch_payload()))
    assert row_async is not None
    assert row_async["topic"] == "crm.outreach.sent"
    assert row_async["transport"] == "in_process_bus"
    assert row_async["key"] == "deal-1"

    row_sync = kafka_bus.produce_sync("outcome.funded", outcome_payload(stage="funded"))
    assert row_sync is not None
    assert row_sync["topic"] == "crm.outcome.funded"
    assert row_sync["transport"] == "in_process_bus"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 2
    assert stats["transport"] == "in_process_bus"


# ── Real producer graceful degradation ──────────────────────────────────────


def test_kafka_real_producer_start_failure_degrades_to_in_process_bus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()
    kafka_bus.reset_kafka_bus()

    import aiokafka

    monkeypatch.setattr(aiokafka, "AIOKafkaProducer", _BoomProducer)

    assert asyncio.run(kafka_bus.start_producer()) is False

    row = asyncio.run(kafka_bus.produce("outreach.sent", touch_payload()))
    assert row is not None
    assert row["transport"] == "in_process_bus"
    assert kafka_bus.bus_stats()["messages_total"] == 1


def test_kafka_real_producer_sends_with_partition_key(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    kafka_bus.reset_kafka_bus()
    assert asyncio.run(kafka_bus.start_producer()) is True

    payload = touch_payload(deal_id="D1")
    row = asyncio.run(kafka_bus.produce("outreach.sent", payload))
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"
    assert row["key"] == "D1"
    assert row["transport"] == "kafka"

    fake_producer.send_and_wait.assert_awaited_once()
    call = fake_producer.send_and_wait.call_args
    assert call is not None
    assert call.args[0] == "crm.outreach.sent"
    assert json.loads(call.kwargs["value"]) == payload
    assert call.kwargs["key"] == b"D1"

    asyncio.run(kafka_bus.stop_producer())


def test_kafka_real_producer_degrades_to_in_process_bus_on_send_failure(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    kafka_bus.reset_kafka_bus()
    assert asyncio.run(kafka_bus.start_producer()) is True

    fake_producer.send_and_wait.side_effect = Exception("broker down")

    row = asyncio.run(kafka_bus.produce("outreach.sent", touch_payload()))
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"
    assert row["transport"] == "in_process_bus"
    assert kafka_bus.bus_stats()["messages_total"] == 1

    fake_producer.send_and_wait.assert_awaited_once()
    asyncio.run(kafka_bus.stop_producer())


# ── Sync produce behavior (no event loop / worker thread) ───────────────────


def test_kafka_produce_sync_without_event_loop() -> None:
    kafka_bus.reset_kafka_bus()
    assert kafka_bus._producer is None
    assert kafka_bus._loop is None

    row = kafka_bus.produce_sync("outreach.sent", touch_payload())
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"
    assert row["transport"] == "in_process_bus"
    assert kafka_bus.bus_stats()["messages_total"] == 1


def test_kafka_produce_sync_from_worker_thread_sends_with_partition_key(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    kafka_bus.reset_kafka_bus()
    payload = touch_payload(deal_id="D2")

    async def _scenario() -> None:
        assert await kafka_bus.start_producer() is True
        # Simulate a synchronous FastAPI handler running in a worker thread.
        await asyncio.to_thread(kafka_bus.produce_sync, "outreach.sent", payload)
        await asyncio.sleep(0.05)

    try:
        asyncio.run(_scenario())
    finally:
        asyncio.run(kafka_bus.stop_producer())

    fake_producer.send_and_wait.assert_awaited_once()
    call = fake_producer.send_and_wait.call_args
    assert call is not None
    assert call.args[0] == "crm.outreach.sent"
    assert json.loads(call.kwargs["value"]) == payload
    assert call.kwargs["key"] == b"D2"


# ── Consumer start/stop and dispatch ────────────────────────────────────────


async def _drain_consumer(stop: asyncio.Event) -> None:
    for _ in range(200):
        if kafka_bus.bus_stats()["consumer_lag"] == 0:
            break
        await asyncio.sleep(0.01)
    await kafka_bus.stop_consumer()


def test_kafka_consumer_start_and_stop(
    monkeypatch: pytest.MonkeyPatch,
    enable_kafka_consumer: None,
) -> None:
    import aiokafka

    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)
    kafka_bus.reset_kafka_bus()

    async def _run() -> None:
        start = await kafka_bus.start_consumer()
        assert start["started"] is True
        assert "topics" in start
        assert "group" in start
        await _drain_consumer(kafka_bus._consumer_stop or asyncio.Event())

    asyncio.run(_run())


def test_kafka_consumer_dispatches_short_topics(
    monkeypatch: pytest.MonkeyPatch,
    enable_kafka_consumer: None,
) -> None:
    import aiokafka

    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)
    service.reset_state()
    kafka_bus.reset_kafka_bus()

    async def _run() -> None:
        start = await kafka_bus.start_consumer()
        assert start["started"] is True

        # Produce using short topic names; the bus canonicalizes them.
        await kafka_bus.produce(
            "outreach.sent",
            touch_payload(deal_id="d-consume"),
        )
        await kafka_bus.produce(
            "outcome.funded",
            outcome_payload(deal_id="d-consume", stage="funded"),
        )

        await _drain_consumer(kafka_bus._consumer_stop or asyncio.Event())

    asyncio.run(_run())

    assert service.get_stats()["touches"] == 1
    assert service.get_stats()["outcomes"] == 1
    # Canonical -> short mapping is preserved on the in-memory bus.
    assert kafka_bus.short_topic("crm.outreach.sent") == "outreach.sent"
    assert kafka_bus.short_topic("crm.outcome.funded") == "outcome.funded"


def test_kafka_consumer_ignores_unrouted_topics(
    monkeypatch: pytest.MonkeyPatch,
    enable_kafka_consumer: None,
) -> None:
    monkeypatch.setenv("KAFKA_TOPICS", "crm.outreach.sent")
    get_settings.cache_clear()
    import aiokafka

    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)
    service.reset_state()
    kafka_bus.reset_kafka_bus()

    async def _run() -> None:
        start = await kafka_bus.start_consumer()
        assert start["started"] is True
        assert start["topics"] == ["crm.outreach.sent"]

        await kafka_bus.produce("crm.outreach.sent", touch_payload(deal_id="d-1"))
        await kafka_bus.produce(
            "crm.deal.stage_changed",
            {"deal_id": "d-1", "stage": "lead"},
        )

        await _drain_consumer(kafka_bus._consumer_stop or asyncio.Event())

    asyncio.run(_run())

    assert service.get_stats()["touches"] == 1


# ── Dead-letter / poison message handling ───────────────────────────────────


def test_kafka_consumer_dead_letters_poison_payload(
    monkeypatch: pytest.MonkeyPatch,
    enable_kafka_consumer: None,
) -> None:
    import aiokafka

    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)
    service.reset_state()
    kafka_bus.reset_kafka_bus()

    async def _run() -> None:
        start = await kafka_bus.start_consumer()
        assert start["started"] is True

        # A valid touch, then a poison payload, then another valid touch.
        await kafka_bus.produce("crm.outreach.sent", touch_payload(deal_id="d-1"))
        kafka_bus._append_to_memory("crm.outreach.sent", "not-a-dict", "k")  # type: ignore[arg-type]
        await kafka_bus.produce("crm.outreach.sent", touch_payload(deal_id="d-2"))

        await _drain_consumer(kafka_bus._consumer_stop or asyncio.Event())

    asyncio.run(_run())

    assert service.get_stats()["touches"] == 2


# ── Domain-specific produce helpers ─────────────────────────────────────────


def test_kafka_produce_touch_and_outcome_are_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    touch = TouchEvent(**touch_payload())
    outcome = OutcomeEvent(**outcome_payload(stage="funded"))

    # With KAFKA_PRODUCE=false (default) the helpers should be no-ops.
    assert kafka_bus.produce_touch(touch) is None
    assert kafka_bus.produce_outcome(outcome) is None
    assert kafka_bus.bus_stats()["messages_total"] == 0

    # With KAFKA_PRODUCE=true they append to the in-memory bus.
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()
    kafka_bus.reset_kafka_bus()

    row = kafka_bus.produce_touch(touch)
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"

    row = kafka_bus.produce_outcome(outcome)
    assert row is not None
    assert row["topic"] == "crm.outcome.funded"

    assert kafka_bus.bus_stats()["messages_total"] == 2
