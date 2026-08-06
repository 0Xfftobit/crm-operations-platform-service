"""Smoke tests for the lean core: health/readiness, OpenAPI, and env-driven config parsing."""
import importlib


def test_root_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_api_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_ready(client):
    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["app"] == "crequity-followup-sequence"
    assert body["uses_sql"] is False


def test_openapi(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "crequity-followup-sequence"


def test_cors_list_parsing_and_sql_toggle(monkeypatch):
    # CSV form and JSON form both parse; uses_sql flips only with postgres + a DATABASE_URL.
    from app.core.config import Settings
    csv = Settings(CORS_ORIGINS="http://a.com, http://b.com")
    assert csv.cors_origins == ["http://a.com", "http://b.com"]
    js = Settings(CORS_ORIGINS='["http://c.com"]')
    assert js.cors_origins == ["http://c.com"]
    assert Settings(STORAGE_GATEWAY="postgres", DATABASE_URL="").uses_sql is False
    assert Settings(STORAGE_GATEWAY="postgres",
                    DATABASE_URL="postgresql+psycopg://u:p@h/db").uses_sql is True
    importlib.import_module("app.main")   # module imports cleanly
