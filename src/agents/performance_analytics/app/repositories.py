"""In-memory repositories for PAS (swappable for DW/Postgres/S3 in production)."""

# ruff: noqa: UP006, UP035

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import UTC, datetime
from functools import wraps
from threading import RLock
from typing import Any, List

from app.core.config import get_settings
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


def _synchronized(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class TouchRepository:
    def __init__(self) -> None:
        self._items: List[TouchEvent] = []
        self._by_event: dict[str, TouchEvent] = {}
        self._lock = RLock()

    @_synchronized
    def add(self, t: TouchEvent) -> None:
        self._items.append(t)
        self._by_event[t.event_id] = t

    @_synchronized
    def get_by_event(self, event_id: str) -> TouchEvent | None:
        return self._by_event.get(event_id)

    @_synchronized
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
    ) -> List[TouchEvent]:
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

    @_synchronized
    def clear(self) -> None:
        self._items.clear()
        self._by_event.clear()


class OutcomeRepository:
    def __init__(self) -> None:
        self._items: List[OutcomeEvent] = []
        self._by_event: dict[str, OutcomeEvent] = {}
        self._lock = RLock()

    @_synchronized
    def add(self, o: OutcomeEvent) -> None:
        self._items.append(o)
        self._by_event[o.event_id] = o

    @_synchronized
    def get_by_event(self, event_id: str) -> OutcomeEvent | None:
        return self._by_event.get(event_id)

    @_synchronized
    def list(
        self,
        tenant_id: str | None = None,
        team_id: str | None = None,
        rep_id: str | None = None,
        deal_id: str | None = None,
        contact_pid: str | None = None,
        stage: str | None = None,
    ) -> List[OutcomeEvent]:
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

    @_synchronized
    def clear(self) -> None:
        self._items.clear()
        self._by_event.clear()


class CampaignCostRepository:
    def __init__(self) -> None:
        self._items: List[CampaignCost] = []
        self._lock = RLock()

    @_synchronized
    def add(self, c: CampaignCost) -> None:
        self._items.append(c)

    @_synchronized
    def list(self, campaign_id: str | None = None, period: str | None = None) -> List[CampaignCost]:
        return [
            c
            for c in self._items
            if (campaign_id is None or c.campaign_id == campaign_id) and (period is None or c.period == period)
        ]

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class FinanceSourceRepository:
    def __init__(self) -> None:
        self._items: List[FinanceSourceOfTruth] = []
        self._lock = RLock()

    @_synchronized
    def add(self, f: FinanceSourceOfTruth) -> None:
        self._items.append(f)

    @_synchronized
    def get(self, period: str) -> FinanceSourceOfTruth | None:
        matches = [f for f in self._items if f.period == period]
        return max(matches, key=lambda f: f.recorded_at) if matches else None

    @_synchronized
    def list(self) -> List[FinanceSourceOfTruth]:
        return copy.copy(self._items)

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class MetricDefinitionRepository:
    def __init__(self) -> None:
        self._items: dict[str, List[MetricDefinition]] = defaultdict(list)
        self._lock = RLock()

    @_synchronized
    def add(self, m: MetricDefinition) -> None:
        self._items[m.name].append(m)

    @_synchronized
    def get(self, name: str, version: int | None = None) -> MetricDefinition | None:
        versions = self._items.get(name, [])
        if version is not None:
            return next((m for m in versions if m.version == version), None)
        return max(versions, key=lambda m: m.version) if versions else None

    @_synchronized
    def list(self, status: str | None = None) -> List[MetricDefinition]:
        out: List[MetricDefinition] = []
        for versions in self._items.values():
            for m in versions:
                if status is None or m.status == status:
                    out.append(m)
        return out

    @_synchronized
    def history(self, name: str) -> List[MetricDefinition]:
        return sorted(self._items.get(name, []), key=lambda m: m.version)

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class AttributionRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], AttributionRecord] = {}
        self._lock = RLock()

    @_synchronized
    def put(self, a: AttributionRecord) -> None:
        self._items[(a.deal_id, a.model)] = a

    @_synchronized
    def get(self, deal_id: str, model: str | None = None) -> AttributionRecord | None:
        if model is None:
            matches = [a for a in self._items.values() if a.deal_id == deal_id]
            return max(matches, key=lambda a: a.computed_at) if matches else None
        return self._items.get((deal_id, model))

    @_synchronized
    def list(self, model: str | None = None) -> List[AttributionRecord]:
        return [a for a in self._items.values() if model is None or a.model == model]

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class ReportScheduleRepository:
    def __init__(self) -> None:
        self._items: dict[str, ReportSchedule] = {}
        self._lock = RLock()

    @_synchronized
    def put(self, r: ReportSchedule) -> None:
        self._items[r.report_id] = r

    @_synchronized
    def get(self, report_id: str) -> ReportSchedule | None:
        return self._items.get(report_id)

    @_synchronized
    def list(self) -> List[ReportSchedule]:
        return list(self._items.values())

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class ReconciliationRepository:
    def __init__(self) -> None:
        self._items: List[ReconciliationRecord] = []
        self._lock = RLock()

    @_synchronized
    def add(self, r: ReconciliationRecord) -> None:
        self._items.append(r)

    @_synchronized
    def list(self, period: str | None = None) -> List[ReconciliationRecord]:
        return [r for r in self._items if period is None or r.period == period]

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class AlertRepository:
    def __init__(self) -> None:
        self._items: List[AlertRecord] = []
        self._lock = RLock()

    @_synchronized
    def add(self, a: AlertRecord) -> None:
        self._items.append(a)

    @_synchronized
    def list(self, metric: str | None = None, severity: str | None = None) -> List[AlertRecord]:
        return [
            a
            for a in self._items
            if (metric is None or a.metric == metric) and (severity is None or a.severity == severity)
        ]

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class KpiSnapshotRepository:
    def __init__(self) -> None:
        self._items: List[KpiSnapshot] = []
        self._lock = RLock()

    @_synchronized
    def add(self, s: KpiSnapshot) -> None:
        self._items.append(s)

    @_synchronized
    def list(self, metric_name: str | None = None) -> List[KpiSnapshot]:
        return [s for s in self._items if metric_name is None or s.metric_name == metric_name]

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class ExperimentResultRepository:
    def __init__(self) -> None:
        self._items: List[ExperimentResult] = []
        self._lock = RLock()

    @_synchronized
    def add(self, e: ExperimentResult) -> None:
        self._items.append(e)

    @_synchronized
    def list(self) -> List[ExperimentResult]:
        return copy.copy(self._items)

    @_synchronized
    def clear(self) -> None:
        self._items.clear()


