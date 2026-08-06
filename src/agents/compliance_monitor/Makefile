.PHONY: install test lint fmt run docker-build compose-up compose-down

install:            ## create .venv and install deps (incl. dev)
	uv sync

test:               ## run the test suite
	uv run pytest -q

lint:               ## ruff lint (autofix)
	uv run ruff check --fix app tests

fmt:                ## ruff format
	uv run ruff format app tests

run:                ## run the API locally (reload)
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8004 --reload

docker-build:       ## build the production image
	docker build -t crequity-compliance-monitoring:latest .

compose-up:         ## local stack (api + postgres + redis)
	docker compose up --build

compose-down:
	docker compose down
