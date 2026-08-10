"""Security, CORS, and row-level security (RLS) behavior tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.analytics import _rls_filter
from app.api.deps import parse_role_headers
from app.core.config import get_settings
from app.main import create_app

BASE = "/api/v1"
API_KEY = "test-key"


def _seed_one_deal(client: TestClient, contact: str = "c1", team: str = "team-a", rep: str = "rep-1") -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    deal_id = f"deal-{contact}"
    client.post(
        f"{BASE}/ingest/touch",
        json={
            "touch_id": f"touch-{contact}",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": rep,
            "team_id": team,
            "tenant_id": "default",
            "channel": "email",
            "sent_at": now.isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-lead",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": rep,
            "team_id": team,
            "tenant_id": "default",
            "stage": "lead",
            "event_type": "stage.lead",
            "occurred_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-app",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": rep,
            "team_id": team,
            "tenant_id": "default",
            "stage": "app",
            "event_type": "stage.app",
            "occurred_at": (now + timedelta(days=2)).isoformat(),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-funded",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": rep,
            "team_id": team,
            "tenant_id": "default",
            "stage": "funded",
            "amount": 100000,
            "event_type": "loan.funded",
            "occurred_at": (now + timedelta(days=14)).isoformat(),
        },
    )


class TestCorsAndApiKey:
    def test_default_api_key_rejected_in_non_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("API_KEY", "change-me-dev-key")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="Default dev API key is not allowed"):
            get_settings()

    def test_wildcard_cors_rejected_in_non_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("API_KEY", "prod-key")
        monkeypatch.setenv("CORS_ORIGINS", "*")
        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="Wildcard CORS origin"):
            get_settings()

    def test_local_env_allows_default_key_and_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "local")
        monkeypatch.setenv("API_KEY", "change-me-dev-key")
        monkeypatch.setenv("CORS_ORIGINS", "*")
        get_settings.cache_clear()

        settings = get_settings()
        assert settings.api_key == "change-me-dev-key"
        assert settings.cors_origins == ["*"]

    def test_cors_wildcard_allowed_in_local_preflight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "local")
        monkeypatch.setenv("API_KEY", API_KEY)
        monkeypatch.setenv("CORS_ORIGINS", "*")
        get_settings.cache_clear()

        app = create_app()
        tc = TestClient(app)
        origin = "http://example.com"
        res = tc.options(
            f"{BASE}/kpi/deal_count",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert res.status_code == 200
        assert res.headers["access-control-allow-origin"] == origin
        assert "GET" in res.headers["access-control-allow-methods"]


class TestRlsHeaders:
    def test_parse_role_headers(self) -> None:
        result = parse_role_headers(
            role="manager",
            x_tenant_id="tenant-1",
            x_team_id="team-a",
            x_rep_id="rep-1",
        )
        assert result == {
            "role": "manager",
            "tenant_id": "tenant-1",
            "team_id": "team-a",
            "rep_id": "rep-1",
        }

    def test_rls_filter_applies_team_and_rep_and_ignores_role(self) -> None:
        filters = _rls_filter(
            {"source": "email"},
            {"role": "manager", "tenant_id": "t1", "team_id": "team-a", "rep_id": "rep-1"},
        )
        assert filters == {
            "source": "email",
            "tenant_id": "t1",
            "team_id": "team-a",
            "rep_id": "rep-1",
        }

    def test_rls_filter_header_overrides_query_filter(self) -> None:
        filters = _rls_filter({"team_id": "team-a"}, {"team_id": "team-b"})
        assert filters["team_id"] == "team-b"

    def test_team_header_filters_kpi(self, client: TestClient) -> None:
        _seed_one_deal(client, contact="c1", team="team-a")
        res = client.get(f"{BASE}/kpi/deal_count", headers={"X-Team-Id": "team-b"})
        assert res.status_code == 200
        assert res.json()["rows"] == []

    def test_rls_header_overrides_query_param(self, client: TestClient) -> None:
        _seed_one_deal(client, contact="c1", team="team-a", rep="rep-1")
        res = client.get(
            f"{BASE}/kpi/deal_count",
            params={"team_id": "team-a", "rep_id": "rep-1"},
            headers={"X-Team-Id": "team-b"},
        )
        assert res.status_code == 200
        assert res.json()["rows"] == []

    def test_role_header_does_not_filter(self, client: TestClient) -> None:
        _seed_one_deal(client, contact="c1", team="team-a", rep="rep-1")
        res = client.get(f"{BASE}/kpi/deal_count", headers={"X-Role": "manager"})
        assert res.status_code == 200
        assert res.json()["rows"][0]["value"] == 1
