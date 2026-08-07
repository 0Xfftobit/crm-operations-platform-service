# syntax=docker/dockerfile:1
#
# Feedback Loop System — FastAPI service with uv-based packaging.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY README.md ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /app app

COPY --from=builder --chown=app:app /app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --log-level $(printf '%s' \"${LOG_LEVEL:-info}\" | tr '[:upper:]' '[:lower:]') --no-access-log"]
