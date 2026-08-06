# syntax=docker/dockerfile:1
#
# Multi-stage build (CREquity engine convention): uv resolves + installs into a self-contained
# .venv in the builder; the slim runtime just copies it. Runs as a non-root user, exposes the
# ECS health check on /api/v1/health.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# 1) deps only (cached until pyproject/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) the app + install the project
COPY app ./app
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production \
    PORT=3000 \
    WEB_CONCURRENCY=2 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /app app

COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/api/v1/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --log-level $(printf '%s' \"${LOG_LEVEL:-info}\" | tr '[:upper:]' '[:lower:]') --no-access-log"]
