"""ORM tables. Operational rows use a hybrid of key columns + a JSON ``data`` blob holding the full
pydantic model. The DB schema stays stable as models evolve while key columns keep queries indexable.
"""

from sqlalchemy import JSON, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PredictionRow(Base):
    __tablename__ = "prediction"
    prediction_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    tenant_id: Mapped[str] = mapped_column(String, default="default")
    data: Mapped[dict] = mapped_column(JSON)


class OutcomeRow(Base):
    __tablename__ = "outcome"
    outcome_id: Mapped[str] = mapped_column(String, primary_key=True)
    prediction_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    engine_name: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class FeedbackRow(Base):
    __tablename__ = "feedback"
    feedback_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    feedback_type: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class LabelRow(Base):
    __tablename__ = "label"
    label_id: Mapped[str] = mapped_column(String, primary_key=True)
    prediction_id: Mapped[str] = mapped_column(String, index=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    label_type: Mapped[str] = mapped_column(String, index=True)
    label_source: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class TrainingDatasetRow(Base):
    __tablename__ = "training_dataset"
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)
    frame: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class ModelVersionRow(Base):
    __tablename__ = "model_version"
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class ModelArtifactRow(Base):
    __tablename__ = "model_artifact"
    model_id: Mapped[str] = mapped_column(String, primary_key=True)
    blob: Mapped[bytes] = mapped_column(LargeBinary)


class DriftRecordRow(Base):
    __tablename__ = "drift_record"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    feature: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class ExperimentRow(Base):
    __tablename__ = "experiment"
    experiment_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class RetrainingRunRow(Base):
    __tablename__ = "retraining_run"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    engine_name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class EventLogRow(Base):
    __tablename__ = "event_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)
