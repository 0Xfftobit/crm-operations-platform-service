"""Postgres-backed metric store — same interface as the in-memory functions in app.store."""
from __future__ import annotations

from app.db.engine import new_session
from app.db.orm import MetricPointORM
from app.domain.models import MetricPoint


def add_point(p: MetricPoint) -> None:
    with new_session() as db:
        db.add(MetricPointORM(point_id=p.point_id, metric=p.metric, data=p.model_dump(mode="json")))
        db.commit()


def points(metric: str | None = None, limit: int = 200) -> list[MetricPoint]:
    from sqlalchemy import select
    with new_session() as db:
        stmt = select(MetricPointORM).order_by(MetricPointORM.seq.desc()).limit(limit)
        if metric:
            stmt = (select(MetricPointORM).where(MetricPointORM.metric == metric)
                    .order_by(MetricPointORM.seq.desc()).limit(limit))
        return [MetricPoint.model_validate(r.data) for r in db.execute(stmt).scalars().all()]


def points_for(metric: str, window: int | None = None) -> list[MetricPoint]:
    from sqlalchemy import select
    with new_session() as db:
        stmt = select(MetricPointORM).where(MetricPointORM.metric == metric).order_by(MetricPointORM.seq.desc())
        if window:
            stmt = stmt.limit(window)
        rows = db.execute(stmt).scalars().all()
    return [MetricPoint.model_validate(r.data) for r in reversed(rows)]   # oldest → newest


def metrics() -> list[str]:
    from sqlalchemy import distinct, select
    with new_session() as db:
        return sorted(db.execute(select(distinct(MetricPointORM.metric))).scalars().all())


def reset() -> None:
    from sqlalchemy import delete
    with new_session() as db:
        db.execute(delete(MetricPointORM)); db.commit()
