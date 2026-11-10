.PHONY: setup dev test lint fmt demo-a demo-b demo-c
setup:
	cd backend && uv sync
	@test -f frontend/package.json && cd frontend && npm install || true
dev:
	cd backend && uv run uvicorn relay.main:app --reload --port 8000
test:
	cd backend && uv run pytest -q
lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .
demo-a demo-b demo-c:
	curl -X POST localhost:8000/scenarios/$(subst demo-,,$@)/run
