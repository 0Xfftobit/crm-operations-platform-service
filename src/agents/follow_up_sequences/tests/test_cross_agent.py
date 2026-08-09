"""Cross-agent HTTP client wiring tests (FUS -> CMS, mocked)."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx

from app.clients import compliance_client
from app.core.config import get_settings
from app.domain.models import Contact


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


def test_compliance_client_calls_precheck_endpoint(monkeypatch):
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://mock-cms:8000")
    monkeypatch.setenv("FUS_PRECHECK_ENABLED", "true")
    get_settings.cache_clear()

    captured: dict = {}

    class FakeClient(_mocked_httpx_client({"status": "allowed"}, 200)):
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
    contact = Contact(contact_id="c1", consent_basis="express_written")
    result = compliance_client.precheck(
        contact, channel="email", body="hello", scheduled_for=datetime.now(UTC),
        settings=settings,
    )

    assert result == {"status": "allowed"}
    assert captured["url"] == "http://mock-cms:8000/api/v1/precheck"
    assert captured["json"]["channel"] == "email"
    assert captured["json"]["contact_id"] == "c1"
    assert captured["headers"]["X-Request-ID"]
    assert captured["headers"]["X-API-Key"]
