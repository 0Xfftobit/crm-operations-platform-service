"""In-memory repositories for PAS (swappable for DW/Postgres/S3 in production)."""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.domain.models import (
    AlertRecord,
    AttributionRecord,
    CampaignCost,
    ExperimentResult,
    FinanceSourceOfTruth,
    KpiSnapshot,
    MetricDefinition,
    OutcomeEvent,
    ReconciliationRecord,
    ReportSchedule,
    TouchEvent,
)


class TouchRepository:
    def __init__(self) -> None:
        self._items: list[TouchEvent] = []
        self._by_event: dict[str, TouchEvent] = {}

    def add(self, t: TouchEvent) -> None:
        self._items.append(t)
        self._by_event[t.event_id] = t

    def get_by_event(self, event_id: str) -> TouchEvent | None:
        return self._by_event.get(event_id)

    def list(
        self,
        tenant_id: str | None = None,
        team_id: str | None = None,
        rep_id: str | None = None,
        deal_id: str | None = None,
        contact_pid: str | None = None,
        channel: str | None = None,
        campaign_id: str | None = None,
        sequence_id: str | None = None,
        source: str | None = None,
        stage: str | None = None,
        segment: str | None = None,
    ) -> list[TouchEvent]:
        return [
            t
            for t in self._items
            if (tenant_id is None or t.tenant_id == tenant_id)
            and (team_id is None or t.team_id == team_id)
            and (rep_id is None or t.rep_id == rep_id)
            and (deal_id is None or t.deal_id == deal_id)
            and (contact_pid is None or t.contact_pid == contact_pid)
            and (channel is None or t.channel == channel)
            and (campaign_id is None or t.campaign_id == campaign_id)
            and (sequence_id is None or t.sequence_id == sequence_id)
            and (source is None or t.source == source)
            and (stage is None or t.stage == stage)
            and (segment is None or t.segment == segment)
        ]

    def clear(self) -> None:
        self._items.clear()
        self._by_event.clear()


class OutcomeRepository:
    def __init__(self) -> None:
        self._items: list[OutcomeEvent] = []
        self._by_event: dict[str, OutcomeEvent] = {}

    def add(self, o: OutcomeEvent) -> None:
        self._items.append(o)
        self._by_event[o.event_id] = o

    def get_by_event(self, event_id: str) -> OutcomeEvent | None:
        return self._by_event.get(event_id)

    def list(
        self,
        tenant_id: str | None = None,
        team_id: str | None = None,
        rep_id: str | None = None,
        deal_id: str | None = None,
        contact_pid: str | None = None,
        stage: str | None = None,
    ) -> list[OutcomeEvent]:
        return [
            o
            for o in self._items
            if (tenant_id is None or o.tenant_id == tenant_id)
            and (team_id is None or o.team_id == team_id)
            and (rep_id is None or o.rep_id == rep_id)
            and (deal_id is None or o.deal_id == deal_id)
            and (contact_pid is None or o.contact_pid == contact_pid)
            and (stage is None or o.stage == stage)
        ]

    def clear(self) -> None:
        self._items.clear()
        self._by_event.clear()


class CampaignCostRepository:
    def __init__(self) -> None:
        self._items: list[CampaignCost] = []

    def add(self, c: CampaignCost) -> None:
        self._items.append(c)

    def list(self, campaign_id: str | None = None, period: str | None = None) -> list[CampaignCost]:
        return [
            c
            for c in self._items
            if (campaign_id is None or c.campaign_id == campaign_id) and (period is None or c.period == period)
        ]

    def clear(self) -> None:
        self._items.clear()


class FinanceSourceRepository:
    def __init__(self) -> None:
        self._items: list[FinanceSourceOfTruth] = []

    def add(self, f: FinanceSourceOfTruth) -> None:
        self._items.append(f)

    def get(self, period: str) -> FinanceSourceOfTruth | None:
        matches = [f for f in self._items if f.period == period]
        return max(matches, key=lambda f: f.recorded_at) if matches else None

    def list(self) -> list[FinanceSourceOfTruth]:
        return copy.copy(self._items)

    def clear(self) -> None:
        self._items.clear()


class MetricDefinitionRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[MetricDefinition]] = defaultdict(list)

    def add(self, m: MetricDefinition) -> None:
        self._items[m.name].append(m)

    def get(self, name: str, version: int | None = None) -> MetricDefinition | None:
        versions = self._items.get(name, [])
        if version is not None:
            return next((m for m in versions if m.version == version), None)
        return max(versions, key=lambda m: m.version) if versions else None

    def list(self, status: str | None = None) -> list[MetricDefinition]:
        out: list[MetricDefinition] = []
        for versions in self._items.values():
            for m in versions:
                if status is None or m.status == status:
                    out.append(m)
        return out

    def history(self, name: str) -> list[MetricDefinition]:
        return sorted(self._items.get(name, []), key=lambda m: m.version)

    def clear(self) -> None:
        self._items.clear()


