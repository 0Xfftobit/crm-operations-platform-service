"""P2 — Dashboard marts: funnel, scorecards, channel effectiveness, velocity, KPI API."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from itertools import groupby
from typing import Any

from app.core.config import get_settings
from app.domain.models import MetricDefinition
from app.period_utils import _in_period, _parse_grain, _period_bucket
from app.repositories import get_outcome_repository, get_touch_repository


def _now() -> datetime:
    return datetime.now(UTC)


def _filter_touches(filters: dict[str, Any]) -> list[Any]:
    return get_touch_repository().list(
        tenant_id=filters.get("tenant_id"),
        team_id=filters.get("team_id"),
        rep_id=filters.get("rep_id"),
        deal_id=filters.get("deal_id"),
        contact_pid=filters.get("contact_pid"),
        channel=filters.get("channel"),
        campaign_id=filters.get("campaign"),
        sequence_id=filters.get("sequence"),
        source=filters.get("source"),
        stage=filters.get("stage"),
        segment=filters.get("segment"),
    )


def _filter_outcomes(filters: dict[str, Any], stage: str | None = None) -> list[Any]:
    return get_outcome_repository().list(
        tenant_id=filters.get("tenant_id"),
        team_id=filters.get("team_id"),
        rep_id=filters.get("rep_id"),
        deal_id=filters.get("deal_id"),
        contact_pid=filters.get("contact_pid"),
        stage=stage if stage else filters.get("stage"),
    )


def _touch_dim_value(touch: Any, dim: str, period: str | None) -> str:
    if dim == "period":
        return _period_bucket(touch.sent_at, period)
    if dim == "team":
        return touch.team_id or "(none)"
    if dim == "rep":
        return touch.rep_id or "(none)"
    if dim == "channel":
        return touch.channel or "(none)"
    if dim == "segment":
        return touch.segment or "(none)"
    if dim == "stage":
        return touch.stage or "(none)"
    if dim == "campaign":
        return touch.campaign_id or "(none)"
    if dim == "hour":
        return f"{touch.sent_at.hour:02d}"
    if dim == "day_of_week":
        return touch.sent_at.strftime("%A")
    if dim == "area":
        return touch.area or "(none)"
    if dim == "property_type":
        return touch.property_type or "(none)"
    return "(none)"


def _outcome_dim_value(outcome: Any, dim: str, period: str | None) -> str:
    """Resolve a grouping dimension for an outcome or a deal row."""
    # Detect whether `outcome` is a deal dict (from _deals_from_outcomes) or an OutcomeEvent.
    is_deal = isinstance(outcome, dict)

    if dim == "period":
        ts = _deal_timestamp(outcome, is_deal)
        return _period_bucket(ts, period) if ts else "(none)"
    if dim == "team":
        return outcome["team_id"] if is_deal else (outcome.team_id or "(none)")
    if dim == "rep":
        return outcome["rep_id"] if is_deal else (outcome.rep_id or "(none)")
    if dim == "stage":
        return outcome.get("final_stage", "(none)") if is_deal else outcome.stage
    if dim in ("segment", "campaign", "channel"):
        ts = _deal_timestamp(outcome, is_deal)
        contact_pid = outcome["contact_pid"] if is_deal else outcome.contact_pid
        touches = get_touch_repository().list(deal_id=outcome["deal_id"]) + get_touch_repository().list(
            contact_pid=contact_pid
        )
        if not ts:
            return "(none)"
        before = [t for t in touches if t.sent_at <= ts]
        if before:
            latest = max(before, key=lambda x: x.sent_at)
            if dim == "segment":
                return latest.segment or "(none)"
            if dim == "campaign":
                return latest.campaign_id or "(none)"
            return latest.channel or "(none)"
        return "(none)"
    return "(none)"


def _deal_timestamp(outcome: Any, is_deal: bool) -> datetime | None:
    if is_deal:
        if outcome.get("final_stage") and outcome["stages"].get(outcome["final_stage"]):
            return outcome["stages"][outcome["final_stage"]].occurred_at
        return _max_touch_at(outcome.get("touches", []))
    return outcome.occurred_at


def _group_key(item: Any, dims: list[str], period: str | None, kind: str = "touch") -> tuple[str, ...]:
    if kind == "outcome":
        return tuple(_outcome_dim_value(item, d, period) for d in dims)
    return tuple(_touch_dim_value(item, d, period) for d in dims)


def _group(
    items: list[Any], dims: list[str], period: str | None, kind: str = "touch"
) -> dict[tuple[str, ...], list[Any]]:
    key = lambda item: _group_key(item, dims, period, kind)  # noqa: E731
    sorted_items = sorted(items, key=key)
    return {k: list(g) for k, g in groupby(sorted_items, key=key)}


# ── Deal-level helpers ────────────────────────────────────────────────────────


def _deals_from_outcomes() -> dict[str, dict[str, Any]]:
    """Aggregate pipeline stages per deal (or contact if no deal id)."""
    deals: dict[str, dict[str, Any]] = {}
    outcomes = get_outcome_repository().list()
    for o in outcomes:
        key = o.deal_id or o.contact_pid
        if key not in deals:
            deals[key] = {
                "deal_id": key,
                "contact_pid": o.contact_pid,
                "rep_id": o.rep_id,
                "team_id": o.team_id,
                "tenant_id": o.tenant_id,
                "stages": {},
                "touches": [],
                "funded_amount": 0.0,
                "final_stage": None,
                "final_at": None,
            }
        d = deals[key]
        d["stages"][o.stage] = o
        if o.stage == "funded" and o.amount:
            d["funded_amount"] += o.amount
        if d["final_at"] is None or o.occurred_at > d["final_at"]:
            d["final_at"] = o.occurred_at
            d["final_stage"] = o.stage

    # Attach touches
    for t in get_touch_repository().list():
        key = t.deal_id or t.contact_pid
        if key in deals:
            deals[key]["touches"].append(t)
        else:
            # Deal may exist as a touch without an outcome; still include as a lead
            deals[key] = {
                "deal_id": key,
                "contact_pid": t.contact_pid,
                "rep_id": t.rep_id,
                "team_id": t.team_id,
                "tenant_id": t.tenant_id,
                "stages": {},
                "touches": [t],
                "funded_amount": 0.0,
                "final_stage": None,
                "final_at": t.sent_at,
            }
    return deals


def _deal_filter_match(deal: dict[str, Any], filters: dict[str, Any]) -> bool:
    if filters.get("tenant_id") and deal["tenant_id"] != filters["tenant_id"]:
        return False
    if filters.get("team_id") and deal["team_id"] != filters["team_id"]:
        return False
    if filters.get("rep_id") and deal["rep_id"] != filters["rep_id"]:
        return False
    if filters.get("contact_pid") and deal["contact_pid"] != filters["contact_pid"]:
        return False
    if filters.get("deal_id") and deal["deal_id"] != filters["deal_id"]:
        return False
    return True


def _deal_has_stage(deal: dict[str, Any], stage: str) -> bool:
    return stage in deal["stages"]


def _deal_stage_at(deal: dict[str, Any], stage: str) -> datetime | None:
    o = deal["stages"].get(stage)
    return o.occurred_at if o else None


def _cycle_days(deal: dict[str, Any], from_stage: str, to_stage: str) -> float | None:
    start = _deal_stage_at(deal, from_stage)
    end = _deal_stage_at(deal, to_stage)
    if start and end:
        return (end - start).total_seconds() / 86400
    return None


# ── Metric computers ─────────────────────────────────────────────────────────


def _compute_lead_to_app(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        leads = sum(1 for r in group if _deal_has_stage(r, "lead"))
        apps = sum(1 for r in group if _deal_has_stage(r, "app"))
        result.append(
            _row(
                dims,
                key,
                {
                    "leads": leads,
                    "apps": apps,
                    "numerator": apps,
                    "denominator": leads,
                    "value": round(apps / leads, 4) if leads else 0.0,
                },
            )
        )
    return result


def _compute_app_to_fund(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        apps = sum(1 for r in group if _deal_has_stage(r, "app"))
        funded = sum(1 for r in group if _deal_has_stage(r, "funded"))
        result.append(
            _row(
                dims,
                key,
                {
                    "apps": apps,
                    "funded": funded,
                    "numerator": funded,
                    "denominator": apps,
                    "value": round(funded / apps, 4) if apps else 0.0,
                },
            )
        )
    return result


def _compute_funded_amount(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        funded = [r for r in group if _deal_has_stage(r, "funded")]
        amount = sum(r["funded_amount"] for r in funded)
        count = len(funded)
        result.append(
            _row(
                dims,
                key,
                {
                    "funded_count": count,
                    "funded_amount": round(amount, 2),
                    "numerator": round(amount, 2),
                    "denominator": 1,
                    "value": round(amount, 2),
                },
            )
        )
    return result


def _compute_deal_count(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        count = sum(1 for r in group if _deal_has_stage(r, "funded"))
        result.append(_row(dims, key, {"funded_count": count, "numerator": count, "denominator": 1, "value": count}))
    return result


def _compute_conversion_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        leads = sum(1 for r in group if _deal_has_stage(r, "lead"))
        funded = sum(1 for r in group if _deal_has_stage(r, "funded"))
        result.append(
            _row(
                dims,
                key,
                {
                    "leads": leads,
                    "funded": funded,
                    "numerator": funded,
                    "denominator": leads,
                    "value": round(funded / leads, 4) if leads else 0.0,
                },
            )
        )
    return result


def _touch_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None, flag: str) -> list[dict[str, Any]]:
    touches = [t for r in rows for t in r.get("touches", [])]
    result = []
    for key, group in _group(touches, dims, period, kind="touch").items():
        delivered = [t for t in group if t.delivery_status not in ("failed", "bounced")]
        num = sum(1 for t in delivered if getattr(t, flag, False))
        den = len(delivered)
        result.append(
            _row(
                dims,
                key,
                {
                    "delivered": den,
                    flag: num,
                    "numerator": num,
                    "denominator": den,
                    "value": round(num / den, 4) if den else 0.0,
                },
            )
        )
    return result


def _row(dims: list[str], key: tuple[str, ...], values: dict[str, Any]) -> dict[str, Any]:
    row = dict(zip(dims, key))
    row.update(values)
    return row


def _compute_response_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    return _touch_rate(rows, dims, period, "replied")


def _compute_engagement_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    touches = [t for r in rows for t in r.get("touches", [])]
    result = []
    for key, group in _group(touches, dims, period, kind="touch").items():
        delivered = [t for t in group if t.delivery_status not in ("failed", "bounced")]
        num = sum(1 for t in delivered if t.engaged or t.opened or t.clicked or t.replied)
        den = len(delivered)
        result.append(
            _row(
                dims,
                key,
                {
                    "delivered": den,
                    "engaged": num,
                    "numerator": num,
                    "denominator": den,
                    "value": round(num / den, 4) if den else 0.0,
                },
            )
        )
    return result


def _compute_open_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    return _touch_rate(rows, dims, period, "opened")


def _compute_click_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    return _touch_rate(rows, dims, period, "clicked")


def _compute_reply_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    return _touch_rate(rows, dims, period, "replied")


def _compute_opt_out_rate(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    return _touch_rate(rows, dims, period, "opted_out")


def _compute_median_cycle_time(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        days = [_cycle_days(r, "lead", "funded") for r in group if _deal_has_stage(r, "funded")]
        days = [d for d in days if d is not None]
        value = round(statistics.median(days), 2) if days else None
        total_days = round(sum(days), 2) if days else 0.0
        count = len(days)
        result.append(
            _row(
                dims,
                key,
                {
                    "funded_count": count,
                    "median_days": value,
                    "numerator": total_days,
                    "denominator": count,
                    "value": value,
                },
            )
        )
    return result


def _compute_pipeline_velocity(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    result = []
    transitions = [("lead", "app"), ("app", "approved"), ("approved", "funded")]
    for key, group in _group(rows, dims, period, kind="outcome").items():
        total_days = 0.0
        count = 0
        for r in group:
            for a, b in transitions:
                d = _cycle_days(r, a, b)
                if d is not None:
                    total_days += d
                    count += 1
        value = round(total_days / count, 2) if count else None
        result.append(
            _row(
                dims,
                key,
                {
                    "stage_transitions": count,
                    "avg_days": value,
                    "numerator": round(total_days, 2),
                    "denominator": count,
                    "value": value,
                },
            )
        )
    return result


def _compute_cost_per_funded_loan(
    rows: list[dict[str, Any]], dims: list[str], period: str | None
) -> list[dict[str, Any]]:
    from app.repositories import get_campaign_cost_repository

    result = []
    # Group by campaign (or requested dims)
    for key, group in _group(rows, dims, period, kind="outcome").items():
        # Determine campaign from key if campaign is a dim
        campaign = None
        if "campaign" in dims:
            campaign = key[dims.index("campaign")]
        funded = sum(1 for r in group if _deal_has_stage(r, "funded"))
        cost_total = 0.0
        if campaign and campaign != "(none)":
            cost_total = sum(c.cost for c in get_campaign_cost_repository().list(campaign_id=campaign))
        value = round(cost_total / funded, 2) if funded else 0.0
        result.append(
            _row(
                dims,
                key,
                {
                    "funded": funded,
                    "cost": round(cost_total, 2),
                    "numerator": round(cost_total, 2),
                    "denominator": funded,
                    "value": value,
                },
            )
        )
    return result


def _compute_roi_per_campaign(rows: list[dict[str, Any]], dims: list[str], period: str | None) -> list[dict[str, Any]]:
    from app import attribution
    from app.repositories import get_campaign_cost_repository

    result = []
    for key, group in _group(rows, dims, period, kind="outcome").items():
        campaign = None
        if "campaign" in dims:
            campaign = key[dims.index("campaign")]
        funded_amount = sum(r["funded_amount"] for r in group if _deal_has_stage(r, "funded"))
        # If no explicit campaign grouping, attribute all revenue
        cost_total = 0.0
        if campaign and campaign != "(none)":
            cost_total = sum(c.cost for c in get_campaign_cost_repository().list(campaign_id=campaign))
            attributed = attribution.attributed_revenue_for_campaign(campaign)
            funded_amount = attributed
        value = round((funded_amount - cost_total) / cost_total * 100, 2) if cost_total else None
        result.append(
            _row(
                dims,
                key,
                {
                    "revenue": round(funded_amount, 2),
                    "cost": round(cost_total, 2),
                    "numerator": round(funded_amount - cost_total, 2),
                    "denominator": round(cost_total, 2),
                    "value": value,
                },
            )
        )
    return result


METRIC_COMPUTERS = {
    "lead_to_app_conversion": _compute_lead_to_app,
    "app_to_fund_conversion": _compute_app_to_fund,
    "funded_amount": _compute_funded_amount,
    "deal_count": _compute_deal_count,
    "conversion_rate": _compute_conversion_rate,
    "response_rate": _compute_response_rate,
    "engagement_rate": _compute_engagement_rate,
    "open_rate": _compute_open_rate,
    "click_rate": _compute_click_rate,
    "reply_rate": _compute_reply_rate,
    "opt_out_rate": _compute_opt_out_rate,
    "median_cycle_time": _compute_median_cycle_time,
    "pipeline_velocity": _compute_pipeline_velocity,
    "cost_per_funded_loan": _compute_cost_per_funded_loan,
    "roi_per_campaign": _compute_roi_per_campaign,
}


def _prepare_deal_rows(filters: dict[str, Any], period: str | None) -> list[dict[str, Any]]:
    """Return deal rows matching filters and period."""
    deals = _deals_from_outcomes()
    rows: list[dict[str, Any]] = []
    for deal in deals.values():
        if not _deal_filter_match(deal, filters):
            continue
        # Period filter: use latest outcome or latest touch
        ts = deal["final_at"] or (_max_touch_at(deal["touches"]) if deal["touches"] else None)
        if ts and not _in_period(ts, period):
            continue
        rows.append(deal)
    return rows


def _max_touch_at(touches: list[Any]) -> datetime | None:
    return max((t.sent_at for t in touches), default=None)


def compute_kpi(
    metric: MetricDefinition,
    period: str | None = None,
    dims: list[str] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a governed metric for the given period, dimensions, and filters."""
    filters = filters or {}
    dims = dims or []
    if "period" in dims and not _parse_grain(period):
        # If period is not a bucket keyword but a bucket is requested, fall back to monthly grouping.
        period = "monthly"

    rows = _prepare_deal_rows(filters, period)
    computer = METRIC_COMPUTERS.get(metric.name)
    if computer is None:
        return {
            "metric": metric.name,
            "version": metric.version,
            "period": period,
            "rows": [],
            "error": "metric not implemented",
        }
    computed = computer(rows, dims, period)
    return {
        "metric": metric.name,
        "version": metric.version,
        "label": metric.label,
        "metric_type": metric.metric_type,
        "period": period,
        "dimensions": dims,
        "rows": computed,
    }


