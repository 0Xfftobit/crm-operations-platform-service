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
os.environ.setdefault("AUDIT_SOURCE_URL", "")
os.environ.setdefault("CACHE_GATEWAY", "memory")
os.environ.setdefault("CHAIN_VERIFY_INTERVAL_MIN", "0")   # no background tasks in tests
os.environ.setdefault("MERKLE_ANCHOR_INTERVAL_MIN", "0")
os.environ.setdefault("WORM_GATEWAY", "none")
os.environ.setdefault("COLD_STORE_GATEWAY", "none")
os.environ.setdefault("AUDIT_LIFECYCLE_INTERVAL_MIN", "0")
os.environ.setdefault("KAFKA_PRODUCE", "false")
os.environ.setdefault("KAFKA_INGEST", "false")


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear the ledger, repositories, and event log between tests (module-level singletons)."""
    from app import audit, cache, casework, coldstore, coverage, events, worm
    from app.repositories import reset_repositories

    def _reset_all() -> None:
        for step in (audit.reset_ledger, reset_repositories, events.reset_events,
                     casework.reset_suppression, cache.reset, worm.reset, coverage.reset,
                     coldstore.reset):
            try:
                step()
            except Exception:  # noqa: BLE001,S110 — best-effort teardown across backends
                pass

    _reset_all()
    yield
    _reset_all()


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def api_headers() -> dict:
    return {"X-API-Key": os.environ["API_KEY"]}
