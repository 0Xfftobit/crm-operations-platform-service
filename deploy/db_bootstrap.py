"""Neon/Postgres schema-per-agent bootstrap (operations-platform).

Only DB-capable agents get a dedicated schema on the shared Neon database; the rest stay in-memory.
Given a base DATABASE_URL this creates each durable agent's schema and prints shell `export`s giving
that agent a schema-scoped, driver-normalized URL. Schema names are globally unique (agent names),
so nothing collides with the other CRM services sharing the same Neon DB. Pure psycopg — no agent
imports. Empty base URL → memory-mode exports so supervisord's %(ENV_*)s always resolve.
"""
import sys

# durable agent (has SQL tables) → (schema, sqlalchemy driver). Others run in-memory.
DURABLE = {
    "compliance_monitor": ("compliance_monitor", "psycopg"),
}


def _normalize(url: str, driver: str) -> str:
    for prefix in ("postgresql+psycopg://", "postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return f"postgresql+{driver}://" + url[len(prefix):]
    return url


def _with_schema(url: str, schema: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options=-c%20search_path%3D{schema}"


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not base:
        for agent in DURABLE:
            print(f'export {agent.upper()}_STORAGE="memory" {agent.upper()}_URL=""')
        return

    conninfo = base
    for pfx in ("postgresql+psycopg://", "postgresql+asyncpg://"):
        conninfo = conninfo.replace(pfx, "postgresql://")

    import psycopg

    with psycopg.connect(conninfo, autocommit=True) as conn:
        for _agent, (schema, _drv) in DURABLE.items():
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    for agent, (schema, driver) in DURABLE.items():
        url = _with_schema(_normalize(base, driver), schema)
        print(f'export {agent.upper()}_STORAGE="postgres" {agent.upper()}_URL="{url}"')


if __name__ == "__main__":
    main()
