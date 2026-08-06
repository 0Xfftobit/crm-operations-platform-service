"""System design acceptance tests — simulate API, runs, workflows, idempotency."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def test_system_design_endpoint(client: TestClient) -> None:
    body = client.get(f"{BASE}/system-design").json()
    assert body["document"] == "System Design"
    assert "trigger_ingestor" in body["components"]
    assert body["redis_keys"]["idempotency"] == "idem:{trigger_id}:{rule_id}:{action_key}"
    assert body["configuration"]["per_deal_action_budget"] == 25


def test_simulate_rule_design_shape(client: TestClient) -> None:
    rule = client.post(
        f"{BASE}/rules",
        json={
            "name": "Design simulate rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
            "task_type": "call",
        },
    ).json()
    for i in range(2):
        client.post(
            f"{BASE}/events/ingest",
            json={
                "topic": "deal.stage_changed",
                "payload": {
                    "lead_id": f"L-sim-{i}",
                    "stage": "underwriting",
                    "agent_id": "lo:amy",
                    "trigger_id": f"trg-sim-{i}",
                },
            },
        )
    result = client.post(
        f"/automation/v1/rules/{rule['rule_id']}/simulate",
        json={"lookback_days": 7, "sample_size": 10},
    ).json()
    assert "would_fire_count" in result
    assert "sample_actions" in result
    assert "blast_radius_warning" in result
    assert result["would_fire_count"] >= 1


def test_run_api_design_shape(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Run shape rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "request_document",
            "auto_document_set": True,
        },
    )
    ingest = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-run-shape",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "processor_id": "proc:alice",
                "trigger_id": "trg_run_shape",
            },
        },
    ).json()
    run = client.get(f"/automation/v1/runs/{ingest['run_id']}").json()
    assert run["status"] == "completed"
    assert run["trigger"]["type"] == "deal.stage_changed"
    assert run["trigger"]["deal_id"] == "L-run-shape"
    assert run["actions"]
    assert run["actions"][0]["outcome"] in ("executed", "deduped")
    assert "ledger_id" in run["actions"][0]


def test_event_id_dedupe(client: TestClient) -> None:
    payload = {
        "lead_id": "L-dedupe",
        "stage": "processing",
        "agent_id": "lo:amy",
        "event_id": "evt_unique_001",
        "trigger_id": "trg_dedupe_1",
    }
    first = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": payload},
    ).json()
    assert not first.get("duplicate")
    second = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": {**payload, "trigger_id": "trg_dedupe_2"}},
    ).json()
    assert second.get("duplicate") is True


def test_doc_request_workflow_started(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Doc workflow rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "request_document",
            "auto_document_set": True,
        },
    )
    client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-wf",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "trigger_id": "trg_wf",
            },
        },
    )
    workflows = client.get(f"{BASE}/system-design/workflows", params={"deal_id": "L-wf"}).json()
    assert workflows["workflows"]
    assert workflows["workflows"][0]["state"] == "AwaitingDocs"


def test_document_received_signal(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Signal rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "request_document",
            "document_types": ["pay_stub"],
            "auto_document_set": False,
        },
    )
    client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {"lead_id": "L-sig", "stage": "pre_approval", "product": "FHA", "trigger_id": "trg_sig"},
        },
    )
    wf_id = client.get(f"{BASE}/system-design/workflows", params={"deal_id": "L-sig"}).json()["workflows"][0]["workflow_id"]
    signal = client.post(
        f"{BASE}/events/document-received",
        json={"deal_id": "L-sig", "workflow_id": wf_id, "document_id": "doc-1", "doc_type": "pay_stub"},
    ).json()
    assert signal["state"] == "Completed"
    assert signal.get("verified") is True
