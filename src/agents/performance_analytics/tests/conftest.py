from __future__ import annotations
import os

# Tests must never touch the live shared-Neon database the agent's .env
# points at: force in-memory storage before any app module reads settings.
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["STORAGE_GATEWAY"] = "memory"
os.environ["DATABASE_URL"] = ""

"""Test fixtures for the Performance Analytics System."""


import pytest
from fastapi.testclient import TestClient

from app.clients import feedback_client
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
    # env vars override the .env file, which points at live Neon; deleting the
    # var would fall back to it, so pin memory/empty explicitly.
    monkeypatch.setenv("STORAGE_GATEWAY", "memory")
    monkeypatch.setenv("DATABASE_URL", "")
    for env_var in (
        "REDIS_URL",
        "CACHE_GATEWAY",
        "IDEMPOTENCY_BACKEND",
        "EVENT_SOURCE_GATEWAY",
        "KAFKA_PRODUCE",
        "KAFKA_CONSUMER_ENABLED",
    ):
        monkeypatch.delenv(env_var, raising=False)
    get_settings.cache_clear()
    reset_state()
    kafka_bus.reset_kafka_bus()
    feedback_client.reset_resilient_client()
    yield
    get_settings.cache_clear()
    reset_state()
    kafka_bus.reset_kafka_bus()
    feedback_client.reset_resilient_client()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), headers={"X-API-Key": API_KEY})
