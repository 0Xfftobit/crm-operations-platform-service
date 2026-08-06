"""Temporal connector activity tests — retry/backoff from config."""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.integrations.activity_runner import (
    ConnectorActivityError,
    build_retry_policy_dict,
    execute_connector_activity,
    get_activity_executions,
    reset_activity_runner,
)
from app.integrations.documents import request_documents


def test_retry_policy_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "10")
    monkeypatch.setenv("RETRY_BACKOFF_MAX_SECONDS", "60")
    monkeypatch.setenv("RETRY_BACKOFF_COEFFICIENT", "3")
    get_settings.cache_clear()

    policy = build_retry_policy_dict()
    assert policy["maximum_attempts"] == 4
    assert policy["initial_interval_seconds"] == 10.0
    assert policy["maximum_interval_seconds"] == 60.0
    assert policy["backoff_coefficient"] == 3.0


def test_activity_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.1")
    monkeypatch.setenv("RETRY_BACKOFF_MAX_SECONDS", "1")
    get_settings.cache_clear()
    reset_activity_runner()

    calls = {"n": 0}

    def flaky_request(**kwargs: object) -> dict:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient CRM outage")
        return request_documents(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.integrations.activities.connectors.request_documents", flaky_request)

    result = execute_connector_activity(
        "request_document",
        {
            "lead_id": "L-retry",
            "document_types": ["w2"],
            "assignee_id": "lo:amy",
            "rule_id": "rule_r",
        },
    )
    assert result["activity"] == "request_document"
    assert calls["n"] == 3
    executions = get_activity_executions()
    assert any(e["outcome"] == "failed" for e in executions)
    assert executions[0]["outcome"] == "success"


def test_activity_exhausts_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.1")
    get_settings.cache_clear()
    reset_activity_runner()

    def always_fail(**kwargs: object) -> dict:
        raise TimeoutError("CRM down")

    monkeypatch.setattr("app.integrations.activities.connectors.request_documents", always_fail)

    with pytest.raises(ConnectorActivityError) as exc:
        execute_connector_activity(
            "request_document",
            {"lead_id": "L-fail", "document_types": ["w2"], "assignee_id": "lo:amy"},
        )
    assert exc.value.attempts == 2
    assert len(get_activity_executions()) == 2


def test_connector_activity_via_rule_engine(client) -> None:
    """End-to-end: document request goes through activity runner."""
    from tests.conftest import BASE

    client.post(
        f"{BASE}/rules",
        json={
            "name": "Activity path doc rule",
            "trigger_event": "deal.stage_changed",
            "conditions": [{"field": "stage", "operator": "eq", "value": "pre_approval"}],
            "action_type": "request_document",
            "auto_document_set": True,
        },
    )
    result = client.post(
        f"{BASE}/events/ingest",
        json={
            "topic": "deal.stage_changed",
            "payload": {
                "lead_id": "L-act",
                "stage": "pre_approval",
                "product": "CONVENTIONAL_30YR",
                "trigger_id": "trg_act",
            },
        },
    ).json()
    assert result.get("document_requests")
    executions = get_activity_executions()
    assert any(e["activity_name"] == "request_document" and e["outcome"] == "success" for e in executions)
