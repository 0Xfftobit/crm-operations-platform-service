"""Static-body Zoho relay — request bodies carry no dynamic values.

Dynamic data (ids, stages, amounts, emails, probabilities) is resolved
inside the endpoint from the fetched Zoho record and never appears in the
webhook request body.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import zoho_relay
from src.api.main import app

client = TestClient(app)

STATIC_BODY = {"journey": "LEAD"}


class FakeUpstreams:
    """Capture agent calls; serve a canned OK response."""

    def __init__(self):
        self.calls = []

    async def post_agent(self, client_, agent, path, payload, headers):
        self.calls.append((agent, path, payload))
        return 200, {"ok": True}


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeUpstreams()

    async def fake_get_record(module, record_id):
        assert module in ("Leads", "Deals", "Tasks")
        base = {"id": record_id}
        if module == "Leads":
            base.update(
                {
                    "Email": "lead@example.com",
                    "Full_Name": "Test Lead",
                    "Company": "Acme",
                }
            )
        if module == "Deals":
            base.update(
                {
                    "Stage": "Closed Won",
                    "Amount": 250000,
                    "Probability": 80,
                    "Contact_Name": {"id": "CONTACT-1", "name": "Test"},
                    "Owner": {"id": "OWNER-1", "email": "lo@example.com"},
                }
            )
        if module == "Tasks":
            base.update(
                {
                    "Status": "Completed",
                    "Subject": "Call borrower",
                    "Due_Date": "2026-08-30",
                    "Owner": {"id": "OWNER-1", "email": "lo@example.com"},
                    "What_Id": {"id": "DEAL-7", "name": "Probe"},
                }
            )
        return base

    monkeypatch.setattr(zoho_relay.zoho_client, "get_record", fake_get_record)
    monkeypatch.setattr(zoho_relay, "_post_agent", fake.post_agent)
    return fake


def _patch_record(monkeypatch, record):
    async def fixed(module, record_id):
        return {"id": record_id, **record}

    monkeypatch.setattr(zoho_relay.zoho_client, "get_record", fixed)


def test_static_body_has_no_dynamic_values():
    """The accepted request body is a pure constant."""
    assert set(STATIC_BODY) == {"journey"}


def test_lead_journey_resolves_dynamics_into_trigger(fake):
    resp = client.post(
        "/zoho/event", json=STATIC_BODY, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "LEAD-42"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["zoho_fetch"] == "live"
    assert [(c[0], c[1]) for c in fake.calls] == [
        ("follow_up_sequences", "/api/v1/triggers/lead-scored")
    ]
    trigger = fake.calls[0][2]
    assert trigger["sequence_id"] == "default-lead-nurture"
    assert trigger["contact_id"] == "LEAD-42"
    assert trigger["context"]["email"] == "lead@example.com"  # resolved inside
    assert trigger["context"]["source"] == "zoho-lead-create"
    assert [s["status"] for s in body["steps"]] == [200]


def test_deal_open_stage_runs_two_steps_only(fake, monkeypatch):
    _patch_record(
        monkeypatch,
        {"Stage": "Proposal", "Amount": 100000, "Probability": 40,
         "Contact_Name": {"id": "CONTACT-2", "name": "Open"}},
    )
    resp = client.post(
        "/zoho/event", json={"journey": "DEAL"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "DEAL-1"}
    )
    assert resp.status_code == 200
    assert [(c[0], c[1]) for c in fake.calls] == [
        ("follow_up_sequences", "/api/v1/stage-changed"),
        ("compliance_monitor", "/api/v1/disclosures/stage"),
    ]
    stage_changed = fake.calls[0][2]
    assert stage_changed["stage"] == "Proposal"
    assert stage_changed["contact_id"] == "CONTACT-2"  # resolved inside, not from body
    assert fake.calls[1][2] == {"deal_id": "DEAL-1", "stage": "Proposal"}


def test_deal_closed_won_adds_feedback_outcome(fake):
    resp = client.post(
        "/zoho/event", json={"journey": "DEAL"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "DEAL-7"}
    )
    assert resp.status_code == 200
    agents_paths = [(c[0], c[1]) for c in fake.calls]
    assert agents_paths == [
        ("follow_up_sequences", "/api/v1/stage-changed"),
        ("compliance_monitor", "/api/v1/disclosures/stage"),
        ("feedback_loop", "/api/v1/outcomes"),
    ]
    outcome = fake.calls[2][2]
    assert outcome["engine"] == "lead_scoring"
    assert outcome["entity_id"] == "DEAL-7"
    assert outcome["prediction"] == 0.8  # Probability 80 → 0.8, computed inside
    assert outcome["outcome"] == 1.0
    assert outcome["meta"] == {"stage": "Closed Won", "amount": 250000.0}


def test_deal_closed_lost_records_zero_outcome(fake, monkeypatch):
    _patch_record(
        monkeypatch,
        {"Stage": "Closed Lost to Competition", "Amount": 90000, "Probability": 25,
         "Contact_Name": {"id": "CONTACT-3", "name": "Lost"}},
    )
    resp = client.post(
        "/zoho/event", json={"journey": "DEAL"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "DEAL-9"}
    )
    assert resp.status_code == 200
    outcome = next(c[2] for c in fake.calls if c[1] == "/api/v1/outcomes")
    assert outcome["prediction"] == 0.25
    assert outcome["outcome"] == 0.0


def test_task_completed_ingests_and_measures(fake):
    resp = client.post(
        "/zoho/event", json={"journey": "TASK"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "TASK-3"}
    )
    assert resp.status_code == 200
    assert [(c[0], c[1]) for c in fake.calls] == [
        ("task_automation", "/api/v1/events/ingest"),
        ("performance_analytics", "/api/v1/metrics"),
    ]
    ingest = fake.calls[0][2]
    assert ingest["topic"] == "task.completed"
    assert ingest["payload"]["task_id"] == "TASK-3"
    assert ingest["payload"]["owner"] == "OWNER-1"
    assert ingest["payload"]["related_to"] == "DEAL-7"  # What_Id lookup id
    assert ingest["payload"]["source"] == "zoho-task"
    metric = fake.calls[1][2]
    assert metric["metric"] == "task_event"
    assert metric["dimensions"] == {"agent": "task_automation", "status": "Completed"}
    assert metric["meta"] == {"task_id": "TASK-3"}


def test_task_other_status_uses_manual_topic(fake, monkeypatch):
    _patch_record(monkeypatch, {"Status": "In Progress", "Subject": "Follow up"})
    resp = client.post(
        "/zoho/event", json={"journey": "TASK"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "TASK-4"}
    )
    assert resp.status_code == 200
    ingest = fake.calls[0][2]
    assert ingest["topic"] == "manual"
    assert ingest["payload"]["status"] == "In Progress"
    assert ingest["payload"]["related_to"] == ""


def test_unknown_journey_rejected(fake):
    resp = client.post(
        "/zoho/event", json={"journey": "NOPE"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "X"}
    )
    assert resp.status_code == 400


def test_missing_record_header_rejected(fake):
    assert (
        client.post(
            "/zoho/event", json=STATIC_BODY, headers={"X-API-Key": "change-me-dev-key"}
        ).status_code
        == 400
    )


def test_zoho_fallback_still_runs_journey(fake, monkeypatch):
    async def minimal(module, record_id):
        return {"id": record_id}

    monkeypatch.setattr(zoho_relay.zoho_client, "get_record", minimal)
    resp = client.post(
        "/zoho/event", json=STATIC_BODY, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "LEAD-1"}
    )
    assert resp.status_code == 200
    assert resp.json()["zoho_fetch"] == "fallback"
    assert len(fake.calls) == 1  # journey still completes with defaults


def test_transport_failure_does_not_abort_chain(fake, monkeypatch):
    async def flaky(client_, agent, path, payload, headers):
        fake.calls.append((agent, path, payload))
        if agent == "follow_up_sequences":
            return 0, "ConnectError"
        return 200, {"ok": True}

    monkeypatch.setattr(zoho_relay, "_post_agent", flaky)
    resp = client.post(
        "/zoho/event", json={"journey": "DEAL"}, headers={"X-API-Key": "change-me-dev-key", "X-CRM-Record-Id": "DEAL-7"}
    )
    statuses = [s["status"] for s in resp.json()["steps"]]
    assert statuses == [0, 200, 200]


def test_relay_requires_gateway_api_key(fake):
    """Without the gateway X-API-Key the relay is rejected like the proxy."""
    resp = client.post(
        "/zoho/event", json=STATIC_BODY, headers={"X-CRM-Record-Id": "LEAD-1"}
    )
    assert resp.status_code == 401
