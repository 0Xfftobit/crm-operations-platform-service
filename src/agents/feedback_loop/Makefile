.PHONY: install test lint fmt run docker-build

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check --fix app tests

fmt:
	uv run ruff format app tests

run:
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8009 --reload

docker-build:
	docker build -t crequity-feedback-loop:latest .
