"""Functional requirements acceptance tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def _us1_pre_approval_rules(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Pre-approval doc request",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "request_document",
            "auto_document_set": True,
            "priority": "high",
        },
    )
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Verify pre-approval docs",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "create_task",
            "task_type": "send_document",
            "task_title": "Verify pre-approval docs",
            "assignee_strategy": "processor",
            "sla_hours": 48,
            "priority": "urgent",
        },
    )


def test_us1_stage_driven_doc_request(client: TestClient) -> None:
    """US-1: Pre-Approval → document.requested + processor task with 48h SLA."""
    _us1_pre_approval_rules(client)
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-US1",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "processor_id": "proc:alice",
                "trigger_id": "trg_us1",
            },
        },
    ).json()
    assert len(result["document_requests"]) >= 1
    doc_types = result["document_requests"][0]["document_types"]
    assert "pay_stub" in doc_types or "w2" in doc_types

    tasks = [t for t in result["created"] if "Verify pre-approval" in t.get("title", "")]
    assert tasks
    assert tasks[0]["assignee_id"] == "proc:alice"
    assert result.get("run_id")

    ledger = client.get(f"{BASE}/vision/ledger", params={"lead_id": "L-US1"}).json()["entries"]
    assert any(e["action_type"] == "request_document" for e in ledger)
    assert any(e["action_type"] == "create_task" for e in ledger)


def test_us2_blast_radius_dry_run(client: TestClient) -> None:
    """US-2: Dry-run against historical events with zero side effects."""
    rule = client.post(
        f"{BASE}/rules",
        json={
            "name": "Blast radius rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
            "task_type": "call",
        },
    ).json()
    for i in range(3):
        client.post(
            f"{BASE}/events/ingest",
            json={
                "topic": "deal.stage_changed",
                "payload": {"lead_id": f"L-hist-{i}", "stage": "underwriting", "agent_id": "lo:amy", "trigger_id": f"t{i}"},
            },
        )
    before_tasks = len(client.get(f"{BASE}/tasks").json()["tasks"])
    blast = client.post(
        f"{BASE}/rules/{rule['rule_id']}/blast-radius",
        json={"days": 7},
    ).json()
    assert blast["events_evaluated"] >= 3
    assert blast["would_fire_count"] >= 1
    assert blast["side_effects"] is False
    after_tasks = len(client.get(f"{BASE}/tasks").json()["tasks"])
    assert after_tasks == before_tasks


def test_us3_llm_approval_feedback(client: TestClient) -> None:
    """US-3: LLM note staged pending approval; approval emits feedback."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "LLM note rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "processing"}],
            "action_type": "post_note",
            "use_llm_notes": True,
        },
    )
    client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-us3", "stage": "processing", "agent_id": "lo:amy", "trigger_id": "trg_us3"},
        },
    )
    pending = client.get(f"{BASE}/agents/llm/pending").json()["pending"]
    assert pending
    client.post(
        f"{BASE}/agents/llm/approve",
        json={"approval_id": pending[0]["approval_id"]},
        headers={"X-Role": "revops"},
    )
    evts = client.get(f"{BASE}/vision/events", params={"topic": "automation.llm.feedback"}).json()["events"]
    assert evts


def test_automation_run_query(client: TestClient) -> None:
    _us1_pre_approval_rules(client)
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-run", "stage": "pre_approval", "processor_id": "proc:bob", "trigger_id": "trg_run"},
        },
    ).json()
    run = client.get(f"/automation/v1/runs/{result['run_id']}").json()
    assert run["status"] == "completed"
    assert run.get("ledger_entries")


def test_functional_requirements_matrix(client: TestClient) -> None:
    body = client.get(f"{BASE}/functional-requirements").json()
    assert body["document"] == "Functional Requirements"
    assert len(body["functional_requirements"]) == 20
    assert body["implemented_count"] >= 17


def test_rule_conflict_resolution(client: TestClient) -> None:
    """FR-003: lower-priority conflicting rule loses."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Low priority call",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "call",
            "priority": "low",
        },
    )
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Urgent call",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "call",
            "priority": "urgent",
        },
    )
    result = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-conf", "agent_id": "lo:amy", "trigger_id": "trg_c"}},
    ).json()
    assert len(result["created"]) == 1
    assert any(c.get("reason") == "conflict_lost" for c in result.get("skipped", []))


def test_protected_class_proxy_blocked(client: TestClient) -> None:
    """BR-5: conditions on protected proxies are blocked."""
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Bad rule",
            "trigger_event": "lead.scored",
            "conditions": [{"field": "race", "operator": "eq", "value": "x"}],
            "task_type": "call",
        },
    )
    result = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.scored", "payload": {"lead_id": "L-br5", "race": "x", "agent_id": "lo:amy"}},
    ).json()
    assert not result.get("created")
    assert any(s.get("reason") == "protected_class_proxy_blocked" for s in result.get("skipped", []))


def test_task_completed_event(client: TestClient) -> None:
    task = client.post(
        f"{BASE}/tasks",
        json={"lead_id": "L-tc", "task_type": "call", "assignee_id": "lo:amy"},
    ).json()
    client.post(
        f"{BASE}/tasks/{task['task_id']}/complete",
        json={"outcome_notes": "Done", "outcome": "connected"},
    )
    evts = client.get(f"{BASE}/vision/events", params={"topic": "task.completed"}).json()["events"]
    assert evts
