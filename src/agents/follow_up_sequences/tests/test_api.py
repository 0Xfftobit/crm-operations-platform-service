"""HTTP wiring + auth for the sequences router.

These tests drive the real ``/scheduler/tick`` at wall-clock time, so they run with the quiet-hours
window opened (the guardrail's quiet-hours enforcement itself is time-injected + covered
deterministically in test_pipeline / test_guardrails). Keeps the HTTP suite from flaking overnight.
"""
import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _open_quiet_hours(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS_END", "0")      # allowed all day → sends are deterministic
    monkeypatch.setenv("QUIET_HOURS_START", "24")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


CONTACT = {"contact_id": "c1", "name": "Ana", "property_name": "12 Oak", "agent_name": "Lee",
           "timezone": "America/New_York", "consent_basis": "express_written", "email": "a@x.com"}
SEQ = {"sequence_id": "hot", "name": "hot", "version": 1,
       "steps": [{"id": "s1", "channel": "sms", "delay": "PT0M", "template": "Hi {{lead_name}}"}],
       "stop_rules": ["replied", "converted"]}


def _setup(client, api_headers):
    assert client.post("/api/v1/contacts", json=CONTACT, headers=api_headers).status_code == 200
    assert client.post("/api/v1/sequences", json=SEQ, headers=api_headers).status_code == 200


def test_write_requires_api_key(client):
    assert client.post("/api/v1/sequences", json=SEQ).status_code == 401
    assert client.post("/api/v1/enrollments",
                       json={"contact_id": "c1", "sequence_id": "hot"}).status_code == 401


def test_enroll_tick_and_get(client, api_headers):
    _setup(client, api_headers)
    r = client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                    headers=api_headers)
    assert r.status_code == 200 and r.json()["status"] == "enrolled"
    eid = r.json()["enrollment"]["enrollment_id"]
    t = client.post("/api/v1/scheduler/tick", headers=api_headers).json()
    assert t["processed"] >= 1
    got = client.get(f"/api/v1/enrollments/{eid}").json()
    assert got["enrollment_id"] == eid and "history" in got


def test_get_sequence_and_404(client, api_headers):
    _setup(client, api_headers)
    assert client.get("/api/v1/sequences/hot").json()["name"] == "hot"
    assert client.get("/api/v1/sequences/nope").status_code == 404
    assert client.get("/api/v1/enrollments/nope").status_code == 404


def test_engagement_webhook_reply_pauses(client, api_headers):
    _setup(client, api_headers)
    eid = client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                      headers=api_headers).json()["enrollment"]["enrollment_id"]
    client.post("/api/v1/scheduler/tick", headers=api_headers)
    client.post("/api/v1/webhooks/engagement",
                json={"contact_id": "c1", "enrollment_id": eid, "kind": "reply"},
                headers=api_headers)
    assert client.get(f"/api/v1/enrollments/{eid}").json()["state"] == "paused"


def test_opt_out_and_consent_endpoints(client, api_headers):
    _setup(client, api_headers)
    client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                headers=api_headers)
    assert client.post("/api/v1/contacts/c1/opt-out", headers=api_headers).json()["opted_out"]
    # re-enroll blocked after opt-out
    assert client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                       headers=api_headers).json()["status"] == "blocked"


def test_stage_change_and_triggers(client, api_headers):
    _setup(client, api_headers)
    client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                headers=api_headers)
    sc = client.post("/api/v1/stage-changed", json={"contact_id": "c1", "stage": "application"},
                     headers=api_headers)
    assert sc.json()["stopped"] >= 1
    ls = client.post("/api/v1/triggers/lead-scored",
                     json={"contact_id": "c1", "score": 90, "sequence_id": "hot"},
                     headers=api_headers)
    assert ls.status_code == 200


def test_analytics_ledger_events(client, api_headers):
    _setup(client, api_headers)
    client.post("/api/v1/enrollments", json={"contact_id": "c1", "sequence_id": "hot"},
                headers=api_headers)
    client.post("/api/v1/scheduler/tick", headers=api_headers)
    assert "steps" in client.get("/api/v1/analytics/sequences/hot").json()
    assert len(client.get("/api/v1/ledger").json()["ledger"]) >= 1
    ev = client.get("/api/v1/events", params={"event_type": "outreach.sent"}).json()["events"]
    assert len(ev) >= 1
    assert "dlq" in client.get("/api/v1/events/dlq").json()
