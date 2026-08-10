"""Kafka integration contract tests for FUS (no live broker required).

These cover the public Kafka bus API shared across agents: canonical/short topics,
partition-key ordering, in-memory fallback, real producer call shapes, consumer
routing, poison-message isolation, and sync producer behavior.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_settings
from app.messaging import kafka_bus

# ── Canonical / short topic helpers ─────────────────────────────────────────


def test_canonical_and_short_topic_helpers() -> None:
    assert kafka_bus.canonical_topic("deal.stage_changed") == "crm.deal.stage_changed"
    assert kafka_bus.canonical_topic("crm.deal.stage_changed") == "crm.deal.stage_changed"
    assert kafka_bus.canonical_topic("compliance.alert") == "compliance.alert"
    assert kafka_bus.canonical_topic("lead.assigned") == "crm.lead.assigned"

    assert kafka_bus.short_topic("crm.deal.stage_changed") == "deal.stage_changed"
    assert kafka_bus.short_topic("compliance.alert") == "alert"
    assert kafka_bus.short_topic("deal.stage_changed") == "deal.stage_changed"
    assert kafka_bus.short_topic("crm.lead.assigned") == "lead.assigned"


# ── Partition key precedence ────────────────────────────────────────────────


def test_partition_key_precedence() -> None:
    assert kafka_bus.partition_key({"deal_id": "d1", "lead_id": "l1"}) == "d1"
    assert kafka_bus.partition_key({"lead_id": "l1", "contact_id": "c1"}) == "l1"
    assert kafka_bus.partition_key({"contact_id": "c1", "entity_id": "e1"}) == "c1"
    assert kafka_bus.partition_key({"entity_id": "e1", "id": "i1"}) == "e1"
    assert kafka_bus.partition_key({"id": "i1"}) == "i1"
    assert kafka_bus.partition_key({}) == "unknown"


# ── Bus stats / reset ───────────────────────────────────────────────────────


def test_kafka_bus_stats_and_reset() -> None:
    kafka_bus.reset_kafka_bus()

    row = asyncio.run(kafka_bus.produce("deal.stage_changed", {"deal_id": "d1"}))
    assert row is not None
    assert row["topic"] == "crm.deal.stage_changed"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["in_memory_lag"] == 1
    assert stats["topics"]["crm.deal.stage_changed"] == 1
    assert stats["transport"] == "in_process_bus"

    kafka_bus.reset_kafka_bus()
    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 0
    assert stats["in_memory_lag"] == 0
    assert stats["topics"] == {}
    assert stats["transport"] == "in_process_bus"


# ── Producer graceful degradation ───────────────────────────────────────────


def test_kafka_producer_defaults_to_in_memory_bus() -> None:
    kafka_bus.reset_kafka_bus()

    row = asyncio.run(kafka_bus.produce("outreach.sent", {"contact_id": "c1"}))
    assert row is not None
    assert row["topic"] == "crm.outreach.sent"
    assert row["key"] == "c1"
    assert row["transport"] == "in_process_bus"

    sync_row = kafka_bus.produce_sync("sequence.enrolled", {"contact_id": "c2"})
    assert sync_row is not None
    assert sync_row["topic"] == "crm.sequence.enrolled"
    assert sync_row["transport"] == "in_process_bus"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 2
    assert stats["transport"] == "in_process_bus"


def _make_fake_producer() -> MagicMock:
    mock = MagicMock()
    mock.start = AsyncMock(return_value=None)
    mock.stop = AsyncMock(return_value=None)
    mock.send_and_wait = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def fake_producer(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``aiokafka.AIOKafkaProducer`` to a controllable in-memory fake."""
    mock = _make_fake_producer()
    monkeypatch.setattr("aiokafka.AIOKafkaProducer", lambda *_, **__: mock)
    return mock


