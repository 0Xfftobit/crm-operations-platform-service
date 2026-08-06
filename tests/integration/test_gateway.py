"""Gateway smoke test."""
from fastapi.testclient import TestClient
from src.api.main import app


def test_health():
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_agents_listed():
    body = TestClient(app).get("/agents").json()
    assert isinstance(body, dict) and body
