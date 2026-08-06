"""Project requirements acceptance tests (§10)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def _pre_approval_rule(client: TestClient) -> dict:
    return client.post(
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
            "record_updates": {
                "flags": ["docs_pending"],
                "stage_field": "automation_status",
                "stage_value": "docs_requested",
            },
            "auto_document_set": True,
        },
    ).json()


def test_requirements_pre_approval_acceptance(client: TestClient) -> None:
    """§10: Pre-Approval → doc+processor tasks within p95≤3s, once, ledgered."""
    _pre_approval_rule(client)
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-REQ-PA",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "agent_id": "lo:amy",
                "trigger_id": "trg_pa_1",
            },
        },
    ).json()
    assert len(result["created"]) >= 1
    assert len(result["document_requests"]) >= 1
    assert result["sla_met"] is True

    ledger = client.get(f"{BASE}/vision/ledger", params={"lead_id": "L-REQ-PA"}).json()["entries"]
    assert any(e["action_type"] == "create_task" for e in ledger)
    assert any(e["action_type"] == "request_document" for e in ledger)
    assert all(e.get("inputs_hash") for e in ledger if e["action_type"] in ("create_task", "request_document"))


def test_action_level_idempotency_on_replay(client: TestClient) -> None:
    """§10: Replay produces no duplicate side effects per (trigger,rule,action)."""
    _pre_approval_rule(client)
    payload = {
        "topic": "deal.stage_changed",
        "payload": {
            "lead_id": "L-replay",
            "stage": "pre_approval",
            "product": "CONVENTIONAL_30YR",
            "agent_id": "lo:amy",
            "trigger_id": "trg_replay_fixed",
        },
    }
    first = client.post(f"{BASE}/events/ingest", json=payload).json()
    second = client.post(f"{BASE}/events/ingest", json=payload).json()
    assert len(first["created"]) >= 1
    assert len(second.get("idempotent_skips", [])) >= 1 or len(second.get("created") or []) == len(first["created"])
    tasks = client.get(f"{BASE}/tasks", params={"lead_id": "L-replay"}).json()["tasks"]
    assert len(tasks) == len(first["created"])


def test_publish_requires_dry_run_and_canary(client: TestClient) -> None:
    """§10: Rule cannot reach production without dry-run + canary."""
    rule = client.post(
        f"{BASE}/rules",
        json={
            "name": "Gated rule",
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
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-gate", "agent_id": "lo:amy"}},
    )
    canary = client.post(f"{BASE}/rules/{rule['rule_id']}/publish").json()
    assert canary["rollout_status"] == "canary"

    active = client.post(f"{BASE}/rules/{rule['rule_id']}/publish").json()
    assert active["rollout_status"] == "active"


def test_ledger_inputs_hash(client: TestClient) -> None:
    _pre_approval_rule(client)
    client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-hash", "stage": "pre_approval", "agent_id": "lo:amy", "trigger_id": "trg_h1"},
        },
    )
    entries = client.get(f"{BASE}/vision/ledger", params={"lead_id": "L-hash"}).json()["entries"]
    assert entries
    assert all("inputs_hash" in e for e in entries)


def test_llm_write_blocked_without_approval(client: TestClient) -> None:
    """§10: LLM write blocked unless confidence≥threshold AND approval."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "LLM notes rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "processing"}],
            "task_type": "send_document",
            "use_llm_notes": True,
        },
    )
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-llm", "stage": "processing", "agent_id": "lo:amy", "trigger_id": "trg_llm"},
        },
    ).json()
    assert any(s.get("reason") == "llm_write_blocked_pending_approval" for s in result.get("skipped", []))
    assert not result.get("created")

    pending = client.get(f"{BASE}/agents/llm/pending").json()["pending"]
    assert pending
    approval_id = pending[0]["approval_id"]
    client.post(
        f"{BASE}/agents/llm/approve",
        json={"approval_id": approval_id},
        headers={"X-Role": "revops"},
    )


def test_jsonlogic_conditions(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "JSONLogic rule",
            "trigger_event": "lead.scored",
            "jsonlogic": {"and": [{"==": [{"var": "grade"}, "A"]}, {">=": [{"var": "score"}, 80]}]},
            "task_type": "call",
        },
    )
    match = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.scored", "payload": {"lead_id": "L-jl", "grade": "A", "score": 90, "agent_id": "lo:amy"}},
    ).json()
    assert len(match["created"]) >= 1

    no_match = client.post(
        f"{BASE}/events/simulate",
        json={"topic": "lead.scored", "payload": {"lead_id": "L-jl2", "grade": "C", "score": 50}},
    ).json()
    assert not no_match.get("planned")


def test_kafka_consume_stub(client: TestClient) -> None:
    _pre_approval_rule(client)
    msg = client.post(
        f"{BASE}/triggers/kafka/consume",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-kafka", "stage": "pre_approval", "agent_id": "lo:amy"},
            "offset": 100,
            "partition": 0,
        },
    ).json()
    assert msg["status"] == "processed"


def test_dlq_replay(client: TestClient) -> None:
    from app.dlq import enqueue

    row = enqueue(
        topic="deal.stage_changed",
        payload={"lead_id": "L-dlq", "stage": "underwriting", "agent_id": "lo:amy", "trigger_id": "trg_dlq"},
        error="simulated failure",
    )
    _pre_approval_rule(client)
    client.post(
        f"{BASE}/rules",
        json={
            "name": "UW call",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
            "task_type": "call",
        },
    )
    replay = client.post(
        f"{BASE}/vision/dlq/{row['dlq_id']}/replay",
        headers={"X-Role": "sre"},
    ).json()
    assert replay["status"] == "replayed"


def test_requirements_matrix(client: TestClient) -> None:
    body = client.get(f"{BASE}/requirements").json()
    assert body["engine"] == "Task Automation Engine"
    assert len(body["acceptance_criteria"]) >= 5
    assert any(m["phase"] == "P0" for m in body["milestones"])
