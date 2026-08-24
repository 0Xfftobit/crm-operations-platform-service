#!/usr/bin/env bash
# Unified-image entrypoint. If a Neon/Postgres DATABASE_URL is provided, give each DB-capable agent
# its own schema on that one database (durable agents auto-create their tables on startup). With no
# DATABASE_URL, everything runs on in-memory defaults. Then hand off to the process supervisor.
set -euo pipefail

eval "$(python /app/deploy/db_bootstrap.py "${DATABASE_URL:-}")"

if [ -n "${DATABASE_URL:-}" ]; then
  echo "[entrypoint] Postgres mode — schema-per-agent for DB-capable agents."
else
  echo "[entrypoint] No DATABASE_URL — all agents on in-memory storage."
fi

# Don't leak the base DSN to every agent (supervisord children inherit the env). Each DB-capable
# agent's program sets its own schema-scoped DATABASE_URL from the *_URL exports above; the rest
# keep their in-memory/sqlite defaults.
unset DATABASE_URL

exec supervisord -c /etc/supervisord.conf
