"""Advanced multi-touch attribution tests: time-decay, Markov, Shapley, lookback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.attribution import (
    _shapley_value,
    _time_decay,
    _touch_path,
    compute_attribution_for_deal,
)
from app.core.config import get_settings
from app.domain.models import OutcomeEvent, TouchEvent
from app.repositories import get_outcome_repository, get_touch_repository


def _make_touch(
    touch_id: str,
    channel: str,
    sent_at: datetime,
    contact: str = "c1",
    deal_id: str = "d1",
) -> TouchEvent:
    return TouchEvent(
        touch_id=touch_id,
        contact_pid=contact,
        deal_id=deal_id,
        channel=channel,
        sent_at=sent_at,
    )


def _make_funded(deal_id: str, contact: str, occurred_at: datetime, amount: float = 1000.0) -> OutcomeEvent:
    return OutcomeEvent(
        outcome_id=f"outcome-{contact}-funded",
        contact_pid=contact,
        deal_id=deal_id,
        stage="funded",
        event_type="loan.funded",
        amount=amount,
        occurred_at=occurred_at,
    )


class TestTimeDecay:
    def test_time_decay_credit_increases_toward_conversion(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        touches = [
            _make_touch("t1", "email", now - timedelta(days=14)),
            _make_touch("t2", "email", now - timedelta(days=7)),
            _make_touch("t3", "email", now - timedelta(days=1)),
        ]

        shares = _time_decay(touches, funded_at=now)
        assert len(shares) == 3
        assert round(sum(c for _, c in shares), 4) == 1.0
        assert shares[0][1] < shares[1][1] < shares[2][1]

    def test_time_decay_defaults_to_last_touch_reference(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        t1 = _make_touch("t1", "email", now - timedelta(days=7))
        t2 = _make_touch("t2", "email", now - timedelta(days=1))

        shares = _time_decay([t1, t2])  # no funded_at supplied
        assert round(sum(c for _, c in shares), 4) == 1.0
        assert shares[0][1] < shares[1][1]


class TestAttributionWindow:
    def test_touch_path_excludes_touches_beyond_lookback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ATTRIBUTION_LOOKBACK_D", "30")
        get_settings.cache_clear()

        funded_at = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        get_touch_repository().add(_make_touch("old", "email", funded_at - timedelta(days=60)))
        get_touch_repository().add(_make_touch("recent", "email", funded_at - timedelta(days=10)))
        get_outcome_repository().add(_make_funded("d1", "c1", funded_at))

        path = _touch_path("d1", funded_at)
        assert [t.touch_id for t in path] == ["recent"]


class TestShapley:
    def test_shapley_splits_channel_credit_equally_across_touches(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        touches = [
            _make_touch(f"t{i}", "email", now - timedelta(days=i))
            for i in range(3)
        ]
        # One funded deal with three email touches => channel conversion rate = 1.0.
        get_touch_repository().add(touches[0])
        get_touch_repository().add(touches[1])
        get_touch_repository().add(touches[2])
        get_outcome_repository().add(_make_funded("d1", "c1", now))

        shares = _shapley_value(touches)
        assert len(shares) == 3
        assert round(sum(c for _, c in shares), 4) == 1.0
        for _, credit in shares:
            assert round(credit, 4) == round(1 / 3, 4)

    def test_shapley_falls_back_to_linear_for_more_than_seven_channels(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        touches = [
            _make_touch(f"t{i}", f"ch{i}", now - timedelta(days=i), contact="c1", deal_id="d1")
            for i in range(9)
        ]

        shares = _shapley_value(touches)
        assert len(shares) == 9
        assert round(sum(c for _, c in shares), 4) == 1.0
        for _, credit in shares:
            assert round(credit, 4) == round(1 / 9, 4)


class TestMarkov:
    def test_markov_removal_effect_skews_credit_toward_high_converting_channel(self) -> None:
        now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

        # Background: 3 email-only deals that funded (email converts).
        for i in range(3):
            contact = f"ce{i}"
            deal_id = f"de{i}"
            get_touch_repository().add(_make_touch(f"back-e{i}", "email", now - timedelta(days=1), contact, deal_id))
            get_outcome_repository().add(_make_funded(deal_id, contact, now))

        # Background: 3 sms-only deals that did not fund (sms does not convert).
        for i in range(3):
            contact = f"cs{i}"
            deal_id = f"ds{i}"
            get_touch_repository().add(_make_touch(f"back-s{i}", "sms", now - timedelta(days=1), contact, deal_id))

        # Main deal: email then sms, funded.
        main_contact = "cmain"
        main_deal = "dmain"
        get_touch_repository().add(
            _make_touch("main-e", "email", now - timedelta(hours=2), main_contact, main_deal)
        )
        get_touch_repository().add(
            _make_touch("main-s", "sms", now - timedelta(hours=1), main_contact, main_deal)
        )
        get_outcome_repository().add(_make_funded(main_deal, main_contact, now))

        rec = compute_attribution_for_deal(main_deal, model="markov")
        assert rec is not None
        by_channel = {c.channel: c.credit for c in rec.credits}
        assert round(sum(by_channel.values()), 4) == 1.0
        assert by_channel["email"] > 0.9
        assert by_channel["sms"] < 0.1
