"""Store router: in-memory by default, Postgres/Neon when STORAGE_GATEWAY=postgres + DATABASE_URL
(settings.uses_sql). SQLAlchemy is imported lazily (only on the SQL path) so the default build is dep-free.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.domain.models import Outcome, RetrainRun

_outcomes: list[Outcome] = []
_runs: list[RetrainRun] = []


def _sql() -> bool:
    return get_settings().uses_sql


def add_outcome(o: Outcome) -> None:
    if _sql():
        from app.db import sql_store
        sql_store.add_outcome(o); return
    _outcomes.append(o)


def outcomes(engine: str | None = None, limit: int = 200) -> list[Outcome]:
    if _sql():
        from app.db import sql_store
        return sql_store.outcomes(engine, limit)
    items = [o for o in _outcomes if engine is None or o.engine == engine]
    return list(reversed(items[-limit:]))


def outcomes_for(engine: str) -> list[Outcome]:
    if _sql():
        from app.db import sql_store
        return sql_store.outcomes_for(engine)
    return [o for o in _outcomes if o.engine == engine]


def engines() -> list[str]:
    if _sql():
        from app.db import sql_store
        return sql_store.engines()
    return sorted({o.engine for o in _outcomes})


def add_run(r: RetrainRun) -> None:
    if _sql():
        from app.db import sql_store
        sql_store.add_run(r); return
    _runs.append(r)


def runs(engine: str | None = None) -> list[RetrainRun]:
    if _sql():
        from app.db import sql_store
        return sql_store.runs(engine)
    items = [r for r in _runs if engine is None or r.engine == engine]
    return list(reversed(items))


def baseline(engine: str) -> int:
    """Outcome count at the engine's last retrain (derived from the latest run's samples_used)."""
    if _sql():
        from app.db import sql_store
        return sql_store.baseline(engine)
    eng_runs = [r for r in _runs if r.engine == engine]
    return eng_runs[-1].samples_used if eng_runs else 0


def reset() -> None:
    if _sql():
        from app.db import sql_store
        sql_store.reset()
    _outcomes.clear()
    _runs.clear()
