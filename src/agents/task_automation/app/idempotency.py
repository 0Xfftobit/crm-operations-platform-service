"""Idempotency guard — Redis-backed with memory fallback (§7)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.persistence import redis_store


def _stable_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_action_key(*, trigger_id: str, rule_id: str, action_key: str) -> str:
    return f"{trigger_id}:{rule_id}:{action_key}"


def check_idempotency(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    return redis_store.get_key(f"idem:{key}")


def check_action_idempotency(*, trigger_id: str, rule_id: str, action_key: str) -> dict[str, Any] | None:
    key = redis_store.idempotency_key(trigger_id=trigger_id, rule_id=rule_id, action_key=action_key)
    return redis_store.get_key(key)


def record_idempotency(key: str, result: dict[str, Any]) -> None:
    if key:
        redis_store.set_key(f"idem:{key}", result)


def record_action_idempotency(
    *,
    trigger_id: str,
    rule_id: str,
    action_key: str,
    result: dict[str, Any],
) -> None:
    key = redis_store.idempotency_key(trigger_id=trigger_id, rule_id=rule_id, action_key=action_key)
    redis_store.set_key(key, result)


def inputs_hash(inputs: dict[str, Any] | None) -> str:
    return _stable_hash(inputs or {})


def record_violation_attempt(key: str) -> None:
    redis_store.record_violation()


def violation_count() -> int:
    return redis_store.violation_count()


def reset_idempotency() -> None:
    redis_store.reset_redis_store()
