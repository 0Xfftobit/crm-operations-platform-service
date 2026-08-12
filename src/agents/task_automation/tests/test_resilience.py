"""Resilient HTTP client / circuit breaker tests."""
from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from app.core.resilience import ResilientClient


class _FailingClient:
    """Fake httpx.Client that always raises a network error."""

    def __init__(self, *args, **kwargs):  # noqa: ARG002
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):  # noqa: ARG002
        return False

    def post(self, *args, **kwargs):  # noqa: ARG002
        raise httpx.ConnectError("nope")


def test_resilient_client_retries_and_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _FailingClient)
    client = ResilientClient(client_label="test", retry_base_delay=0.0)

    result = client.request("POST", "http://example.com/api", fallback={"stub": True})

    assert result == {"stub": True}


def test_resilient_client_returns_fallback_when_circuit_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _FailingClient)
    client = ResilientClient(client_label="test", retry_base_delay=0.0)

    with patch("time.sleep", return_value=None):
        # 3 attempts on first call open the circuit
        client.request("POST", "http://example.com/api", fallback={"stub": True})

    # Next call should short-circuit and return the fallback immediately
    start = time.monotonic()
    result = client.request("POST", "http://example.com/api", fallback={"stub": True})
    elapsed = time.monotonic() - start

    assert result == {"stub": True}
    assert elapsed < 0.1  # immediate fallback, no retries
