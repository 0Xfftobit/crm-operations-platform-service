"""Kafka bus contract tests for the Feedback Loop System — no live broker required.

These tests exercise the public API and graceful-degradation behavior expected by
the cross-agent Kafka integration contract: topic canonicalisation, partition-key
precedence, in-memory fallback, real-producer hand-off, consumer dispatch, and
poison-message handling.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.messaging import kafka_bus
from app.repositories import get_outcome_repository

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def enable_kafka_produce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the real Kafka producer path for a single test."""
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()


@pytest.fixture
def enable_kafka_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable the Kafka consumer for a single test."""
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()


@pytest.fixture
def stub_aiokafka(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``aiokafka`` with a broker-free stub.

    The producer factory returns a MagicMock whose ``send_and_wait`` is an
    AsyncMock so tests can assert on the exact ``key`` and ``value`` bytes
    passed to the broker.  The consumer class fails on ``start`` to force the
    in-memory fallback.
    """
    producer = MagicMock()
    producer.start = AsyncMock(return_value=None)
    producer.stop = AsyncMock(return_value=None)
    producer.send_and_wait = AsyncMock(return_value=None)

    fake = types.ModuleType("aiokafka")

    def _producer_factory(**_: Any) -> MagicMock:
        return producer

    fake.AIOKafkaProducer = _producer_factory

    class _StubConsumer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def start(self) -> None:
            raise OSError("broker unavailable")

        async def stop(self) -> None:
            pass

    fake.AIOKafkaConsumer = _StubConsumer
    monkeypatch.setitem(sys.modules, "aiokafka", fake)
    return producer


# ── Helpers ───────────────────────────────────────────────────────────────


