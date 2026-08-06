"""SQLAlchemy engine + session factory, built lazily from ``DATABASE_URL``.

SQLite URLs get a shared static pool so an in-memory/file DB is visible across sessions (tests);
Postgres uses a pre-ping pool. ``reset_engine`` lets the test suite repoint the DB between runs.
"""
from functools import lru_cache

from sqlalchemy import create_engine
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
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache
def _factory() -> sessionmaker:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session, future=True)


def session() -> Session:
    """Open a new session (caller manages the transaction / context)."""
    return _factory()()


def create_all() -> None:
    from app.db.orm import Base
    Base.metadata.create_all(get_engine())


def reset_engine() -> None:
    """Drop cached engine/session factory (tests switching DATABASE_URL)."""
    try:
        get_engine().dispose()
    except Exception:  # noqa: BLE001 — best-effort teardown
        pass
    get_engine.cache_clear()
    _factory.cache_clear()
