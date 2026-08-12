"""Gateway cross-agent header propagation and readiness tests."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from config.settings import get_settings
from src.api.main import app


class _MockResponse:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b'{"status":"ok"}',
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    @property
    def text(self) -> str:
        return self.content.decode()


class _AsyncContextManager:
    def __init__(self, client: _MockAsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> _MockAsyncClient:
        return self.client

    async def __aexit__(self, *args: Any) -> None:
        pass


class _MockAsyncClient:
    def __init__(self, responses: dict[str, _MockResponse] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> _MockResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.get(url, _MockResponse())

    async def get(self, url: str, **kwargs: Any) -> _MockResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.get(url, _MockResponse())

    def __call__(self, *args: Any, **kwargs: Any) -> _MockAsyncClient:
        return self

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_agent_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("ENV", "local")
    monkeypatch.setenv("COMPLIANCE_MONITOR_URL", "http://cms:8001")
    monkeypatch.setenv("TASK_AUTOMATION_URL", "http://tas:8002")
    monkeypatch.setenv("FOLLOW_UP_SEQUENCES_URL", "http://fus:8003")
    monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8004")
    monkeypatch.setenv("PERFORMANCE_ANALYTICS_URL", "http://pas:8005")


def _all_ready(base: str, status: int = 200) -> dict[str, _MockResponse]:
    return {f"{base}/api/v1/ready": _MockResponse(status_code=status)}


def _all_health(base: str, status: int = 200) -> dict[str, _MockResponse]:
    return {
        f"{base}/api/v1/ready": _MockResponse(status_code=status),
        f"{base}/api/v1/health": _MockResponse(status_code=status),
        f"{base}/health": _MockResponse(status_code=status),
        f"{base}/healthz": _MockResponse(status_code=status),
    }


def test_proxy_forwards_api_key_and_request_id(monkeypatch: pytest.MonkeyPatch):
    _set_agent_urls(monkeypatch)
    client_cls = _MockAsyncClient({
        "http://cms:8001/api/v1/health": _MockResponse(),
    })
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    client = TestClient(app)
    resp = client.get(
        "/compliance-monitor/api/v1/health",
        headers={"X-API-Key": "test-key", "X-Request-ID": "gateway-rid-123"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "gateway-rid-123"
    assert len(client_cls.calls) == 1
    forwarded = client_cls.calls[0]["headers"]
    assert forwarded["x-api-key"] == "test-key"
    assert forwarded["x-request-id"] == "gateway-rid-123"


def test_proxy_generates_request_id_when_absent(monkeypatch: pytest.MonkeyPatch):
    _set_agent_urls(monkeypatch)
    client_cls = _MockAsyncClient({
        "http://tas:8002/api/v1/health": _MockResponse(),
    })
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    client = TestClient(app)
    resp = client.get(
        "/task-automation/api/v1/health",
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) == 32
    forwarded = client_cls.calls[0]["headers"]
    assert forwarded["x-request-id"] == resp.headers["x-request-id"]


def test_proxy_forwards_rls_headers(monkeypatch: pytest.MonkeyPatch):
    _set_agent_urls(monkeypatch)
    client_cls = _MockAsyncClient({
        "http://pas:8005/api/v1/health": _MockResponse(),
    })
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    client = TestClient(app)
    client.get(
        "/performance-analytics/api/v1/health",
        headers={
            "X-API-Key": "test-key",
            "X-Tenant": "t-1",
            "X-Team": "team-a",
            "X-Rep": "rep-1",
        },
    )
    forwarded = client_cls.calls[0]["headers"]
    assert forwarded["x-tenant"] == "t-1"
    assert forwarded["x-team"] == "team-a"
    assert forwarded["x-rep"] == "rep-1"


def test_ready_reports_ok_when_all_agents_up(monkeypatch: pytest.MonkeyPatch):
    _set_agent_urls(monkeypatch)
    responses: dict[str, _MockResponse] = {}
    for base in ("http://cms:8001", "http://tas:8002", "http://fus:8003", "http://fls:8004", "http://pas:8005"):
        responses.update(_all_health(base, 200))
    client_cls = _MockAsyncClient(responses)
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    body = TestClient(app).get("/ready").json()
    assert body["status"] == "ok"
    for _agent, status in body["agents"].items():
        assert status == "ok"


def test_ready_degraded_when_one_agent_unhealthy(monkeypatch: pytest.MonkeyPatch):
    _set_agent_urls(monkeypatch)
    responses: dict[str, _MockResponse] = {}
    for base in ("http://cms:8001", "http://tas:8002", "http://fls:8004", "http://pas:8005"):
        responses.update(_all_health(base, 200))
    responses.update(_all_health("http://fus:8003", 503))
    client_cls = _MockAsyncClient(responses)
    monkeypatch.setattr("httpx.AsyncClient", client_cls)

    body = TestClient(app).get("/ready").json()
    assert body["status"] == "degraded"
    assert body["agents"]["follow_up_sequences"] == "http 503"
