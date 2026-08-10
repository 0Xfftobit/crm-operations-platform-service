"""Anomaly detection, threshold alerts, and reconciliation period/tolerance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1"


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=UTC).isoformat()


def _seed_one_deal(client: TestClient, contact: str, funded: bool = True) -> None:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    deal_id = f"deal-{contact}"
    client.post(
        f"{BASE}/ingest/touch",
        json={
            "touch_id": f"touch-{contact}",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "channel": "email",
            "sent_at": _iso(now),
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-app",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "tenant_id": "default",
            "stage": "app",
            "event_type": "stage.app",
            "occurred_at": _iso(now + timedelta(days=2)),
        },
    )
    if funded:
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": f"outcome-{contact}-funded",
                "contact_pid": contact,
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "stage": "funded",
                "amount": 100000,
                "event_type": "loan.funded",
                "occurred_at": _iso(now + timedelta(days=14)),
            },
        )


def _seed_month(client: TestClient, month: int, apps: int, funded: int) -> None:
    base = datetime(2026, month, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(apps):
        contact = f"m{month}-{i}"
        deal_id = f"deal-{contact}"
        client.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": f"touch-{contact}",
                "contact_pid": contact,
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "channel": "email",
                "sent_at": _iso(base),
            },
        )
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": f"outcome-{contact}-app",
                "contact_pid": contact,
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "stage": "app",
                "event_type": "stage.app",
                "occurred_at": _iso(base + timedelta(days=2)),
            },
        )
        if i < funded:
            client.post(
                f"{BASE}/ingest/outcome",
                json={
                    "outcome_id": f"outcome-{contact}-funded",
                    "contact_pid": contact,
                    "deal_id": deal_id,
                    "rep_id": "rep-1",
                    "team_id": "team-a",
                    "tenant_id": "default",
                    "stage": "funded",
                    "amount": 100000,
                    "event_type": "loan.funded",
                    "occurred_at": _iso(base + timedelta(days=10)),
                },
            )


class TestAnomalyDetection:
    def test_detect_anomalies_flags_outlier_period(self, client: TestClient) -> None:
        # History: 0.4, 0.5, 0.6; current month: 0.0 => z-score ~ -5.
        _seed_month(client, 1, apps=5, funded=2)
        _seed_month(client, 2, apps=4, funded=2)
        _seed_month(client, 3, apps=5, funded=3)
        _seed_month(client, 4, apps=5, funded=0)

        res = client.post(f"{BASE}/detect-anomalies/app_to_fund_conversion", params={"period": "monthly"})
        assert res.status_code == 200
        alerts = res.json()
        assert len(alerts) >= 1
        alert = alerts[0]
        assert alert["metric"] == "app_to_fund_conversion"
        assert abs(alert["zscore"]) > 2.0
        assert alert["severity"] == "critical"
        assert "2026-04" in alert["dimension"]

        # Alerts endpoint should surface the same record.
        res = client.get(f"{BASE}/alerts", params={"metric": "app_to_fund_conversion"})
        assert res.status_code == 200
        assert any(a["zscore"] == alert["zscore"] for a in res.json())

    def test_detect_anomalies_returns_empty_with_insufficient_history(self, client: TestClient) -> None:
        _seed_month(client, 1, apps=4, funded=2)
        _seed_month(client, 2, apps=4, funded=1)

        res = client.post(f"{BASE}/detect-anomalies/app_to_fund_conversion", params={"period": "monthly"})
        assert res.status_code == 200
        assert res.json() == []


class TestThresholdAlerts:
    def test_threshold_alert_fires_above(self, client: TestClient) -> None:
        _seed_one_deal(client, contact="c1", funded=True)
        res = client.post(
            f"{BASE}/threshold-alert/app_to_fund_conversion",
            params={"threshold": 0.99, "direction": "above"},
        )
        assert res.status_code == 200
        assert res.json()["triggered"]
        assert res.json()["triggered"][0]["value"] == 1.0

    def test_threshold_alert_does_not_fire_when_no_breach(self, client: TestClient) -> None:
        _seed_one_deal(client, contact="c1", funded=True)
        res = client.post(
            f"{BASE}/threshold-alert/app_to_fund_conversion",
            params={"threshold": 0.99, "direction": "below"},
        )
        assert res.status_code == 200
        assert res.json()["triggered"] == []

    def test_threshold_alert_unknown_metric(self, client: TestClient) -> None:
        res = client.post(
            f"{BASE}/threshold-alert/unknown_metric",
            params={"threshold": 0.5, "direction": "below"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "unknown_metric"


class TestReconciliation:
    def test_reconcile_p30d_iso_window_excludes_outside_window(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import reconciliation

        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(reconciliation, "_now", lambda: now)

        # Inside the 30-day window.
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "outcome-in",
                "contact_pid": "c-in",
                "stage": "funded",
                "amount": 1000.0,
                "event_type": "loan.funded",
                "occurred_at": _iso(now - timedelta(days=15)),
            },
        )
        # Outside the 30-day window.
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "outcome-out",
                "contact_pid": "c-out",
                "stage": "funded",
                "amount": 500.0,
                "event_type": "loan.funded",
                "occurred_at": _iso(now - timedelta(days=45)),
            },
        )
        client.post(
            f"{BASE}/ingest/finance-source",
            json={"period": "P30D", "funded_amount": 1000.0},
        )

        res = client.post(f"{BASE}/reconcile/P30D")
        assert res.status_code == 200
        body = res.json()
        assert body["passed"] is True
        assert body["mart_amount"] == 1000.0
        assert body["finance_amount"] == 1000.0

    def test_reconcile_drift_tolerance_boundary(self, client: TestClient) -> None:
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "outcome-1",
                "contact_pid": "c1",
                "stage": "funded",
                "amount": 1004.9,
                "event_type": "loan.funded",
                "occurred_at": "2026-06-15T00:00:00+00:00",
            },
        )
        client.post(
            f"{BASE}/ingest/finance-source",
            json={"period": "2026-06", "funded_amount": 1000.0},
        )

        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.json()["passed"] is True
        # 4.9 / 1000 = 0.0049, just under the 0.005 tolerance.

        # Add just enough to push the relative drift over 0.5%.
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "outcome-2",
                "contact_pid": "c2",
                "stage": "funded",
                "amount": 0.2,
                "event_type": "loan.funded",
                "occurred_at": "2026-06-16T00:00:00+00:00",
            },
        )
        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.json()["passed"] is False
        # 5.1 / 1000 = 0.0051, just over the tolerance.

    def test_reconciliations_history_endpoint(self, client: TestClient) -> None:
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "o-2026-05",
                "contact_pid": "c1",
                "stage": "funded",
                "amount": 500.0,
                "event_type": "loan.funded",
                "occurred_at": "2026-05-15T00:00:00+00:00",
            },
        )
        client.post(f"{BASE}/ingest/finance-source", json={"period": "2026-05", "funded_amount": 500.0})
        client.post(f"{BASE}/reconcile/2026-05")

        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "o-2026-06",
                "contact_pid": "c2",
                "stage": "funded",
                "amount": 300.0,
                "event_type": "loan.funded",
                "occurred_at": "2026-06-15T00:00:00+00:00",
            },
        )
        client.post(f"{BASE}/ingest/finance-source", json={"period": "2026-06", "funded_amount": 300.0})
        client.post(f"{BASE}/reconcile/2026-06")

        res = client.get(f"{BASE}/reconciliations")
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 2
        assert {r["period"] for r in body} == {"2026-05", "2026-06"}

        res = client.get(f"{BASE}/reconciliations", params={"period": "2026-06"})
        assert len(res.json()) == 1
        assert res.json()[0]["period"] == "2026-06"
