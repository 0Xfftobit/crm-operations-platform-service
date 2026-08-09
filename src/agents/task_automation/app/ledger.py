"""Immutable action ledger — append-only audit store (§7)."""
from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from app.idempotency import inputs_hash
from app.messaging.kafka_bus import publish_kafka
from app.persistence.db import persist_ledger_entry as persist_ledger_db

_ledger: list[dict[str, Any]] = []
_ledger_keys: set[tuple[str | None, str | None, str | None]] = set()

_lock = RLock()


def _idempotency_tuple(
    trigger_id: str | None, rule_id: str | None, action_key: str | None,
) -> tuple[str | None, str | None, str | None] | None:
    if trigger_id and rule_id and action_key:
        return (trigger_id, rule_id, action_key)
    return None


def _find_in_memory(
    trigger_id: str | None, rule_id: str | None, action_key: str | None,
) -> dict[str, Any] | None:
    key = _idempotency_tuple(trigger_id, rule_id, action_key)
    if not key:
        return None
    for entry in reversed(_ledger):
        if _idempotency_tuple(entry.get("trigger_id"), entry.get("rule_id"), entry.get("action_key")) == key:
            return entry
    return None


def append_ledger_entry(
    *,
    action_type: str,
    lead_id: str,
    rule_id: str | None = None,
    rule_version: str | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    actor: str = "system",
    outcome: str = "executed",
    provenance: dict[str, Any] | None = None,
    trigger_id: str | None = None,
    action_key: str | None = None,
    deal_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    idem_key = _idempotency_tuple(trigger_id, rule_id, action_key)

    with _lock:
        if idem_key and idem_key in _ledger_keys:
            existing = _find_in_memory(trigger_id, rule_id, action_key)
            if existing:
                return {**existing, "outcome": "deduped", "duplicate": True}

    inp = inputs or {}
    entry = {
        "ledger_id": f"ldg_{uuid4().hex[:16]}",
        "action_type": action_type,
        "lead_id": lead_id,
        "deal_id": deal_id or lead_id,
        "rule_id": rule_id,
        "rule_version": rule_version,
        "inputs": inp,
        "inputs_hash": inputs_hash(inp),
        "outputs": outputs or {},
        "actor": actor,
        "outcome": outcome,
        "provenance": provenance or {},
        "trigger_id": trigger_id,
        "action_key": action_key,
        "detail": detail or {},
        "recorded_at": datetime.now(UTC).isoformat(),
        "immutable": True,
        "retention_years": 7,
    }

    persist_result = persist_ledger_db(entry)

    with _lock:
        if idem_key and idem_key in _ledger_keys:
            existing = _find_in_memory(trigger_id, rule_id, action_key)
            if existing:
                return {**existing, "outcome": "deduped", "duplicate": True, "ledger_id": persist_result["ledger_id"]}

        if persist_result.get("duplicate"):
            existing = persist_result.get("existing") or _find_in_memory(trigger_id, rule_id, action_key)
            if existing:
                result: dict[str, Any] = {**existing, "outcome": "deduped", "duplicate": True, "ledger_id": persist_result["ledger_id"]}
            else:
                entry["outcome"] = "deduped"
                entry["duplicate"] = True
                entry["ledger_id"] = persist_result["ledger_id"]
                result = entry
        else:
            result = entry

        if idem_key:
            _ledger_keys.add(idem_key)
        _ledger.append(result)

    publish_kafka(
        "action.ledger.appended",
        {
            "ledger_id": result["ledger_id"],
            "action_type": action_type,
            "lead_id": lead_id,
            "rule_id": rule_id,
            "outcome": result["outcome"],
        },
        key=lead_id,
    )
    return result


def list_ledger(*, lead_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        rows = list(_ledger)
    if lead_id:
        rows = [e for e in rows if e.get("lead_id") == lead_id]
    return sorted(rows, key=lambda e: e["recorded_at"], reverse=True)[:limit]


def ledger_completeness() -> dict[str, Any]:
    with _lock:
        total = len(_ledger)
        with_provenance = sum(
            1 for e in _ledger
            if e.get("provenance") and e.get("rule_id") and e.get("inputs_hash")
        )
    return {
        "total_entries": total,
        "with_full_provenance": with_provenance,
        "completeness_pct": round(100.0 * with_provenance / total, 2) if total else 100.0,
    }


def reset_ledger() -> None:
    with _lock:
        _ledger.clear()
        _ledger_keys.clear()
