.PHONY: help install dev test lint format type-check build clean \
	openapi verify-openapi sdk sdk-js sdk-py postman cli cli-smoke all \
	docker-build docker-smoke docker-run verify verify-generated action-test \
	release-patch release-minor release-major

help:
	@echo "ReliAPI Makefile"
	@echo ""
	@echo "Development:"
	@echo "  make install        Install dev dependencies"
	@echo "  make dev            Run the FastAPI app locally"
	@echo "  make test           Run the test suite"
	@echo "  make lint           Run a gating lint pass"
	@echo "  make format         Format Python sources with black"
	@echo "  make type-check     Run mypy on the maintained surfaces"
	@echo "  make build          Build the root Python distribution"
	@echo ""
	@echo "Artifacts:"
	@echo "  make openapi        Generate OpenAPI from the FastAPI app"
	@echo "  make verify-openapi Validate the OpenAPI schema"
	@echo "  make sdk-js         Generate the JavaScript SDK"
	@echo "  make sdk-py         Generate the Python SDK"
	@echo "  make sdk            Generate both SDKs"
	@echo "  make postman        Generate the Postman collection"
	@echo "  make all            Regenerate OpenAPI, SDKs, and Postman"
	@echo ""
	@echo "Release:"
	@echo "  make cli            Install the CLI package locally"
	@echo "  make cli-smoke      Check the CLI entrypoint"
	@echo "  make verify         Run practical pre-release checks"
	@echo "  make release-patch  Bump patch version and prepare release metadata"
	@echo "  make release-minor  Bump minor version and prepare release metadata"
	@echo "  make release-major  Bump major version and prepare release metadata"

DOCKER_IMAGE := $(shell python3 -c "import yaml; print(yaml.safe_load(open('product.yaml'))['docker']['image'])" 2>/dev/null || echo "kikudoc/reliapi")
DOCKERFILE := $(shell python3 -c "import yaml; print(yaml.safe_load(open('product.yaml'))['docker']['dockerfile'])" 2>/dev/null || echo "Dockerfile")
VERSION := $(shell python3 -c "import yaml; print(yaml.safe_load(open('product.yaml'))['product']['version'])" 2>/dev/null || echo "1.1.0")

install:
	pip install -r requirements-dev.txt
	@if command -v npm >/dev/null 2>&1; then \
		cd action && npm install 2>/dev/null || true; \
	else \
		echo "npm not found, skipping action dependencies"; \
	fi

dev:
	RELIAPI_STRICT_CONFIG=false uvicorn reliapi.app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	RELIAPI_STRICT_CONFIG=false pytest tests/ -v --tb=short

lint:
	ruff check reliapi cli scripts tests --select E9,F63,F7,F82

format:
	black reliapi cli scripts tests

type-check:
	mypy reliapi cli --ignore-missing-imports

build:
	python3 -m build

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist htmlcov .coverage
	rm -rf sdk/js/dist sdk/js/src sdk/js/node_modules
	rm -rf sdk/python/build sdk/python/dist sdk/python/reliapi_sdk

openapi:
	python3 scripts/generate_openapi.py

verify-openapi: openapi
	python3 -c "from openapi_spec_validator import validate_spec; import yaml; validate_spec(yaml.safe_load(open('openapi/openapi.yaml')))"
	@echo "OpenAPI spec is valid"

sdk: sdk-js sdk-py

sdk-js:
	bash scripts/generate_sdk.sh js

sdk-py:
	bash scripts/generate_sdk.sh python

postman:
	bash scripts/generate_postman.sh

cli:
	pip install -e ./cli

cli-smoke:
	python3 -m cli.main --help >/dev/null
	@echo "CLI smoke test passed"

all: openapi sdk postman
	@echo "All artifacts generated successfully"

docker-build:
	docker build -f $(DOCKERFILE) -t $(DOCKER_IMAGE):$(VERSION) -t $(DOCKER_IMAGE):latest .

docker-smoke: docker-build
	@echo "Starting Redis for Docker smoke test..."
	@docker run -d --name reliapi-redis-smoke -p 6379:6379 redis:7-alpine >/dev/null 2>&1 || true
	@sleep 2
	@CONTAINER_ID=$$(docker run -d -p 8000:8000 \
		-e REDIS_URL=redis://host.docker.internal:6379/0 \
		-e RELIAPI_STRICT_CONFIG=false \
		-e RELIAPI_CONFIG_PATH=/app/config.yaml \
		--add-host=host.docker.internal:host-gateway \
		$(DOCKER_IMAGE):$(VERSION)); \
	sleep 8; \
	curl -sf http://localhost:8000/healthz >/dev/null; \
	docker stop $$CONTAINER_ID >/dev/null; \
	docker rm $$CONTAINER_ID >/dev/null; \
	docker stop reliapi-redis-smoke >/dev/null 2>&1 || true; \
	docker rm reliapi-redis-smoke >/dev/null 2>&1 || true; \
	echo "Docker smoke test passed"

docker-run:
	docker run -p 8000:8000 \
		-e REDIS_URL=redis://host.docker.internal:6379/0 \
		-e RELIAPI_STRICT_CONFIG=false \
		-e RELIAPI_CONFIG_PATH=/app/config.yaml \
		--add-host=host.docker.internal:host-gateway \
		$(DOCKER_IMAGE):$(VERSION)

verify: lint verify-openapi build
	@echo "Running targeted regression tests..."
	RELIAPI_STRICT_CONFIG=false pytest tests/test_free_tier_restrictions.py tests/test_routes_business.py tests/test_llm_proxy.py -q
	@echo "Running tooling smoke tests..."
	bash scripts/smoke_test.sh action

verify-generated:
	@test -f openapi/openapi.yaml
	@test -f postman/collection.json
	@echo "Generated artifact checks passed"

action-test:
	@if [ -f action/package.json ]; then \
		cd action && npm test 2>/dev/null || echo "No action tests defined"; \
	else \
		echo "Action not set up"; \
	fi

release-patch:
	python3 scripts/bump_version.py patch

release-minor:
	python3 scripts/bump_version.py minor

release-major:
	python3 scripts/bump_version.py major