async def _wait_until(predicate: Callable[[], bool], timeout: float = 0.5) -> None:
    """Poll ``predicate`` until it is true or ``timeout`` elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"predicate did not become true within {timeout}s")
        await asyncio.sleep(0.01)


# ── Canonical / short topic helpers ───────────────────────────────────────


def test_canonical_and_short_topic_helpers() -> None:
    assert kafka_bus.canonical_topic("outcome.funded") == "crm.outcome.funded"
    assert kafka_bus.canonical_topic("outreach.converted") == "crm.outreach.converted"
    assert kafka_bus.canonical_topic("crm.outcome.funded") == "crm.outcome.funded"
    assert kafka_bus.canonical_topic("compliance.alert") == "compliance.alert"
    assert kafka_bus.canonical_topic("unknown.event") == "crm.unknown.event"

    assert kafka_bus.short_topic("crm.outcome.funded") == "outcome.funded"
    assert kafka_bus.short_topic("crm.outcome.lost") == "outcome.lost"
    assert kafka_bus.short_topic("crm.outreach.converted") == "outreach.converted"
    assert kafka_bus.short_topic("crm.unknown.event") == "unknown.event"


# ── Partition key precedence ──────────────────────────────────────────────


def test_partition_key_precedence() -> None:
    assert kafka_bus.partition_key({"deal_id": "D1", "lead_id": "L1"}) == "D1"
    assert kafka_bus.partition_key({"lead_id": "L1", "contact_id": "C1"}) == "L1"
    assert kafka_bus.partition_key({"contact_id": "C1", "entity_id": "E1"}) == "C1"
    assert kafka_bus.partition_key({"entity_id": "E1", "id": "I1"}) == "E1"
    assert kafka_bus.partition_key({"id": "I1"}) == "I1"
    assert kafka_bus.partition_key({}) == "unknown"


# ── Bus stats / reset ─────────────────────────────────────────────────────


def test_bus_stats_and_reset() -> None:
    kafka_bus.reset_kafka_bus()
    kafka_bus.produce_sync("outcome.funded", {"entity_id": "D1", "engine_name": "lead_scoring"})

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["consumer_lag"] == 1
    assert stats["topics"] == {"crm.outcome.funded": 1}
    assert stats["dead_letters"] == 0
    assert stats["transport"] == "in_process_bus"

    kafka_bus.reset_kafka_bus()
    reset = kafka_bus.bus_stats()
    assert reset["messages_total"] == 0
    assert reset["consumer_lag"] == 0
    assert reset["topics"] == {}
    assert reset["dead_letters"] == 0
    assert reset["transport"] == "in_process_bus"


# ── Producer defaults to in-memory bus ────────────────────────────────────


def test_producer_defaults_to_in_memory_bus() -> None:
    kafka_bus.reset_kafka_bus()
    payload = {"entity_id": "D1", "engine_name": "lead_scoring"}
    row = asyncio.run(kafka_bus.produce("outcome.funded", payload))

    assert row is not None
    assert row["topic"] == "crm.outcome.funded"
    assert row["transport"] == "in_process_bus"
    assert row["key"] == "D1"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["topics"]["crm.outcome.funded"] == 1
    assert stats["transport"] == "in_process_bus"


# ── Real producer using MagicMock ─────────────────────────────────────────


def test_real_producer_sends_with_partition_key(
    enable_kafka_produce: None,
    stub_aiokafka: MagicMock,
) -> None:
    producer = stub_aiokafka
    kafka_bus.reset_kafka_bus()

    assert asyncio.run(kafka_bus.start_producer()) is True

    payload = {"deal_id": "D1", "lead_id": "L1", "engine_name": "lead_scoring"}
    row = asyncio.run(kafka_bus.produce("outcome.funded", payload))

    assert row is not None
    assert row["topic"] == "crm.outcome.funded"
    assert row["transport"] == "kafka"
    assert row["key"] == "D1"

    assert kafka_bus.bus_stats()["messages_total"] == 1

    call = producer.send_and_wait.call_args
    assert call is not None
    assert call.args[0] == "crm.outcome.funded"
    assert call.args[1] == json.dumps(payload).encode("utf-8")
    assert call.kwargs["key"] == b"D1"

    asyncio.run(kafka_bus.stop_producer())


def test_real_producer_degrades_to_in_memory_bus_on_send_failure(
    enable_kafka_produce: None,
    stub_aiokafka: MagicMock,
) -> None:
    producer = stub_aiokafka
    kafka_bus.reset_kafka_bus()
    producer.send_and_wait.side_effect = Exception("broker down")

    assert asyncio.run(kafka_bus.start_producer()) is True

    row = asyncio.run(kafka_bus.produce("outcome.funded", {"deal_id": "D1"}))

    assert row is not None
    assert row["topic"] == "crm.outcome.funded"
    assert row["transport"] == "in_process_bus"
    assert row["key"] == "D1"
    assert kafka_bus.bus_stats()["messages_total"] == 1
    producer.send_and_wait.assert_called_once()

    asyncio.run(kafka_bus.stop_producer())


def test_producer_degrades_to_in_memory_bus_when_broker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    enable_kafka_produce: None,
) -> None:
    fake = types.ModuleType("aiokafka")

    class _BrokenProducer:
        def __init__(self, **_: Any) -> None:
            pass

        async def start(self) -> None:
            raise OSError("broker unavailable")

    class _BrokenConsumer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def start(self) -> None:
            raise OSError("broker unavailable")

    fake.AIOKafkaProducer = _BrokenProducer
    fake.AIOKafkaConsumer = _BrokenConsumer
    monkeypatch.setitem(sys.modules, "aiokafka", fake)

    kafka_bus.reset_kafka_bus()
    assert asyncio.run(kafka_bus.start_producer()) is False

    row = asyncio.run(kafka_bus.produce("outcome.funded", {"deal_id": "D1"}))
    assert row is not None
    assert row["transport"] == "in_process_bus"
    assert kafka_bus.bus_stats()["messages_total"] == 1


# ── Consumer start / stop ─────────────────────────────────────────────────


def test_consumer_start_and_stop(
    stub_aiokafka: MagicMock,
    enable_kafka_consumer: None,
) -> None:
    async def _run() -> None:
        info = await kafka_bus.start_consumer()
        assert info["status"] == "started"
        assert "task" in info
        assert info["topics"] == get_settings().kafka_topics
        await asyncio.sleep(0.05)
        await kafka_bus.stop_consumer()

    asyncio.run(_run())


def test_consumer_start_disabled_is_noop() -> None:
    async def _run() -> None:
        info = await kafka_bus.start_consumer()
        assert info == {"status": "disabled"}

    asyncio.run(_run())


# ── Consumer dispatch with canonical -> short mapping ─────────────────────


def test_consumer_dispatches_outcome_funded(
    stub_aiokafka: MagicMock,
    enable_kafka_consumer: None,
) -> None:
    async def _run() -> None:
        await kafka_bus.start_consumer()
        await kafka_bus.produce(
            "outcome.funded",
            {"entity_id": "deal-funded", "engine_name": "lead_scoring"},
        )
        await _wait_until(lambda: kafka_bus.bus_stats()["consumer_lag"] == 0)
        await kafka_bus.stop_consumer()

    asyncio.run(_run())
    outcomes = get_outcome_repository().list()
    assert any(o.entity_id == "deal-funded" and o.event_type == "funded" for o in outcomes)


def test_consumer_dispatches_outcome_lost(
    stub_aiokafka: MagicMock,
    enable_kafka_consumer: None,
) -> None:
    async def _run() -> None:
        await kafka_bus.start_consumer()
        await kafka_bus.produce(
            "outcome.lost",
            {"entity_id": "deal-lost", "engine_name": "lead_scoring"},
        )
        await _wait_until(lambda: kafka_bus.bus_stats()["consumer_lag"] == 0)
        await kafka_bus.stop_consumer()

    asyncio.run(_run())
    outcomes = get_outcome_repository().list()
    assert any(o.entity_id == "deal-lost" and o.event_type == "lost" for o in outcomes)


def test_consumer_dispatches_outreach_converted(
    stub_aiokafka: MagicMock,
    enable_kafka_consumer: None,
) -> None:
    async def _run() -> None:
        await kafka_bus.start_consumer()
        await kafka_bus.produce(
            "outreach.converted",
            {"entity_id": "lead-converted"},
        )
        await _wait_until(lambda: kafka_bus.bus_stats()["consumer_lag"] == 0)
        await kafka_bus.stop_consumer()

    asyncio.run(_run())
    outcomes = get_outcome_repository().list()
    assert any(o.entity_id == "lead-converted" and o.event_type == "converted" for o in outcomes)


# ── Dead-letter / poison message handling ─────────────────────────────────


def test_consumer_dead_letters_poison_payload(
    stub_aiokafka: MagicMock,
    enable_kafka_consumer: None,
) -> None:
    async def _run() -> None:
        await kafka_bus.start_consumer()
        await kafka_bus.produce("outcome.funded", {})  # missing entity_id -> poison
        await _wait_until(lambda: kafka_bus.bus_stats()["dead_letters"] == 1)
        await kafka_bus.stop_consumer()

    asyncio.run(_run())
    assert kafka_bus.bus_stats()["dead_letters"] == 1
    assert not get_outcome_repository().list()


# ── Sync produce behaviour ────────────────────────────────────────────────


def test_produce_sync_without_event_loop_records_in_memory() -> None:
    kafka_bus.reset_kafka_bus()
    kafka_bus.produce_sync(
        "outcome.funded",
        {"entity_id": "D1", "engine_name": "lead_scoring"},
    )

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["topics"]["crm.outcome.funded"] == 1
    assert stats["transport"] == "in_process_bus"


def test_produce_sync_with_captured_loop_uses_run_coroutine_threadsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True
    kafka_bus._loop = mock_loop
    try:
        runner = MagicMock()
        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", runner)

        kafka_bus.produce_sync(
            "outcome.funded",
            {"entity_id": "D1", "engine_name": "lead_scoring"},
        )

        assert runner.call_count == 1
        call = runner.call_args
        assert call is not None
        coro, loop = call.args
        assert asyncio.iscoroutine(coro)
        assert loop is mock_loop
        coro.close()
    finally:
        kafka_bus._loop = None


# ── Model retrained producer (FLS-specific, keeps existing value) ─────────


def test_produce_model_retrained_emits_both_events() -> None:
    kafka_bus.reset_kafka_bus()
    kafka_bus.produce_model_retrained(
        engine_name="lead_scoring",
        model_id="m1",
        metrics={"auc": 0.9},
        dataset_id="ds-1",
    )

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 2
    assert stats["topics"]["crm.model.retrained"] == 1
    assert stats["topics"]["crm.champion.promoted"] == 1
