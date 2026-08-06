"""Smoke tests for the lean-core scaffold."""


def test_root_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready(client):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["app"] == "crequity-compliance-monitoring"
    assert body["storage"] == "memory"       # hermetic default
    assert body["uses_sql"] is False


def test_openapi(client):
    assert client.get("/openapi.json").status_code == 200


def test_settings_csv_and_sql_toggle():
    from app.core.config import Settings

    # CSV or JSON list parsing (the .env NoDecode fix)
    assert Settings(_env_file=None, CORS_ORIGINS="a,b,c").cors_origins == ["a", "b", "c"]
    assert Settings(_env_file=None, CORS_ORIGINS='["x"]').cors_origins == ["x"]
    assert Settings(_env_file=None).uses_sql is False
    s = Settings(_env_file=None, STORAGE_GATEWAY="postgres",
                 DATABASE_URL="postgresql+psycopg://u:p@h/db")
    assert s.uses_sql is True