class AttributionRepository:
    def __init__(self) -> None:
        self._items: dict[str, AttributionRecord] = {}

    def put(self, a: AttributionRecord) -> None:
        self._items[(a.deal_id, a.model)] = a

    def get(self, deal_id: str, model: str | None = None) -> AttributionRecord | None:
        if model is None:
            matches = [a for a in self._items.values() if a.deal_id == deal_id]
            return max(matches, key=lambda a: a.computed_at) if matches else None
        return self._items.get((deal_id, model))

    def list(self, model: str | None = None) -> list[AttributionRecord]:
        return [a for a in self._items.values() if model is None or a.model == model]

    def clear(self) -> None:
        self._items.clear()


class ReportScheduleRepository:
    def __init__(self) -> None:
        self._items: dict[str, ReportSchedule] = {}

    def put(self, r: ReportSchedule) -> None:
        self._items[r.report_id] = r

    def get(self, report_id: str) -> ReportSchedule | None:
        return self._items.get(report_id)

    def list(self) -> list[ReportSchedule]:
        return list(self._items.values())

    def clear(self) -> None:
        self._items.clear()


class ReconciliationRepository:
    def __init__(self) -> None:
        self._items: list[ReconciliationRecord] = []

    def add(self, r: ReconciliationRecord) -> None:
        self._items.append(r)

    def list(self, period: str | None = None) -> list[ReconciliationRecord]:
        return [r for r in self._items if period is None or r.period == period]

    def clear(self) -> None:
        self._items.clear()


class AlertRepository:
    def __init__(self) -> None:
        self._items: list[AlertRecord] = []

    def add(self, a: AlertRecord) -> None:
        self._items.append(a)

    def list(self, metric: str | None = None, severity: str | None = None) -> list[AlertRecord]:
        return [
            a
            for a in self._items
            if (metric is None or a.metric == metric) and (severity is None or a.severity == severity)
        ]

    def clear(self) -> None:
        self._items.clear()


class KpiSnapshotRepository:
    def __init__(self) -> None:
        self._items: list[KpiSnapshot] = []

    def add(self, s: KpiSnapshot) -> None:
        self._items.append(s)

    def list(self, metric_name: str | None = None) -> list[KpiSnapshot]:
        return [s for s in self._items if metric_name is None or s.metric_name == metric_name]

    def clear(self) -> None:
        self._items.clear()


class ExperimentResultRepository:
    def __init__(self) -> None:
        self._items: list[ExperimentResult] = []

    def add(self, e: ExperimentResult) -> None:
        self._items.append(e)

    def list(self) -> list[ExperimentResult]:
        return copy.copy(self._items)

    def clear(self) -> None:
        self._items.clear()


class EventLogRepository:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, event_type: str, payload: dict[str, Any]) -> None:
        self._items.append({"event": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()})

    def list(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            return copy.copy(self._items)
        return [e for e in self._items if e["event"] == event_type]

    def clear(self) -> None:
        self._items.clear()


# ── Singletons ───────────────────────────────────────────────────────────────
_touches = TouchRepository()
_outcomes = OutcomeRepository()
_campaign_costs = CampaignCostRepository()
_finance = FinanceSourceRepository()
_metrics = MetricDefinitionRepository()
_attributions = AttributionRepository()
_reports = ReportScheduleRepository()
_reconciliations = ReconciliationRepository()
_alerts = AlertRepository()
_snapshots = KpiSnapshotRepository()
_experiments = ExperimentResultRepository()
_events = EventLogRepository()


def get_touch_repository() -> TouchRepository:
    return _touches


def get_outcome_repository() -> OutcomeRepository:
    return _outcomes


def get_campaign_cost_repository() -> CampaignCostRepository:
    return _campaign_costs


def get_finance_repository() -> FinanceSourceRepository:
    return _finance


def get_metric_repository() -> MetricDefinitionRepository:
    return _metrics


def get_attribution_repository() -> AttributionRepository:
    return _attributions


def get_report_repository() -> ReportScheduleRepository:
    return _reports


def get_reconciliation_repository() -> ReconciliationRepository:
    return _reconciliations


def get_alert_repository() -> AlertRepository:
    return _alerts


def get_snapshot_repository() -> KpiSnapshotRepository:
    return _snapshots


def get_experiment_repository() -> ExperimentResultRepository:
    return _experiments


def get_event_log_repository() -> EventLogRepository:
    return _events


def reset_repositories() -> None:
    _touches.clear()
    _outcomes.clear()
    _campaign_costs.clear()
    _finance.clear()
    _metrics.clear()
    _attributions.clear()
    _reports.clear()
    _reconciliations.clear()
    _alerts.clear()
    _snapshots.clear()
    _experiments.clear()
    _events.clear()
