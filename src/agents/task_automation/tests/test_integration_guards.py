"""Integration clients, FUS fan-out, DLQ replay/idempotency, and guardrail behavior."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients import followup_client
from app.core.config import Settings, get_settings
from app.dlq import enqueue, get_dlq_entry, list_dlq, mark_replayed
from app.engine.action_executor import evaluate_rules
from app.guards import (
    canary_eligible,
    canary_stats,
    check_action_budget,
    check_loop_guard,
    clear_trigger_chain,
    record_actions,
    record_canary_exposure,
)
from app.integrations import activity_runner
from app.integrations import calendar as calendar_client
from app.integrations import crm as crm_client
from app.integrations import feature_store as fs_client
from app.persistence import redis_store


def _mock_httpx_client(*, status: int = 200, json_body: Any | None = None, method_exc: Exception | None = None, captured: dict | None = None):
    """Return a fake ``httpx.Client`` class that records and/or returns a canned response."""
    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()
            response = MagicMock()
            response.status_code = status
            response.is_success = 200 <= status < 300
            response.text = str(json_body) if json_body is not None else ""
            response.json.return_value = json_body if json_body is not None else {}
            if 200 <= status < 400:
                response.raise_for_status.return_value = None
            else:
                response.raise_for_status.side_effect = Exception("HTTP error")

            def _handler(method: str):
                def _call(*args, **kwargs):
                    if captured is not None:
                        captured["method"] = method
                        captured["url"] = args[0] if args else None
                        captured["json"] = kwargs.get("json")
                        captured["headers"] = kwargs.get("headers")
                        captured["params"] = kwargs.get("params")
                    if method_exc is not None:
                        raise method_exc
                    return response

                return _call

            for m in ("post", "get", "patch"):
                getattr(mock, m).side_effect = _handler(m)
            return mock

        def __exit__(self, *args, **kwargs):  # noqa: ARG002
            return False

    return FakeClient


def _fus_settings(**overrides) -> Settings:
    defaults = {
        "env": "test",
        "api_key": "test-key",
        "follow_up_sequences_url": "http://mock-fus:8000",
        "follow_up_sequences_api_key": "fus-key",
        "fus_fanout_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# CRM / calendar / feature-store integration clients
# ---------------------------------------------------------------------------

def test_crm_apply_record_updates_uses_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-005: CRM service URL configured → real PATCH with X-API-Key."""
    monkeypatch.setenv("CRM_SERVICE_URL", "http://mock-crm")
    monkeypatch.setenv("CRM_API_KEY", "crm-key")
    get_settings.cache_clear()

    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"ok": True}, captured=captured))

    result = crm_client.apply_record_updates(
        lead_id="L-CRM",
        updates={"stage_field": "automation_status", "stage_value": "docs_requested", "flags": ["pending"]},
        event_payload={"agent_id": "lo:amy"},
    )

    assert result["provider"] == "crm_http"
    assert result["ok"] is True
    assert captured["method"] == "patch"
    assert captured["url"] == "http://mock-crm/leads/L-CRM"
    assert captured["headers"].get("X-API-Key") == "crm-key"
    assert captured["json"]["automation_status"] == "docs_requested"
    assert captured["json"]["flags"] == ["pending"]


def test_crm_apply_record_updates_falls_back_to_stub_when_url_unset() -> None:
    """FR-005: no CRM_SERVICE_URL → in-process synthetic record, no HTTP."""
    get_settings.cache_clear()
    result = crm_client.apply_record_updates(
        lead_id="L-CRM",
        updates={"stage_field": "status", "stage_value": "funded"},
        event_payload={},
    )
    assert result["provider"] == "crm_stub"
    assert result["lead_id"] == "L-CRM"
    assert result["applied"]["status"] == "funded"


