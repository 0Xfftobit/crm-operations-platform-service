"""Unit + functional tests for the Performance Analytics System (P0–P3)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

API_KEY = "test-key"
BASE = "/api/v1"


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=UTC).isoformat()


def _seed_full_deal(client: TestClient, contact: str = "contact-1", funded: bool = True, channel: str = "email") -> str:
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    deal_id = f"deal-{contact}"
    client.post(
        f"{BASE}/ingest/touch",
        json={
            "touch_id": f"touch-{contact}-1",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "channel": channel,
            "campaign_id": "camp-1",
            "sent_at": _iso(now),
            "opened": True,
        },
    )
    client.post(
        f"{BASE}/ingest/outcome",
        json={
            "outcome_id": f"outcome-{contact}-lead",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": "rep-1",
            "team_id": "team-a",
            "stage": "lead",
            "event_type": "stage.lead",
            "occurred_at": _iso(now + timedelta(hours=1)),
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
                "stage": "funded",
                "amount": 300000,
                "event_type": "loan.funded",
                "occurred_at": _iso(now + timedelta(days=14)),
            },
        )
    return deal_id


class TestIngestion:
    def test_ingest_touch(self, client: TestClient) -> None:
        res = client.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": "t-1",
                "contact_pid": "c-1",
                "deal_id": "d-1",
                "rep_id": "r-1",
                "team_id": "team-a",
                "channel": "email",
                "sent_at": _iso(datetime.now(UTC)),
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ingested"

    def test_duplicate_touch(self, client: TestClient) -> None:
        payload = {
            "event_id": "evt-1",
            "touch_id": "t-1",
            "contact_pid": "c-1",
            "deal_id": "d-1",
            "rep_id": "r-1",
            "team_id": "team-a",
            "channel": "email",
            "sent_at": _iso(datetime.now(UTC)),
        }
        assert client.post(f"{BASE}/ingest/touch", json=payload).json()["status"] == "ingested"
        assert client.post(f"{BASE}/ingest/touch", json=payload).json()["status"] == "duplicate"

    def test_ingest_outcome(self, client: TestClient) -> None:
        res = client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "o-1",
                "contact_pid": "c-1",
                "deal_id": "d-1",
                "stage": "funded",
                "amount": 250000,
                "event_type": "loan.funded",
                "occurred_at": _iso(datetime.now(UTC)),
            },
        )
        assert res.status_code == 200
        assert res.json()["status"] == "ingested"

    def test_requires_auth(self, client: TestClient) -> None:
        anon = TestClient(client.app)
        res = anon.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": "t-1",
                "contact_pid": "c-1",
                "channel": "email",
            },
        )
        assert res.status_code == 401


class TestRLS:
    def test_team_rls_filter(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count", headers={"X-Team-Id": "team-b"})
        assert res.status_code == 200
        assert res.json()["rows"] == []

    def test_rep_rls_filter(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count", headers={"X-Rep-Id": "rep-99"})
        assert res.status_code == 200
        assert res.json()["rows"] == []

    def test_tenant_rls_filter(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count", headers={"X-Tenant-Id": "other"})
        assert res.status_code == 200
        assert res.json()["rows"] == []


class TestSemantic:
    def test_builtin_metrics_seeded(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/metrics/definitions")
        assert res.status_code == 200
        assert any(m["name"] == "app_to_fund_conversion" for m in res.json())

    def test_publish_metric_creates_version(self, client: TestClient) -> None:
        res = client.post(
            f"{BASE}/metrics/definitions",
            json={
                "name": "test_metric",
                "label": "Test Metric",
                "metric_type": "count",
                "description": "A test metric",
                "owner": "analytics-eng",
                "depends_on": ["deal_count"],
                "lineage": ["metrics_collector"],
            },
        )
        assert res.status_code == 200
        assert res.json()["version"] == 1

        res2 = client.post(
            f"{BASE}/metrics/definitions",
            json={
                "name": "test_metric",
                "label": "Test Metric V2",
                "metric_type": "count",
                "description": "A test metric",
                "owner": "analytics-eng",
            },
        )
        assert res2.json()["version"] == 2

        # The previous version should now be superseded.
        v1 = client.get(f"{BASE}/metrics/definitions/test_metric", params={"version": 1}).json()
        assert v1["status"] == "superseded"
        v2 = client.get(f"{BASE}/metrics/definitions/test_metric", params={"version": 2}).json()
        assert v2["status"] == "published"

        # History and lineage endpoints (FR-016)
        history = client.get(f"{BASE}/metrics/definitions/test_metric/history").json()
        assert len(history) == 2
        assert history[0]["version"] == 1
        assert history[1]["version"] == 2
        assert "deal_count" in history[0]["depends_on"]

        lineage = client.get(f"{BASE}/metrics/definitions/test_metric/lineage").json()
        assert lineage["metric"] == "test_metric"

    def test_explore(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/explore")
        assert res.status_code == 200
        body = res.json()
        assert len(body["metrics"]) > 0
        assert "dimensions" in body

    def test_metric_lifecycle(self, client: TestClient) -> None:
        # Publish a new version of a governed metric and compute it
        client.post(
            f"{BASE}/metrics/definitions",
            json={
                "name": "deal_count",
                "label": "Funded Deal Count V2",
                "metric_type": "count",
                "description": "Count of funded deals",
                "owner": "qa",
                "depends_on": ["fct_outcome"],
            },
        )
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count")
        assert res.status_code == 200
        assert res.json()["rows"][0]["value"] == 1
        assert res.json()["rows"][0]["numerator"] == 1

        history = client.get(f"{BASE}/metrics/definitions/deal_count/history").json()
        assert len(history) == 2
        assert history[-1]["version"] == 2


class TestKpi:
    def test_app_to_fund_conversion(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=False)
        res = client.get(f"{BASE}/kpi/app_to_fund_conversion")
        assert res.status_code == 200
        body = res.json()
        assert body["metric"] == "app_to_fund_conversion"
        # Two apps, one funded
        assert body["rows"][0]["apps"] == 2
        assert body["rows"][0]["funded"] == 1
        assert body["rows"][0]["numerator"] == 1
        assert body["rows"][0]["denominator"] == 2
        assert body["rows"][0]["value"] == 0.5

    def test_conversion_rate_by_rep(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=False)
        res = client.get(f"{BASE}/kpi/conversion_rate", params={"dim": "rep"})
        body = res.json()
        assert body["rows"][0]["rep"] == "rep-1"
        assert body["rows"][0]["value"] == 0.5

    def test_funded_amount_by_channel(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="sms")
        res = client.get(f"{BASE}/kpi/funded_amount", params={"dim": "channel"})
        body = res.json()
        by_channel = {r["channel"]: r["funded_amount"] for r in body["rows"]}
        assert by_channel.get("email") == 300000
        assert by_channel.get("sms") == 300000

    def test_period_filter(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count", params={"period": "2026-06"})
        assert res.status_code == 200
        assert res.json()["rows"][0]["value"] == 1


class TestFunnel:
    def test_funnel(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=True)
        _seed_full_deal(client, "c3", funded=False)
        res = client.get(f"{BASE}/funnel")
        body = res.json()
        assert body["stages"][0]["stage"] == "lead"
        assert body["stages"][0]["count"] == 3
        assert body["stages"][-1]["stage"] == "funded"
        assert body["stages"][-1]["count"] == 2


class TestCohorts:
    def test_cohorts_by_acquisition_month(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=True)
        res = client.get(f"{BASE}/cohorts", params={"dim": "acquisition_month"})
        assert res.status_code == 200
        assert len(res.json()) >= 1
        assert "cohort" in res.json()[0]


class TestScorecards:
    def test_rep_scorecard(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/scorecards/reps/rep-1")
        body = res.json()
        assert body["rep_id"] == "rep-1"
        assert body["funded"] == 1
        assert body["funded_amount"] == 300000
        assert "as_of_date" in body

    def test_leaderboard(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=True)
        res = client.get(f"{BASE}/leaderboard", params={"metric": "funded_amount"})
        body = res.json()
        assert body[0]["rank"] == 1


class TestAttribution:
    def test_attribution_credits_sum_to_one(self, client: TestClient) -> None:
        deal_id = _seed_full_deal(client, "c1", funded=True)
        for channel in ["sms", "call"]:
            client.post(
                f"{BASE}/ingest/touch",
                json={
                    "touch_id": f"touch-c1-{channel}",
                    "contact_pid": "c1",
                    "deal_id": deal_id,
                    "channel": channel,
                    "sent_at": _iso(datetime(2026, 6, 1, 13, 0, 0, tzinfo=UTC)),
                },
            )
        for model in ["first_touch", "last_touch", "linear", "time_decay", "markov", "shapley"]:
            res = client.get(f"{BASE}/attribution", params={"deal_id": deal_id, "model": model})
            assert res.status_code == 200, model
            credits = res.json()["credits"]
            total = round(sum(c["credit"] for c in credits), 4)
            assert abs(total - 1.0) < 0.001 or len(credits) == 0, model

    def test_attribution_summary_ranked(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="sms")
        res = client.get(f"{BASE}/attribution/summary")
        body = res.json()
        assert "channels" in body
        assert len(body["channels"]) >= 2

    def test_attribution_by_entity(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="sms")

        res = client.get(f"{BASE}/attribution/entities", params={"entity": "channel"})
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 2
        by_channel = {r["entity_id"]: r["attributed_revenue"] for r in body}
        assert "email" in by_channel
        assert "sms" in by_channel

        res = client.get(f"{BASE}/attribution/entities", params={"entity": "campaign_id"})
        assert res.status_code == 200
        assert res.json()[0]["entity_id"] == "camp-1"

        res = client.get(f"{BASE}/attribution/entities", params={"entity": "rep_id"})
        assert res.status_code == 200
        assert res.json()[0]["entity_id"] == "rep-1"

    def test_attribution_summary_period_filter(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        res = client.get(f"{BASE}/attribution/summary", params={"period": "2026-06"})
        assert res.status_code == 200
        assert res.json()["period"] == "2026-06"
        assert len(res.json()["channels"]) >= 1

        res = client.get(f"{BASE}/attribution/summary", params={"period": "2025"})
        assert res.status_code == 200
        assert res.json()["period"] == "2025"
        assert res.json()["channels"] == []

    def test_attribution_export_csv(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        res = client.get(f"{BASE}/attribution/export", params={"entity": "channel", "report_format": "csv"})
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        reader = csv.reader(io.StringIO(res.text))
        rows = list(reader)
        assert rows[0] == ["entity", "entity_id", "attributed_revenue"]
        assert rows[1][0] == "channel"


class TestChannelEffectiveness:
    def test_channel_effectiveness(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="sms")
        res = client.get(f"{BASE}/channel-effectiveness")
        body = res.json()
        assert len(body) >= 2
        by_channel = {r["channel"]: r for r in body}
        assert by_channel["email"]["funded_deals"] == 1
        assert by_channel["email"]["conversion_contribution_rate"] == 1.0

    def test_channel_recommendations(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="email")
        _seed_full_deal(client, "c3", funded=False, channel="sms")
        res = client.get(f"{BASE}/channel-recommendations")
        body = res.json()
        assert any(r["channel"] == "email" for r in body)


class TestReconciliation:
    def test_reconcile_passes(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        client.post(
            f"{BASE}/ingest/finance-source",
            json={
                "period": "2026-06",
                "funded_amount": 300000,
            },
        )
        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.status_code == 200
        assert res.json()["passed"] is True

    def test_reconcile_fails(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        client.post(
            f"{BASE}/ingest/finance-source",
            json={
                "period": "2026-06",
                "funded_amount": 500000,
            },
        )
        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.json()["passed"] is False


class TestAlerts:
    def test_threshold_alert(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        _seed_full_deal(client, "c2", funded=False)
        res = client.post(
            f"{BASE}/threshold-alert/app_to_fund_conversion", params={"threshold": 0.99, "direction": "below"}
        )
        assert res.status_code == 200
        assert len(res.json()["triggered"]) >= 1


class TestReports:
    def test_schedule_and_export_csv(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        scheduled = client.post(
            f"{BASE}/reports/schedule",
            json={
                "name": "monthly-funnel",
                "metric": "funnel",
                "period": "2026-06",
                "report_format": "csv",
            },
        ).json()
        report_id = scheduled["report_id"]
        res = client.get(f"{BASE}/reports/{report_id}/run")
        assert res.status_code == 200
        assert res.headers["content-type"] == "text/csv; charset=utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        assert rows[0][0] == "stage"

    def test_export_pdf(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/export/deal_count", params={"report_format": "pdf"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content.startswith(b"%PDF")

    def test_export_channel_effectiveness_csv(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True, channel="email")
        _seed_full_deal(client, "c2", funded=True, channel="sms")
        res = client.get(f"{BASE}/export/channel_effectiveness", params={"report_format": "csv"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "text/csv; charset=utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        assert rows[0][0] == "channel"
        channels = {r[0] for r in rows[1:]}
        assert "email" in channels
        assert "sms" in channels

    def test_export_funnel_csv(self, client: TestClient) -> None:
        _seed_full_deal(client, "c1", funded=True)
        res = client.get(f"{BASE}/export/funnel", params={"report_format": "csv"})
        assert res.status_code == 200
        assert res.headers["content-type"] == "text/csv; charset=utf-8"
        rows = list(csv.reader(io.StringIO(res.text)))
        assert rows[0][0] == "stage"


class TestABUplift:
    def test_ab_uplift(self, client: TestClient) -> None:
        _seed_full_deal(client, "treatment-1", funded=True)
        _seed_full_deal(client, "treatment-2", funded=True)
        _seed_full_deal(client, "control-1", funded=False)
        _seed_full_deal(client, "control-2", funded=False)
        res = client.post(
            f"{BASE}/ab-uplift",
            json={
                "treatment": ["deal-treatment-1", "deal-treatment-2"],
                "control": ["deal-control-1", "deal-control-2"],
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["treatment_rate"] == 1.0
        assert body["control_rate"] == 0.0
        assert body["lift"] == 1.0
