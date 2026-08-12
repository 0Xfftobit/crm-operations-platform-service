"""SQL-backed repositories — same method surface as the in-memory ones, one short session per call.

Operational models round-trip through a JSON ``data`` blob (``model_dump`` ↔ ``Model(**)``), so the
DB schema stays stable as models evolve while key columns keep queries indexable. DataFrames and
sklearn artifacts are stored as jobl-lib BLOBs.
"""
from datetime import UTC, datetime
from io import BytesIO

import joblib

from app.db import engine
from app.db.orm import (
    DriftRecordRow,
    EventLogRow,
    ExperimentRow,
    FeedbackRow,
    LabelRow,
    ModelArtifactRow,
    ModelVersionRow,
    OutcomeRow,
    PredictionRow,
    RetrainingRunRow,
    TrainingDatasetRow,
)
from app.domain.models import (
    DriftRecord,
    Experiment,
    Feedback,
    Label,
    ModelVersion,
    Outcome,
    Prediction,
    RetrainingRun,
    TrainingDataset,
)


def _dumps_blob(obj: object) -> bytes:
    buf = BytesIO()
    joblib.dump(obj, buf)
    return buf.getvalue()


def _loads_blob(blob: bytes) -> object:
    return joblib.load(BytesIO(blob))


class SqlPredictionRepository:
    def put(self, p: Prediction) -> None:
        with engine.session() as s:
            s.merge(
                PredictionRow(
                    prediction_id=p.prediction_id,
                    engine_name=p.engine_name,
                    entity_id=p.entity_id,
                    tenant_id=p.tenant_id,
                    data=p.model_dump(mode="json"),
                )
            )
            s.commit()

    def get(self, prediction_id: str) -> Prediction | None:
        with engine.session() as s:
            row = s.get(PredictionRow, prediction_id)
            return Prediction(**row.data) if row else None

    def list(self, engine_name: str | None = None, entity_id: str | None = None) -> list[Prediction]:
        with engine.session() as s:
            q = s.query(PredictionRow)
            if engine_name is not None:
                q = q.filter(PredictionRow.engine_name == engine_name)
            if entity_id is not None:
                q = q.filter(PredictionRow.entity_id == entity_id)
            return [Prediction(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(PredictionRow).delete()
            s.commit()


class SqlOutcomeRepository:
    def add(self, o: Outcome) -> None:
        with engine.session() as s:
            s.merge(
                OutcomeRow(
                    outcome_id=o.outcome_id,
                    prediction_id=o.prediction_id,
                    engine_name=o.engine_name,
                    entity_id=o.entity_id,
                    data=o.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(
        self,
        engine_name: str | None = None,
        entity_id: str | None = None,
        unlabeled: bool = False,
        labels=None,
    ) -> list[Outcome]:
        with engine.session() as s:
            q = s.query(OutcomeRow)
            if engine_name is not None:
                q = q.filter(OutcomeRow.engine_name == engine_name)
            if entity_id is not None:
                q = q.filter(OutcomeRow.entity_id == entity_id)
            rows = q.all()
            outcomes = [Outcome(**r.data) for r in rows]

        if unlabeled and labels is not None:
            labeled_ids = {lab.prediction_id for lab in labels.list() if lab.label_source == "outcome_event"}
            outcomes = [o for o in outcomes if o.prediction_id not in labeled_ids]
        return outcomes

    def clear(self) -> None:
        with engine.session() as s:
            s.query(OutcomeRow).delete()
            s.commit()


class SqlFeedbackRepository:
    def add(self, f: Feedback) -> None:
        with engine.session() as s:
            s.merge(
                FeedbackRow(
                    feedback_id=f.feedback_id,
                    engine_name=f.engine_name,
                    feedback_type=f.feedback_type,
                    data=f.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, engine_name: str | None = None, feedback_type: str | None = None) -> list[Feedback]:
        with engine.session() as s:
            q = s.query(FeedbackRow)
            if engine_name is not None:
                q = q.filter(FeedbackRow.engine_name == engine_name)
            if feedback_type is not None:
                q = q.filter(FeedbackRow.feedback_type == feedback_type)
            return [Feedback(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(FeedbackRow).delete()
            s.commit()


class SqlLabelRepository:
    def add(self, label: Label) -> None:
        with engine.session() as s:
            s.merge(
                LabelRow(
                    label_id=label.label_id,
                    prediction_id=label.prediction_id,
                    engine_name=label.engine_name,
                    label_type=label.label_type,
                    label_source=label.label_source,
                    data=label.model_dump(mode="json"),
                )
            )
            s.commit()

    def get_for_prediction(self, prediction_id: str) -> list[Label]:
        with engine.session() as s:
            rows = s.query(LabelRow).filter(LabelRow.prediction_id == prediction_id).all()
            return [Label(**r.data) for r in rows]

    def list(self, engine_name: str | None = None, label_type: str | None = None) -> list[Label]:
        with engine.session() as s:
            q = s.query(LabelRow)
            if engine_name is not None:
                q = q.filter(LabelRow.engine_name == engine_name)
            if label_type is not None:
                q = q.filter(LabelRow.label_type == label_type)
            return [Label(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(LabelRow).delete()
            s.commit()


class SqlTrainingDatasetRepository:
    def put(self, meta: TrainingDataset, frame: object | None = None) -> None:
        with engine.session() as s:
            s.merge(
                TrainingDatasetRow(
                    dataset_id=meta.dataset_id,
                    engine_name=meta.engine_name,
                    data=meta.model_dump(mode="json"),
                    frame=_dumps_blob(frame) if frame is not None else None,
                )
            )
            s.commit()

    def get(self, dataset_id: str) -> tuple[TrainingDataset | None, object | None]:
        with engine.session() as s:
            row = s.get(TrainingDatasetRow, dataset_id)
            if row is None:
                return None, None
            meta = TrainingDataset(**row.data)
            frame = _loads_blob(row.frame) if row.frame is not None else None
            return meta, frame

    def list(self, engine_name: str | None = None) -> list[TrainingDataset]:
        with engine.session() as s:
            q = s.query(TrainingDatasetRow)
            if engine_name is not None:
                q = q.filter(TrainingDatasetRow.engine_name == engine_name)
            return [TrainingDataset(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(TrainingDatasetRow).delete()
            s.commit()


class SqlModelVersionRepository:
    def put(self, m: ModelVersion) -> None:
        with engine.session() as s:
            s.merge(
                ModelVersionRow(
                    model_id=m.model_id,
                    engine_name=m.engine_name,
                    data=m.model_dump(mode="json"),
                )
            )
            s.commit()

    def get(self, model_id: str) -> ModelVersion | None:
        with engine.session() as s:
            row = s.get(ModelVersionRow, model_id)
            return ModelVersion(**row.data) if row else None

    def get_production(self, engine_name: str) -> ModelVersion | None:
        with engine.session() as s:
            rows = s.query(ModelVersionRow).filter(ModelVersionRow.engine_name == engine_name).all()
        production = [m for m in (ModelVersion(**r.data) for r in rows) if m.status == "production"]
        if not production:
            return None
        return max(production, key=lambda m: m.promoted_at or datetime.min.replace(tzinfo=UTC))

    def set_production(self, engine_name: str, model_id: str) -> None:
        with engine.session() as s:
            rows = s.query(ModelVersionRow).filter(ModelVersionRow.engine_name == engine_name).all()
            for row in rows:
                m = ModelVersion(**row.data)
                if m.status == "production":
                    m.status = "archived"
                    m.promoted_at = None
                    row.data = m.model_dump(mode="json")

            target = s.get(ModelVersionRow, model_id)
            if target is not None:
                m = ModelVersion(**target.data)
                m.status = "production"
                m.promoted_at = datetime.now(UTC)
                target.data = m.model_dump(mode="json")
            s.commit()

    def list(self, engine_name: str | None = None) -> list[ModelVersion]:
        with engine.session() as s:
            q = s.query(ModelVersionRow)
            if engine_name is not None:
                q = q.filter(ModelVersionRow.engine_name == engine_name)
            return [ModelVersion(**r.data) for r in q.all()]

    def next_version(self, engine_name: str) -> str:
        with engine.session() as s:
            rows = s.query(ModelVersionRow).filter(ModelVersionRow.engine_name == engine_name).all()
            versions = [
                int(m.version[1:])
                for m in (ModelVersion(**r.data) for r in rows)
                if m.version.startswith("v")
            ]
            next_int = max(versions, default=0) + 1
            return f"v{next_int}"

    def add_artifact(self, model_id: str, artifact: dict) -> None:
        with engine.session() as s:
            s.merge(ModelArtifactRow(model_id=model_id, blob=_dumps_blob(artifact)))
            s.commit()

    def get_artifact(self, model_id: str) -> object | None:
        with engine.session() as s:
            row = s.get(ModelArtifactRow, model_id)
            return _loads_blob(row.blob) if row else None

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ModelVersionRow).delete()
            s.query(ModelArtifactRow).delete()
            s.commit()


class SqlDriftRepository:
    def add(self, d: DriftRecord) -> None:
        with engine.session() as s:
            s.merge(
                DriftRecordRow(
                    record_id=d.record_id,
                    engine_name=d.engine_name,
                    feature=d.feature,
                    data=d.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, engine_name: str | None = None, feature: str | None = None) -> list[DriftRecord]:
        with engine.session() as s:
            q = s.query(DriftRecordRow)
            if engine_name is not None:
                q = q.filter(DriftRecordRow.engine_name == engine_name)
            if feature is not None:
                q = q.filter(DriftRecordRow.feature == feature)
            return [DriftRecord(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(DriftRecordRow).delete()
            s.commit()


class SqlExperimentRepository:
    def put(self, e: Experiment) -> None:
        with engine.session() as s:
            s.merge(
                ExperimentRow(
                    experiment_id=e.experiment_id,
                    engine_name=e.engine_name,
                    data=e.model_dump(mode="json"),
                )
            )
            s.commit()

    def get(self, experiment_id: str) -> Experiment | None:
        with engine.session() as s:
            row = s.get(ExperimentRow, experiment_id)
            return Experiment(**row.data) if row else None

    def list(self, engine_name: str | None = None) -> list[Experiment]:
        with engine.session() as s:
            q = s.query(ExperimentRow)
            if engine_name is not None:
                q = q.filter(ExperimentRow.engine_name == engine_name)
            return [Experiment(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(ExperimentRow).delete()
            s.commit()


class SqlRetrainingRunRepository:
    def add(self, run: RetrainingRun) -> None:
        with engine.session() as s:
            s.merge(
                RetrainingRunRow(
                    run_id=run.run_id,
                    engine_name=run.engine_name,
                    data=run.model_dump(mode="json"),
                )
            )
            s.commit()

    def list(self, engine_name: str | None = None) -> list[RetrainingRun]:
        with engine.session() as s:
            q = s.query(RetrainingRunRow)
            if engine_name is not None:
                q = q.filter(RetrainingRunRow.engine_name == engine_name)
            return [RetrainingRun(**r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(RetrainingRunRow).delete()
            s.commit()


class SqlEventLogRepository:
    def add(self, event_type: str, payload: dict) -> None:
        with engine.session() as s:
            s.add(
                EventLogRow(
                    event=event_type,
                    data={"event": event_type, "payload": payload, "at": datetime.now(UTC).isoformat()},
                )
            )
            s.commit()

    def list(self, event_type: str | None = None) -> list[dict]:
        with engine.session() as s:
            q = s.query(EventLogRow)
            if event_type is not None:
                q = q.filter(EventLogRow.event == event_type)
            return [dict(r.data) for r in q.all()]

    def clear(self) -> None:
        with engine.session() as s:
            s.query(EventLogRow).delete()
            s.commit()