def test_crm_apply_record_updates_falls_back_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-005: CRM returns 500 → log warning and return stub."""
    monkeypatch.setenv("CRM_SERVICE_URL", "http://mock-crm")
    monkeypatch.setenv("CRM_API_KEY", "crm-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(status=500))

    result = crm_client.apply_record_updates(lead_id="L-CRM", updates={"stage_field": "status", "stage_value": "funded"}, event_payload={})
    assert result["provider"] == "crm_stub"


def test_crm_assign_owner_uses_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_SERVICE_URL", "http://mock-crm")
    get_settings.cache_clear()
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"assigned": True}, captured=captured))

    result = crm_client.assign_owner(lead_id="L-CRM", owner_id="lo:amy", reason="rule:r1")
    assert result["provider"] == "crm_http"
    assert captured["method"] == "post"
    assert captured["url"] == "http://mock-crm/leads/L-CRM/assign"
    assert captured["json"]["owner_id"] == "lo:amy"


def test_crm_post_note_uses_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRM_SERVICE_URL", "http://mock-crm")
    get_settings.cache_clear()
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"posted": True}, captured=captured))

    result = crm_client.post_note(lead_id="L-CRM", body="call made", author="lo:amy")
    assert result["provider"] == "crm_http"
    assert captured["method"] == "post"
    assert captured["url"] == "http://mock-crm/leads/L-CRM/notes"
    assert captured["json"]["body"] == "call made"


def test_calendar_create_event_uses_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calendar API integration: configured service is called with X-API-Key."""
    monkeypatch.setenv("CALENDAR_SERVICE_URL", "http://mock-calendar")
    monkeypatch.setenv("CALENDAR_API_KEY", "cal-key")
    get_settings.cache_clear()

    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"event_id": "evt-1"}, captured=captured))

    result = calendar_client.create_calendar_event(
        lead_id="L-CAL",
        assignee_id="lo:amy",
        due_at="2025-01-01T10:00:00",
        title="Meeting",
        payload={"location": "zoom"},
    )

    assert result["provider"] == "calendar_http"
    assert result["event_id"] == "evt-1"
    assert captured["method"] == "post"
    assert captured["url"] == "http://mock-calendar/events"
    assert captured["headers"].get("X-API-Key") == "cal-key"
    assert captured["json"]["lead_id"] == "L-CAL"
    assert captured["json"]["payload"]["location"] == "zoom"


def test_calendar_create_event_falls_back_to_stub_when_url_unset() -> None:
    get_settings.cache_clear()
    result = calendar_client.create_calendar_event(
        lead_id="L-CAL", assignee_id="lo:amy", due_at="2025-01-01T10:00:00", title="Meeting"
    )
    assert result["provider"] == "stub"
    assert result["lead_id"] == "L-CAL"
    assert result["calendar_event_id"].startswith("cal_")


def test_feature_store_fetches_via_http_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feature Store integration: enrichment data fetched via GET with X-API-Key."""
    monkeypatch.setenv("FEATURE_STORE_URL", "http://mock-fs")
    monkeypatch.setenv("FEATURE_STORE_API_KEY", "fs-key")
    get_settings.cache_clear()

    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"loan_amount": 450_000}, captured=captured))

    result = fs_client.fetch_features(
        deal_id="L-FS",
        lead_id="L-FS",
        product="CONVENTIONAL",
        stage="pre_approval",
    )

    assert result["_provider"] == "feature_store_http"
    assert result["loan_amount"] == 450_000
    assert captured["method"] == "get"
    assert captured["url"] == "http://mock-fs/features/L-FS"
    assert captured["headers"].get("X-API-Key") == "fs-key"
    assert captured["params"]["product"] == "CONVENTIONAL"
    assert captured["params"]["stage"] == "pre_approval"


def test_feature_store_falls_back_to_in_process_stub() -> None:
    get_settings.cache_clear()
    fs_client.seed_features("L-FS", {"loan_amount": 500_000, "product_type": "FHA"})
    result = fs_client.fetch_features(deal_id="L-FS", product="FHA", stage="pre_approval")
    assert result["_provider"] == "in_process_feature_store"
    assert result["loan_amount"] == 500_000
    assert result["product_type"] == "FHA"


def test_activity_runner_routes_crm_update_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activity runner executes connector activities through the integration client."""
    monkeypatch.setenv("CRM_SERVICE_URL", "http://mock-crm")
    monkeypatch.setenv("CRM_API_KEY", "crm-key")
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.1")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"updated": True}))

    result = activity_runner.execute_connector_activity(
        "update_record",
        {"lead_id": "L-ACT", "updates": {"stage_field": "status", "stage_value": "funded"}, "event_payload": {}},
    )

    assert result["provider"] == "crm_http"
    assert result["activity"] == "update_record"
    assert result["_activity_meta"]["outcome"] == "success"


# ---------------------------------------------------------------------------
# Cross-agent FUS fan-out client
# ---------------------------------------------------------------------------

