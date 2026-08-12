"""Acceptance / requirements test suite for the Performance Analytics System (PAS).

This module exercises the functional and non-functional requirements, business
rules, and acceptance criteria captured in:

- ``docs/ticket_description_pas.md``
- ``docs/implementation_status_pas.md``

Tests are self-contained, run in-memory, and do not require a real Kafka broker,
Postgres, warehouse, or external feedback-loop service.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.clients import feedback_client
from app.core.config import get_settings
from app.domain.models import OutcomeEvent
from app.messaging import kafka_bus

from .helpers import outcome_payload, touch_payload

BASE = "/api/v1"


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=UTC).isoformat()


def _seed_deal(
    client: TestClient,
    contact: str,
    deal_id: str,
    funded: bool = True,
    channel: str = "email",
    team: str = "team-a",
    rep: str = "rep-1",
    tenant: str = "default",
    amount: float = 300000.0,
) -> None:
    """Seed one deal with a touch, lead, app and optional funded outcome."""
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    client.post(
        f"{BASE}/ingest/touch",
        json={
            "touch_id": f"touch-{contact}-{channel}",
            "contact_pid": contact,
            "deal_id": deal_id,
            "rep_id": rep,
            "team_id": team,
            "tenant_id": tenant,
            "channel": channel,
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
            "rep_id": rep,
            "team_id": team,
            "tenant_id": tenant,
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
            "rep_id": rep,
            "team_id": team,
            "tenant_id": tenant,
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
                "rep_id": rep,
                "team_id": team,
                "tenant_id": tenant,
                "stage": "funded",
                "amount": amount,
                "event_type": "loan.funded",
                "occurred_at": _iso(now + timedelta(days=14)),
            },
        )


class TestIngestion:
    """FR-001 / FR-002 — touch, outcome, campaign-cost and finance-source ingestion."""

    def test_ingest_touch_outcome_cost_and_finance(self, client: TestClient) -> None:
        touch = client.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": "t-req-1",
                "contact_pid": "c-req-1",
                "deal_id": "d-req-1",
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "channel": "email",
                "sent_at": _iso(datetime.now(UTC)),
            },
        )
        assert touch.status_code == 200
        assert touch.json()["status"] == "ingested"

        lead = client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "o-req-1-lead",
                "contact_pid": "c-req-1",
                "deal_id": "d-req-1",
                "stage": "lead",
                "event_type": "stage.lead",
                "occurred_at": _iso(datetime.now(UTC) + timedelta(hours=1)),
            },
        )
        assert lead.status_code == 200

        cost = client.post(
            f"{BASE}/ingest/campaign-cost",
            json={
                "campaign_id": "camp-req-1",
                "cost": 5000.0,
                "period": "2026-06",
            },
        )
        assert cost.status_code == 200
        assert cost.json()["status"] == "ingested"

        finance = client.post(
            f"{BASE}/ingest/finance-source",
            json={"period": "2026-06", "funded_amount": 100000.0},
        )
        assert finance.status_code == 200
        assert finance.json()["status"] == "ingested"

    def test_ingest_idempotent_deduplication(self, client: TestClient) -> None:
        payload = {
            "event_id": "evt-req-dup",
            "touch_id": "t-req-dup",
            "contact_pid": "c-req-dup",
            "deal_id": "d-req-dup",
            "channel": "email",
            "sent_at": _iso(datetime.now(UTC)),
        }
        first = client.post(f"{BASE}/ingest/touch", json=payload)
        second = client.post(f"{BASE}/ingest/touch", json=payload)
        assert first.json()["status"] == "ingested"
        assert second.json()["status"] == "duplicate"


class TestGovernedMetrics:
    """FR-003 / BR-001 / FR-016 — governed, versioned semantic metric layer."""

    def test_builtin_metric_definition_is_queryable(self, client: TestClient) -> None:
        res = client.get(f"{BASE}/metrics/definitions/deal_count")
        assert res.status_code == 200
        assert res.json()["name"] == "deal_count"
        assert res.json()["status"] == "published"

    def test_metric_versioning_and_lineage(self, client: TestClient) -> None:
        publish = client.post(
            f"{BASE}/metrics/definitions",
            json={
                "name": "acceptance_metric",
                "label": "Acceptance Metric",
                "metric_type": "count",
                "description": "A governed test metric",
                "owner": "qa",
                "depends_on": ["deal_count"],
                "lineage": ["metrics_collector", "dashboard_builder"],
            },
        )
        assert publish.status_code == 200
        assert publish.json()["version"] == 1
        assert publish.json()["status"] == "published"

        v2 = client.post(
            f"{BASE}/metrics/definitions",
            json={
                "name": "acceptance_metric",
                "label": "Acceptance Metric V2",
                "metric_type": "count",
                "owner": "qa",
                "depends_on": ["deal_count"],
                "lineage": ["dashboard_builder"],
            },
        )
        assert v2.json()["version"] == 2

        latest = client.get(f"{BASE}/metrics/definitions/acceptance_metric")
        assert latest.json()["version"] == 2

        history = client.get(f"{BASE}/metrics/definitions/acceptance_metric/history")
        assert len(history.json()) == 2

        lineage = client.get(f"{BASE}/metrics/definitions/acceptance_metric/lineage")
        assert lineage.json()["metric"] == "acceptance_metric"
        assert "deal_count" in lineage.json()["depends_on"]

    def test_kpi_api_queries_governed_metric(self, client: TestClient) -> None:
        _seed_deal(client, "gm1", "deal-gm1", funded=True)
        res = client.get(f"{BASE}/kpi/deal_count")
        assert res.status_code == 200
        assert res.json()["metric"] == "deal_count"
        assert res.json()["rows"][0]["value"] == 1


class TestDashboards:
    """FR-004 / FR-006 / FR-013 / acceptance criteria — funnel, scorecards, cohorts, leaderboard."""

    def test_funnel_scorecard_cohort_and_leaderboard(self, client: TestClient) -> None:
        _seed_deal(client, "dash1", "deal-dash1", funded=True)
        _seed_deal(client, "dash2", "deal-dash2", funded=False, channel="sms")

        funnel = client.get(f"{BASE}/funnel", params={"period": "2026-06"})
        assert funnel.status_code == 200
        stages = {s["stage"]: s["count"] for s in funnel.json()["stages"]}
        assert stages["lead"] == 2
        assert stages["funded"] == 1

        scorecards = client.get(f"{BASE}/scorecards/reps")
        assert scorecards.status_code == 200
        reps = {s["rep_id"]: s for s in scorecards.json()}
        assert reps["rep-1"]["funded"] == 1

        rep_card = client.get(f"{BASE}/scorecards/reps/rep-1")
        assert rep_card.status_code == 200
        assert rep_card.json()["funded_amount"] == 300000.0

        leaderboard = client.get(f"{BASE}/leaderboard", params={"metric": "funded_amount"})
        assert leaderboard.status_code == 200
        assert leaderboard.json()[0]["rank"] == 1

        cohorts = client.get(f"{BASE}/cohorts", params={"dim": "acquisition_month"})
        assert cohorts.status_code == 200
        assert all("cohort" in c for c in cohorts.json())


class TestAttribution:
    """FR-008 / CEAAPSS-562 — all six attribution models credit touches to funded deals."""

    def _background_for_markov_and_shapley(self, client: TestClient) -> None:
        # Email-only deals that funded -> email channel converts well.
        for i in range(3):
            _seed_deal(client, f"back-e{i}", f"deal-back-e{i}", funded=True, channel="email")
        # SMS-only deals that did not fund -> sms channel does not convert.
        for i in range(3):
            _seed_deal(client, f"back-s{i}", f"deal-back-s{i}", funded=False, channel="sms")

    def test_all_six_attribution_models_sum_to_one(self, client: TestClient) -> None:
        self._background_for_markov_and_shapley(client)

        now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
        deal_id = "deal-main"
        # Main deal: email first, sms last, then funds.
        client.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": "main-email",
                "contact_pid": "main",
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "channel": "email",
                "sent_at": _iso(now + timedelta(days=1)),
                "opened": True,
            },
        )
        client.post(
            f"{BASE}/ingest/touch",
            json={
                "touch_id": "main-sms",
                "contact_pid": "main",
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "channel": "sms",
                "sent_at": _iso(now + timedelta(days=7)),
                "opened": True,
            },
        )
        client.post(
            f"{BASE}/ingest/outcome",
            json={
                "outcome_id": "main-funded",
                "contact_pid": "main",
                "deal_id": deal_id,
                "rep_id": "rep-1",
                "team_id": "team-a",
                "tenant_id": "default",
                "stage": "funded",
                "amount": 250000.0,
                "event_type": "loan.funded",
                "occurred_at": _iso(now + timedelta(days=14)),
            },
        )

        models = ["first_touch", "last_touch", "linear", "time_decay", "markov", "shapley"]
        for model in models:
            res = client.get(f"{BASE}/attribution", params={"deal_id": deal_id, "model": model})
            assert res.status_code == 200, model
            credits = res.json()["credits"]
            assert credits, f"{model} returned no credits"
            total = round(sum(c["credit"] for c in credits), 4)
            assert abs(total - 1.0) < 0.001, f"{model} credits sum to {total}"

    def test_attribution_entities_ranked_by_revenue(self, client: TestClient) -> None:
        _seed_deal(client, "attr1", "deal-attr1", funded=True, channel="email")
        _seed_deal(client, "attr2", "deal-attr2", funded=True, channel="sms")
        res = client.get(f"{BASE}/attribution/entities", params={"entity": "channel"})
        assert res.status_code == 200
        channels = {r["entity_id"]: r["attributed_revenue"] for r in res.json()}
        assert "email" in channels
        assert "sms" in channels


class TestChannelEffectiveness:
    """FR-009 / CEAAPSS-563 — channel effectiveness, heatmaps and recommendations."""

    def test_channel_effectiveness_and_recommendations(self, client: TestClient) -> None:
        _seed_deal(client, "ce1", "deal-ce1", funded=True, channel="email")
        _seed_deal(client, "ce2", "deal-ce2", funded=True, channel="email")
        _seed_deal(client, "ce3", "deal-ce3", funded=False, channel="sms")

        res = client.get(f"{BASE}/channel-effectiveness", params={"period": "2026-06"})
        assert res.status_code == 200
        by_channel = {r["channel"]: r for r in res.json()}
        assert by_channel["email"]["funded_deals"] == 2
        assert by_channel["email"]["conversion_contribution_rate"] == 1.0

        recs = client.get(f"{BASE}/channel-recommendations", params={"period": "2026-06"})
        assert recs.status_code == 200
        assert any(r["channel"] == "email" for r in recs.json())

        heatmaps = client.get(f"{BASE}/channel-heatmaps", params={"period": "2026-06"})
        assert heatmaps.status_code == 200
        assert "by_hour" in heatmaps.json()


class TestReconciliation:
    """FR-012 / BR-002 / NFR-04 — reconcile PAS funded amounts to finance source-of-truth."""

    def test_reconciliation_passes_when_amounts_match(self, client: TestClient) -> None:
        _seed_deal(client, "rec1", "deal-rec1", funded=True, amount=300000.0)
        client.post(f"{BASE}/ingest/finance-source", json={"period": "2026-06", "funded_amount": 300000.0})
        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.status_code == 200
        body = res.json()
        assert body["passed"] is True
        assert body["mart_amount"] == 300000.0
        assert body["finance_amount"] == 300000.0
        assert abs(body["delta"]) <= 0.01

    def test_reconciliation_fails_when_drift_exceeds_tolerance(self, client: TestClient) -> None:
        _seed_deal(client, "rec2", "deal-rec2", funded=True, amount=300000.0)
        client.post(f"{BASE}/ingest/finance-source", json={"period": "2026-06", "funded_amount": 500000.0})
        res = client.post(f"{BASE}/reconcile/2026-06")
        assert res.status_code == 200
        assert res.json()["passed"] is False


class TestAlertsAndReports:
    """FR-014 — threshold alerts and scheduled reports."""

    def test_threshold_alert_fires_and_scheduled_report_runs(self, client: TestClient) -> None:
        _seed_deal(client, "alert1", "deal-alert1", funded=True)
        _seed_deal(client, "alert2", "deal-alert2", funded=False)

        alert = client.post(
            f"{BASE}/threshold-alert/app_to_fund_conversion",
            params={"threshold": 0.99, "direction": "below"},
        )
        assert alert.status_code == 200
        assert len(alert.json()["triggered"]) >= 1

        scheduled = client.post(
            f"{BASE}/reports/schedule",
            json={
                "name": "acceptance-funnel",
                "metric": "funnel",
                "period": "2026-06",
                "report_format": "csv",
            },
        )
        assert scheduled.status_code == 200
        report_id = scheduled.json()["report_id"]

        run = client.get(f"{BASE}/reports/{report_id}/run")
        assert run.status_code == 200
        assert run.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(run.text)))
        assert rows[0][0] == "stage"


class TestExport:
    """Export KPI, funnel and channel reports as CSV and PDF."""

    def test_csv_and_pdf_export(self, client: TestClient) -> None:
        _seed_deal(client, "exp1", "deal-exp1", funded=True)

        csv_res = client.get(f"{BASE}/export/deal_count", params={"report_format": "csv", "period": "2026-06"})
        assert csv_res.status_code == 200
        assert csv_res.headers["content-type"].startswith("text/csv")

        pdf_res = client.get(f"{BASE}/export/deal_count", params={"report_format": "pdf", "period": "2026-06"})
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"
        assert pdf_res.content.startswith(b"%PDF")

        funnel_csv = client.get(f"{BASE}/export/funnel", params={"report_format": "csv", "period": "2026-06"})
        assert funnel_csv.status_code == 200
        rows = list(csv.reader(io.StringIO(funnel_csv.text)))
        assert rows[0][0] == "stage"


class TestFeedbackFanout:
    """FLS outcome fan-out (P4) with unique ``X-Request-ID`` headers."""

    def test_fanout_posts_to_fls_with_x_request_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTCOME_FANOUT_ENABLED", "true")
        monkeypatch.setenv("FEEDBACK_LOOP_URL", "http://fls:8000")
        monkeypatch.setenv("FEEDBACK_LOOP_API_KEY", "fls-secret")
        get_settings.cache_clear()

        captured: dict = {}

        class FakeClient:
            def __init__(self, *args, **kwargs):  # noqa: ARG002
                pass

            def __enter__(self):
                mock = MagicMock()

                def _post(url, *, json, headers, **kwargs):  # noqa: ARG001
                    captured["url"] = url
                    captured["json"] = json
                    captured["headers"] = headers
                    response = MagicMock()
                    response.status_code = 200
                    response.raise_for_status = lambda: None
                    response.json.return_value = {"status": "recorded"}
                    return response

                mock.post = _post
                return mock

            def __exit__(self, *args, **kwargs):  # noqa: ARG002
                return False

        monkeypatch.setattr(httpx, "Client", FakeClient)

        outcome = OutcomeEvent(
            outcome_id="out-fanout-1",
            deal_id="deal-fanout-1",
            contact_pid="c-fanout-1",
            event_type="loan.funded",
            stage="funded",
            amount=400000.0,
            occurred_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
        )
        result = feedback_client.fanout_outcome(outcome, settings=get_settings())

        assert result == {"status": "recorded"}
        assert captured["url"] == "http://fls:8000/api/v1/outcomes"
        assert captured["headers"]["X-API-Key"] == "fls-secret"
        assert captured["headers"]["X-Request-ID"]
        assert captured["json"]["event_type"] == "loan.funded"
        assert captured["json"]["metric_value"] == 400000.0


class TestKafkaContract:
    """FR-018 / NFR-02 — Kafka producer/consumer contract with in-memory fallback."""

    def test_canonical_topics_and_partition_key(self) -> None:
        assert kafka_bus.canonical_topic("outreach.sent") == "crm.outreach.sent"
        assert kafka_bus.short_topic("crm.outreach.sent") == "outreach.sent"
        assert kafka_bus.partition_key({"deal_id": "D1", "contact_pid": "C1"}) == "D1"
        assert kafka_bus.partition_key({"contact_pid": "CP1"}) == "CP1"
        assert kafka_bus.partition_key({}) == "unknown"

    def test_in_memory_produce_sync_and_bus_stats(self) -> None:
        kafka_bus.reset_kafka_bus()
        row = kafka_bus.produce_sync("outreach.sent", touch_payload())
        assert row is not None
        assert row["topic"] == "crm.outreach.sent"
        assert row["transport"] == "in_process_bus"

        stats = kafka_bus.bus_stats()
        assert stats["messages_total"] == 1
        assert stats["transport"] == "in_process_bus"

    def test_kafka_touch_and_outcome_producers_gated_by_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAFKA_PRODUCE", "true")
        get_settings.cache_clear()
        kafka_bus.reset_kafka_bus()

        # Use the real model to ensure serialization matches
        from app.domain.models import TouchEvent

        touch_event = TouchEvent(**touch_payload())
        outcome_event = OutcomeEvent(**outcome_payload(stage="funded"))

        row_touch = kafka_bus.produce_touch(touch_event)
        assert row_touch is not None
        assert row_touch["topic"] == "crm.outreach.sent"

        row_outcome = kafka_bus.produce_outcome(outcome_event)
        assert row_outcome is not None
        assert row_outcome["topic"] == "crm.outcome.funded"

        stats = kafka_bus.bus_stats()
        assert stats["messages_total"] == 2
        assert stats["topics"]["crm.outreach.sent"] == 1
        assert stats["topics"]["crm.outcome.funded"] == 1

    def test_consumer_dispatches_short_topics_from_in_memory_bus(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app import service

        class _BoomConsumer:
            def __init__(self, *args, **kwargs):  # noqa: ARG002
                pass

            async def start(self) -> None:
                raise OSError("no broker")

            async def stop(self) -> None:
                pass

        try:
            import aiokafka
        except Exception:  # pragma: no cover
            pytest.skip("aiokafka not installed")

        monkeypatch.setenv("KAFKA_CONSUMER_ENABLED", "true")
        get_settings.cache_clear()
        service.reset_state()
        kafka_bus.reset_kafka_bus()
        monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", _BoomConsumer)

        async def _drain() -> None:
            for _ in range(200):
                if kafka_bus.bus_stats()["consumer_lag"] == 0:
                    break
                await asyncio.sleep(0.01)

        async def _run() -> None:
            start = await kafka_bus.start_consumer()
            assert start["started"] is True

            await kafka_bus.produce("outreach.sent", touch_payload(deal_id="d-consume"))
            await kafka_bus.produce(
                "outcome.funded",
                outcome_payload(deal_id="d-consume", stage="funded"),
            )

            await _drain()
            await kafka_bus.stop_consumer()

        asyncio.run(_run())

        assert service.get_stats()["touches"] == 1
        assert service.get_stats()["outcomes"] == 1


class TestSecurityAndRLS:
    """Default-dev-key / wildcard-CORS rejection and row-level security header filters."""

    def test_default_dev_key_rejected_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("API_KEY", "change-me-dev-key")
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="Default dev API key is not allowed"):
            get_settings()

    def test_wildcard_cors_rejected_in_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("API_KEY", "prod-secret-key")
        monkeypatch.setenv("CORS_ORIGINS", "*")
        get_settings.cache_clear()

        with pytest.raises(ValidationError, match="Wildcard CORS origin"):
            get_settings()

    def test_rls_headers_filter_by_tenant_team_and_rep(self, client: TestClient) -> None:
        _seed_deal(client, "rls1", "deal-rls1", funded=True, tenant="default", team="team-a", rep="rep-1")

        for header, value in [
            ("X-Tenant-Id", "other"),
            ("X-Team-Id", "team-b"),
            ("X-Rep-Id", "rep-99"),
        ]:
            res = client.get(f"{BASE}/kpi/deal_count", headers={header: value})
            assert res.status_code == 200, header
            assert res.json()["rows"] == [], header

    def test_header_filter_overrides_query_param(self, client: TestClient) -> None:
        _seed_deal(client, "rls2", "deal-rls2", funded=True, team="team-a", rep="rep-1")
        res = client.get(
            f"{BASE}/kpi/deal_count",
            params={"team_id": "team-a", "rep_id": "rep-1"},
            headers={"X-Team-Id": "team-b"},
        )
        assert res.status_code == 200
        assert res.json()["rows"] == []