# ── Funnel ──────────────────────────────────────────────────────────────────


def build_funnel(filters: dict[str, Any] | None = None, period: str | None = None) -> dict[str, Any]:
    """Lead → app → approved → funded funnel with conversion and cycle times."""
    filters = filters or {}
    deals = _deals_from_outcomes()
    stages = ["lead", "app", "approved", "funded"]
    rows: list[dict[str, Any]] = []
    previous_count: int | None = None
    for stage in stages:
        matched = [
            d
            for d in deals.values()
            if _deal_filter_match(d, filters)
            and _deal_has_stage(d, stage)
            and _in_period(_deal_stage_at(d, stage) or d["final_at"] or datetime.min.replace(tzinfo=UTC), period)
        ]
        cycle_days: list[float] = []
        if stage != "lead":
            prev = stages[stages.index(stage) - 1]
            for d in matched:
                cd = _cycle_days(d, prev, stage)
                if cd is not None:
                    cycle_days.append(cd)
        conversion_from_previous = round(len(matched) / previous_count, 4) if previous_count else None
        drop_off = round(1 - (len(matched) / previous_count), 4) if previous_count else None
        rows.append(
            {
                "stage": stage,
                "count": len(matched),
                "median_cycle_days": round(statistics.median(cycle_days), 2) if cycle_days else None,
                "conversion_from_previous": conversion_from_previous,
                "drop_off_rate": drop_off,
            }
        )
        previous_count = len(matched)
    return {"stages": rows, "filters": filters, "period": period}


