.PHONY: sync test lint format typecheck qa

sync:
	@uv sync

test:
	@uv run pytest tests/ --cov=nanodirt --cov-report term-missing:skip-covered

lint:
	@uv run ruff check --fix src/ tests/

format:
	@uv run ruff format src/ tests/

typecheck:
	@uv run mypy --strict src/

qa: lint test typecheck format
