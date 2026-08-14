"""SQLAlchemy engine + session factory, built lazily from ``DATABASE_URL``.

SQLite URLs get a shared static pool so an in-memory/file DB is visible across sessions (tests);
Postgres uses a pre-ping pool. ``reset_engine`` lets the test suite repoint the DB between runs.
"""
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        return create_engine(
            url, future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    _schema = get_settings().db_schema
    if _schema:
        # shared-Neon deployment: one database, one schema per engine (search_path via libpq)
        kwargs["connect_args"] = {"options": f"-csearch_path={_schema}"}
    engine = create_engine(url, **kwargs)
    if get_settings().db_auto_create:
        _ensure_schema(engine, url)
        from app.db.orm import Base
        Base.metadata.create_all(engine)
    return engine


@lru_cache
def _factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session, future=True)


def session() -> Session:
    """Open a new session (caller manages the transaction / context)."""
    return _factory()()


def create_all() -> None:
    engine = get_engine()
    _ensure_schema(engine, get_settings().database_url)
    from app.db.orm import Base
    Base.metadata.create_all(engine)


def reset_engine() -> None:
    """Drop cached engine/session factory (tests switching DATABASE_URL)."""
    try:
        get_engine().dispose()
    except Exception:  # noqa: BLE001 — best-effort teardown
        pass
    get_engine.cache_clear()
    _factory.cache_clear()


def postgres_health() -> dict[str, str]:
    """Best-effort Postgres/SQLite connectivity check for /ready probes."""
    settings = get_settings()
    if not settings.uses_sql:
        return {"status": "disabled"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def _ensure_schema(engine, url: str) -> None:
    _schema = get_settings().db_schema
    if _schema and not url.startswith("sqlite"):
        with engine.begin() as c:
            c.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{_schema}"')
