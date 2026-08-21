"""Postgres-backed store — same interface as the in-memory functions in app.store."""
from __future__ import annotations

from app.db.engine import new_session
from app.db.orm import OutcomeORM, RetrainRunORM
from app.domain.models import Outcome, RetrainRun


def add_outcome(o: Outcome) -> None:
    with new_session() as db:
        db.add(OutcomeORM(outcome_id=o.outcome_id, engine=o.engine, data=o.model_dump(mode="json")))
        db.commit()


def outcomes(engine: str | None = None, limit: int = 200) -> list[Outcome]:
    from sqlalchemy import select
    with new_session() as db:
        stmt = select(OutcomeORM).order_by(OutcomeORM.seq.desc()).limit(limit)
        if engine:
            stmt = (select(OutcomeORM).where(OutcomeORM.engine == engine)
                    .order_by(OutcomeORM.seq.desc()).limit(limit))
        return [Outcome.model_validate(r.data) for r in db.execute(stmt).scalars().all()]


def outcomes_for(engine: str) -> list[Outcome]:
    from sqlalchemy import select
    with new_session() as db:
        rows = db.execute(select(OutcomeORM).where(OutcomeORM.engine == engine)
                          .order_by(OutcomeORM.seq)).scalars().all()
        return [Outcome.model_validate(r.data) for r in rows]


def engines() -> list[str]:
    from sqlalchemy import distinct, select
    with new_session() as db:
        return sorted(db.execute(select(distinct(OutcomeORM.engine))).scalars().all())


def add_run(r: RetrainRun) -> None:
    with new_session() as db:
        db.add(RetrainRunORM(run_id=r.run_id, engine=r.engine, samples_used=r.samples_used,
                             data=r.model_dump(mode="json")))
        db.commit()


def runs(engine: str | None = None) -> list[RetrainRun]:
    from sqlalchemy import select
    with new_session() as db:
        stmt = select(RetrainRunORM).order_by(RetrainRunORM.seq.desc())
        if engine:
            stmt = (select(RetrainRunORM).where(RetrainRunORM.engine == engine)
                    .order_by(RetrainRunORM.seq.desc()))
        return [RetrainRun.model_validate(r.data) for r in db.execute(stmt).scalars().all()]


def baseline(engine: str) -> int:
    from sqlalchemy import select
    with new_session() as db:
        row = db.execute(select(RetrainRunORM.samples_used).where(RetrainRunORM.engine == engine)
                         .order_by(RetrainRunORM.seq.desc()).limit(1)).first()
        return row[0] if row else 0


def reset() -> None:
    from sqlalchemy import delete
    with new_session() as db:
        db.execute(delete(OutcomeORM)); db.execute(delete(RetrainRunORM)); db.commit()