class EventLogRepository:
    def __init__(self) -> None:
        self._items: List[dict[str, Any]] = []
        self._lock = RLock()

    @_synchronized
    def add(self, event_type: str, payload: dict[str, Any]) -> None:
        self._items.append({"event": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()})

    @_synchronized
    def list(self, event_type: str | None = None) -> List[dict[str, Any]]:
        if event_type is None:
            return copy.copy(self._items)
        return [e for e in self._items if e["event"] == event_type]

    @_synchronized
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


def _sql():
    from app.db import sql_repositories as sr
    return sr


def _uses_sql() -> bool:
    try:
        return get_settings().uses_sql
    except Exception:  # noqa: BLE001 — callers may run with temporarily invalid env
        return False


def get_touch_repository() -> TouchRepository:
    return _sql().SqlTouchRepository() if _uses_sql() else _touches


def get_outcome_repository() -> OutcomeRepository:
    return _sql().SqlOutcomeRepository() if _uses_sql() else _outcomes


def get_campaign_cost_repository() -> CampaignCostRepository:
    return _sql().SqlCampaignCostRepository() if _uses_sql() else _campaign_costs


def get_finance_repository() -> FinanceSourceRepository:
    return _sql().SqlFinanceSourceRepository() if _uses_sql() else _finance


def get_metric_repository() -> MetricDefinitionRepository:
    return _sql().SqlMetricDefinitionRepository() if _uses_sql() else _metrics


def get_attribution_repository() -> AttributionRepository:
    return _sql().SqlAttributionRepository() if _uses_sql() else _attributions


def get_report_repository() -> ReportScheduleRepository:
    return _sql().SqlReportScheduleRepository() if _uses_sql() else _reports


def get_reconciliation_repository() -> ReconciliationRepository:
    return _sql().SqlReconciliationRepository() if _uses_sql() else _reconciliations


def get_alert_repository() -> AlertRepository:
    return _sql().SqlAlertRepository() if _uses_sql() else _alerts


def get_snapshot_repository() -> KpiSnapshotRepository:
    return _sql().SqlKpiSnapshotRepository() if _uses_sql() else _snapshots


def get_experiment_repository() -> ExperimentResultRepository:
    return _sql().SqlExperimentResultRepository() if _uses_sql() else _experiments


def get_event_log_repository() -> EventLogRepository:
    return _sql().SqlEventLogRepository() if _uses_sql() else _events


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
    if _uses_sql():
        _sql().SqlTouchRepository().clear()
        _sql().SqlOutcomeRepository().clear()
        _sql().SqlCampaignCostRepository().clear()
        _sql().SqlFinanceSourceRepository().clear()
        _sql().SqlMetricDefinitionRepository().clear()
        _sql().SqlAttributionRepository().clear()
        _sql().SqlReportScheduleRepository().clear()
        _sql().SqlReconciliationRepository().clear()
        _sql().SqlAlertRepository().clear()
        _sql().SqlKpiSnapshotRepository().clear()
        _sql().SqlExperimentResultRepository().clear()
        _sql().SqlEventLogRepository().clear()