def test_fus_stage_changed_fans_out_with_required_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """P4 cross-agent fan-out: stage_changed hits the right endpoint with X-API-Key and X-Request-ID."""
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"stopped": 1}, captured=captured))

    settings = _fus_settings()
    result = followup_client.stage_changed("lead-123", "funded", tenant="default", settings=settings)

    assert result == {"stopped": 1}
    assert captured["url"] == "http://mock-fus:8000/api/v1/stage-changed"
    assert captured["json"]["contact_id"] == "lead-123"
    assert captured["json"]["stage"] == "funded"
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"] == "fus-key"


def test_fus_lead_scored_fans_out_with_required_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"status": "enrolled"}, captured=captured))

    settings = _fus_settings()
    result = followup_client.lead_scored(
        "lead-456", 92.0, "seq-a", tenant="default", context={"deal_id": "d1"}, settings=settings
    )

    assert result == {"status": "enrolled"}
    assert captured["url"] == "http://mock-fus:8000/api/v1/triggers/lead-scored"
    assert captured["json"]["score"] == 92.0
    assert captured["json"]["context"]["deal_id"] == "d1"
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"] == "fus-key"


def test_fus_uses_primary_api_key_as_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(json_body={"ok": True}, captured=captured))

    settings = _fus_settings(follow_up_sequences_api_key="")
    followup_client.stage_changed("lead-123", "applied", tenant="default", settings=settings)
    assert captured["headers"]["X-API-Key"] == "test-key"


def test_fus_disabled_or_unset_returns_none() -> None:
    """Fan-out is skipped when disabled or no URL configured."""
    assert followup_client.stage_changed(
        "lead-123", "funded", tenant="default", settings=_fus_settings(fus_fanout_enabled=False)
    ) is None
    assert followup_client.lead_scored(
        "lead-456", 90.0, "seq-a", tenant="default", context={}, settings=_fus_settings(follow_up_sequences_url="")
    ) is None


def test_fus_logs_and_ignores_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """FUS failures must not break TAS processing."""
    monkeypatch.setattr(httpx, "Client", _mock_httpx_client(status=500))
    result = followup_client.stage_changed("lead-123", "funded", tenant="default", settings=_fus_settings())
    assert result is None


# ---------------------------------------------------------------------------
# DLQ replay and idempotency
# ---------------------------------------------------------------------------

def test_dlq_replay_is_idempotent(client: TestClient) -> None:
    """FR-016: DLQ replay creates the action once and refuses second replay."""
    from app.dlq import enqueue

    row = enqueue(
        topic="deal.stage_changed",
        payload={
            "lead_id": "L-DLQ",
            "stage": "underwriting",
            "agent_id": "lo:amy",
            "trigger_id": "trg-dlq",
        },
        error="simulated failure",
    )

    client.post(
        "/api/v1/rules",
        json={
            "name": "DLQ replay rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "underwriting"}],
            "task_type": "call",
            "assignee_strategy": "lead_owner",
        },
    )

    first = client.post(f"/api/v1/vision/dlq/{row['dlq_id']}/replay", headers={"X-Role": "sre"})
    assert first.status_code == 200
    assert first.json()["status"] == "replayed"
    assert first.json()["result"]["created"]

    tasks = client.get("/api/v1/tasks", params={"lead_id": "L-DLQ"}).json()["tasks"]
    assert len(tasks) == 1

    second = client.post(f"/api/v1/vision/dlq/{row['dlq_id']}/replay", headers={"X-Role": "sre"})
    assert second.status_code == 409
    assert second.json()["detail"] == "already replayed"

    tasks_after = client.get("/api/v1/tasks", params={"lead_id": "L-DLQ"}).json()["tasks"]
    assert len(tasks_after) == len(tasks)

    entry = get_dlq_entry(row["dlq_id"])
    assert entry["status"] == "replayed"
    assert entry["retry_count"] == 1


def test_dlq_mark_replayed_updates_entry() -> None:
    row = enqueue(topic="deal.stage_changed", payload={"lead_id": "L1"}, error="boom")
    updated = mark_replayed(row["dlq_id"], result={"ok": True})
    assert updated
    assert updated["status"] == "replayed"
    assert updated["replayed_at"]
    assert updated["replay_result"] == {"ok": True}


def test_dlq_list_filter_by_status() -> None:
    enqueue(topic="t", payload={"lead_id": "L1"}, error="boom")
    row2 = enqueue(topic="t", payload={"lead_id": "L2"}, error="boom")
    mark_replayed(row2["dlq_id"], result={})
    pending = list_dlq(status="pending")
    replayed = list_dlq(status="replayed")
    assert all(r["status"] == "pending" for r in pending)
    assert all(r["status"] == "replayed" for r in replayed)


