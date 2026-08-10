"""Cross-agent feedback loop client edge cases and scheduled report/exports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients import feedback_client
from app.core.config import get_settings
from app.domain.models import OutcomeEvent

BASE = "/api/v1"


def _make_outcome() -> OutcomeEvent:
    return OutcomeEvent(
        outcome_id="out-1",
        deal_id="deal-123",
        contact_pid="c1",
        event_type="loan.funded",
        stage="funded",
        amount=250000.0,
        occurred_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
    )


def _seed_one_deal(client: TestClient, contact: str = "c1") -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    deal_id = f"deal-{contact}"
    client.post(
        f"{BASE}/ingest/touch",
        json={
            "touch_id": f"touch-{contact}",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "channel": "email",
            "sent_at": now.isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-lead",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "stage": "lead",
            "event_type": "stage.lead",
            "occurred_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-app",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "stage": "app",
            "event_type": "stage.app",
            "occurred_at": (now + timedelta(days=2)).isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-funded",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "stage": "funded",
            "amount": 100000,
            "event_type": "loan.funded",
            "occurred_at": (now + timedelta(days=14)).isoformat(),
        },
    )


class TestFeedbackClient:
    def _fake_client(self, post_handler):
        class FakeClient:
            def __init__(self, *args, **kwargs):  # noqa: ARG002
                pass

            def __enter__(self):
                mock = MagicMock()
                mock.post = post_handler
                return mock

            def __exit__(self, *args, **kwargs):  # noqa: ARG002
                return False

        return FakeClient

    def test_fanout_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "false")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        get_settings.cache_clear()

        settings = get_settings()
        result = feedback_client.fanout_outcome(_make_outcome(), settings=settings)
        assert result is None

    def test_fanout_missing_url_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "")
        get_settings.cache_clear()

        settings = get_settings()
        result = feedback_client.fanout_outcome(_make_outcome(), settings=settings)
        assert result is None

    def test_fanout_uses_feedback_loop_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        monkeypatch.setenv("FEEDBACK_LOOP_API_KEY", "fls-secret")
        monkeypatch.setenv("API_KEY", "fallback-key")
        get_settings.cache_clear()

        captured: dict = {}

        def _post(url, *, json, headers, **kwargs):  # noqa: ARG001
            captured["url"] = url
            captured["headers"] = headers
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {"status": "recorded"}
            return response

        monkeypatch.setattr(httpx, "Client", self._fake_client(_post))

        settings = get_settings()
        result = feedback_client.fanout_outcome(_make_outcome(), settings=settings)

        assert result == {"status": "recorded"}
        assert captured["url"] == "http://fls:8000/api/v1/outcomes"
        assert captured["headers"]["X-API-Key"] == "fls-secret"
        assert captured["headers"]["X-Request-ID"]

    def test_fanout_falls_back_to_api_key_when_feedback_key_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        monkeypatch.delenv("FEEDBACK_LOOP_API_KEY", raising=False)
        monkeypatch.setenv("API_KEY", "fallback-key")
        get_settings.cache_clear()

        captured: dict = {}

        def _post(url, *, json, headers, **kwargs):  # noqa: ARG001
            captured["headers"] = headers
            return MagicMock()

        monkeypatch.setattr(httpx, "Client", self._fake_client(_post))

        settings = get_settings()
        feedback_client.fanout_outcome(_make_outcome(), settings=settings)

        assert captured["headers"]["X-API-Key"] == "fallback-key"

    def test_fanout_x_request_id_is_unique(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        get_settings.cache_clear()

        request_ids: list[str] = []

        def _post(url, *, json, headers, **kwargs):  # noqa: ARG001
            request_ids.append(headers["X-Request-ID"])
            return MagicMock()

        monkeypatch.setattr(httpx, "Client", self._fake_client(_post))

        settings = get_settings()
        outcome = _make_outcome()
        feedback_client.fanout_outcome(outcome, settings=settings)
        feedback_client.fanout_outcome(outcome, settings=settings)

        assert len(request_ids) == 2
        assert request_ids[0]
        assert request_ids[1]
        assert request_ids[0] != request_ids[1]

    def test_fanout_failure_returns_none_and_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        get_settings.cache_clear()

        class FailingClient:
            def __init__(self, *args, **kwargs):  # noqa: ARG002
                pass

            def __enter__(self):
                raise httpx.ConnectError("connection refused")

            def __exit__(self, *args, **kwargs):  # noqa: ARG002
                return False

        monkeypatch.setattr(httpx, "Client", FailingClient)

        settings = get_settings()
        result = feedback_client.fanout_outcome(_make_outcome(), settings=settings)

        assert result is None
        assert "feedback_loop_fanout_failed" in caplog.text


class TestScheduledReportsAndExports:
    def test_scheduled_report_manual_run_pdf_override(self, client: TestClient) -> None:
        _seed_one_deal(client)
        scheduled = client.post(
            f"{BASE}/reports/schedule",
            json={
                "name": "weekly-funnel",
                "metric": "funnel",
                "period": "2026-06",
                "report_format": "csv",
            },
        ).json()
        report_id = scheduled["report_id"]

        res = client.get(f"{BASE}/reports/{report_id}/run", params={"report_format": "pdf"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF")

    def test_scheduled_report_manual_run_csv_override(self, client: TestClient) -> None:
        _seed_one_deal(client)
        scheduled = client.post(
            f"{BASE}/reports/schedule",
            json={
                "name": "weekly-funnel",
                "metric": "funnel",
                "period": "2026-06",
                "report_format": "pdf",
            },
        ).json()
        report_id = scheduled["report_id"]

        res = client.get(f"{BASE}/reports/{report_id}/run", params={"report_format": "csv"})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert res.text.startswith("stage")

    def test_list_reports_after_schedule(self, client: TestClient) -> None:
        _seed_one_deal(client)
        scheduled = client.post(
            f"{BASE}/reports/schedule",
            json={
                "name": "weekly-funnel",
                "metric": "funnel",
                "period": "2026-06",
                "report_format": "csv",
            },
        ).json()

        res = client.get(f"{BASE}/reports")
        assert res.status_code == 200
        assert any(r["report_id"] == scheduled["report_id"] for r in res.json())

    def test_export_metric_pdf_with_filters(self, client: TestClient) -> None:
        _seed_one_deal(client)
        res = client.get(
            f"{BASE}/export/deal_count",
            params={"report_format": "pdf", "period": "2026-06", "team_id": "team-a"},
        )
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF")

    def test_export_unknown_metric_returns_404(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/export/unknown_metric", params={"report_format": "csv"})
        assert res.status_code == 404
        assert "unknown metric or no data" in res.text
