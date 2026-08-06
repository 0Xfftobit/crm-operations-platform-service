"""Kafka bus (producer/consumer logic + graceful degradation), the Spark batch aggregation, and the
Redis consent hot-cache. Broker-free: the streaming transform + degradation paths are exercised
directly; the distributed group-by is covered by the pure aggregation the Spark job delegates to.
"""
import asyncio
import json

from app import audit, cache, kafka_bus
from app.core.config import get_settings
from app.domain.models import Consent
from app.spark_batch import disparity_from_counts


# ── Kafka producer/consumer ────────────────────────────────────────────────
def test_producer_disabled_by_default():
    assert asyncio.run(kafka_bus.start_producer()) is False   # KAFKA_PRODUCE=false in tests


def test_producer_degrades_when_broker_unavailable(monkeypatch):
    monkeypatch.setenv("KAFKA_PRODUCE", "true")
    get_settings.cache_clear()

    class _Boom:
        def __init__(self, **_):
            pass

        async def start(self):
            raise OSError("no broker")

    import aiokafka
    monkeypatch.setattr(aiokafka, "AIOKafkaProducer", _Boom)
    assert asyncio.run(kafka_bus.start_producer()) is False    # graceful degrade, no crash


def test_produce_forwards_to_the_running_producer():
    sent = []

    class _Fake:
        async def send_and_wait(self, topic, value):
            sent.append((topic, json.loads(value)))

    kafka_bus._producer = _Fake()
    try:
        asyncio.run(kafka_bus.produce("compliance.alert", {"x": 1}))
    finally:
        kafka_bus._producer = None
    assert sent == [("compliance.alert", {"x": 1})]


def test_consumer_handler_ingests_activity():
    before = len(audit.all_entries())
    kafka_bus._handle(json.dumps({"event_type": "comm_sent", "entity_id": "l"}).encode())
    assert len(audit.all_entries()) == before + 1


def test_consumer_handler_survives_a_poison_message():
    kafka_bus._handle(b"{not valid json")               # must not raise


# ── Spark batch aggregation (the job's pure core) ──────────────────────────
def test_batch_disparity_flags_geographic_redlining():
    counts = [
        {"sub_agent": "lead_scoring", "cohort_dimension": "geo_tract", "cohort": "a",
         "selected": 42, "total": 100},
        {"sub_agent": "lead_scoring", "cohort_dimension": "geo_tract", "cohort": "b",
         "selected": 31, "total": 100},
    ]
    [res] = disparity_from_counts(counts)
    assert res["ratio"] == round(0.31 / 0.42, 4)
    assert res["passes"] is False and res["redlining_risk"] is True


def test_batch_non_geographic_is_not_redlining():
    counts = [
        {"sub_agent": "pricing", "cohort_dimension": "channel", "cohort": "a",
         "selected": 42, "total": 100},
        {"sub_agent": "pricing", "cohort_dimension": "channel", "cohort": "b",
         "selected": 31, "total": 100},
    ]
    [res] = disparity_from_counts(counts)
    assert res["passes"] is False and res["redlining_risk"] is False


def test_batch_small_sample_not_flagged():
    counts = [
        {"sub_agent": "x", "cohort_dimension": "geo", "cohort": "a", "selected": 4, "total": 10},
        {"sub_agent": "x", "cohort_dimension": "geo", "cohort": "b", "selected": 3, "total": 10},
    ]
    [res] = disparity_from_counts(counts)
    assert res["min_sample_met"] is False and res["redlining_risk"] is False


# ── Redis consent hot-cache ────────────────────────────────────────────────
def test_memory_cache_roundtrip_and_invalidate():
    cache.set(Consent(lead_id="l", channel="sms", opted_in=True))
    assert cache.get("l", "sms").opted_in is True
    cache.invalidate("l", "sms")
    assert cache.get("l", "sms") is None


def test_redis_cache_path_with_fakeredis(monkeypatch):
    import fakeredis
    import redis
    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    get_settings.cache_clear()
    cache.reset()
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *a, **k: fake)
    cache.set(Consent(lead_id="r", channel="email", opted_in=False))
    got = cache.get("r", "email")
    assert got is not None and got.opted_in is False
    assert fake.get("consent:r:email") is not None       # actually stored in (fake) Redis