@pytest.fixture
def enable_kafka_produce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable real Kafka producer mode for a single test."""
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()


def test_kafka_real_producer_sends_key_and_value(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    kafka_bus.reset_kafka_bus()
    assert asyncio.run(kafka_bus.start_producer()) is True

    payload = {"lead_id": "L1", "deal_id": "D1"}
    row = asyncio.run(kafka_bus.produce("lead.assigned", payload))
    assert row is not None
    assert row["topic"] == "crm.lead.assigned"
    assert row["key"] == "D1"
    assert row["transport"] == "kafka"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == 1
    assert stats["transport"] == "kafka"

    call = fake_producer.send_and_wait.call_args
    assert call is not None
    assert call.args[0] == "crm.lead.assigned"
    assert json.loads(call.args[1]) == payload
    assert call.kwargs["key"] == b"D1"

    asyncio.run(kafka_bus.stop_producer())


def test_kafka_real_producer_degrades_to_in_memory_bus(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    kafka_bus.reset_kafka_bus()
    fake_producer.send_and_wait.side_effect = Exception("broker down")

    assert asyncio.run(kafka_bus.start_producer()) is True

    row = asyncio.run(kafka_bus.produce("deal.stage_changed", {"lead_id": "L1"}))
    assert row is not None
    assert row["topic"] == "crm.deal.stage_changed"
    assert row["transport"] == "in_process_bus"
    assert kafka_bus.bus_stats()["messages_total"] == 1
    fake_producer.send_and_wait.assert_called_once()

    asyncio.run(kafka_bus.stop_producer())


# ── Sync produce behavior (no event loop) ───────────────────────────────────


def test_kafka_produce_sync_without_event_loop() -> None:
    # KAFKA_PRODUCE is false by default; calling from a plain sync test must not raise.
    kafka_bus.reset_kafka_bus()
    before = kafka_bus.bus_stats()["messages_total"]

    row = kafka_bus.produce_sync("deal.stage_changed", {"deal_id": "d1", "stage": "applied"})
    assert row is not None
    assert row["topic"] == "crm.deal.stage_changed"
    assert row["key"] == "d1"
    assert row["transport"] == "in_process_bus"

    stats = kafka_bus.bus_stats()
    assert stats["messages_total"] == before + 1
    assert stats["in_memory_lag"] == before + 1
    assert stats["transport"] == "in_process_bus"


# ── Consumer dispatch (canonical -> short topic mapping) ─────────────────────


def test_kafka_consumer_dispatches_deal_stage_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_record = MagicMock(return_value={"stopped": 0})
    monkeypatch.setattr("app.service.record_stage_change", mock_record)
    kafka_bus.reset_kafka_bus()

    asyncio.run(
        kafka_bus._handle(
            {"topic": "crm.deal.stage_changed", "payload": {"contact_id": "c1", "stage": "applied"}}
        )
    )

    mock_record.assert_called_once_with("c1", "applied", tenant="default")


def test_kafka_consumer_dispatches_lead_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_handle = MagicMock(return_value={"status": "enrolled"})
    monkeypatch.setattr("app.service.handle_lead_scored", mock_handle)
    kafka_bus.reset_kafka_bus()

    asyncio.run(
        kafka_bus._handle(
            {
                "topic": "crm.lead.scored",
                "payload": {"contact_id": "c1", "score": 85.5, "sequence_id": "seq1"},
            }
        )
    )

    mock_handle.assert_called_once_with("c1", 85.5, "seq1", tenant="default", context=None)


def test_kafka_consumer_ignores_nba_recommended(capsys: pytest.CaptureFixture[str]) -> None:
    kafka_bus.reset_kafka_bus()
    asyncio.run(kafka_bus._handle({"topic": "crm.nba.recommended", "payload": {"deal_id": "d1"}}))
    captured = capsys.readouterr()
    assert "kafka_nba_recommended_ignored" in captured.out


# ── Consumer start/stop and in-memory fallback ──────────────────────────────


class _FakeAIOKafkaConsumer:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def start(self) -> None:
        raise OSError("no broker")

    async def stop(self) -> None:
        pass


@pytest.fixture
def enable_kafka_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable Kafka consumer mode for a single test."""
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    get_settings.cache_clear()


def test_kafka_consumer_start_and_stop(
    enable_kafka_consumer: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aiokafka.AIOKafkaConsumer", _FakeAIOKafkaConsumer)
    kafka_bus.reset_kafka_bus()

    async def run() -> None:
        info = await kafka_bus.start_consumer()
        assert info["started"] is True
        assert "crm.deal.stage_changed" in info["topics"]
        await kafka_bus.stop_consumer()

    asyncio.run(run())


def test_kafka_consumer_polls_in_memory_bus(
    enable_kafka_consumer: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aiokafka.AIOKafkaConsumer", _FakeAIOKafkaConsumer)
    kafka_bus.reset_kafka_bus()

    mock_record = MagicMock(return_value={"stopped": 0})
    monkeypatch.setattr("app.service.record_stage_change", mock_record)

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(kafka_bus.consume_loop(stop))
        await asyncio.sleep(0.01)

        kafka_bus.produce_sync("deal.stage_changed", {"contact_id": "c1", "stage": "applied"})

        for _ in range(100):
            if mock_record.called:
                break
            await asyncio.sleep(0.01)

        stop.set()
        await task

    asyncio.run(run())
    mock_record.assert_called_once_with("c1", "applied", tenant="default")


# ── Dead-letter / poison message handling ───────────────────────────────────


def test_kafka_consumer_poison_message_does_not_stop_consumer(
    enable_kafka_consumer: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aiokafka.AIOKafkaConsumer", _FakeAIOKafkaConsumer)
    kafka_bus.reset_kafka_bus()

    mock_record = MagicMock(side_effect=[RuntimeError("poison payload"), {"stopped": 0}])
    monkeypatch.setattr("app.service.record_stage_change", mock_record)

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(kafka_bus.consume_loop(stop))
        await asyncio.sleep(0.01)

        # poison message followed by a valid message
        kafka_bus.produce_sync("deal.stage_changed", {"contact_id": "c1", "stage": "applied"})
        kafka_bus.produce_sync("deal.stage_changed", {"contact_id": "c2", "stage": "funded"})

        for _ in range(100):
            if mock_record.call_count >= 2:
                break
            await asyncio.sleep(0.01)

        stop.set()
        await task

    asyncio.run(run())
    assert mock_record.call_count == 2
    assert kafka_bus.bus_stats()["in_memory_lag"] == 0
