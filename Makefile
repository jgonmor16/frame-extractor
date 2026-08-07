# Development commands. CI calls the same targets, so the workflow and
# local development cannot drift apart.

.PHONY: help install test lint format typecheck check clean

# Keep this first so a bare `make` prints the available targets.
help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-9s %s\n", $$1, $$2}'

install:  ## Install the package with its development dependencies
	pip install -e ".[dev]"

test:  ## Run the test suite
	pytest -v

lint:  ## Report lint and formatting problems without changing anything
	ruff check .
	ruff format --check .

format:  ## Apply ruff's fixes and formatting
	ruff check --fix .
	ruff format .

typecheck:  ## Run the type checker
	mypy

check: lint typecheck test  ## Everything CI runs

clean:  ## Remove build artefacts and tool caches
	rm -rf build dist src/*.egg-info
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
