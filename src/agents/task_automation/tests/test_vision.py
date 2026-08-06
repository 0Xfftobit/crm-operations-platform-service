"""Vision document acceptance tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE, create_stage_change_rule


def test_vision_overview(client: TestClient) -> None:
    body = client.get(f"{BASE}/vision").json()
    assert body["role_in_engine_2"] == "execution_arm"
    assert "task.created" in body["produced_events"]
    assert "document.requested" in body["produced_events"]


def test_pre_approval_compound_automation(client: TestClient) -> None:
    """Stage → pre_approval spawns tasks, record hygiene, and doc requests."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Pre-approval bundle",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "compound",
            "task_type": "send_document",
            "delay_hours": 0,
            "assignee_strategy": "lead_owner",
            "priority": "urgent",
            "record_updates": {"flags": ["docs_pending"], "stage_field": "automation_status", "stage_value": "docs_requested"},
            "auto_document_set": True,
        },
    )
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-PA-1",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "agent_id": "lo:amy",
            },
        },
    ).json()
    assert len(result["created"]) >= 1
    assert len(result["record_updates"]) >= 1
    assert len(result["document_requests"]) >= 1
    assert result["document_requests"][0]["document_types"]
    assert result["sla_met"] is True

    events = client.get(f"{BASE}/vision/events").json()["events"]
    topics = {e["topic"] for e in events}
    assert "task.created" in topics
    assert "record.updated" in topics
    assert "document.requested" in topics


def test_idempotency_prevents_duplicate_writes(client: TestClient) -> None:
    create_stage_change_rule(client)
    payload = {"topic": "deal.stage_changed", "payload": {"lead_id": "L-idem", "stage": "underwriting", "agent_id": "lo:amy"}}
    headers = {"X-API-Key": "test-key", "X-Idempotency-Key": "idem-key-1"}
    first = client.post(f"{BASE}/events/ingest", json=payload, headers=headers).json()
    second = client.post(f"{BASE}/events/ingest", json=payload, headers=headers).json()
    assert second.get("idempotent_replay") is True
    assert first["created"][0]["task_id"] == second["created"][0]["task_id"]
    kpis = client.get(f"{BASE}/vision/kpis").json()
    assert kpis["idempotency_violations"] >= 1


def test_rule_simulate_dry_run(client: TestClient) -> None:
    rule = client.post(
        f"{BASE}/rules",
        json={
            "name": "Draft rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "processing"}],
            "task_type": "call",
            "rollout_status": "draft",
            "active": True,
        },
    ).json()
    sim = client.post(
        f"{BASE}/rules/{rule['rule_id']}/simulate",
        json={"topic": "deal.stage_changed", "payload": {"lead_id": "L-sim", "stage": "processing", "agent_id": "lo:bob"}},
    ).json()
    assert sim["planned"]
    assert not sim.get("created_tasks") or len(client.get(f"{BASE}/tasks").json()["tasks"]) == 0


def test_rule_publish_rollout(client: TestClient) -> None:
    rule = client.post(
        f"{BASE}/rules",
        json={
            "name": "Draft to active",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "call",
            "rollout_status": "draft",
        },
    ).json()
    blocked = client.post(f"{BASE}/rules/{rule['rule_id']}/publish")
    assert blocked.status_code == 409
    client.post(
        f"{BASE}/rules/{rule['rule_id']}/simulate",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-pub", "agent_id": "lo:amy"}},
    )
    canary = client.post(f"{BASE}/rules/{rule['rule_id']}/publish").json()
    assert canary["rollout_status"] == "canary"
    pub = client.post(f"{BASE}/rules/{rule['rule_id']}/publish").json()
    assert pub["rollout_status"] == "active"
    created = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-pub", "agent_id": "lo:amy"}},
    ).json()
    assert len(created["created"]) >= 1


def test_action_ledger_provenance(client: TestClient) -> None:
    create_stage_change_rule(client)
    client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": {"lead_id": "L-ledger", "stage": "underwriting", "agent_id": "lo:amy"}},
    )
    ledger = client.get(f"{BASE}/vision/ledger", params={"lead_id": "L-ledger"}).json()["entries"]
    assert any(e["action_type"] == "create_task" for e in ledger)
    assert all(e.get("immutable") for e in ledger)
    kpis = client.get(f"{BASE}/vision/kpis").json()
    assert kpis["ledger"]["completeness_pct"] >= 0


def test_event_latency_kpi(client: TestClient) -> None:
    create_stage_change_rule(client)
    result = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": {"lead_id": "L-lat", "stage": "underwriting", "agent_id": "lo:amy"}},
    ).json()
    assert result["latency_ms"] < 3000
    kpis = client.get(f"{BASE}/vision/kpis").json()
    assert "event_processing_latency_ms_p95" in kpis