# ── Rep scorecards ──────────────────────────────────────────────────────────


def build_rep_scorecards(filters: dict[str, Any] | None = None, period: str | None = None) -> list[dict[str, Any]]:
    """Outcome-weighted rep scorecards."""
    filters = filters or {}
    deals = _deals_from_outcomes()
    by_rep: dict[str, dict[str, Any]] = {}
    for d in deals.values():
        if not _deal_filter_match(d, filters):
            continue
        if not _in_period(d["final_at"] or _max_touch_at(d["touches"]) or datetime.min.replace(tzinfo=UTC), period):
            continue
        rep = d["rep_id"] or "(none)"
        if rep not in by_rep:
            by_rep[rep] = {
                "rep_id": rep,
                "team_id": d["team_id"],
                "tenant_id": d["tenant_id"],
                "leads": 0,
                "apps": 0,
                "funded": 0,
                "funded_amount": 0.0,
                "touches": 0,
                "delivered": 0,
                "replied": 0,
                "opted_out": 0,
                "cycle_days": [],
            }
        r = by_rep[rep]
        if _deal_has_stage(d, "lead"):
            r["leads"] += 1
        if _deal_has_stage(d, "app"):
            r["apps"] += 1
        if _deal_has_stage(d, "funded"):
            r["funded"] += 1
            r["funded_amount"] += d["funded_amount"]
            cd = _cycle_days(d, "lead", "funded")
            if cd is not None:
                r["cycle_days"].append(cd)
        for t in d["touches"]:
            r["touches"] += 1
            if t.delivery_status not in ("failed", "bounced"):
                r["delivered"] += 1
            if t.replied:
                r["replied"] += 1
            if t.opted_out:
                r["opted_out"] += 1

    scorecards: list[dict[str, Any]] = []
    as_of = period if period and _parse_grain(period) is None else _now().date().isoformat()
    for r in by_rep.values():
        scorecards.append(
            {
                "rep_id": r["rep_id"],
                "team_id": r["team_id"],
                "as_of_date": as_of,
                "leads": r["leads"],
                "apps": r["apps"],
                "funded": r["funded"],
                "funded_amount": round(r["funded_amount"], 2),
                "conv_lead_app": round(r["apps"] / r["leads"], 4) if r["leads"] else 0.0,
                "conv_app_fund": round(r["funded"] / r["apps"], 4) if r["apps"] else 0.0,
                "median_cycle_d": round(statistics.median(r["cycle_days"]), 2) if r["cycle_days"] else None,
                "response_rate": round(r["replied"] / r["delivered"], 4) if r["delivered"] else 0.0,
                "guardrail_compliance": round(1 - (r["opted_out"] / r["delivered"]), 4) if r["delivered"] else 1.0,
            }
        )

    # Rank within team by funded amount
    teams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in scorecards:
        teams[s["team_id"] or "(none)"].append(s)
    for team in teams.values():
        team.sort(key=lambda x: x["funded_amount"], reverse=True)
        for i, s in enumerate(team, 1):
            s["rank_in_team"] = i
    return scorecards