# ---------------------------------------------------------------------------
# Loop detection, action budget, canary rollout
# ---------------------------------------------------------------------------

def test_per_deal_action_budget_halt(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015: per-deal action budget blocks runaway automation."""
    monkeypatch.setenv("MAX_ACTIONS_PER_DEAL", "2")
    get_settings.cache_clear()
    redis_store.reset_redis_store()

    ok, _ = check_action_budget(lead_id="L-BUDGET", increment=1)
    assert ok is True

    record_actions(lead_id="L-BUDGET", count=2)
    ok, reason = check_action_budget(lead_id="L-BUDGET", increment=1)
    assert ok is False
    assert reason == "deal_action_budget_exceeded"


def test_action_budget_halt_via_evaluate_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015: evaluate_rules short-circuits when the budget is already blown."""
    monkeypatch.setenv("MAX_ACTIONS_PER_DEAL", "1")
    get_settings.cache_clear()
    redis_store.reset_redis_store()
    record_actions(lead_id="L-BUDGET-2", count=2)

    result = evaluate_rules(
        topic="deal.stage_changed",
        payload={"lead_id": "L-BUDGET-2", "stage": "pre_approval"},
        rules=[],
        lead_id="L-BUDGET-2",
        existing_tasks=[],
        dry_run=False,
    )
    assert result["loop_detected"] is True
    assert any(s["reason"] == "deal_action_budget_exceeded" for s in result["skipped"])


def test_loop_guard_detects_rule_cycle() -> None:
    """FR-015: same rule fired twice within a trigger chain is a cycle."""
    assert check_loop_guard(trigger_id="trg-1", rule_id="r1", lead_id="L-LOOP") == (True, None)
    assert check_loop_guard(trigger_id="trg-1", rule_id="r1", lead_id="L-LOOP") == (False, "rule_cycle_detected")

    clear_trigger_chain(trigger_id="trg-1", lead_id="L-LOOP")
    assert check_loop_guard(trigger_id="trg-1", rule_id="r1", lead_id="L-LOOP") == (True, None)


def test_loop_guard_chain_depth_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-015: too many distinct rules in one trigger chain is a runaway chain."""
    monkeypatch.setenv("MAX_RULE_CHAIN_DEPTH", "2")
    get_settings.cache_clear()

    assert check_loop_guard(trigger_id="trg-2", rule_id="r1", lead_id="L-DEPTH") == (True, None)
    assert check_loop_guard(trigger_id="trg-2", rule_id="r2", lead_id="L-DEPTH") == (True, None)
    ok, reason = check_loop_guard(trigger_id="trg-2", rule_id="r3", lead_id="L-DEPTH")
    assert ok is False
    assert reason == "rule_chain_depth_exceeded"


def test_canary_rollout_deterministic_and_traffic_split() -> None:
    """FR-017: canary rollout uses deterministic hash and respects percent."""
    assert canary_eligible(rule={"rule_id": "r-active", "rollout_status": "active"}, lead_id="L1") == (True, None)
    assert canary_eligible(rule={"rule_id": "r-draft", "rollout_status": "draft"}, lead_id="L1") == (
        False,
        "rule_not_in_canary_or_active",
    )

    rule = {"rule_id": "r-canary", "rollout_status": "canary", "canary_percent": 100.0}
    assert canary_eligible(rule=rule, lead_id="L1") == (True, None)

    rule_zero = {"rule_id": "r-canary", "rollout_status": "canary", "canary_percent": 0.0}
    ok, reason = canary_eligible(rule=rule_zero, lead_id="any-lead")
    assert ok is False
    assert reason == "canary_traffic_excluded"

    # Determinism: same (rule_id, lead_id) always yields the same answer.
    rule_half = {"rule_id": "r-canary", "rollout_status": "canary", "canary_percent": 50.0}
    first, _ = canary_eligible(rule=rule_half, lead_id="L-stable")
    second, _ = canary_eligible(rule=rule_half, lead_id="L-stable")
    assert first == second


def test_canary_exposure_counter_and_stats() -> None:
    record_canary_exposure("rule-canary-1")
    record_canary_exposure("rule-canary-1")
    record_canary_exposure("rule-canary-2")
    assert canary_stats()["exposures_by_rule"]["rule-canary-1"] == 2
    assert canary_stats()["exposures_by_rule"]["rule-canary-2"] == 1
