"""Postgres/SQLite persistence — ledger, rule registry, workflow runs (§7)."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_sqlite_conn: sqlite3.Connection | None = None
_pg_conn: Any = None

TABLES = ("action_ledger", "rule_registry", "workflow_runs", "task_index")

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS action_ledger (
    ledger_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    deal_id TEXT,
    rule_id TEXT,
    rule_version TEXT,
    inputs_json TEXT,
    inputs_hash TEXT,
    outputs_json TEXT,
    detail_json TEXT,
    actor TEXT,
    outcome TEXT,
    trigger_id TEXT,
    action_key TEXT,
    recorded_at TEXT NOT NULL,
    immutable INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT,
    trigger_event TEXT,
    event_types_json TEXT,
    priority INTEGER DEFAULT 100,
    rollout_status TEXT,
    canary_pct REAL DEFAULT 0,
    created_by TEXT,
    definition_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    trigger_id TEXT,
    topic TEXT,
    lead_id TEXT,
    deal_id TEXT,
    workflow_type TEXT,
    status TEXT,
    latency_ms REAL,
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_index (
    task_id TEXT PRIMARY KEY,
    lead_id TEXT,
    assignee_id TEXT,
    task_type TEXT,
    status TEXT,
    rule_id TEXT,
    sla_due_at TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_lead ON action_ledger(lead_id);
CREATE INDEX IF NOT EXISTS idx_ledger_trigger ON action_ledger(trigger_id);
CREATE INDEX IF NOT EXISTS idx_runs_lead ON workflow_runs(lead_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_idempotency
    ON action_ledger(trigger_id, rule_id, action_key);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS action_ledger (
    ledger_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    lead_id TEXT NOT NULL,
    deal_id TEXT,
    rule_id TEXT,
    rule_version TEXT,
    inputs_json JSONB,
    inputs_hash TEXT,
    outputs_json JSONB,
    detail_json JSONB,
    actor TEXT,
    outcome TEXT NOT NULL DEFAULT 'executed',
    trigger_id TEXT,
    action_key TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    immutable BOOLEAN DEFAULT TRUE,
    CONSTRAINT uq_ledger_idempotency UNIQUE (trigger_id, rule_id, action_key)
);

CREATE TABLE IF NOT EXISTS rule_registry (
    rule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT,
    trigger_event TEXT,
    event_types_json JSONB,
    priority INTEGER DEFAULT 100,
    rollout_status TEXT,
    canary_pct NUMERIC DEFAULT 0,
    created_by TEXT,
    definition_json JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    trigger_id TEXT,
    topic TEXT,
    lead_id TEXT,
    deal_id TEXT,
    workflow_type TEXT,
    status TEXT,
    latency_ms DOUBLE PRECISION,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS task_index (
    task_id TEXT PRIMARY KEY,
    lead_id TEXT,
    assignee_id TEXT,
    task_type TEXT,
    status TEXT,
    rule_id TEXT,
    sla_due_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ledger_lead ON action_ledger(lead_id);
CREATE INDEX IF NOT EXISTS idx_ledger_trigger ON action_ledger(trigger_id);
CREATE INDEX IF NOT EXISTS idx_runs_lead ON workflow_runs(lead_id);
"""


def reset_db() -> None:
    global _sqlite_conn, _pg_conn
    if _sqlite_conn:
        _sqlite_conn.close()
        _sqlite_conn = None
    if _pg_conn:
        _pg_conn.close()
        _pg_conn = None


def _active_backend() -> str:
    return get_settings().storage_backend


def _migrate_schema(conn: Any, *, is_postgres: bool) -> None:
    alters_sqlite = [
        "ALTER TABLE workflow_runs ADD COLUMN deal_id TEXT",
        "ALTER TABLE workflow_runs ADD COLUMN workflow_type TEXT",
        "ALTER TABLE task_index ADD COLUMN sla_due_at TEXT",
    ]
    alters_postgres = [
        "ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS deal_id TEXT",
        "ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS workflow_type TEXT",
        "ALTER TABLE task_index ADD COLUMN IF NOT EXISTS sla_due_at TIMESTAMPTZ",
    ]
    for sql in alters_postgres if is_postgres else alters_sqlite:
        try:
            if is_postgres:
                with conn.cursor() as cur:
                    cur.execute(sql)
            else:
                conn.execute(sql)
        except Exception:
            pass
    if is_postgres:
        conn.commit()
    else:
        conn.commit()


