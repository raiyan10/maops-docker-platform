# MAOps Docker Platform

A secure, minimal Docker/Compose platform foundation — Project 3 of the
MAOps DevOps portfolio, built as seven scoped daily releases starting
from v0.1.0.

## What this is

This repository is intentionally **Docker/platform-focused**. The small
Python stdlib HTTP application in `app/` exists only as a deterministic
workload through which real Docker/container engineering practices
(image hardening, non-root execution, capability dropping, read-only
filesystems, Compose platform design, recursive build-context
verification, kernel-level security proof) are demonstrated. The
application is intentionally tiny — a few JSON endpoints — so that
essentially all of the engineering effort and all of the review surface
is the container layer, not application logic.

## Day 1 functionality

- `GET /`, `/healthz`, `/readyz`, `/info` — deterministic JSON endpoints,
  `application/json` content type, HEAD support, controlled 404 and
  unsupported-method (405) responses, no traceback or environment
  disclosure.
- A digest-pinned, non-root (`10001:10001`), single-stage Dockerfile with
  the application running directly as PID 1 and a stdlib-only
  `HEALTHCHECK`.
- A one-service `compose.yaml` with `read_only: true`, `cap_drop:
  [ALL]`, and `security_opt: [no-new-privileges:true]`.
- A recursively-correct `.dockerignore`, proven (not just asserted) to
  reject nested `__pycache__`/`.pyc` content.
- `unittest`-based tests, project-specific source/Dockerfile validators,
  a real-image smoke test, and a runtime security verifier that
  distinguishes source configuration, Docker-runtime inspection, and
  kernel/process-level proof (see [docs/security.md](docs/security.md)).

See [docs/roadmap.md](docs/roadmap.md) for the full seven-day arc — only
Day 1 is implemented; everything else is explicitly planned, not built.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`), working without
  `sudo`.
- Python 3.11+ (for running tests/scripts locally; the container itself
  only needs Docker).
- GNU Make (optional — every `make` target is a thin wrapper around a
  `python3`/`docker` command you can also run directly).

## Build / test / run

```bash
make test             # unittest suite
make lint              # project-specific source validator (app/)
make dockerfile-check   # project-specific Dockerfile validator
make quality              # test + lint + dockerfile-check

make build                  # docker build, tagged maops-docker-platform:<VERSION>
make inspect                  # docker image inspect / ls / history
make smoke                      # real-image container smoke test
make security-check               # hardened-runtime security verification
make release-check                  # quality + build + inspect + smoke + security-check + compose config

docker compose up -d                  # run the service locally
curl http://localhost:8080/healthz      # (or any stdlib HTTP client - the
                                          #  app itself has no curl/wget)
docker compose down
```

Run `make help` for the full target list.

## Security posture

Every runtime security claim in this repository is backed by evidence
labeled by what kind of proof it actually is — desired/source
configuration, Docker-runtime inspection, or kernel/process-level
verification — never a config-only claim presented as proof of
enforcement. See [docs/security.md](docs/security.md) for the complete,
dated verification record (non-root execution, capabilities,
no-new-privileges, read-only rootfs, namespaces, Docker socket exposure,
image content leakage, secrets, healthcheck) and
[docs/architecture.md](docs/architecture.md) for the application/
container boundary and PID 1 process model.

## Repository structure

```
app/                    # stdlib-only Python HTTP workload
docker/app/Dockerfile   # hardened, non-root, digest-pinned image
compose.yaml            # one-service hardened Compose baseline
tests/                  # unittest suite
scripts/lint/           # project-specific source + Dockerfile validators
scripts/smoke/          # real-image container smoke test
scripts/verify/         # runtime security verification
docs/                   # architecture, security, roadmap
.claude/                # project agents, skills, and guidance
VERSION                 # single authoritative version source
```

## Current version

`0.1.0` (see `VERSION`) — Day 1 of 7.

## Seven-day roadmap (high level)

| Day | Theme |
|---|---|
| 1 | Secure container foundation *(this release)* |
| 2 | Compose multi-service topology |
| 3 | Networking, configuration, volumes, persistence |
| 4 | Build/image security and reproducibility |
| 5 | Health, reliability, resources, observability |
| 6 | CI/CD, integration, release engineering |
| 7 | Hardening, reviews, showcase -> v1.0.0 |

Full detail: [docs/roadmap.md](docs/roadmap.md).

## License

MIT — see [LICENSE](LICENSE).
