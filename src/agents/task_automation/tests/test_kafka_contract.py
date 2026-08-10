"""Kafka integration contract tests for TAS (no live broker required)."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.kafka_consumer_group import _process_record, canonical_topic, short_topic
from app.messaging.kafka_bus import (
    bus_stats,
    partition_key,
    produce,
    produce_sync,
    publish_kafka,
    reset_kafka_bus,
    start_producer,
    stop_producer,
)


def test_canonical_and_short_topic_helpers() -> None:
    assert canonical_topic("deal.stage_changed") == "crm.deal.stage_changed"
    assert canonical_topic("lead.assigned") == "crm.lead.assigned"
    assert canonical_topic("crm.deal.stage_changed") == "crm.deal.stage_changed"
    assert canonical_topic("unknown.event") == "crm.unknown.event"

    assert short_topic("crm.deal.stage_changed") == "deal.stage_changed"
    assert short_topic("crm.lead.assigned") == "lead.assigned"
    assert short_topic("deal.stage_changed") == "deal.stage_changed"
    assert short_topic("crm.unknown.event") == "unknown.event"


def test_kafka_partition_key_uses_deal_or_lead() -> None:
    assert partition_key({"deal_id": "D1", "lead_id": "L1"}) == "D1"
    assert partition_key({"lead_id": "L1", "id": "I1"}) == "L1"
    assert partition_key({"contact_id": "C1", "entity_id": "E1", "id": "I1"}) == "C1"
    assert partition_key({"entity_id": "E1", "id": "I1"}) == "E1"
    assert partition_key({"id": "I1"}) == "I1"
    assert partition_key({}) == "unknown"


def test_kafka_bus_stats_resets() -> None:
    reset_kafka_bus()
    produce_sync("lead.assigned", {"lead_id": "L1"})
    stats = bus_stats()
    assert stats["messages_total"] == 1
    assert stats["consumer_lag"] == 0
    assert "crm.lead.assigned" in stats["topics"]

    reset_kafka_bus()
    stats = bus_stats()
    assert stats["messages_total"] == 0
    assert stats["consumer_lag"] == 0
    assert stats["topics"] == {}


def test_kafka_producer_defaults_to_memory() -> None:
    reset_kafka_bus()
    row = produce_sync("deal.stage_changed", {"deal_id": "D1"})
    assert row is not None
    assert row["topic"] == "crm.deal.stage_changed"
    assert row["source_topic"] == "deal.stage_changed"
    assert row["key"] == "D1"
    assert row["transport"] == "in_process_bus"
    assert bus_stats()["transport"] == "in_process_bus"

    row2 = asyncio.run(produce("lead.assigned", {"lead_id": "L1"}))
    assert row2 is not None
    assert bus_stats()["messages_total"] == 2

    row3 = publish_kafka("task.created", {"task_id": "T1", "lead_id": "L1"})
    assert row3 is not None
    assert row3["topic"] == "crm.task.created"


def test_kafka_consumer_dispatches_short_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_process_event(*, topic: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append({"topic": topic, "payload": payload, "kwargs": kwargs})
        return {"processed": True}

    monkeypatch.setattr("app.kafka_consumer_group.process_event", fake_process_event)
    _process_record("crm.deal.stage_changed", {"lead_id": "L1"}, offset=100, partition=0)
    _process_record("crm.lead.assigned", {"lead_id": "L2"}, offset=101, partition=1)

    assert len(calls) == 2
    assert calls[0]["topic"] == "deal.stage_changed"
    assert calls[0]["payload"]["trigger_id"] == "kafka:deal.stage_changed:0:100"
    assert calls[1]["topic"] == "lead.assigned"


def _fake_producer() -> MagicMock:
    mock = MagicMock()
    future = MagicMock()
    future.get.return_value = MagicMock(topic="crm.test", partition=0, offset=1)
    mock.send.return_value = future
    mock.close.return_value = None
    return mock


@pytest.fixture
def fake_producer(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch KafkaProducer creation to return a controllable fake."""
    mock = _fake_producer()
    monkeypatch.setattr("app.messaging.kafka_bus._create_kafka_producer", lambda *_: mock)
    return mock


@pytest.fixture
def enable_kafka_produce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable real Kafka producer mode for a single test."""
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()


def test_kafka_real_producer_sends_with_partition_key(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    reset_kafka_bus()
    assert asyncio.run(start_producer()) is True

    row = asyncio.run(produce("lead.assigned", {"deal_id": "D1", "lead_id": "L1"}))
    assert row is not None
    assert row["topic"] == "crm.lead.assigned"
    assert row["transport"] == "kafka"
    assert bus_stats()["messages_total"] == 1

    call = fake_producer.send.call_args
    assert call is not None
    assert call.kwargs["key"] == b"D1"
    assert call.kwargs["value"] == {"deal_id": "D1", "lead_id": "L1"}

    asyncio.run(stop_producer())


def test_kafka_real_producer_degrades_to_memory(
    fake_producer: MagicMock,
    enable_kafka_produce: None,
) -> None:
    reset_kafka_bus()
    fake_producer.send.side_effect = Exception("broker down")

    assert asyncio.run(start_producer()) is True

    row = asyncio.run(produce("deal.stage_changed", {"lead_id": "L1"}))
    assert row is not None
    assert row["topic"] == "crm.deal.stage_changed"
    # Fall-back row is still written to the in-memory bus.
    assert row["transport"] == "in_process_bus"
    assert bus_stats()["messages_total"] == 1
    fake_producer.send.assert_called_once()

    asyncio.run(stop_producer())