def _row_to_rule(row: Any) -> dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        raw = row["definition_json"]
    else:
        raw = row.get("definition_json")
    if isinstance(raw, dict):
        return raw
    return json.loads(raw or "{}")


def fetch_all_rules() -> list[dict[str, Any]]:
    backend = _active_backend()
    if backend not in ("sqlite", "postgres"):
        return []
    if backend == "postgres":
        conn = _get_postgres_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT definition_json FROM rule_registry ORDER BY updated_at DESC")
            rows = cur.fetchall()
        return [_row_to_rule(r) for r in rows]
    conn = _get_sqlite_conn()
    rows = conn.execute("SELECT definition_json FROM rule_registry ORDER BY updated_at DESC").fetchall()
    return [_row_to_rule(r) for r in rows]


def fetch_rules_for_event(
    trigger_event: str,
    *,
    include_draft: bool = False,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    rules = fetch_all_rules()

    def _matches(r: dict[str, Any]) -> bool:
        event_types = r.get("event_types") or []
        if event_types:
            return trigger_event in event_types
        return r.get("trigger_event") == trigger_event

    rows = [r for r in rules if _matches(r) and r.get("active", True)]
    if not include_draft:
        rows = [r for r in rows if r.get("rollout_status", "active") in ("active", "canary")]
    if tenant_id:
        rows = [r for r in rows if not r.get("tenant_id") or r.get("tenant_id") == tenant_id]
    return rows


def _get_sqlite_conn() -> sqlite3.Connection:
    global _sqlite_conn
    if _sqlite_conn is None:
        path = Path(get_settings().database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _sqlite_conn = sqlite3.connect(str(path), check_same_thread=False)
        _sqlite_conn.row_factory = sqlite3.Row
        _sqlite_conn.executescript(SCHEMA_SQLITE)
        _sqlite_conn.commit()
        _migrate_schema(_sqlite_conn, is_postgres=False)
    return _sqlite_conn


def _get_postgres_conn():
    global _pg_conn
    if _pg_conn is None:
        import psycopg
        from psycopg.rows import dict_row

        url = get_settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL is required when STORAGE_BACKEND=postgres")
        _pg_conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        with _pg_conn.cursor() as cur:
            cur.execute(SCHEMA_POSTGRES)
        _pg_conn.commit()
        _migrate_schema(_pg_conn, is_postgres=True)
    return _pg_conn


def _ledger_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry["ledger_id"],
        entry["action_type"],
        entry["lead_id"],
        entry.get("deal_id") or entry["lead_id"],
        entry.get("rule_id"),
        entry.get("rule_version"),
        json.dumps(entry.get("inputs") or {}),
        entry.get("inputs_hash"),
        json.dumps(entry.get("outputs") or {}),
        json.dumps(entry.get("detail") or {}),
        entry.get("actor", "system"),
        entry.get("outcome", "executed"),
        entry.get("trigger_id"),
        entry.get("action_key"),
        entry.get("recorded_at", datetime.now(UTC).isoformat()),
        entry.get("immutable", True),
    )


def _fetch_existing_ledger(
    *,
    trigger_id: str | None,
    rule_id: str | None,
    action_key: str | None,
    conn: Any,
    is_postgres: bool,
) -> dict[str, Any] | None:
    if not (trigger_id and rule_id and action_key):
        return None
    sql = """
        SELECT ledger_id, outcome, action_type, lead_id, rule_id, trigger_id, action_key, recorded_at
        FROM action_ledger
        WHERE trigger_id = %s AND rule_id = %s AND action_key = %s
        LIMIT 1
    """
    if not is_postgres:
        sql = sql.replace("%s", "?")
    row = conn.execute(sql, (trigger_id, rule_id, action_key)).fetchone()
    if not row:
        return None
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(row)


def persist_ledger_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Persist ledger row. Returns {inserted, duplicate, ledger_id, existing?}."""
    backend = _active_backend()
    if backend not in ("sqlite", "postgres"):
        return {"inserted": False, "duplicate": False, "backend": "memory", "ledger_id": entry["ledger_id"]}

    trigger_id = entry.get("trigger_id")
    rule_id = entry.get("rule_id")
    action_key = entry.get("action_key")
    values = _ledger_row_values(entry)

    try:
        if backend == "postgres":
            conn = _get_postgres_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO action_ledger
                    (ledger_id, action_type, lead_id, deal_id, rule_id, rule_version, inputs_json,
                     inputs_hash, outputs_json, detail_json, actor, outcome, trigger_id, action_key,
                     recorded_at, immutable)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trigger_id, rule_id, action_key) DO NOTHING
                    RETURNING ledger_id
                    """,
                    (
                        values[0], values[1], values[2], values[3], values[4], values[5],
                        values[6], values[7], values[8], values[9], values[10], values[11],
                        values[12], values[13], values[14], values[15],
                    ),
                )
                inserted_row = cur.fetchone()
            conn.commit()
            if inserted_row:
                return {
                    "inserted": True,
                    "duplicate": False,
                    "ledger_id": inserted_row["ledger_id"],
                    "backend": "postgres",
                }
            existing = _fetch_existing_ledger(
                trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, conn=conn, is_postgres=True,
            )
            return {
                "inserted": False,
                "duplicate": True,
                "ledger_id": existing["ledger_id"] if existing else entry["ledger_id"],
                "existing": existing,
                "backend": "postgres",
            }

        conn = _get_sqlite_conn()
        try:
            conn.execute(
                """
                INSERT INTO action_ledger
                (ledger_id, action_type, lead_id, deal_id, rule_id, rule_version, inputs_json, inputs_hash,
                 outputs_json, detail_json, actor, outcome, trigger_id, action_key, recorded_at, immutable)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            conn.commit()
            return {"inserted": True, "duplicate": False, "ledger_id": entry["ledger_id"], "backend": "sqlite"}
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = _fetch_existing_ledger(
                trigger_id=trigger_id, rule_id=rule_id, action_key=action_key, conn=conn, is_postgres=False,
            )
            return {
                "inserted": False,
                "duplicate": True,
                "ledger_id": existing["ledger_id"] if existing else entry["ledger_id"],
                "existing": existing,
                "backend": "sqlite",
            }
    except Exception as exc:
        logger.exception("persist_ledger_failed", extra={"ledger_id": entry.get("ledger_id")})
        return {
            "inserted": False,
            "duplicate": False,
            "error": str(exc),
            "ledger_id": entry["ledger_id"],
            "backend": backend,
        }


def persist_rule(rule: dict[str, Any]) -> None:
    backend = _active_backend()
    if backend not in ("sqlite", "postgres"):
        return

    event_types = rule.get("event_types") or ([rule["trigger_event"]] if rule.get("trigger_event") else [])
    params = (
        rule["rule_id"],
        rule.get("name", ""),
        rule.get("version"),
        rule.get("trigger_event"),
        json.dumps(event_types),
        int(rule.get("priority") or 100),
        rule.get("rollout_status"),
        float(rule.get("canary_percent") or rule.get("canary_pct") or 0),
        rule.get("created_by", "system"),
        json.dumps(rule),
        rule.get("updated_at", datetime.now(UTC).isoformat()),
    )

    if backend == "postgres":
        conn = _get_postgres_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rule_registry
                (rule_id, name, version, trigger_event, event_types_json, priority, rollout_status,
                 canary_pct, created_by, definition_json, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (rule_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    version = EXCLUDED.version,
                    trigger_event = EXCLUDED.trigger_event,
                    event_types_json = EXCLUDED.event_types_json,
                    priority = EXCLUDED.priority,
                    rollout_status = EXCLUDED.rollout_status,
                    canary_pct = EXCLUDED.canary_pct,
                    definition_json = EXCLUDED.definition_json,
                    updated_at = EXCLUDED.updated_at
                """,
                params,
            )
        conn.commit()
        return

    conn = _get_sqlite_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO rule_registry
        (rule_id, name, version, trigger_event, event_types_json, priority, rollout_status,
         canary_pct, created_by, definition_json, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        params,
    )
    conn.commit()


