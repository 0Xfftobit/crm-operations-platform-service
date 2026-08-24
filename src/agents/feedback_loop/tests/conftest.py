import os

# Tests must never touch the live shared-Neon database the agent's .env
# points at: force in-memory storage before any app module reads settings.
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["DATABASE_URL"] = ""

"""Test fixtures for the Feedback Loop System."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.messaging import kafka_bus
from app.service import reset_state

API_KEY = "test-key"
BASE = "/api/v1"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("STORAGE_BACKEND", "memory")
    monkeypatch.setenv("AUTO_RETRAIN_ENABLED", "false")
    get_settings.cache_clear()
    reset_state()
    kafka_bus.reset_kafka_bus()
    yield
    get_settings.cache_clear()
    reset_state()
    kafka_bus.reset_kafka_bus()


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app(), headers={"X-API-Key": API_KEY}) as c:
        yield c
