"""Additive schema-drift repair for the shared Neon database.

``Base.metadata.create_all`` only creates *missing tables*; it never adds
columns to tables that already exist. On the shared-Neon deployment each
engine's tables were created by whichever image ran first, so a newer ORM
that adds a column (e.g. ``enrollments.sequence_id``) crashes every query
with ``UndefinedColumn`` until someone runs a migration by hand.

``ensure_columns`` closes that gap at startup: for every mapped table that
exists, any ORM column absent from the live table is added with
``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` (nullable, carrying the ORM's
scalar default when it has one). It is additive only — nothing is dropped,
renamed or retyped — and a no-op on SQLite, where the test suite recreates
schemas from scratch anyway.
"""
from __future__ import annotations

import decimal
import logging

from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _sql_literal(value: object) -> str | None:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def ensure_columns(conn: Connection, metadata: MetaData) -> list[str]:
    """Add ORM columns missing from existing live tables. Returns ``table.column`` names added."""
    if conn.dialect.name != "postgresql":
        return []
    insp = inspect(conn)
    added: list[str] = []
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue  # create_all owns brand-new tables
        live = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in live:
                continue
            ddl_type = col.type.compile(dialect=conn.dialect)
            default_sql = ""
            default = getattr(col, "default", None)
            if default is not None and getattr(default, "is_scalar", False):
                lit = _sql_literal(default.arg)
                if lit is not None:
                    default_sql = f" DEFAULT {lit}"
            conn.exec_driver_sql(
                f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                f'"{col.name}" {ddl_type}{default_sql}'
            )
            added.append(f"{table.name}.{col.name}")
    if added:
        logger.warning("schema_drift_repaired columns=%s", added)
    return added
