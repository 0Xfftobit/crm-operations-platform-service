"""Coverage-gap / HTTP-poll / dead-letter monitoring, batch fairness boundaries,
and thread-safety of audit append + case updates."""
import asyncio
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import httpx
import pytest

from app import audit, casework, http_source, service
from app.core.config import get_settings
from app.repositories import get_alert_repository, get_case_repository
from app.spark_batch import disparity_from_counts


# ── HTTP-poll upstream (EVENT_SOURCE_GATEWAY=http) ─────────────────────────
def _http_env(monkeypatch):
    monkeypatch.setenv("EVENT_SOURCE_GATEWAY", "http")
    monkeypatch.setenv("AUDIT_SOURCE_URL", "http://upstream.local/api")
    get_settings.cache_clear()


def test_http_poll_uses_source_seq_when_no_server_cursor(monkeypatch):
    _http_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "events": [
                {"event_type": "comm_sent", "entity_id": "l1", "source_seq": 10,
                 "engine": "up"},
            ],
        })

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await http_source.poll_once(client, 0)

    count, cursor = asyncio.run(_run())
    assert count == 1
    assert cursor == 10


def test_http_poll_empty_page_advances_server_cursor(monkeypatch):
    _http_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"events": [], "cursor": 7})

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await http_source.poll_once(client, 5)

    count, cursor = asyncio.run(_run())
    assert count == 0
    assert cursor == 7


def test_http_poll_survives_upstream_error(monkeypatch):
    _http_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await http_source.poll_once(client, 0)

    asyncio.run(_run())


# ── Coverage-gap / dead-letter monitoring over HTTP ────────────────────────
def test_coverage_report_and_sweep_endpoints(client, api_headers):
    client.post("/api/v1/activity", headers=api_headers, json={
        "event_type": "comm_sent", "engine": "follow-up",
        "source_seq": 1, "entity_id": "l1"})
    client.post("/api/v1/activity", headers=api_headers, json={
        "event_type": "comm_sent", "engine": "follow-up",
        "source_seq": 3, "entity_id": "l2"})

    report = client.get("/api/v1/coverage").json()
    assert report["sources"]["follow-up"]["outstanding_gaps"] == [2]

    sweep = client.post("/api/v1/coverage/sweep", headers=api_headers).json()
    assert "stale_sources" in sweep
    assert "dead_letters" in sweep


def test_dead_letter_alert_respects_threshold(monkeypatch):
    monkeypatch.setenv("DEAD_LETTER_ALERT_THRESHOLD", "3")
    get_settings.cache_clear()

    service.note_dead_letter()
    service.note_dead_letter()
    assert not any(a.rule_id == "dead_letter_backlog"
                   for a in get_alert_repository().list())

    service.note_dead_letter()
    assert any(a.rule_id == "dead_letter_backlog" and a.severity == "critical"
               for a in get_alert_repository().list())


# ── Batch fairness / spark_batch boundaries ────────────────────────────────
def test_batch_fairness_exact_floor_and_min_sample():
    # Ratio exactly 0.80 (the default floor) → passes.
    r = service.evaluate_fairness(
        "lead_scoring", "channel",
        {"a": {"selected": 80, "total": 100},
         "b": {"selected": 100, "total": 100}})
    assert r.passes is True
    assert r.ratio == 0.8

    # Just below the floor → fails.
    r2 = service.evaluate_fairness(
        "lead_scoring", "channel",
        {"a": {"selected": 79, "total": 100},
         "b": {"selected": 100, "total": 100}})
    assert r2.passes is False

    # Below the minimum sample size → min_sample_met False; the guard suppresses
    # the alert even when the ratio would otherwise fail.
    r3 = service.evaluate_fairness(
        "lead_scoring_small", "channel",
        {"a": {"selected": 3, "total": 10},
         "b": {"selected": 8, "total": 10}})
    assert r3.min_sample_met is False
    assert r3.passes is False
    assert not any(a.rule_id == "four_fifths_rule" and a.entity_id == "lead_scoring_small"
                   for a in get_alert_repository().list())


def test_spark_batch_aggregates_multiple_groups_and_redlining():
    counts = [
        {"sub_agent": "pricing", "cohort_dimension": "channel",
         "cohort": "sms", "selected": 50, "total": 100},
        {"sub_agent": "pricing", "cohort_dimension": "channel",
         "cohort": "email", "selected": 60, "total": 100},
        {"sub_agent": "scoring", "cohort_dimension": "zip",
         "cohort": "a", "selected": 42, "total": 100},
        {"sub_agent": "scoring", "cohort_dimension": "zip",
         "cohort": "b", "selected": 31, "total": 100},
    ]
    results = {r["sub_agent"]: r for r in disparity_from_counts(counts)}
    assert results["pricing"]["passes"] is True
    assert results["scoring"]["passes"] is False
    assert results["scoring"]["redlining_risk"] is True
    assert results["pricing"]["redlining_risk"] is False


# ── Concurrency: thread-safety of audit append and case updates ────────────
def test_concurrent_audit_appends_are_unique_and_chain_verifies():
    n = 50

    def worker(i):
        return audit.append(
            event_id=f"concurrent-{i}", event_type="test",
            actor_type="system", entity_id="lead", engine="concurrency")

    with ThreadPoolExecutor(max_workers=10) as ex:
        entries = list(ex.map(worker, range(n)))

    seqs = [e.seq for e in entries]
    assert len(set(seqs)) == n
    assert sorted(seqs) == list(range(1, n + 1))
    assert audit.verify_chain()["verified"] is True


def test_concurrent_audit_appends_do_not_interleave_writes():
    """Append is atomic: the returned sequence numbers are monotonic and gap-free."""
    n = 100
    results = queue.Queue()

    def worker(i):
        results.put(audit.append(
            event_id=f"race-{i}", event_type="test",
            actor_type="system", entity_id="x", engine="race"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = list(results.queue)
    assert len(entries) == n
    assert sorted(e.seq for e in entries) == list(range(1, n + 1))


def test_concurrent_case_updates_are_safe():
    alert, case, _ = casework.raise_alert(
        get_alert_repository(), get_case_repository(),
        rule_id="concurrency", domain="audit", severity="critical",
        entity_id="x", message="test", now=datetime.now(UTC),
        sla_hours=get_settings().case_sla_hours, dedup_window_min=0)
    assert case is not None

    statuses = ["investigating", "remediated", "closed"]

    def worker(status):
        return casework.update_case(
            get_case_repository(), case_id=case.case_id, status=status)

    with ThreadPoolExecutor(max_workers=len(statuses)) as ex:
        results = list(ex.map(worker, statuses))

    final = get_case_repository().get(case.case_id)
    assert final is not None
    assert final.status in statuses
    assert all(r.case_id == case.case_id for r in results)
