"""Redis hot-cache path for consent: CACHE_GATEWAY=redis switch, TTL, invalidation,
unavailable-broker fallback, and pre-send hook integration (FR-003 / consent NFR)."""
import json
import time

import pytest

from app import cache, service
from app.core.config import get_settings
from app.domain.models import Consent, PrecheckRequest


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch redis.Redis.from_url to return a fresh FakeRedis instance."""
    fakeredis = pytest.importorskip("fakeredis")
    redis = pytest.importorskip("redis")

    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    get_settings.cache_clear()
    cache.reset()

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: fake)
    return fake


# ── CACHE_GATEWAY=redis uses Redis as the hot store ─────────────────────────
def test_redis_cache_switch_stores_consent_in_redis(fake_redis):
    consent = Consent(lead_id="l1", channel="sms", opted_in=False, on_dnd_registry=True)
    cache.set(consent)

    raw = fake_redis.get("consent:l1:sms")
    assert raw is not None
    stored = Consent(**json.loads(raw))
    assert stored.opted_in is False
    assert stored.on_dnd_registry is True

    got = cache.get("l1", "sms")
    assert got is not None
    assert got.opted_in is False


def test_redis_cache_invalidation(fake_redis):
    cache.set(Consent(lead_id="l2", channel="email", opted_in=True))
    assert cache.get("l2", "email") is not None
    cache.invalidate("l2", "email")
    assert cache.get("l2", "email") is None
    assert fake_redis.get("consent:l2:email") is None


def test_redis_cache_ttl_expires_entry(fake_redis, monkeypatch):
    monkeypatch.setenv("CONSENT_CACHE_TTL_S", "1")
    get_settings.cache_clear()

    cache.set(Consent(lead_id="l3", channel="call", opted_in=True))
    assert cache.get("l3", "call") is not None
    assert 0 < fake_redis.ttl("consent:l3:call") <= 1

    time.sleep(1.2)
    assert cache.get("l3", "call") is None
    assert fake_redis.get("consent:l3:call") is None


# ── Unavailable Redis degrades to in-process memory cache ──────────────────
def test_redis_unavailable_falls_back_to_memory(monkeypatch):
    redis = pytest.importorskip("redis")
    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://unreachable")
    get_settings.cache_clear()
    cache.reset()

    def _raise(*args, **kwargs):
        raise redis.ConnectionError("no broker")

    monkeypatch.setattr(redis.Redis, "from_url", _raise)

    consent = Consent(lead_id="l4", channel="sms", opted_in=True)
    cache.set(consent)
    got = cache.get("l4", "sms")
    assert got is not None
    assert got.opted_in is True
    assert cache.redis_health()["status"] == "unavailable"


# ── Redis cache is wired into the pre-send hook ────────────────────────────
def test_redis_precheck_uses_cached_consent(fake_redis):
    service.upsert_consent(Consent(lead_id="lead_redis", channel="sms", opted_in=False))
    assert fake_redis.get("consent:lead_redis:sms") is not None

    result = service.run_precheck(
        PrecheckRequest(channel="sms", contact_id="lead_redis"))
    assert result.decision == "deny"
    assert any("opt_out" in r for r in result.reasons)
