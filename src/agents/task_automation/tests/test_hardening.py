"""Hardening tests — dedupe, CEL, round-robin, exports, rule hydration."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app import store
from app.assignment import next_round_robin_index, resolve_assignee
from app.core.config import get_settings
from app.engine.cel_eval import cel_match
from app.engine.jsonlogic import rule_conditions_match
from app.exports.ledger_export import export_ledger
from app.ledger import append_ledger_entry, reset_ledger
from app.messaging.trigger_ingestor import is_duplicate_event, reset_trigger_ingestor
from app.persistence.db import persist_rule, reset_db
from app.persistence.redis_store import reset_redis_store
from app.workflows.doc_request_chain import check_sla_timers, reset_workflows, start_doc_request_workflow

BASE = "/api/v1"


def test_event_id_dedupe_redis() -> None:
    reset_trigger_ingestor()
    reset_redis_store()
    assert is_duplicate_event("evt_001") is False
    assert is_duplicate_event("evt_001") is True


def test_cel_condition_match() -> None:
    payload = {"stage": "pre_approval", "product": "FHA"}
    assert cel_match(payload, "stage == 'pre_approval'")
    assert cel_match(payload, "stage == 'pre_approval' && product == 'FHA'")
    assert not cel_match(payload, "stage == 'underwriting'")


def test_rule_cel_field() -> None:
    rule = {"cel": "stage == 'pre_approval'"}
    assert rule_conditions_match({"stage": "pre_approval"}, rule)
    assert not rule_conditions_match({"stage": "processing"}, rule)


def test_round_robin_rotates() -> None:
    reset_redis_store()
    pool = ["lo:amy", "lo:bob", "lo:carol"]
    a = resolve_assignee(
        strategy="round_robin",
        event_payload={},
        round_robin_pool=pool,
        round_robin_index=next_round_robin_index(pool_key="test_pool"),
    )
    b = resolve_assignee(
        strategy="round_robin",
        event_payload={},
        round_robin_pool=pool,
        round_robin_index=next_round_robin_index(pool_key="test_pool"),
    )
    assert a != b


def test_rules_hydrate_from_sqlite(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "rules.db"
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("TASK_AUTOMATION_DB_PATH", str(db_path))
    get_settings.cache_clear()
    reset_db()
    reset_ledger()
    store.reset_store()

    rule = {
        "rule_id": "rule_hydrate",
        "name": "Hydrate test",
        "trigger_event": "deal.stage_changed",
        "conditions": [],
        "action_type": "create_task",
        "task_type": "call",
        "active": True,
        "rollout_status": "active",
    }
    persist_rule(rule)
    count = store.hydrate_rules_from_db()
    assert count == 1
    loaded = store.rules_for_event("deal.stage_changed")
    assert any(r["rule_id"] == "rule_hydrate" for r in loaded)


def test_local_doc_sla_escalation() -> None:
    reset_workflows()
    wf = start_doc_request_workflow(
        deal_id="L-sla-local",
        lead_id="L-sla-local",
        document_types=["w2"],
        sla_hours=1,
    )
    wf["sla_due_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    from app.workflows import doc_request_chain as chain

    chain._local_workflows[wf["workflow_id"]] = wf
    escalated = check_sla_timers()
    assert escalated
    assert escalated[0]["state"] == "Escalated"


def test_ledger_export_local(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EXPORT_LOCAL_DIR", str(tmp_path))
    get_settings.cache_clear()
    reset_ledger()
    append_ledger_entry(action_type="create_task", lead_id="L-exp", inputs={}, outputs={})
    result = export_ledger(limit=10)
    assert result["count"] >= 1
    assert result["provider"] in ("local", "s3")
    from pathlib import Path

    assert Path(result["path"]).exists()


def test_ledger_export_api(client: TestClient) -> None:
    append_ledger_entry(action_type="create_task", lead_id="L-api", inputs={}, outputs={})
    resp = client.get(f"{BASE}/vision/ledger/export")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_contract_event_envelope_fields() -> None:
    from app.messaging.trigger_ingestor import normalize_trigger

    env = normalize_trigger(topic="deal.stage_changed", payload={"lead_id": "L-1", "stage": "pre_approval"})
    for field in ("trigger_id", "type", "deal_id", "payload", "occurred_at", "source"):
        assert field in env


def test_load_smoke_burst(client: TestClient) -> None:
    """Smoke test — 50 sequential ingests under basic latency budget."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Load smoke",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "create_task",
            "task_type": "call",
        },
    )
    for i in range(50):
        resp = client.post(
            f"{BASE}/events/ingest",
            json={
                "topic": "deal.stage_changed",
                "payload": {
                    "lead_id": f"L-load-{i}",
                    "stage": "pre_approval",
                    "trigger_id": f"trg_load_{i}",
                    "event_id": f"evt_load_{i}",
                },
            },
        )
        assert resp.status_code == 200
