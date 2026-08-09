"""In-memory store — fallback when Redis/Postgres unavailable or in tests."""
from __future__ import annotations

import json
import time
from threading import RLock
from typing import Any


class MemoryStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: dict[str, tuple[dict[str, Any], float]] = {}
        self._loop_budget: dict[str, tuple[int, float]] = {}
        self._round_robin: dict[str, int] = {}
        self._rule_cache: dict[str, dict[str, Any]] = {}
        self._violations: int = 0

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._loop_budget.clear()
            self._round_robin.clear()
            self._rule_cache.clear()
            self._violations = 0

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._entries.items() if exp < now]
        for k in expired:
            del self._entries[k]

    def _purge_loop_expired(self) -> None:
        now = time.time()
        expired = [k for k, (_, exp) in self._loop_budget.items() if exp < now]
        for k in expired:
            del self._loop_budget[k]

    def get_key(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_expired()
            row = self._entries.get(key)
            return row[0] if row else None

    def set_key(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        with self._lock:
            self._entries[key] = (value, time.time() + ttl_seconds)

    def get_loop_budget(self, deal_id: str) -> int:
        with self._lock:
            self._purge_loop_expired()
            row = self._loop_budget.get(f"loopbudget:{deal_id}")
            return row[0] if row else 0

    def incr_loop_budget(self, deal_id: str, *, increment: int, ttl_seconds: int) -> int:
        key = f"loopbudget:{deal_id}"
        with self._lock:
            self._purge_loop_expired()
            row = self._loop_budget.get(key)
            current = (row[0] if row else 0) + increment
            self._loop_budget[key] = (current, time.time() + ttl_seconds)
            return current

    def invalidate_rule_cache(self, rule_id: str) -> None:
        with self._lock:
            keys = [k for k in self._rule_cache if k.startswith(f"{rule_id}:")]
            for k in keys:
                del self._rule_cache[k]

    def cache_rule_version(self, rule_id: str, version: str, rule: dict[str, Any]) -> None:
        with self._lock:
            self._rule_cache[f"{rule_id}:v{version}"] = rule

    def get_cached_rule_version(self, rule_id: str, version: str) -> dict[str, Any] | None:
        with self._lock:
            return self._rule_cache.get(f"{rule_id}:v{version}")

    def cache_rule(self, rule_id: str, rule: dict[str, Any]) -> None:
        with self._lock:
            self._rule_cache[rule_id] = rule

    def get_cached_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._rule_cache.get(rule_id)

    def record_violation(self) -> None:
        with self._lock:
            self._violations += 1

    def violation_count(self) -> int:
        with self._lock:
            return self._violations

    def incr_round_robin(self, pool_key: str) -> int:
        with self._lock:
            idx = self._round_robin.get(pool_key, 0)
            self._round_robin[pool_key] = idx + 1
            return idx

    def mark_event_seen(self, event_id: str, *, ttl_seconds: int) -> bool:
        """Return True when event_id is newly seen; False when duplicate."""
        key = f"event:seen:{event_id}"
        with self._lock:
            self._purge_expired()
            if key in self._entries:
                return False
            self._entries[key] = ({"event_id": event_id}, time.time() + ttl_seconds)
            return True

    def stats(self, *, backend_label: str, ttl_days: int) -> dict[str, Any]:
        with self._lock:
            self._purge_expired()
            return {
                "backend": backend_label,
                "keys_active": len(self._entries),
                "ttl_days": ttl_days,
                "round_robin_pools": len(self._round_robin),
                "cached_rules": len(self._rule_cache),
                "loop_budget_keys": len(self._loop_budget),
                "violations": self._violations,
            }

    def ping(self) -> bool:
        return True

    def dump_json(self, value: dict[str, Any]) -> str:
        return json.dumps(value, default=str)

    def load_json(self, raw: str) -> dict[str, Any]:
        return json.loads(raw)
