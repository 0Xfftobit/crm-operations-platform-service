"""API smoke tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import BASE


def test_health(client: TestClient) -> None:
    assert client.get(f"{BASE}/health").json()["status"] == "ok"


def test_ready(client: TestClient) -> None:
    body = client.get(f"{BASE}/ready").json()
    assert body["status"] == "ready"


def test_requires_api_key(client: TestClient) -> None:
    anon = TestClient(client.app)
    assert anon.get(f"{BASE}/tasks").status_code == 401
