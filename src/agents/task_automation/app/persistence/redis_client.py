"""Real Redis client — idempotency, loop budget, rule cache, round-robin."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.persistence.memory_store import MemoryStore

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis-backed store using SETEX / INCR / GET."""

    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.from_url(url, decode_responses=True)
        self._client.ping()
        self._url = url

    def ping(self) -> bool:
        return bool(self._client.ping())

    def get_key(self, key: str) -> dict[str, Any] | None:
        raw = self._client.get(key)
        if not raw:
            return None
        return json.loads(raw)

    def set_key(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)

    def get_loop_budget(self, deal_id: str) -> int:
        raw = self._client.get(f"loopbudget:{deal_id}")
        return int(raw) if raw else 0

    def incr_loop_budget(self, deal_id: str, *, increment: int, ttl_seconds: int) -> int:
        key = f"loopbudget:{deal_id}"
        pipe = self._client.pipeline()
        pipe.incrby(key, increment)
        pipe.expire(key, ttl_seconds)
        results = pipe.execute()
        return int(results[0])

    def invalidate_rule_cache(self, rule_id: str) -> None:
        pattern = f"rule:{rule_id}:v*"
        for key in self._client.scan_iter(match=pattern):
            self._client.delete(key)
        self._client.delete(f"rule:{rule_id}")

    def cache_rule_version(self, rule_id: str, version: str, rule: dict[str, Any]) -> None:
        key = f"rule:{rule_id}:v{version}"
        self._client.set(key, json.dumps(rule, default=str))

    def get_cached_rule_version(self, rule_id: str, version: str) -> dict[str, Any] | None:
        raw = self._client.get(f"rule:{rule_id}:v{version}")
        return json.loads(raw) if raw else None

    def cache_rule(self, rule_id: str, rule: dict[str, Any]) -> None:
        self._client.set(f"rule:{rule_id}", json.dumps(rule, default=str))

    def get_cached_rule(self, rule_id: str) -> dict[str, Any] | None:
        raw = self._client.get(f"rule:{rule_id}")
        return json.loads(raw) if raw else None

    def record_violation(self) -> None:
        self._client.incr("tae:idempotency:violations")

    def violation_count(self) -> int:
        raw = self._client.get("tae:idempotency:violations")
        return int(raw) if raw else 0

    def incr_round_robin(self, pool_key: str) -> int:
        key = f"tae:round_robin:{pool_key}"
        idx = int(self._client.incr(key)) - 1
        return idx

    def mark_event_seen(self, event_id: str, *, ttl_seconds: int) -> bool:
        """Return True when event_id is newly seen; False when duplicate."""
        key = f"event:seen:{event_id}"
        return bool(self._client.set(key, "1", nx=True, ex=ttl_seconds))

    def stats(self, *, backend_label: str, ttl_days: int) -> dict[str, Any]:
        try:
            info = self._client.info("keyspace")
            db_keys = 0
            for _db, meta in (info or {}).items():
                if isinstance(meta, dict):
                    db_keys += int(meta.get("keys", 0))
        except Exception:
            db_keys = -1
        return {
            "backend": backend_label,
            "redis_url": self._url.split("@")[-1] if "@" in self._url else self._url,
            "keys_active": db_keys,
            "ttl_days": ttl_days,
            "violations": self.violation_count(),
        }

    def flush_db(self) -> None:
        """Test helper — flush current Redis DB."""
        self._client.flushdb()


def connect_redis(url: str) -> RedisClient | None:
    try:
        return RedisClient(url)
    except Exception as exc:
        logger.warning("redis_connect_failed", extra={"error": str(exc), "url": url})
        return None


StoreBackend = RedisClient | MemoryStore
