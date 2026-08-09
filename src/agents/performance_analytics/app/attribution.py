"""P3 — Multi-touch attribution: first, last, linear, time-decay, Markov, Shapley."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import chain, combinations
from typing import Any

from app.core.config import get_settings
from app.domain.models import AttributionCredit, AttributionRecord
from app.period_utils import _in_period
from app.repositories import get_attribution_repository, get_outcome_repository, get_touch_repository


def _now() -> datetime:
    return datetime.now(UTC)


def _funded_outcomes() -> list[Any]:
    return [o for o in get_outcome_repository().list(stage="funded")]


def _funded_deals(period: str | None = None) -> dict[str, dict[str, Any]]:
    """Map deal/contact -> funded outcome, optionally filtered by period."""
    funded: dict[str, dict[str, Any]] = {}
    for o in _funded_outcomes():
        if not _in_period(o.occurred_at, period):
            continue
        key = o.deal_id or o.contact_pid
        if key not in funded or o.occurred_at > funded[key]["occurred_at"]:
            funded[key] = {"outcome": o, "amount": o.amount or 0.0, "occurred_at": o.occurred_at}
    return funded


def _touch_path(deal_key: str, funded_at: datetime) -> list[Any]:
    settings = get_settings()
    touches = get_touch_repository().list(deal_id=deal_key) + get_touch_repository().list(contact_pid=deal_key)
    # De-duplicate by touch_id while preserving order
    seen: set[str] = set()
    path: list[Any] = []
    for t in sorted(touches, key=lambda x: x.sent_at):
        if t.touch_id in seen:
            continue
        if funded_at - timedelta(days=settings.attribution_lookback_d) <= t.sent_at <= funded_at:
            seen.add(t.touch_id)
            path.append(t)
    return path


# ── Rule-based attribution ──────────────────────────────────────────────────


def _first_touch(path: list[Any]) -> list[tuple[Any, float]]:
    if not path:
        return []
    return [(path[0], 1.0)]


def _last_touch(path: list[Any]) -> list[tuple[Any, float]]:
    if not path:
        return []
    return [(path[-1], 1.0)]


def _linear(path: list[Any]) -> list[tuple[Any, float]]:
    if not path:
        return []
    share = 1.0 / len(path)
    return [(t, share) for t in path]


def _time_decay(path: list[Any], funded_at: datetime | None = None) -> list[tuple[Any, float]]:
    if not path:
        return []
    settings = get_settings()
    # Use the actual conversion time when available; fall back to the last touch.
    reference = funded_at if funded_at is not None else max(t.sent_at for t in path)
    weights: list[float] = []
    for t in path:
        days = (reference - t.sent_at).total_seconds() / 86400
        half_life = settings.time_decay_halflife_d
        w = 0.5 ** (days / half_life) if half_life else 1.0
        weights.append(w)
    total = sum(weights)
    return [(t, w / total) for t, w in zip(path, weights)]


# ── Data-driven: Markov removal effect (simplified) ───────────────────────────


def _channel_conversion_rates() -> dict[str, float]:
    """Empirical conversion rate after a touch by channel (deal-level)."""
    deals: dict[str, dict[str, Any]] = {}
    for t in get_touch_repository().list():
        key = t.deal_id or t.contact_pid
        if key not in deals:
            deals[key] = {"channels": set(), "funded": False}
        deals[key]["channels"].add(t.channel)
    for o in get_outcome_repository().list(stage="funded"):
        key = o.deal_id or o.contact_pid
        if key in deals:
            deals[key]["funded"] = True

    channel_deals: dict[str, dict[str, int]] = defaultdict(lambda: {"funded": 0, "total": 0})
    for d in deals.values():
        for ch in d["channels"]:
            channel_deals[ch]["total"] += 1
            if d["funded"]:
                channel_deals[ch]["funded"] += 1
    return {ch: (v["funded"] / v["total"] if v["total"] else 0.0) for ch, v in channel_deals.items()}


def _markov_sequence_prob(path: list[Any], channel_rates: dict[str, float]) -> float:
    """Approximate conversion probability of a path using a Markov chain of channel conversion rates."""
    if not path:
        return 0.0
    # Sequential survival: P(convert) = 1 - prod(1 - p_i) where p_i is the channel's conversion rate.
    # This is a first-order independence approximation suitable for small cohorts.
    prob = 1.0
    for t in path:
        p = channel_rates.get(t.channel, 0.0)
        prob *= 1 - p
    return 1 - prob


def _markov(path: list[Any]) -> list[tuple[Any, float]]:
    channel_rates = _channel_conversion_rates()
    base = _markov_sequence_prob(path, channel_rates)
    if base == 0 or not path:
        return _linear(path)
    effects: list[tuple[Any, float]] = []
    for i, t in enumerate(path):
        # Remove this touch from the path (bridge adjacent touches if any)
        reduced = path[:i] + path[i + 1 :]
        if not reduced:
            reduced_prob = 0.0
        else:
            reduced_prob = _markov_sequence_prob(reduced, channel_rates)
        effect = max(0.0, base - reduced_prob)
        effects.append((t, effect))
    total = sum(e for _, e in effects)
    if total == 0:
        return _linear(path)
    return [(t, e / total) for t, e in effects]


# ── Data-driven: Shapley value attribution ──────────────────────────────────


def _shapley_value(path: list[Any]) -> list[tuple[Any, float]]:
    channel_rates = _channel_conversion_rates()
    unique_channels = list({t.channel: None for t in path})
    if len(unique_channels) > 7:
        # Shapley exact is factorial; fall back to linear for very long paths.
        return _linear(path)

    def value(subset: frozenset[str]) -> float:
        # Expected conversion given a set of channel types.
        prob = 1.0
        for ch in subset:
            p = channel_rates.get(ch, 0.0)
            prob *= 1 - p
        return 1 - prob

    n = len(unique_channels)
    shapley: dict[str, float] = {ch: 0.0 for ch in unique_channels}
    for ch in unique_channels:
        others = [c for c in unique_channels if c != ch]
        for k in range(len(others) + 1):
            for subset in combinations(others, k):
                s = frozenset(subset)
                s_with = frozenset(chain(subset, [ch]))
                marginal = value(s_with) - value(s)
                weight = math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n)
                shapley[ch] += marginal * weight

    # Map per-touch channel Shapley value to touch credit (split equally among same-channel touches)
    touch_shares: dict[str, list[Any]] = defaultdict(list)
    for t in path:
        touch_shares[t.channel].append(t)
    credits: list[tuple[Any, float]] = []
    total = sum(abs(v) for v in shapley.values())
    if total == 0:
        return _linear(path)
    for ch, touches in touch_shares.items():
        channel_credit = shapley.get(ch, 0.0) / total
        share = channel_credit / len(touches)
        for t in touches:
            credits.append((t, share))
    return credits


# ── Public attribution API ──────────────────────────────────────────────────


def _apply_model(path: list[Any], model: str, funded_at: datetime | None = None) -> list[tuple[Any, float]]:
    if model == "first_touch":
        return _first_touch(path)
    if model == "last_touch":
        return _last_touch(path)
    if model == "linear" or model == "multi_touch":
        return _linear(path)
    if model == "time_decay":
        return _time_decay(path, funded_at=funded_at)
    if model == "markov":
        return _markov(path)
    if model == "shapley":
        return _shapley_value(path)
    return _time_decay(path, funded_at=funded_at)


def compute_attribution_for_deal(deal_id: str, model: str | None = None) -> AttributionRecord | None:
    settings = get_settings()
    model = model or settings.attribution_default_model

    funded = _funded_deals().get(deal_id)
    if not funded:
        return None

    funded_at = funded["occurred_at"]
    path = _touch_path(deal_id, funded_at)
    if not path:
        # Fallback: heuristic attribution flagged low-confidence.
        return AttributionRecord(
            deal_id=deal_id,
            funded_amount=funded["amount"],
            model=model,
            credits=[],
        )

    credited = _apply_model(path, model, funded_at=funded_at)
    credits = [
        AttributionCredit(
            touch_id=t.touch_id,
            channel=t.channel,
            source=t.source,
            credit=round(c, 4),
            touch_at=t.sent_at,
            days_before_conversion=round((funded["occurred_at"] - t.sent_at).total_seconds() / 86400, 2),
        )
        for t, c in credited
    ]
    record = AttributionRecord(
        deal_id=deal_id,
        funded_amount=funded["amount"],
        model=model,
        credits=credits,
    )
    get_attribution_repository().put(record)
    return record


def compute_all_attribution(model: str | None = None, period: str | None = None) -> list[AttributionRecord]:
    settings = get_settings()
    model = model or settings.attribution_default_model
    records: list[AttributionRecord] = []
    for deal_id in _funded_deals(period=period):
        rec = compute_attribution_for_deal(deal_id, model=model)
        if rec:
            records.append(rec)
    return records


def _touch_for_credit(rec: AttributionRecord, credit: AttributionCredit) -> Any | None:
    return next(
        (
            t
            for t in get_touch_repository().list(deal_id=rec.deal_id)
            + get_touch_repository().list(contact_pid=rec.deal_id)
            if t.touch_id == credit.touch_id
        ),
        None,
    )


def attributed_revenue_for_campaign(campaign_id: str, model: str | None = None, period: str | None = None) -> float:
    """Return the total funded revenue attributed to a campaign."""
    settings = get_settings()
    model = model or settings.attribution_default_model
    records = compute_all_attribution(model=model, period=period)
    total = 0.0
    for rec in records:
        for credit in rec.credits:
            touch = _touch_for_credit(rec, credit)
            if touch and touch.campaign_id == campaign_id:
                total += rec.funded_amount * credit.credit
    return round(total, 2)


def attributed_revenue_for_channel(channel: str, model: str | None = None, period: str | None = None) -> float:
    settings = get_settings()
    model = model or settings.attribution_default_model
    records = compute_all_attribution(model=model, period=period)
    total = 0.0
    for rec in records:
        for credit in rec.credits:
            if credit.channel == channel:
                total += rec.funded_amount * credit.credit
    return round(total, 2)


def attributed_revenue_by_entity(
    entity: str, entity_id: str, model: str | None = None, period: str | None = None
) -> float:
    settings = get_settings()
    model = model or settings.attribution_default_model
    records = compute_all_attribution(model=model, period=period)
    total = 0.0
    for rec in records:
        for credit in rec.credits:
            touch = _touch_for_credit(rec, credit)
            if not touch:
                continue
            val = getattr(touch, entity, None)
            if val == entity_id:
                total += rec.funded_amount * credit.credit
    return round(total, 2)


def attribution_by_entity(
    entity: str,
    *,
    model: str | None = None,
    period: str | None = None,
) -> list[dict[str, Any]]:
    """Rank attributed revenue for every distinct value of `entity`.

    Supported entities: channel, campaign_id, sequence_id, source, rep_id,
    team_id, property_type, area, contact_pid, deal_id.
    """
    settings = get_settings()
    model = model or settings.attribution_default_model
    records = compute_all_attribution(model=model, period=period)
    by_value: dict[str, float] = defaultdict(float)

    for rec in records:
        for credit in rec.credits:
            touch = _touch_for_credit(rec, credit)
            if not touch:
                continue
            val = getattr(touch, entity, None)
            if val is None:
                continue
            by_value[str(val)] += rec.funded_amount * credit.credit

    return [
        {"entity_id": k, "entity": entity, "attributed_revenue": round(v, 2)}
        for k, v in sorted(by_value.items(), key=lambda x: x[1], reverse=True)
    ]


def attribution_summary(model: str | None = None, period: str | None = None) -> dict[str, Any]:
    """Ranked view of channels/campaigns/sequences by attributed revenue."""
    model = model or get_settings().attribution_default_model
    records = compute_all_attribution(model=model, period=period)
    by_channel: dict[str, float] = defaultdict(float)
    by_campaign: dict[str, float] = defaultdict(float)
    by_sequence: dict[str, float] = defaultdict(float)
    by_source: dict[str, float] = defaultdict(float)

    for rec in records:
        for credit in rec.credits:
            touch = _touch_for_credit(rec, credit)
            if not touch:
                continue
            revenue = rec.funded_amount * credit.credit
            by_channel[touch.channel] += revenue
            if touch.campaign_id:
                by_campaign[touch.campaign_id] += revenue
            if touch.sequence_id:
                by_sequence[touch.sequence_id] += revenue
            if touch.source:
                by_source[touch.source] += revenue

    return {
        "period": period,
        "model": model,
        "channels": [
            {"entity": k, "attributed_revenue": round(v, 2)}
            for k, v in sorted(by_channel.items(), key=lambda x: x[1], reverse=True)
        ],
        "campaigns": [
            {"entity": k, "attributed_revenue": round(v, 2)}
            for k, v in sorted(by_campaign.items(), key=lambda x: x[1], reverse=True)
        ],
        "sequences": [
            {"entity": k, "attributed_revenue": round(v, 2)}
            for k, v in sorted(by_sequence.items(), key=lambda x: x[1], reverse=True)
        ],
        "sources": [
            {"entity": k, "attributed_revenue": round(v, 2)}
            for k, v in sorted(by_source.items(), key=lambda x: x[1], reverse=True)
        ],
    }
