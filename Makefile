.PHONY: setup dev test lint fmt sync-pronunciations demo-a demo-b demo-c
setup:
	cd backend && uv sync
	cd frontend && npm install
dev:
	(cd backend && uv run uvicorn relay.main:app --reload --port 8000) & (cd frontend && npm run dev) & wait
test:
	cd backend && uv run pytest -q
lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright
	cd frontend && npm run typecheck
fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .
sync-pronunciations:
	cd backend && uv run python scripts/sync_pronunciations.py
demo-a demo-b demo-c:
	curl -X POST localhost:8000/scenarios/$(subst demo-,,$@)/run
