"""Acceptance criteria tests."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.business_hours import calculate_due_at
from tests.conftest import BASE, create_stage_change_rule


def test_ac1_tasks_auto_created_on_trigger(client: TestClient) -> None:
    create_stage_change_rule(client)
    r = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-100",
                "stage": "underwriting",
                "agent_id": "lo:amy",
            },
        },
    )
    body = r.json()
    assert len(body["created"]) == 1
    task = body["created"][0]
    assert task["task_type"] == "call"
    assert task["assignee_id"] == "lo:amy"
    assert task["lead_id"] == "L-100"


def test_ac2_due_date_respects_business_hours(client: TestClient) -> None:
    # Friday 4pm Chicago + 2h delay should roll to Monday 10am, not Saturday night
    tz_base = datetime(2026, 6, 19, 21, 0, tzinfo=UTC)  # Fri 4pm CDT ≈ 21:00 UTC
    due = calculate_due_at(
        base=tz_base,
        delay_hours=2,
        timezone="America/Chicago",
        business_start_hour=9,
        business_end_hour=17,
    )
    local = due.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    assert local.weekday() < 5
    assert 9 <= local.hour < 17


def test_ac3_agent_queue_sorted_by_urgency(client: TestClient) -> None:
    create_stage_change_rule(client)
    client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": {"lead_id": "L-1", "stage": "underwriting", "agent_id": "lo:amy"}},
    )
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Low priority email",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "send_email",
            "delay_hours": 1,
            "assignee_strategy": "lead_owner",
            "priority": "low",
        },
    )
    client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-2", "agent_id": "lo:amy"}},
    )
    dash = client.get(f"{BASE}/dashboard/agent/lo:amy").json()
    assert dash["total_pending"] >= 2
    tasks = dash["tasks"]
    priorities = [t["priority"] for t in tasks]
    assert priorities.index("high") < priorities.index("low")


def test_ac4_overdue_red_alert(client: TestClient) -> None:
    from app import store

    past = datetime(2020, 1, 1, 12, 0, tzinfo=UTC).isoformat()
    store.create_crm_task(
        lead_id="L-old",
        task_type="call",
        assignee_id="lo:amy",
        due_at=past,
        priority="normal",
    )
    dash = client.get(f"{BASE}/dashboard/agent/lo:amy").json()
    assert dash["overdue_alert"] is True
    assert dash["alert_level"] == "red"
    assert dash["overdue_count"] >= 1
    overdue_tasks = [t for t in dash["tasks"] if t.get("overdue")]
    assert overdue_tasks[0]["alert_level"] == "red"


def test_ac5_manager_view_all_agents(client: TestClient) -> None:
    from app import store

    store.create_crm_task(lead_id="L-1", task_type="call", assignee_id="lo:amy", due_at="2030-01-01T12:00:00+00:00")
    store.create_crm_task(lead_id="L-2", task_type="call", assignee_id="lo:bob", due_at="2030-01-01T12:00:00+00:00")
    mgr = client.get(f"{BASE}/dashboard/manager").json()
    agent_ids = {a["assignee_id"] for a in mgr["agents"]}
    assert "lo:amy" in agent_ids
    assert "lo:bob" in agent_ids
    assert mgr["total_active_tasks"] >= 2


def test_ac6_completion_logged_with_notes(client: TestClient) -> None:
    create_stage_change_rule(client)
    created = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "deal.stage_changed", "payload": {"lead_id": "L-99", "stage": "underwriting", "agent_id": "lo:amy"}},
    ).json()["created"][0]
    task_id = created["task_id"]
    done = client.post(
        f"{BASE}/tasks/{task_id}/complete",
        json={"outcome_notes": "Spoke with borrower, docs requested", "outcome": "connected"},
    ).json()
    assert done["status"] == "completed"
    assert done["outcome_notes"] == "Spoke with borrower, docs requested"
    assert done["completed_at"]
    audit = client.get(f"{BASE}/audit/L-99").json()["entries"]
    actions = [e["action"] for e in audit]
    assert "task.completed" in actions
    assert "task.created" in actions


def test_conflict_detection_skips_duplicate(client: TestClient) -> None:
    create_stage_change_rule(client)
    payload = {"lead_id": "L-dup", "stage": "underwriting", "agent_id": "lo:amy"}
    first = client.post(f"{BASE}/events/ingest", json={"topic": "deal.stage_changed", "payload": payload}).json()
    second = client.post(f"{BASE}/events/ingest", json={"topic": "deal.stage_changed", "payload": payload}).json()
    assert len(first["created"]) == 1
    assert len(second["created"]) == 0
    assert second["skipped"][0]["reason"] == "duplicate_within_window"


def test_meeting_task_gets_calendar_stub(client: TestClient) -> None:
    client.post(
        f"{BASE}/rules",
        json={
            "name": "Schedule meeting on assignment",
            "trigger_event": "lead.assigned",
            "conditions": [],
            "task_type": "schedule_meeting",
            "delay_hours": 24,
            "assignee_strategy": "lead_owner",
        },
    )
    created = client.post(
        f"{BASE}/events/ingest",
        json={"topic": "lead.assigned", "payload": {"lead_id": "L-mt", "agent_id": "lo:amy"}},
    ).json()["created"][0]
    assert created["calendar_event_id"]
    assert created["calendar_event_id"].startswith("cal_")
