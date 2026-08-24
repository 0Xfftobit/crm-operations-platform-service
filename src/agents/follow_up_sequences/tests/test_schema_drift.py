"""Additive schema-drift repair (BUG-04: enrollments.sequence_id missing on live Neon)."""
from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine
from sqlalchemy.dialects import postgresql

from app.db import schema_drift
from app.db.schema_drift import ensure_columns


def _meta() -> MetaData:
    md = MetaData()
    Table(
        "enrollments", md,
        Column("enrollment_id", String, primary_key=True),
        Column("sequence_id", String),
        Column("state", String, default="active"),
        Column("attempts", Integer, default=0),
    )
    Table("brand_new", md, Column("id", String, primary_key=True))
    return md


def test_noop_on_sqlite():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        assert ensure_columns(conn, _meta()) == []


class _FakeInspector:
    def __init__(self, tables: dict[str, set[str]]):
        self._tables = tables

    def has_table(self, name: str) -> bool:
        return name in self._tables

    def get_columns(self, name: str):
        return [{"name": c} for c in self._tables[name]]


class _FakeConn:
    dialect = postgresql.dialect()

    def __init__(self):
        self.statements: list[str] = []

    def exec_driver_sql(self, sql: str):
        self.statements.append(sql)


def test_adds_only_missing_columns_on_postgres(monkeypatch):
    conn = _FakeConn()
    # live table predates sequence_id/state/attempts; brand_new table does not exist yet
    monkeypatch.setattr(
        schema_drift, "inspect",
        lambda _c: _FakeInspector({"enrollments": {"enrollment_id"}}),
    )
    added = ensure_columns(conn, _meta())  # type: ignore[arg-type]
    assert added == ["enrollments.sequence_id", "enrollments.state", "enrollments.attempts"]
    joined = "\n".join(conn.statements)
    assert 'ALTER TABLE "enrollments" ADD COLUMN IF NOT EXISTS "sequence_id" VARCHAR' in joined
    assert '"state" VARCHAR DEFAULT \'active\'' in joined
    assert '"attempts" INTEGER DEFAULT 0' in joined
    # tables that don't exist are left to create_all; existing columns untouched
    assert "brand_new" not in joined and '"enrollment_id"' not in joined


def test_nothing_to_do_when_live_matches_orm(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(
        schema_drift, "inspect",
        lambda _c: _FakeInspector({"enrollments": {"enrollment_id", "sequence_id", "state", "attempts"}}),
    )
    assert ensure_columns(conn, _meta()) == []  # type: ignore[arg-type]
    assert conn.statements == []
