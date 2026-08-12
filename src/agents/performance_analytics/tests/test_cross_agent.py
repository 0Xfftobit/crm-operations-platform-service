"""Cross-agent HTTP client wiring tests (PAS -> FLS, mocked)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx

from app.clients import feedback_client
from app.core.config import get_settings
from app.domain.models import OutcomeEvent


def _mocked_httpx_client(response_json: dict, status: int = 200):
    """Return a class that, when used as a context manager, yields a mock client."""
    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = status
            mock_response.is_success = 200 <= status < 300
            mock_response.json.return_value = response_json
            mock_response.text = str(response_json)
            mock.post.return_value = mock_response
            return mock

        def __exit__(self, *args, **kwargs):  # noqa: ARG002
            return False

    return FakeClient


def test_fanout_outcome_posts_to_fls(monkeypatch):
    monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://mock-fls:8000")
    monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient(_mocked_httpx_client({"status": "recorded"}, 200)):
        def __enter__(self):
            mock = super().__enter__()
            original_post = mock.post

            def _capture_post(url, *, json, headers, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return original_post(url, json=json, headers=headers)

            mock.post = _capture_post
            return mock

    monkeypatch.setattr(httpx, "Client", FakeClient)

    settings = get_settings()
    outcome = OutcomeEvent(
        outcome_id="out-1",
        deal_id="deal-123",
        contact_pid="c1",
        event_type="loan.funded",
        stage="funded",
        amount=250000.0,
        occurred_at=datetime.now(UTC),
    )
    result = feedback_client.fanout_outcome(outcome, settings=settings)

    assert result == {"status": "recorded"}
    assert captured["url"] == "http://mock-fls:8000/api/v1/outcomes"
    assert captured["json"]["entity_id"] == "deal-123"
    assert captured["json"]["event_type"] == "loan.funded"
    assert captured["json"]["metric_value"] == 250000.0
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"]


def test_fanout_circuit_opens_and_falls_back(monkeypatch):
    monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://mock-fls:8000")
    monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
    get_settings.cache_clear()

    class FailingClient:
        call_count = 0

        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            FailingClient.call_count += 1
            raise httpx.ConnectError("connection refused")

        def __exit__(self, *args, **kwargs):  # noqa: ARG002
            return False

    monkeypatch.setattr(httpx, "Client", FailingClient)

    settings = get_settings()
    outcome = OutcomeEvent(
        outcome_id="out-2",
        deal_id="deal-123",
        contact_pid="c1",
        event_type="loan.funded",
        stage="funded",
        amount=250000.0,
        occurred_at=datetime.now(UTC),
    )

    result = feedback_client.fanout_outcome(outcome, settings=settings)
    assert result is None
    assert FailingClient.call_count == 3  # max retries before circuit opens

    # The circuit is now open, so the next call should not attempt the network.
    result2 = feedback_client.fanout_outcome(outcome, settings=settings)
    assert result2 is None
    assert FailingClient.call_count == 3
