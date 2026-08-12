"""SQL-backed repositories — same method surface as the in-memory ones, one short session per call.

Operational models round-trip through a JSON ``data`` blob (``model_dump`` ↔ ``Model(**)``), so the
DB schema stays stable as models evolve while key columns keep queries indexable.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import engine
from app.db.orm import (
    AlertRow,
    AttributionRow,
    CampaignCostRow,
    EventLogRow,
    ExperimentResultRow,
    FinanceSourceRow,
    KpiSnapshotRow,
    MetricDefinitionRow,
    OutcomeRow,
    ReconciliationRow,
    ReportScheduleRow,
    TouchRow,
)
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


class SqlTouchRepository:
    def add(self, t: TouchEvent) -> None:
        with engine.session() as s:
            s.merge(
                TouchRow(event_id=t.event_id, touch_id=t.touch_id, data=t.model_dump(mode="json"))
            )
            s.commit()

    def get_by_event(self, event_id: str) -> TouchEvent | None:
        with engine.session() as s:
            row = s.get(TouchRow, event_id)
            return TouchEvent(**row.data) if row else None

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
        with engine.session() as s:
            items = [TouchEvent(**r.data) for r in s.query(TouchRow).all()]
        return [
            t
            for t in items
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
        with engine.session() as s:
            s.query(TouchRow).delete(synchronize_session=False)
            s.commit()


class SqlOutcomeRepository:
    def add(self, o: OutcomeEvent) -> None:
        with engine.session() as s:
            s.merge(OutcomeRow(event_id=o.event_id, data=o.model_dump(mode="json")))
            s.commit()

    def get_by_event(self, event_id: str) -> OutcomeEvent | None:
        with engine.session() as s:
            row = s.get(OutcomeRow, event_id)
            return OutcomeEvent(**row.data) if row else None

    def list(
        self,
        tenant_id: str | None = None,
        team_id: str | None = None,
        rep_id: str | None = None,
        deal_id: str | None = None,
        contact_pid: str | None = None,
        stage: str | None = None,
    ) -> list[OutcomeEvent]:
        with engine.session() as s:
            items = [OutcomeEvent(**r.data) for r in s.query(OutcomeRow).all()]
        return [
            o
            for o in items
            if (tenant_id is None or o.tenant_id == tenant_id)
            and (team_id is None or o.team_id == team_id)
            and (rep_id is None or o.rep_id == rep_id)
            and (deal_id is None or o.deal_id == deal_id)
            and (contact_pid is None or o.contact_pid == contact_pid)
            and (stage is None or o.stage == stage)
        ]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(OutcomeRow).delete(synchronize_session=False)
            s.commit()


class SqlCampaignCostRepository:
    def add(self, c: CampaignCost) -> None:
        with engine.session() as s:
            s.add(
                CampaignCostRow(
                    cost_id=c.cost_id,
                    campaign_id=c.campaign_id,
                    period=c.period,
                    data=c.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, campaign_id: str | None = None, period: str | None = None) -> list[CampaignCost]:
        with engine.session() as s:
            query = s.query(CampaignCostRow)
            if campaign_id is not None:
                query = query.filter(CampaignCostRow.campaign_id == campaign_id)
            if period is not None:
                query = query.filter(CampaignCostRow.period == period)
            return [CampaignCost(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(CampaignCostRow).delete(synchronize_session=False)
            s.commit()


class SqlFinanceSourceRepository:
    def add(self, f: FinanceSourceOfTruth) -> None:
        with engine.session() as s:
            s.add(
                FinanceSourceRow(
                    record_id=f.record_id,
                    period=f.period,
                    recorded_at=f.recorded_at,
                    data=f.model_dump(mode="json"),
                )
            )
            s.commit()

    def get(self, period: str) -> FinanceSourceOfTruth | None:
        with engine.session() as s:
            row = (
                s.query(FinanceSourceRow)
                .filter_by(period=period)
                .order_by(FinanceSourceRow.recorded_at.desc())
                .first()
            )
            return FinanceSourceOfTruth(**row.data) if row else None

    def list(self) -> list[FinanceSourceOfTruth]:
        with engine.session() as s:
            return [FinanceSourceOfTruth(**r.data) for r in s.query(FinanceSourceRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(FinanceSourceRow).delete(synchronize_session=False)
            s.commit()


class SqlMetricDefinitionRepository:
    def add(self, m: MetricDefinition) -> None:
        with engine.session() as s:
            s.merge(
                MetricDefinitionRow(
                    name=m.name, version=m.version, status=m.status, data=m.model_dump(mode="json")
                )
            )
            s.commit()

    def get(self, name: str, version: int | None = None) -> MetricDefinition | None:
        with engine.session() as s:
            if version is not None:
                row = s.get(MetricDefinitionRow, (name, version))
                return MetricDefinition(**row.data) if row else None
            row = (
                s.query(MetricDefinitionRow)
                .filter_by(name=name)
                .order_by(MetricDefinitionRow.version.desc())
                .first()
            )
            return MetricDefinition(**row.data) if row else None

    def list(self, status: str | None = None) -> list[MetricDefinition]:
        with engine.session() as s:
            query = s.query(MetricDefinitionRow)
            if status is not None:
                query = query.filter(MetricDefinitionRow.status == status)
            return [MetricDefinition(**r.data) for r in query.all()]

    def history(self, name: str) -> list[MetricDefinition]:
        with engine.session() as s:
            rows = (
                s.query(MetricDefinitionRow)
                .filter_by(name=name)
                .order_by(MetricDefinitionRow.version)
                .all()
            )
            return [MetricDefinition(**r.data) for r in rows]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(MetricDefinitionRow).delete(synchronize_session=False)
            s.commit()


class SqlAttributionRepository:
    def put(self, a: AttributionRecord) -> None:
        with engine.session() as s:
            s.merge(
                AttributionRow(
                    deal_id=a.deal_id,
                    model=a.model,
                    computed_at=a.computed_at,
                    data=a.model_dump(mode="json"),
                )
            )
            s.commit()

    def get(self, deal_id: str, model: str | None = None) -> AttributionRecord | None:
        with engine.session() as s:
            if model is not None:
                row = s.get(AttributionRow, (deal_id, model))
                return AttributionRecord(**row.data) if row else None
            row = (
                s.query(AttributionRow)
                .filter_by(deal_id=deal_id)
                .order_by(AttributionRow.computed_at.desc())
                .first()
            )
            return AttributionRecord(**row.data) if row else None

    def list(self, model: str | None = None) -> list[AttributionRecord]:
        with engine.session() as s:
            query = s.query(AttributionRow)
            if model is not None:
                query = query.filter(AttributionRow.model == model)
            return [AttributionRecord(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(AttributionRow).delete(synchronize_session=False)
            s.commit()


class SqlReportScheduleRepository:
    def put(self, r: ReportSchedule) -> None:
        with engine.session() as s:
            s.merge(ReportScheduleRow(k=r.report_id, data=r.model_dump(mode="json")))
            s.commit()

    def get(self, report_id: str) -> ReportSchedule | None:
        with engine.session() as s:
            row = s.get(ReportScheduleRow, report_id)
            return ReportSchedule(**row.data) if row else None

    def list(self) -> list[ReportSchedule]:
        with engine.session() as s:
            return [ReportSchedule(**r.data) for r in s.query(ReportScheduleRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ReportScheduleRow).delete(synchronize_session=False)
            s.commit()


class SqlReconciliationRepository:
    def add(self, r: ReconciliationRecord) -> None:
        with engine.session() as s:
            s.add(
                ReconciliationRow(
                    record_id=r.record_id, period=r.period, data=r.model_dump(mode="json")
                )
            )
            s.commit()

    def list(self, period: str | None = None) -> list[ReconciliationRecord]:
        with engine.session() as s:
            query = s.query(ReconciliationRow)
            if period is not None:
                query = query.filter(ReconciliationRow.period == period)
            return [ReconciliationRecord(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ReconciliationRow).delete(synchronize_session=False)
            s.commit()


class SqlAlertRepository:
    def add(self, a: AlertRecord) -> None:
        with engine.session() as s:
            s.add(
                AlertRow(
                    alert_id=a.alert_id,
                    metric=a.metric,
                    severity=a.severity,
                    data=a.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, metric: str | None = None, severity: str | None = None) -> list[AlertRecord]:
        with engine.session() as s:
            query = s.query(AlertRow)
            if metric is not None:
                query = query.filter(AlertRow.metric == metric)
            if severity is not None:
                query = query.filter(AlertRow.severity == severity)
            return [AlertRecord(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(AlertRow).delete(synchronize_session=False)
            s.commit()


class SqlKpiSnapshotRepository:
    def add(self, snapshot: KpiSnapshot) -> None:
        with engine.session() as s:
            s.add(
                KpiSnapshotRow(
                    snapshot_id=snapshot.snapshot_id,
                    metric_name=snapshot.metric_name,
                    data=snapshot.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, metric_name: str | None = None) -> list[KpiSnapshot]:
        with engine.session() as s:
            query = s.query(KpiSnapshotRow)
            if metric_name is not None:
                query = query.filter(KpiSnapshotRow.metric_name == metric_name)
            return [KpiSnapshot(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(KpiSnapshotRow).delete(synchronize_session=False)
            s.commit()


class SqlExperimentResultRepository:
    def add(self, e: ExperimentResult) -> None:
        with engine.session() as s:
            s.add(
                ExperimentResultRow(
                    experiment_id=e.experiment_id, data=e.model_dump(mode="json")
                )
            )
            s.commit()

    def list(self) -> list[ExperimentResult]:
        with engine.session() as s:
            return [ExperimentResult(**r.data) for r in s.query(ExperimentResultRow).all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ExperimentResultRow).delete(synchronize_session=False)
            s.commit()


class SqlEventLogRepository:
    def add(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {"event": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()}
        with engine.session() as s:
            s.add(EventLogRow(event=event_type, data=record))
            s.commit()

    def list(self, event_type: str | None = None) -> list[dict[str, Any]]:
        with engine.session() as s:
            query = s.query(EventLogRow)
            if event_type is not None:
                query = query.filter(EventLogRow.event == event_type)
            return [dict(r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(EventLogRow).delete(synchronize_session=False)
            s.commit()
