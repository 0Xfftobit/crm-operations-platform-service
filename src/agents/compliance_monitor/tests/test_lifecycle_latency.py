"""Audit lifecycle (cold-storage tiering + retention purge with a tamper-evident carry hash) and
precheck latency-budget enforcement (§8 fail-closed / NFR p99<100ms)."""
from datetime import UTC, datetime, timedelta

import pytest

from app import audit, coldstore, service
from app.core.config import get_settings
from app.domain.models import ActivityEvent, Consent, PrecheckRequest


# ── precheck latency budget ────────────────────────────────────────────────
def test_precheck_reports_latency():
    service.upsert_consent(Consent(lead_id="l", channel="sms", opted_in=True))
    r = service.run_precheck(PrecheckRequest(channel="sms", contact_id="l"))
    assert r.decision == "allow" and r.latency_ms is not None and r.latency_ms >= 0


def test_precheck_fails_closed_when_budget_exceeded(monkeypatch):
    monkeypatch.setenv("PRECHECK_LATENCY_BUDGET_MS", "0")   # any real work exceeds → deny
    get_settings.cache_clear()
    service.upsert_consent(Consent(lead_id="l", channel="sms", opted_in=True))
    r = service.run_precheck(PrecheckRequest(channel="sms", contact_id="l"))
    assert r.decision == "deny" and "latency_budget_exceeded" in r.reasons


# ── Audit cold-storage tiering + retention purge ───────────────────────────
@pytest.fixture
def cold_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COLD_STORE_GATEWAY", "local")
    monkeypatch.setenv("COLD_STORE_DIR", str(tmp_path / "cold"))
    monkeypatch.setenv("AUDIT_COLD_AGE_DAYS", "0")     # everything older than now is "aged"
    monkeypatch.setenv("AUDIT_RETENTION_YEARS", "0")   # past-retention cutoff = now
    get_settings.cache_clear()
    coldstore.reset()
    yield


def _ingest_old(n: int) -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    for i in range(n):
        service.record_activity(ActivityEvent(event_type="comm_sent", entity_id=f"l{i}",
                                              engine="crm", occurred_at=old + timedelta(seconds=i)))


def test_cold_tier_archives_aged_entries(cold_env):
    _ingest_old(3)
    result = service.audit_cold_tier()
    assert result["archived"] == 3
    assert coldstore.fetch(2)["seq"] == 2           # retrievable from cold storage


def test_purge_requires_anchor_and_archive(cold_env):
    _ingest_old(3)
    # No anchor yet → purge must refuse to delete (tamper-evidence not retained).
    assert service.audit_purge()["purged"] == 0
    assert len(audit.all_entries()) == 3


def test_purge_preserves_chain_verifiability(cold_env):
    _ingest_old(3)
    service.anchor_ledger(external_ref="pre-purge")   # retain tamper-evidence over the range
    result = service.audit_purge()
    assert result["purged"] == 3
    # Hot ledger is empty but the chain still verifies from the carry hash.
    assert audit.all_entries() == []
    v = audit.verify_chain()
    assert v["verified"] is True and v["purged_through_seq"] == 3
    # Purged records remain retrievable in cold storage.
    assert service.cold_entry(1)["seq"] == 1


def test_append_after_purge_continues_the_chain(cold_env):
    _ingest_old(3)
    service.anchor_ledger()
    service.audit_purge()                              # purge seqs 1..3
    out = service.record_activity(ActivityEvent(event_type="comm_sent", entity_id="new"))
    assert out["audit_seq"] == 4                       # seq continues, no reuse
    v = audit.verify_chain()
    assert v["verified"] is True                       # chain from carry hash → new entry holds


def test_lifecycle_status_and_endpoints(cold_env, client, api_headers):
    _ingest_old(2)
    client.post("/api/v1/audit/anchor", headers=api_headers)
    purged = client.post("/api/v1/audit/purge", headers=api_headers).json()
    assert purged["purged"] == 2
    life = client.get("/api/v1/audit/lifecycle").json()
    assert life["purged_through_seq"] == 2 and life["hot_entries"] == 0
    assert client.get("/api/v1/audit/cold/1").json()["cold_record"]["seq"] == 1


def test_purge_disabled_without_cold_store():
    # Default COLD_STORE_GATEWAY=none → purge refuses (never delete un-preserved records).
    service.record_activity(ActivityEvent(event_type="comm_sent", entity_id="l"))
    service.anchor_ledger()
    assert service.audit_purge()["purged"] == 0