def persist_workflow_run(run: dict[str, Any]) -> None:
    backend = _active_backend()
    if backend not in ("sqlite", "postgres"):
        return

    params = (
        run["run_id"],
        run.get("trigger_id"),
        run.get("topic"),
        run.get("lead_id"),
        run.get("deal_id") or run.get("lead_id"),
        run.get("workflow_type"),
        run.get("status"),
        run.get("latency_ms"),
        run.get("started_at"),
        run.get("completed_at"),
    )

    if backend == "postgres":
        conn = _get_postgres_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workflow_runs
                (run_id, trigger_id, topic, lead_id, deal_id, workflow_type, status, latency_ms, started_at, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    latency_ms = EXCLUDED.latency_ms,
                    completed_at = EXCLUDED.completed_at,
                    deal_id = EXCLUDED.deal_id,
                    workflow_type = EXCLUDED.workflow_type
                """,
                params,
            )
        conn.commit()
        return

    conn = _get_sqlite_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO workflow_runs
        (run_id, trigger_id, topic, lead_id, deal_id, workflow_type, status, latency_ms, started_at, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        params,
    )
    conn.commit()


def persist_task_index(task: dict[str, Any]) -> None:
    backend = _active_backend()
    if backend not in ("sqlite", "postgres"):
        return

    params = (
        task["task_id"],
        task["lead_id"],
        task.get("assignee_id"),
        task.get("task_type"),
        task.get("status"),
        task.get("rule_id"),
        task.get("sla_due_at") or task.get("due_at"),
        task.get("created_at", datetime.now(UTC).isoformat()),
    )

    if backend == "postgres":
        conn = _get_postgres_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO task_index
                (task_id, lead_id, assignee_id, task_type, status, rule_id, sla_due_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (task_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    assignee_id = EXCLUDED.assignee_id,
                    sla_due_at = EXCLUDED.sla_due_at
                """,
                params,
            )
        conn.commit()
        return

    conn = _get_sqlite_conn()
    conn.execute(
        """
        INSERT OR REPLACE INTO task_index
        (task_id, lead_id, assignee_id, task_type, status, rule_id, sla_due_at, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        params,
    )
    conn.commit()


def init_schema() -> dict[str, Any]:
    backend = _active_backend()
    if backend == "postgres":
        try:
            _get_postgres_conn()
            return {"initialized": True, "backend": "postgres", "url": get_settings().database_url.split("@")[-1]}
        except Exception as exc:
            return {"initialized": False, "backend": "postgres", "error": str(exc)}
    if backend == "sqlite":
        _get_sqlite_conn()
        return {"initialized": True, "backend": "sqlite", "path": get_settings().database_path}
    return {"initialized": False, "backend": "memory"}


def store_stats() -> dict[str, Any]:
    backend = _active_backend()
    if backend == "postgres":
        try:
            conn = _get_postgres_conn()
            counts = {}
            with conn.cursor() as cur:
                for table in TABLES:
                    cur.execute(f"SELECT COUNT(*) AS c FROM {table}")
                    counts[table] = cur.fetchone()["c"]
            return {"backend": "postgres", "tables": counts}
        except Exception as exc:
            return {"backend": "postgres", "error": str(exc)}
    if backend == "sqlite":
        conn = _get_sqlite_conn()
        counts = {}
        for table in TABLES:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            counts[table] = row["c"] if row else 0
        return {"backend": "sqlite", "path": get_settings().database_path, "tables": counts}
    return {"backend": "memory", "tables": {}}


def postgres_health() -> dict[str, Any]:
    backend = _active_backend()
    if backend != "postgres":
        return {"enabled": False, "status": "skipped"}
    try:
        conn = _get_postgres_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return {"enabled": True, "status": "ok"}
    except Exception as exc:
        return {"enabled": True, "status": "error", "error": str(exc)}
