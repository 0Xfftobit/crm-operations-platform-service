"""SQL-backed repositories — same method surface as the in-memory ones, one short session per call.

Operational models round-trip through a JSON ``data`` blob (``model_dump`` ↔ ``Model(**)``), so the
DB schema stays stable as models evolve while key columns keep queries indexable.
"""
from __future__ import annotations

from app.db import engine
from app.db.orm import (
    AuditRow,
    ContactRow,
    EngagementEventRow,
    EnrollmentRow,
    OutreachRecordRow,
    SequenceRow,
)
from app.domain.models import (
    Contact,
    EngagementEvent,
    Enrollment,
    OutreachRecord,
    SequenceDef,
)


class SqlSequenceRepository:
    def put(self, s: SequenceDef) -> None:
        with engine.session() as session:
            session.merge(
                SequenceRow(
                    tenant_id=s.tenant_id,
                    sequence_id=s.sequence_id,
                    version=s.version,
                    status=s.status,
                    data=s.model_dump(mode="json"),
                )
            )
            session.commit()

    def get(self, sequence_id: str, tenant: str = "default",
            version: int | None = None) -> SequenceDef | None:
        with engine.session() as session:
            if version is not None:
                row = session.get(SequenceRow, (tenant, sequence_id, version))
                return SequenceDef(**row.data) if row else None
            rows = (
                session.query(SequenceRow)
                .filter_by(tenant_id=tenant, sequence_id=sequence_id)
                .all()
            )
            if not rows:
                return None
            versions = [SequenceDef(**r.data) for r in rows]
            active = [s for s in versions if s.status == "active"] or versions
            return max(active, key=lambda s: s.version, default=None)

    def list(self, tenant: str | None = None) -> list[SequenceDef]:
        with engine.session() as session:
            query = session.query(SequenceRow)
            if tenant is not None:
                query = query.filter_by(tenant_id=tenant)
            return [SequenceDef(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(SequenceRow).delete(synchronize_session=False)
            session.commit()


class SqlContactRepository:
    def put(self, c: Contact) -> None:
        with engine.session() as session:
            session.merge(
                ContactRow(
                    tenant_id=c.tenant_id,
                    contact_id=c.contact_id,
                    data=c.model_dump(mode="json"),
                )
            )
            session.commit()

    def get(self, contact_id: str, tenant: str = "default") -> Contact | None:
        with engine.session() as session:
            row = session.get(ContactRow, (tenant, contact_id))
            return Contact(**row.data) if row else None

    def list(self, tenant: str | None = None) -> list[Contact]:
        with engine.session() as session:
            query = session.query(ContactRow)
            if tenant is not None:
                query = query.filter_by(tenant_id=tenant)
            return [Contact(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(ContactRow).delete(synchronize_session=False)
            session.commit()


class SqlEnrollmentRepository:
    def put(self, e: Enrollment) -> None:
        with engine.session() as session:
            session.merge(
                EnrollmentRow(
                    enrollment_id=e.enrollment_id,
                    tenant_id=e.tenant_id,
                    contact_id=e.contact_id,
                    sequence_id=e.sequence_id,
                    state=e.state,
                    next_action_at=e.next_action_at.isoformat(),
                    data=e.model_dump(mode="json"),
                )
            )
            session.commit()

    def get(self, enrollment_id: str) -> Enrollment | None:
        with engine.session() as session:
            row = session.get(EnrollmentRow, enrollment_id)
            return Enrollment(**row.data) if row else None

    def for_contact(self, contact_id: str, tenant: str) -> list[Enrollment]:
        with engine.session() as session:
            rows = (
                session.query(EnrollmentRow)
                .filter_by(contact_id=contact_id, tenant_id=tenant)
                .all()
            )
            return [Enrollment(**r.data) for r in rows]

    def active_for_contact(self, contact_id: str, tenant: str) -> list[Enrollment]:
        with engine.session() as session:
            rows = (
                session.query(EnrollmentRow)
                .filter(
                    EnrollmentRow.contact_id == contact_id,
                    EnrollmentRow.tenant_id == tenant,
                    EnrollmentRow.state.in_(["active", "paused"]),
                )
                .all()
            )
            return [Enrollment(**r.data) for r in rows]

    def due(self, now, tenant: str | None = None) -> list[Enrollment]:
        with engine.session() as session:
            query = session.query(EnrollmentRow).filter(EnrollmentRow.state == "active")
            if tenant is not None:
                query = query.filter(EnrollmentRow.tenant_id == tenant)
            query = query.filter(EnrollmentRow.next_action_at <= now.isoformat())
            return [Enrollment(**r.data) for r in query.all()]

    def list(self, tenant: str | None = None) -> list[Enrollment]:
        with engine.session() as session:
            query = session.query(EnrollmentRow)
            if tenant is not None:
                query = query.filter_by(tenant_id=tenant)
            return [Enrollment(**r.data) for r in query.all()]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(EnrollmentRow).delete(synchronize_session=False)
            session.commit()


class SqlLedgerRepository:
    def add(self, r: OutreachRecord) -> None:
        with engine.session() as session:
            session.add(
                OutreachRecordRow(
                    record_id=r.id,
                    tenant_id=r.tenant_id,
                    enrollment_id=r.enrollment_id,
                    step_id=r.step_id,
                    status=r.status,
                    occurrence=r.occurrence,
                    sent_at=r.sent_at.isoformat(),
                    data=r.model_dump(mode="json"),
                )
            )
            session.commit()

    def sent_exists(self, enrollment_id: str, step_id: str, occurrence: int) -> bool:
        with engine.session() as session:
            row = (
                session.query(OutreachRecordRow)
                .filter_by(
                    enrollment_id=enrollment_id,
                    step_id=step_id,
                    occurrence=occurrence,
                    status="sent",
                )
                .first()
            )
            return row is not None

    def for_enrollment(self, enrollment_id: str) -> list[OutreachRecord]:
        with engine.session() as session:
            rows = (
                session.query(OutreachRecordRow)
                .filter_by(enrollment_id=enrollment_id)
                .order_by(OutreachRecordRow.sent_at)
                .all()
            )
            return [OutreachRecord(**r.data) for r in rows]

    def for_sequence(self, sequence_id: str, tenant: str) -> list[OutreachRecord]:
        # The in-memory ledger filters by tenant only (sequence is resolved by the caller).
        with engine.session() as session:
            rows = (
                session.query(OutreachRecordRow)
                .filter_by(tenant_id=tenant)
                .order_by(OutreachRecordRow.sent_at)
                .all()
            )
            return [OutreachRecord(**r.data) for r in rows]

    def list(self, tenant: str | None = None) -> list[OutreachRecord]:
        with engine.session() as session:
            query = session.query(OutreachRecordRow)
            if tenant is not None:
                query = query.filter_by(tenant_id=tenant)
            rows = query.order_by(OutreachRecordRow.sent_at).all()
            return [OutreachRecord(**r.data) for r in rows]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(OutreachRecordRow).delete(synchronize_session=False)
            session.commit()


class SqlEngagementRepository:
    def add(self, e: EngagementEvent) -> None:
        with engine.session() as session:
            session.add(
                EngagementEventRow(
                    tenant_id=e.tenant_id,
                    contact_id=e.contact_id,
                    enrollment_id=e.enrollment_id,
                    step_id=e.step_id,
                    kind=e.kind,
                    channel=e.channel,
                    at=e.at.isoformat(),
                    data=e.model_dump(mode="json"),
                )
            )
            session.commit()

    def kinds_for_step(self, enrollment_id: str, step_id: str) -> set[str]:
        with engine.session() as session:
            rows = (
                session.query(EngagementEventRow.kind)
                .filter_by(enrollment_id=enrollment_id, step_id=step_id)
                .distinct()
                .all()
            )
            return {r[0] for r in rows}

    def list(self) -> list[EngagementEvent]:
        with engine.session() as session:
            rows = (
                session.query(EngagementEventRow)
                .order_by(EngagementEventRow.at, EngagementEventRow.id)
                .all()
            )
            return [EngagementEvent(**r.data) for r in rows]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(EngagementEventRow).delete(synchronize_session=False)
            session.commit()


class SqlAuditRepository:
    def add(self, record: dict) -> None:
        with engine.session() as session:
            session.add(AuditRow(data=record))
            session.commit()

    def list(self) -> list[dict]:
        with engine.session() as session:
            rows = session.query(AuditRow).order_by(AuditRow.id).all()
            return [r.data for r in rows]

    def clear(self) -> None:
        with engine.session() as session:
            session.query(AuditRow).delete(synchronize_session=False)
            session.commit()
