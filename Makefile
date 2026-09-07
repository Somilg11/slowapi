# Every target is safe to run from a clean checkout.
.DEFAULT_GOAL := help
PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

.PHONY: install
install: $(BIN)/python ## Install the package and dev tooling
	$(BIN)/pip install -e ".[dev,all]"
	@echo "Done. Activate with: source $(VENV)/bin/activate"

.PHONY: test
test: ## Run the test suite on both protocols
	$(BIN)/pytest

.PHONY: cov
cov: ## Run tests with a coverage report
	$(BIN)/pytest --cov --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

.PHONY: lint
lint: ## Lint without modifying files
	$(BIN)/ruff check src tests examples benchmarks scripts
	$(BIN)/ruff format --check src tests
	$(BIN)/python scripts/check_docs_links.py

.PHONY: format
format: ## Auto-format and auto-fix
	$(BIN)/ruff format src tests examples benchmarks scripts
	$(BIN)/ruff check --fix src tests examples benchmarks scripts

.PHONY: typecheck
typecheck: ## Static type check
	$(BIN)/mypy

.PHONY: validate
validate: ## Analyse every example's routes without starting a server
	@for example in examples/*/; do \
		if [ -f "$$example/main.py" ]; then \
			(cd "$$example" && ../../$(BIN)/python -m slowfw check main:app) || exit 1; \
		fi; \
	done

.PHONY: check
check: lint typecheck validate test ## Everything CI runs

.PHONY: bench
bench: ## Compare the WSGI and ASGI paths
	$(BIN)/python benchmarks/run.py

.PHONY: bench-vs
bench-vs: ## Compare against FastAPI on identical handlers
	$(BIN)/pip install --quiet fastapi
	$(BIN)/python benchmarks/compare.py

.PHONY: docs
docs: ## Build the documentation site (warnings are errors)
	$(BIN)/pip install --quiet -e ".[docs]"
	$(BIN)/mkdocs build --strict
	@echo "Built: site/index.html"

.PHONY: docs-serve
docs-serve: ## Serve the documentation site with live reload
	$(BIN)/pip install --quiet -e ".[docs]"
	$(BIN)/mkdocs serve

.PHONY: openapi
openapi: ## Regenerate the OpenAPI sample used in the docs
	$(BIN)/python -m slowfw openapi examples/rest-api/main:app -o docs/reference/openapi-sample.json

.PHONY: build
build: ## Build the sdist and wheel
	$(BIN)/pip install --quiet build
	$(BIN)/python -m build

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf build dist site htmlcov .coverage .pytest_cache .mypy_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.egg-info' -type d -prune -exec rm -rf {} +

.PHONY: docker
docker: ## Build the runtime image
	docker build -t slowapi/example-api:dev .

.PHONY: up
up: ## Start the local stack
	docker compose up --build