def build_rep_scorecard(
    rep_id: str, filters: dict[str, Any] | None = None, period: str | None = None
) -> dict[str, Any] | None:
    filters = filters or {}
    filters["rep_id"] = rep_id
    cards = build_rep_scorecards(filters, period)
    return cards[0] if cards else None


# ── Leaderboard ─────────────────────────────────────────────────────────────


def build_leaderboard(
    metric: str = "funded_amount", filters: dict[str, Any] | None = None, period: str | None = None
) -> list[dict[str, Any]]:
    """Rank reps by a chosen metric."""
    cards = build_rep_scorecards(filters, period)
    reverse = metric in ("funded_amount", "funded", "conv_app_fund", "conv_lead_app", "response_rate")
    cards.sort(key=lambda x: x.get(metric, 0) or 0, reverse=reverse)
    for i, c in enumerate(cards, 1):
        c["rank"] = i
    return cards


# ── Channel effectiveness ───────────────────────────────────────────────────


def build_channel_effectiveness(
    filters: dict[str, Any] | None = None,
    period: str | None = None,
    breakdown: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Per-channel metrics with optional segment/stage/time breakdown."""
    filters = filters or {}
    breakdown = breakdown or ["channel"]
    touches = _filter_touches(filters)
    if period and _parse_grain(period):
        touches = [t for t in touches if _in_period(t.sent_at, period)]
    elif period:
        touches = [t for t in touches if _in_period(t.sent_at, period)]

    deals = _deals_from_outcomes()
    funded_deals = {k for k, d in deals.items() if _deal_has_stage(d, "funded")}

    rows: list[dict[str, Any]] = []
    for key, group in _group(touches, breakdown, period, kind="touch").items():
        delivered = [t for t in group if t.delivery_status not in ("failed", "bounced")]
        opened = [t for t in delivered if t.opened]
        clicked = [t for t in delivered if t.clicked]
        replied = [t for t in delivered if t.replied]
        opted = [t for t in delivered if t.opted_out]
        touched_deals = {t.deal_id or t.contact_pid for t in group}
        converted = len(touched_deals & funded_deals)
        unique_deals = len(touched_deals)
        row = _row(breakdown, key, {})
        row.update(
            {
                "touches": len(group),
                "delivered": len(delivered),
                "delivery_rate": round(len(delivered) / len(group), 4) if group else 0.0,
                "open_rate": round(len(opened) / len(delivered), 4) if delivered else 0.0,
                "click_rate": round(len(clicked) / len(delivered), 4) if delivered else 0.0,
                "reply_rate": round(len(replied) / len(delivered), 4) if delivered else 0.0,
                "opt_out_rate": round(len(opted) / len(delivered), 4) if delivered else 0.0,
                "conversion_contribution_rate": round(converted / unique_deals, 4) if unique_deals else 0.0,
                "funded_deals": converted,
                "unique_deals": unique_deals,
            }
        )
        rows.append(row)
    return rows


def build_channel_heatmaps(filters: dict[str, Any] | None = None, period: str | None = None) -> dict[str, Any]:
    """Time-of-day engagement heatmap per channel."""
    by_hour = build_channel_effectiveness(filters, period, breakdown=["channel", "hour"])
    by_dow = build_channel_effectiveness(filters, period, breakdown=["channel", "day_of_week"])
    return {"by_hour": by_hour, "by_day_of_week": by_dow}


def build_channel_recommendations(
    filters: dict[str, Any] | None = None, period: str | None = None
) -> list[dict[str, Any]]:
    """Surface channel-segment/stage combinations that diverge from the average."""
    rows = build_channel_effectiveness(filters, period, breakdown=["channel", "stage"])
    if not rows:
        return []
    avg = statistics.mean(r["conversion_contribution_rate"] for r in rows)
    recommendations = []
    for r in rows:
        if r["conversion_contribution_rate"] > avg * 1.2:
            recommendations.append(
                {
                    "channel": r["channel"],
                    "stage": r["stage"],
                    "message": f"{r['channel']} outperforms average at {r['stage']} stage"
                    f" by {round((r['conversion_contribution_rate'] / avg - 1) * 100, 0)}%"
                    if avg
                    else f"{r['channel']} is strong at {r['stage']} stage",
                    "conversion_rate": r["conversion_contribution_rate"],
                }
            )
    return recommendations


# ── Cohort / velocity ───────────────────────────────────────────────────────


def build_cohort_analysis(dim: str = "acquisition_month", period: str | None = None) -> list[dict[str, Any]]:
    """Cohort conversion by acquisition month or segment."""
    deals = _deals_from_outcomes()
    cohorts: dict[str, dict[str, Any]] = {}
    settings = get_settings()
    for d in deals.values():
        lead = d["stages"].get("lead")
        if lead is None:
            continue
        if not _in_period(lead.occurred_at, period):
            continue
        if dim == "acquisition_month":
            key = lead.occurred_at.strftime("%Y-%m")
        elif dim == "segment":
            touches = d["touches"]
            seg = touches[0].segment if touches else None
            key = seg or "(none)"
        else:
            key = "all"
        if key not in cohorts:
            cohorts[key] = {"cohort": key, "n": 0, "funded": 0}
        cohorts[key]["n"] += 1
        if _deal_has_stage(d, "funded"):
            cohorts[key]["funded"] += 1

    result = []
    for c in cohorts.values():
        suppressed = c["n"] < settings.cohort_min_n
        result.append(
            {
                "cohort": c["cohort"],
                "n": c["n"],
                "funded": c["funded"],
                "conversion_rate": None if suppressed else round(c["funded"] / c["n"], 4),
                "suppressed": suppressed,
            }
        )
    return result


# ── A/B uplift ───────────────────────────────────────────────────────────────


def _proportions_z_test(n1: int, x1: int, n2: int, x2: int) -> float:
    if n1 == 0 or n2 == 0:
        return 1.0
    p1 = x1 / n1
    p2 = x2 / n2
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return 2 * (1 - _normal_cdf(abs(z)))


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def compute_ab_uplift(
    treatment: list[str],
    control: list[str],
    stage: str = "funded",
) -> dict[str, Any]:
    """Compare treatment vs control on a pipeline stage (default funded)."""
    deals = _deals_from_outcomes()
    treatment_set = set(treatment)
    control_set = set(control)

    treatment_funded = 0
    control_funded = 0
    treatment_n = 0
    control_n = 0
    for key, d in deals.items():
        in_treatment = key in treatment_set or d["contact_pid"] in treatment_set
        in_control = key in control_set or d["contact_pid"] in control_set
        if not in_treatment and not in_control:
            continue
        if in_treatment:
            treatment_n += 1
            if _deal_has_stage(d, stage):
                treatment_funded += 1
        else:
            control_n += 1
            if _deal_has_stage(d, stage):
                control_funded += 1

    t_rate = round(treatment_funded / treatment_n, 4) if treatment_n else 0.0
    c_rate = round(control_funded / control_n, 4) if control_n else 0.0
    lift = t_rate - c_rate
    rel_lift = round(lift / c_rate, 4) if c_rate else 0.0
    p_value = _proportions_z_test(control_n, control_funded, treatment_n, treatment_funded)
    return {
        "treatment_rate": t_rate,
        "control_rate": c_rate,
        "lift": round(lift, 4),
        "relative_lift": rel_lift,
        "p_value": round(p_value, 4),
        "significant": p_value < 0.05,
        "treatment_n": treatment_n,
        "treatment_funded": treatment_funded,
        "control_n": control_n,
        "control_funded": control_funded,
    }
