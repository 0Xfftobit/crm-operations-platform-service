"""Circuit breaker + ResilientClient cross-agent resilience tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.core.resilience import CircuitBreakerOpen, ResilientClient, reset_circuits


def _failing_httpx_client(*, status: int = 500, fail_count: int = 100):
    """Return a fake httpx.Client class that returns a fixed status code."""

    class FakeClient:
        calls: int = 0

        def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()

            def _capture_post(url, *, json=None, headers=None, **kwargs):  # noqa: ARG001
                FakeClient.calls += 1
                response = MagicMock()
                response.status_code = status
                response.is_success = 200 <= status < 300
                response.json.return_value = {"status": "allowed"}
                response.text = str({"status": "allowed"})
                return response

            mock.post = _capture_post
            return mock

        def __exit__(self, *args, **kwargs) -> bool:  # noqa: ARG002
            return False

    return FakeClient


def test_resilient_client_retries_and_circuit_opens(monkeypatch):
    reset_circuits()
    FakeClient = _failing_httpx_client(status=500)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    client = ResilientClient(client="compliance-test", base_delay=0.0)
    response = client.post("http://mock-cms:8000/api/v1/precheck", json={"x": 1})

    # 500 responses are treated as failures; all 3 attempts are exhausted.
    assert response.status_code == 500
    assert FakeClient.calls == 3

    # The circuit is now open; the next call fails fast without network traffic.
    with pytest.raises(CircuitBreakerOpen):
        client.post("http://mock-cms:8000/api/v1/precheck", json={"x": 1})
    assert FakeClient.calls == 3


def test_resilient_client_propagates_request_id_and_api_key(monkeypatch):
    reset_circuits()
    captured: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
            pass

        def __enter__(self):
            mock = MagicMock()

            def _capture_post(url, *, json=None, headers=None, **kwargs):  # noqa: ARG001
                captured["url"] = url
                captured["headers"] = headers
                response = MagicMock()
                response.status_code = 200
                response.is_success = True
                response.json.return_value = {"ok": True}
                return response

            mock.post = _capture_post
            return mock

        def __exit__(self, *args, **kwargs) -> bool:  # noqa: ARG002
            return False

    monkeypatch.setattr(httpx, "Client", FakeClient)

    client = ResilientClient(client="compliance-test", base_delay=0.0)
    response = client.post("http://svc/x", json={"x": 1}, headers={"X-API-Key": "k"})

    assert response.status_code == 200
    assert captured["url"] == "http://svc/x"
    assert captured["headers"]["X-API-Key"] == "k"
    assert captured["headers"]["X-Request-ID"]
