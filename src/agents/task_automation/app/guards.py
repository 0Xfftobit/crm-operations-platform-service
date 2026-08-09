"""Loop detection, per-deal action budget, and canary rollout guards."""
from __future__ import annotations

import hashlib
from threading import RLock
from typing import Any

from app.core.config import get_settings
from app.persistence import redis_store

_trigger_chain: dict[str, list[str]] = {}
_canary_counters: dict[str, int] = {}

_lock = RLock()


def reset_guards() -> None:
    with _lock:
        _trigger_chain.clear()
        _canary_counters.clear()


def check_action_budget(*, lead_id: str, increment: int = 1) -> tuple[bool, str | None]:
    settings = get_settings()
    deal_id = lead_id
    current = redis_store.get_loop_budget(deal_id)
    if current + increment > settings.max_actions_per_deal:
        return False, "deal_action_budget_exceeded"
    return True, None


def record_actions(*, lead_id: str, count: int = 1) -> None:
    redis_store.incr_loop_budget(lead_id, increment=count)


def check_loop_guard(*, trigger_id: str, rule_id: str, lead_id: str) -> tuple[bool, str | None]:
    """Detect action→event→rule cycles within a trigger chain."""
    settings = get_settings()
    chain_key = f"{lead_id}:{trigger_id}"
    fingerprint = f"{rule_id}"
    with _lock:
        chain = _trigger_chain.setdefault(chain_key, [])
        if fingerprint in chain:
            return False, "rule_cycle_detected"
        if len(chain) >= settings.max_rule_chain_depth:
            return False, "rule_chain_depth_exceeded"
        chain.append(fingerprint)
    return True, None


def clear_trigger_chain(*, trigger_id: str, lead_id: str) -> None:
    with _lock:
        _trigger_chain.pop(f"{lead_id}:{trigger_id}", None)


def canary_eligible(*, rule: dict[str, Any], lead_id: str) -> tuple[bool, str | None]:
    """Return whether rule should execute for this lead (canary traffic split)."""
    status = rule.get("rollout_status", "active")
    if status == "active":
        return True, None
    if status != "canary":
        return False, "rule_not_in_canary_or_active"
    pct = float(rule.get("canary_percent") or get_settings().canary_default_percent)
    bucket = _canary_bucket(rule["rule_id"], lead_id)
    if bucket >= pct:
        return False, "canary_traffic_excluded"
    return True, None


def _canary_bucket(rule_id: str, lead_id: str) -> float:
    digest = hashlib.sha256(f"{rule_id}:{lead_id}".encode()).hexdigest()
    return (int(digest[:8], 16) % 10000) / 100.0


def record_canary_exposure(rule_id: str) -> None:
    with _lock:
        _canary_counters[rule_id] = _canary_counters.get(rule_id, 0) + 1


def canary_stats() -> dict[str, Any]:
    with _lock:
        return {"exposures_by_rule": dict(_canary_counters)}
