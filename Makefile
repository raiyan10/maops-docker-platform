SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c

PYTHON ?= python3
VERSION := $(shell cat VERSION)
IMAGE := maops-docker-platform:$(VERSION)

# Deterministic-build strategy (Day 4, see docs/build-security.md):
# SOURCE_DATE_EPOCH is the current commit's own timestamp - fixed per
# commit, never the current wall clock - used only to normalize otherwise
# real-time-varying file/layer timestamps via BuildKit's
# `rewrite-timestamp=true` exporter option. Falls back to a fixed
# sentinel (0) outside a git repository, never to `date +%s`.
SOURCE_DATE_EPOCH := $(shell git log -1 --format=%ct 2>/dev/null || echo 0)
BUILD_TAR := .cache/build/maops-docker-platform-$(VERSION).tar

# Exported so every `docker compose` invocation (directly, or via the
# Python scripts below through subprocess, which inherit the parent
# environment) resolves compose.yaml's ${VERSION:-...} interpolation to
# the real, current VERSION rather than its fallback default.
export VERSION

.PHONY: help test lint dockerfile-check compose-check workflow-check quality \
	build inspect image-audit smoke security-check compose-test \
	reliability-check reproducibility-check sbom sbom-check vuln-scan \
	supply-chain-check release-check clean

help:
	@echo "Available targets:"
	@echo "  help                 Show this help message"
	@echo "  test                 Run the unittest suite"
	@echo "  lint                 Run the project-specific source validator (app/, gateway/, state/)"
	@echo "  dockerfile-check     Run the project-specific Dockerfile validator"
	@echo "  compose-check        Run the project-specific Compose structural validator"
	@echo "  workflow-check       Run the project-specific GitHub Actions workflow policy validator"
	@echo "  quality              test + lint + dockerfile-check + compose-check + workflow-check"
	@echo ""
	@echo "  build                Deterministic BuildKit build (slim builder -> Distroless runtime), tagged $(IMAGE)"
	@echo "  inspect              Print image inspect/ls/history for $(IMAGE)"
	@echo "  image-audit          Project-specific release-image policy audit (incl. Distroless shell/pip absence)"
	@echo "  smoke                Real-image container smoke test (single-role + multi-role chain)"
	@echo "  security-check       Hardened-runtime security verification"
	@echo "  compose-test         Real Compose stack integration test"
	@echo "  reliability-check    Real Docker resource/restart/timeout-hierarchy/failure-recovery proof"
	@echo "  reproducibility-check  Independent two-build image-identity reproducibility proof"
	@echo ""
	@echo "  sbom                 Generate SPDX JSON SBOM for $(IMAGE) via pinned Syft"
	@echo "  sbom-check           Validate the generated SBOM"
	@echo "  vuln-scan            Generate a Trivy JSON report + enforce vulnerability policy"
	@echo "  supply-chain-check   sbom + sbom-check + vuln-scan"
	@echo ""
	@echo "  release-check        quality + build + inspect + image-audit + smoke +"
	@echo "                       security-check + compose-test + reliability-check +"
	@echo "                       reproducibility-check + supply-chain-check"
	@echo "  clean                Remove known project-owned generated resources"
	@echo ""
	@echo "Image tag is derived from VERSION: $(IMAGE)"

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

lint:
	$(PYTHON) scripts/lint/check_source.py

dockerfile-check:
	$(PYTHON) scripts/lint/check_dockerfile.py

compose-check:
	$(PYTHON) scripts/compose/check_compose.py

workflow-check:
	$(PYTHON) scripts/ci/check_workflows.py

quality: test lint dockerfile-check compose-check workflow-check

build:
	@mkdir -p $(dir $(BUILD_TAR))
	docker buildx build --no-cache \
		--build-arg VERSION=$(VERSION) \
		--build-arg SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) \
		--output type=docker,rewrite-timestamp=true,name=$(IMAGE),dest=$(BUILD_TAR) \
		-f docker/app/Dockerfile .
	docker load -i $(BUILD_TAR)
	rm -f $(BUILD_TAR)

