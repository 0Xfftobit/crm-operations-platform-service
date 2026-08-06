"""Coverage-gap monitoring (BR-3), HTTP-poll upstream, OpenTelemetry tracing, and the
RERA/TILA-RESPA disclosure-at-pipeline-stage tracker (FR-006)."""
import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app import coverage, http_source, service
from app.core.config import get_settings
from app.core.tracing import configure_tracing
from app.domain.models import ActivityEvent
from app.repositories import get_alert_repository, get_disclosure_repository


# ── Coverage-gap / dropped-event monitoring (BR-3) ─────────────────────────
def test_sequence_gap_raises_a_coverage_alert():
    service.record_activity(ActivityEvent(event_type="comm_sent", engine="follow-up", source_seq=1))
    service.record_activity(ActivityEvent(event_type="comm_sent", engine="follow-up", source_seq=3))
    alerts = get_alert_repository().list()
    assert any(a.rule_id == "coverage_gap" and a.domain == "audit" for a in alerts)
    assert coverage.report()["sources"]["follow-up"]["outstanding_gaps"] == [2]
    # A late-arriving seq 2 fills the gap.
    service.record_activity(ActivityEvent(event_type="comm_sent", engine="follow-up", source_seq=2))
    assert coverage.report()["sources"]["follow-up"]["outstanding_gaps"] == []


def test_heartbeat_staleness_detects_silent_source():
    now = datetime.now(UTC)
    coverage.observe("nba", 1, now=now - timedelta(minutes=90))
    stale = coverage.stale_sources(now=now, max_min=60)
    assert stale and stale[0]["source"] == "nba"


def test_dead_letter_crossing_threshold_alerts():
    service.note_dead_letter()      # threshold default = 1
    assert any(a.rule_id == "dead_letter_backlog" and a.severity == "critical"
               for a in get_alert_repository().list())


# ── HTTP-poll upstream (EVENT_SOURCE_GATEWAY=http) ─────────────────────────
def test_http_poll_ingests_events_and_advances_cursor(monkeypatch):
    monkeypatch.setenv("EVENT_SOURCE_GATEWAY", "http")
    monkeypatch.setenv("AUDIT_SOURCE_URL", "http://upstream.local/api")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/events")
        return httpx.Response(200, json={
            "events": [
                {"event_type": "comm_sent", "entity_id": "l1", "source_seq": 4, "engine": "up"},
                {"event_type": "decision", "entity_id": "l2", "source_seq": 5, "engine": "up",
                 "actor_type": "engine", "explain_ref": "x"},
            ],
            "cursor": 5,
        })

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await http_source.poll_once(client, cursor=0)

    count, cursor = asyncio.run(_run())
    assert count == 2 and cursor == 5
    assert len(service.audit.all_entries()) == 2


# ── OpenTelemetry tracing ──────────────────────────────────────────────────
def test_tracing_disabled_is_noop():
    from fastapi import FastAPI
    assert configure_tracing(FastAPI()) is False       # TRACING_ENABLED defaults false


def test_tracing_enabled_instruments_app(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("TRACING_ENABLED", "true")
    get_settings.cache_clear()
    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict:
        return {"ok": True}

    assert configure_tracing(app) is True              # otel libs present → instrumented
    with TestClient(app) as c:
        assert c.get("/ping").json() == {"ok": True}   # still serves under instrumentation


# ── RERA/TILA-RESPA disclosure-at-pipeline-stage (FR-006) ──────────────────
def test_stage_change_starts_required_disclosures():
    started = service.on_stage_change("deal-1", "application")
    kinds = {s["kind"] for s in started}
    assert kinds == {"loan_estimate"}
    timer = get_disclosure_repository().get("deal-1", "loan_estimate")
    assert timer is not None and (timer.due_at - timer.started_at).days == 3


def test_deal_tracks_multiple_disclosures():
    service.on_stage_change("deal-2", "application")     # loan_estimate
    service.on_stage_change("deal-2", "denied")          # adverse_action
    kinds = {t.kind for t in get_disclosure_repository().list() if t.deal_id == "deal-2"}
    assert kinds == {"loan_estimate", "adverse_action"}


def test_satisfy_a_specific_disclosure_kind():
    service.on_stage_change("deal-3", "pre_closing")      # closing_disclosure
    timer = service.satisfy_disclosure("deal-3", kind="closing_disclosure")
    assert timer.satisfied is True


def test_stage_and_requirements_endpoints(client, api_headers):
    resp = client.post("/api/v1/disclosures/stage", headers=api_headers,
                       json={"deal_id": "deal-9", "stage": "application"})
    assert resp.status_code == 200
    assert resp.json()["started"][0]["kind"] == "loan_estimate"
    reqs = client.get("/api/v1/disclosures/requirements").json()["requirements"]
    assert "application" in reqs and "denied" in reqs
