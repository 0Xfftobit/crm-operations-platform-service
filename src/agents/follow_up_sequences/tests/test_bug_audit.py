"""Regression tests for FUS bug-audit / P4 hardening items."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone

from app.domain.models import Contact
from app.events import dead_letter, get_dlq, get_events, publish, reset_events
from app.frequency import FrequencyStore, _day_key
from app.notifications import notification_log, notify_owner_of_reply
from app.notifications import reset as reset_notifications
from app.providers import deliver
from app.providers import reset as reset_providers


def test_frequency_day_key_is_timezone_normalized():
    """3.1 — day keys are normalized to UTC before formatting."""
    utc = datetime(2026, 8, 5, 3, 0, tzinfo=UTC)
    ny = datetime(2026, 8, 4, 23, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert _day_key("t", "c", "seq", utc) == _day_key("t", "c", "seq", ny)


def test_frequency_store_concurrent_increments():
    """3.4 — in-memory frequency counters are thread-safe."""
    freq = FrequencyStore()
    now = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)

    def worker() -> None:
        for _ in range(100):
            freq.increment("t", "c", "seq", now)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert freq.count_day("t", "c", "seq", now) == 400


def test_provider_sent_log_concurrent() -> None:
    """3.4 — provider mock send log is thread-safe."""
    reset_providers()
    c = Contact(contact_id="c", tenant_id="t")

    def worker() -> None:
        for _ in range(50):
            deliver(c, "sms", "hello")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    from app.providers import sent_log
    assert len(sent_log) == 200


def test_event_log_is_thread_safe() -> None:
    """3.4 — produced event log and DLQ are thread-safe."""
    reset_events()

    def worker() -> None:
        for i in range(50):
            publish("outreach.sent", {"n": i})
            if i % 10 == 0:
                dead_letter({"n": i}, reason="bad-schema")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(get_events()) == 200
    assert len(get_dlq()) == 20


def test_notification_log_is_thread_safe() -> None:
    """3.4 — fallback notification log is thread-safe."""
    reset_notifications()

    def worker() -> None:
        for _ in range(50):
            notify_owner_of_reply(
                "c1",
                "owner-1",
                enrollment_id="e1",
                step_id="s1",
                channel="email",
            )

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(notification_log()) == 200