inspect:
	@echo "=== docker image inspect $(IMAGE) ==="
	docker image inspect $(IMAGE)
	@echo "=== docker image ls $(IMAGE) ==="
	docker image ls $(IMAGE)
	@echo "=== docker history $(IMAGE) ==="
	docker history $(IMAGE)

image-audit:
	$(PYTHON) scripts/build/image_audit.py

smoke:
	$(PYTHON) scripts/smoke/container_smoke.py

security-check:
	$(PYTHON) scripts/verify/security_check.py

compose-test:
	$(PYTHON) scripts/compose/compose_integration.py

reliability-check:
	$(PYTHON) scripts/reliability/reliability_check.py

reproducibility-check:
	$(PYTHON) scripts/build/reproducibility_check.py

sbom:
	$(PYTHON) scripts/security/generate_sbom.py

sbom-check:
	$(PYTHON) scripts/security/check_sbom.py

vuln-scan:
	$(PYTHON) scripts/security/vuln_scan.py

supply-chain-check: sbom sbom-check vuln-scan
	@echo "supply-chain-check: sbom + sbom-check + vuln-scan all passed"

# The single authoritative local release-policy contract (Day 6): a
# developer running this locally, and GitHub Actions' release-policy job
# (.github/workflows/ci.yml), exercise the identical target - CI
# orchestrates this Makefile rather than hand-listing the same gate list a
# second time. Depends on supply-chain-check (not sbom/sbom-check/vuln-scan
# individually) so the SBOM/vulnerability policy is genuinely part of the
# authoritative chain without duplicating its own three-step definition.
release-check: quality build inspect image-audit smoke security-check compose-test reliability-check reproducibility-check supply-chain-check
	@echo "=== docker compose config ==="
	docker compose config

clean:
	find . -type d -name '__pycache__' -not -path './.git/*' -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	rm -rf .cache
	@echo "removing any leftover maops-smoke-*/maops-security-*/maops-image-audit-* containers (self-cleaning scripts should leave none)"
	@ids="$$(docker ps -aq --filter 'name=^maops-smoke-' --filter 'name=^maops-security-' --filter 'name=^maops-image-audit-')"; \
	if [ -n "$$ids" ]; then docker rm -f $$ids; else echo "none found"; fi
	@echo "removing any leftover maops-smoke-net-* throwaway networks (multi-role smoke's own teardown should leave none)"
	@nets="$$(docker network ls --filter 'name=^maops-smoke-net-' --format '{{.Name}}')"; \
	if [ -n "$$nets" ]; then echo "$$nets" | xargs -r -n1 docker network rm; else echo "none found"; fi
	@echo "removing any leftover maops-repro-* reproducibility-check images/containers (its own teardown should leave none)"
	@rids="$$(docker ps -aq --filter 'name=^maops-repro-')"; \
	if [ -n "$$rids" ]; then docker rm -f $$rids; else echo "none found"; fi
	@rimgs="$$(docker images --filter 'reference=maops-repro-*' --format '{{.Repository}}:{{.Tag}}')"; \
	if [ -n "$$rimgs" ]; then echo "$$rimgs" | xargs -r -n1 docker rmi -f; else echo "none found"; fi
	@echo "removing any leftover maops-compose-* Compose project resources, including their own named volume (compose_integration.py's own teardown should leave none)"
	@projects="$$(docker ps -a --filter 'name=^maops-compose-' --format '{{.Names}}' | sed -E 's/^(maops-compose-[a-f0-9]+)-(app|gateway|state)-1$$/\1/' | sort -u)"; \
	if [ -n "$$projects" ]; then \
		for p in $$projects; do docker compose -p "$$p" -f compose.yaml down -t 5 -v || true; done; \
	else echo "none found"; fi
	@echo "removing any leftover maops-reliability-* Compose project resources, including their own named volume (reliability_check.py's own teardown should leave none)"
	@rprojects="$$(docker ps -a --filter 'name=^maops-reliability-' --format '{{.Names}}' | sed -E 's/^(maops-reliability-[a-f0-9]+)-(app|gateway|state)-1$$/\1/' | sort -u)"; \
	if [ -n "$$rprojects" ]; then \
		for p in $$rprojects; do docker compose -p "$$p" -f compose.yaml down -t 5 -v || true; done; \
	else echo "none found"; fi
