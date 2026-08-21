"""Store router: in-memory by default, Postgres/Neon when settings.uses_sql. SQLAlchemy is imported
lazily (SQL path only) so the default build is dependency-free."""
from __future__ import annotations

from app.core.config import get_settings
from app.domain.models import MetricPoint

_points: list[MetricPoint] = []


def _sql() -> bool:
    return get_settings().uses_sql


def add_point(p: MetricPoint) -> None:
    if _sql():
        from app.db import sql_store
        sql_store.add_point(p); return
    _points.append(p)


def points(metric: str | None = None, limit: int = 200) -> list[MetricPoint]:
    if _sql():
        from app.db import sql_store
        return sql_store.points(metric, limit)
    items = [p for p in _points if metric is None or p.metric == metric]
    return list(reversed(items[-limit:]))


def points_for(metric: str, window: int | None = None) -> list[MetricPoint]:
    if _sql():
        from app.db import sql_store
        return sql_store.points_for(metric, window)
    items = [p for p in _points if p.metric == metric]
    return items[-window:] if window else items


def metrics() -> list[str]:
    if _sql():
        from app.db import sql_store
        return sql_store.metrics()
    return sorted({p.metric for p in _points})


def reset() -> None:
    if _sql():
        from app.db import sql_store
        sql_store.reset()
    _points.clear()
