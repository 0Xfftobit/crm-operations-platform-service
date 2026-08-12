"""ORM tables. Operational rows use a hybrid of key columns + a JSON ``data`` blob.

The JSON blob holds the full pydantic model, while key columns keep the common query paths
indexable and stable as domain models evolve.
"""
from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SequenceRow(Base):
    __tablename__ = "sequences"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    sequence_id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, index=True, default="active")
    data: Mapped[dict] = mapped_column(JSON)


class ContactRow(Base):
    __tablename__ = "contacts"

    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    contact_id: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class EnrollmentRow(Base):
    __tablename__ = "enrollments"

    enrollment_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    contact_id: Mapped[str] = mapped_column(String, index=True)
    sequence_id: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String, index=True)
    next_action_at: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class OutreachRecordRow(Base):
    __tablename__ = "outreach_records"

    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    enrollment_id: Mapped[str] = mapped_column(String, index=True)
    step_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, index=True)
    occurrence: Mapped[int] = mapped_column(Integer, index=True)
    sent_at: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class EngagementEventRow(Base):
    __tablename__ = "engagement_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True, default="default")
    contact_id: Mapped[str] = mapped_column(String, index=True)
    enrollment_id: Mapped[str] = mapped_column(String, index=True, default="")
    step_id: Mapped[str] = mapped_column(String, index=True, default="")
    kind: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String, default="")
    at: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class AuditRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data: Mapped[dict] = mapped_column(JSON)
