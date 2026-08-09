"""Cross-agent HTTP client wiring tests (TAS -> FUS, mocked)."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.clients import followup_client
from app.core.config import get_settings


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


@pytest.mark.parametrize("stage", ["applied", "funded"])
def test_stage_changed_fans_out_to_fus(stage, monkeypatch):
    monkeypatch.setenv("FOLLOW_UP_SEQUENCES_URL", "http://mock-fus:8000")
    monkeypatch.setenv("FUS_FANOUT_ENABLED", "true")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient(_mocked_httpx_client({"stopped": 1}, 200)):
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
    result = followup_client.stage_changed(
        "lead-123", stage, tenant="default", settings=settings,
    )

    assert result == {"stopped": 1}
    assert captured["url"] == "http://mock-fus:8000/api/v1/stage-changed"
    assert captured["json"]["contact_id"] == "lead-123"
    assert captured["json"]["stage"] == stage
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"]


def test_lead_scored_fans_out_to_fus(monkeypatch):
    monkeypatch.setenv("FOLLOW_UP_SEQUENCES_URL", "http://mock-fus:8000")
    monkeypatch.setenv("FUS_FANOUT_ENABLED", "true")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient(_mocked_httpx_client({"status": "enrolled"}, 200)):
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
    result = followup_client.lead_scored(
        "lead-456", 85.0, "seq-a", tenant="default", context={"deal_id": "d1"},
        settings=settings,
    )

    assert result == {"status": "enrolled"}
    assert captured["url"] == "http://mock-fus:8000/api/v1/triggers/lead-scored"
    assert captured["json"]["score"] == 85.0
    assert captured["json"]["context"]["deal_id"] == "d1"
    assert captured["headers"]["X-Request-ID"]
