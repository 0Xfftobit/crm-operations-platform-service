"""Durable SQL persistence (SQLAlchemy) — engaged when STORAGE_GATEWAY=postgres + DATABASE_URL.

Dialect comes from the URL, so the same ORM runs on Postgres (prod) and SQLite (tests/dev). The
in-memory repositories remain the default; nothing here loads unless ``settings.uses_sql`` is true.
"""
