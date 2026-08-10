"""Kafka bus contract tests — broker-free.

These exercise the public API required by the cross-agent Kafka integration contract:
canonical/short topics, partition-key ordering, graceful producer/consumer degradation,
CRM-surveillance dispatch, and bus stats/reset.
"""
import asyncio

import aiokafka

from app import audit, coverage, kafka_bus
from app.core.config import get_settings

# ── Canonical / partition helpers ─────────────────────────────────────────


def test_canonical_topic_preserves_and_prefixes():
    assert kafka_bus.canonical_topic("compliance.alert") == "compliance.alert"
    assert kafka_bus.canonical_topic("compliance-audit") == "compliance-audit"
    assert kafka_bus.canonical_topic("alert") == "compliance.alert"
    assert kafka_bus.canonical_topic("deal.stage_changed", domain="crm") == "crm.deal.stage_changed"


def test_short_topic_splits_and_preserves():
    assert kafka_bus.short_topic("compliance.case.opened") == "case.opened"
    assert kafka_bus.short_topic("crm.deal.stage_changed") == "deal.stage_changed"
    assert kafka_bus.short_topic("compliance-audit") == "compliance-audit"


def test_partition_key_precedence():
    assert kafka_bus.partition_key({"deal_id": "d1", "lead_id": "l1"}) == "d1"
    assert kafka_bus.partition_key({"lead_id": "l1"}) == "l1"
    assert kafka_bus.partition_key({"contact_id": "c1"}) == "c1"
    assert kafka_bus.partition_key({"entity_id": "e1"}) == "e1"
    assert kafka_bus.partition_key({"id": "i1"}) == "i1"
    assert kafka_bus.partition_key({"foo": "bar"}) == "unknown"


# ── Producer graceful degradation ─────────────────────────────────────────


def test_producer_disabled_by_default():
    assert asyncio.run(kafka_bus.start_producer()) is False


def test_producer_defaults_to_memory():
    record = asyncio.run(kafka_bus.produce("compliance.alert", {"x": 1, "deal_id": "d1"}))
    assert record is not None
    assert record["transport"] == "memory"
    assert record["topic"] == "compliance.alert"
    assert record["key"] == "d1"

    stats = kafka_bus.bus_stats()
    assert stats["total"] == 1
    assert stats["in_memory_lag"] == 1
    assert stats["topics"] == ["compliance.alert"]
    assert stats["transport"]["memory"] == 1


def test_producer_degrades_when_broker_unavailable(monkeypatch):
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()

    class _Boom:
        def __init__(self, **_):
            pass

        async def start(self):
            raise OSError("no broker")

    monkeypatch.setattr(aiokafka, "AIOKafkaProducer", _Boom)
    assert asyncio.run(kafka_bus.start_producer()) is False

    record = asyncio.run(kafka_bus.produce("compliance.alert", {"deal_id": "d1"}))
    assert record is not None
    assert record["transport"] == "memory"
    assert kafka_bus.bus_stats()["total"] == 1


def test_produce_sync_without_loop_stores_in_memory():
    # simulate a plain sync thread with no event loop — must not raise
    before = kafka_bus.bus_stats()["total"]
    kafka_bus.produce_sync("compliance.signal", {"entity_id": "e1"})
    stats = kafka_bus.bus_stats()
    assert stats["total"] == before + 1
    assert stats["topics"][0] == "compliance.signal"
    assert stats["transport"]["memory"] == before + 1


# ── Consumer dispatch (broker-free, in-memory fallback) ───────────────────


def _stage_payload(extra: dict | None = None) -> dict:
    payload = {
        "event_type": "deal.stage_changed",
        "engine": "tas",
        "entity_id": "d1",
        "entity_type": "deal",
        "action": "stage_changed",
        "outcome": "funded",
        "source_seq": 1,
    }
    if extra:
        payload.update(extra)
    return payload


class _BoomConsumer:
    def __init__(self, **_) -> None:
        pass

    async def start(self):
        raise OSError("no broker")

    async def stop(self):
        pass


async def _drain_consumer_once(payload: dict, topic: str = "crm.deal.stage_changed") -> None:
    # Force the consumer into in-memory fallback by making AIOKafkaConsumer fail on start.
    get_settings.cache_clear()
    original = getattr(aiokafka, "AIOKafkaConsumer", None)
    aiokafka.AIOKafkaConsumer = _BoomConsumer
    try:
        start = await kafka_bus.start_consumer()
        assert start["started"] is True

        record = await kafka_bus.produce(topic, payload)
        assert record is not None

        # Give the in-memory consumer a few cycles to drain the event.
        for _ in range(50):
            if kafka_bus.bus_stats()["in_memory_lag"] == 0:
                break
            await asyncio.sleep(0.01)

        await kafka_bus.stop_consumer()
    finally:
        if original is not None:
            aiokafka.AIOKafkaConsumer = original


def test_consumer_dispatches_crm_surveillance(monkeypatch):
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    before = len(audit.all_entries())
    dlq_before = coverage.dead_letters()

    asyncio.run(_drain_consumer_once(_stage_payload()))

    assert len(audit.all_entries()) == before + 1
    assert coverage.dead_letters() == dlq_before


def test_consumer_ignores_extra_crm_fields(monkeypatch):
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    before = len(audit.all_entries())
    asyncio.run(_drain_consumer_once(_stage_payload({"extra_foreign_field": "ignore_me"})))
    assert len(audit.all_entries()) == before + 1


def test_consumer_dead_letters_bad_crm_payload(monkeypatch):
    # Raise the DLQ alert threshold so a single poison message does not raise a critical alert.
    monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
    monkeypatch.setenv("DEAD_LETTER_ALERT_THRESHOLD", "999")
    dlq_before = coverage.dead_letters()
    bad_payload = {"foo": "not an activity event"}
    asyncio.run(_drain_consumer_once(bad_payload, topic="crm.deal.stage_changed"))
    assert coverage.dead_letters() == dlq_before + 1


# ── Bus stats / reset ─────────────────────────────────────────────────────


def test_bus_stats_resets():
    asyncio.run(kafka_bus.produce("compliance.alert", {"deal_id": "d1"}))
    asyncio.run(kafka_bus.produce("compliance.case.opened", {"lead_id": "l1"}))
    stats = kafka_bus.bus_stats()
    assert stats["total"] == 2
    assert sorted(stats["topics"]) == ["compliance.alert", "compliance.case.opened"]

    kafka_bus.reset_kafka_bus()
    reset = kafka_bus.bus_stats()
    assert reset["total"] == 0
    assert reset["in_memory_lag"] == 0
    assert reset["topics"] == []
    assert reset["producer_running"] is False
    assert reset["consumer_running"] is False
