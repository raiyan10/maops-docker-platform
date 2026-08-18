SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

PYTHON ?= python3
VERSION := $(shell cat VERSION)
IMAGE := maops-docker-platform:$(VERSION)

.PHONY: help test lint dockerfile-check build inspect smoke security-check quality release-check clean

help:
	@echo "Available targets:"
	@echo "  help            Show this help message"
	@echo "  test            Run the unittest suite"
	@echo "  lint            Run the project-specific source validator (app/)"
	@echo "  dockerfile-check  Run the project-specific Dockerfile validator"
	@echo "  build           Build the Docker image, tagged $(IMAGE)"
	@echo "  inspect         Print image inspect/ls/history for $(IMAGE)"
	@echo "  smoke           Run the real-image container smoke test"
	@echo "  security-check  Run the hardened-runtime security verification"
	@echo "  quality         test + lint + dockerfile-check"
	@echo "  release-check   quality + build + inspect + smoke + security-check + compose config"
	@echo "  clean           Remove known project-owned generated resources"
	@echo ""
	@echo "Image tag is derived from VERSION: $(IMAGE)"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

lint:
	$(PYTHON) scripts/lint/check_source.py

dockerfile-check:
	$(PYTHON) scripts/lint/check_dockerfile.py

build:
	docker build --no-cache -f docker/app/Dockerfile -t $(IMAGE) .

inspect:
	@echo "=== docker image inspect $(IMAGE) ==="
	docker image inspect $(IMAGE)
	@echo "=== docker image ls $(IMAGE) ==="
	docker image ls $(IMAGE)
	@echo "=== docker history $(IMAGE) ==="
	docker history $(IMAGE)

smoke:
	$(PYTHON) scripts/smoke/container_smoke.py

security-check:
	$(PYTHON) scripts/verify/security_check.py

quality: test lint dockerfile-check

release-check: quality build inspect smoke security-check
	@echo "=== docker compose config ==="
	docker compose config

clean:
	find . -type d -name '__pycache__' -not -path './.git/*' -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "removing any leftover maops-smoke-*/maops-security-* containers (self-cleaning scripts should leave none)"
	@ids="$$(docker ps -aq --filter 'name=^maops-smoke-' --filter 'name=^maops-security-')"; \
	if [ -n "$$ids" ]; then docker rm -f $$ids; else echo "none found"; fi
