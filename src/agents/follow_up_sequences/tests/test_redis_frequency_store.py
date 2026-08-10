"""Redis-backed frequency cap store with a fake Redis client (no optional redis dependency)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.frequency import RedisFrequencyStore, _day_key, _week_key, get_store

NOW = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


class _FakeRedisPipeline:
    def __init__(self, client: _FakeRedis) -> None:
        self.client = client
        self._ops: list[tuple[str, ...]] = []

    def incr(self, key: str) -> _FakeRedisPipeline:
        self._ops.append(("incr", key))
        return self

    def expire(self, key: str, ttl: int) -> _FakeRedisPipeline:
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self) -> list:
        results = []
        for op in self._ops:
            if op[0] == "incr":
                results.append(self.client.incr(op[1]))
            elif op[0] == "expire":
                results.append(self.client.expire(op[1], op[2]))
        return results


class _FakeRedis:
    """In-memory stand-in for the bits of ``redis.Redis`` that ``RedisFrequencyStore`` uses."""

    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self._ttl: dict[str, int] = {}

    def get(self, key: str):
        return self._store.get(key)

    def set(self, key: str, value: int) -> bool:
        self._store[key] = value
        return True

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self._ttl[key] = ttl
        return True

    def ping(self) -> bool:
        return True

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)

    def clear(self) -> None:
        self._store.clear()
        self._ttl.clear()


@pytest.fixture
def redis_client():
    return _FakeRedis()


@pytest.fixture
def redis_store(redis_client):
    return RedisFrequencyStore(redis_client)


def test_redis_count_day_and_week(redis_store, redis_client):
    assert redis_store.count_day("t", "c", "seq", NOW) == 0
    assert redis_store.count_week("t", "c", "seq", NOW) == 0

    redis_client.set(_day_key("t", "c", "seq", NOW), 3)
    redis_client.set(_week_key("t", "c", "seq", NOW), 5)

    assert redis_store.count_day("t", "c", "seq", NOW) == 3
    assert redis_store.count_week("t", "c", "seq", NOW) == 5


def test_redis_increment_bumps_global_and_sequence_counters(redis_store, redis_client):
    redis_store.increment("t", "c", "seq", NOW)

    global_day = _day_key("t", "c", "__global__", NOW)
    global_week = _week_key("t", "c", "__global__", NOW)
    seq_day = _day_key("t", "c", "seq", NOW)
    seq_week = _week_key("t", "c", "seq", NOW)

    assert redis_client.get(global_day) == 1
    assert redis_client.get(global_week) == 1
    assert redis_client.get(seq_day) == 1
    assert redis_client.get(seq_week) == 1

    redis_store.increment("t", "c", "seq", NOW)
    assert redis_client.get(global_day) == 2
    assert redis_client.get(seq_day) == 2


def test_redis_remaining_enforces_lower_per_sequence_cap(redis_store):
    # Global cap is generous, per-sequence cap is tight.
    cap_day, cap_week = 10, 50
    per_seq_day, per_seq_week = 1, 5

    assert redis_store.remaining(
        "t", "c", "seq", NOW, cap_day=cap_day, cap_week=cap_week,
        per_seq_day=per_seq_day, per_seq_week=per_seq_week,
    ) == 1

    redis_store.increment("t", "c", "seq", NOW)

    # Per-sequence day cap is now exhausted, even though global has plenty of headroom.
    assert redis_store.remaining(
        "t", "c", "seq", NOW, cap_day=cap_day, cap_week=cap_week,
        per_seq_day=per_seq_day, per_seq_week=per_seq_week,
    ) == 0

    # A different sequence can still send because the per-seq cap is per-sequence.
    assert redis_store.remaining(
        "t", "c", "other", NOW, cap_day=cap_day, cap_week=cap_week,
        per_seq_day=per_seq_day, per_seq_week=per_seq_week,
    ) == 1


def test_redis_remaining_without_per_sequence_cap_uses_global(redis_store):
    cap_day, cap_week = 2, 5
    assert redis_store.remaining("t", "c", "seq", NOW, cap_day=cap_day, cap_week=cap_week) == 2
    redis_store.increment("t", "c", "seq", NOW)
    redis_store.increment("t", "c", "seq", NOW)
    assert redis_store.remaining("t", "c", "seq", NOW, cap_day=cap_day, cap_week=cap_week) == 0


def test_get_store_uses_redis_when_cache_gateway_is_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setenv("CACHE_GATEWAY", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://fake:6379/0")
    monkeypatch.setattr("app.frequency._redis_client", lambda: fake)

    store = get_store()
    assert isinstance(store, RedisFrequencyStore)
    store.increment("t", "c", "seq", NOW)
    assert fake.get(_day_key("t", "c", "__global__", NOW)) == 1
