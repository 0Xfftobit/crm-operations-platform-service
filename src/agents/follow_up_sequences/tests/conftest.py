import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

# Keep the suite hermetic: never let a real .env (e.g. one pointed at a live DB / upstream)
# leak in. pydantic-settings gives env vars priority over the .env file, so these override it.
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("STORAGE_GATEWAY", "memory")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("EVENT_SOURCE_GATEWAY", "local")
os.environ.setdefault("SEQUENCE_SOURCE_URL", "")
os.environ.setdefault("CACHE_GATEWAY", "memory")


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear repositories, events, providers, and frequency counters between tests (singletons)."""
    from app import events, frequency, providers
    from app.repositories import reset_repositories

    def _clean():
        reset_repositories()
        events.reset_events()
        providers.reset()
        frequency.reset_store()

    _clean()
    yield
    _clean()


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def api_headers() -> dict:
    return {"X-API-Key": os.environ["API_KEY"]}
