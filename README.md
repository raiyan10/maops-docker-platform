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

## Day 1 + Day 2 functionality

- `app`: `GET /`, `/healthz`, `/readyz`, `/info` — deterministic JSON
  endpoints, `application/json` content type, HEAD support, controlled
  404 and unsupported-method (405) responses, no traceback or environment
  disclosure. Unchanged from Day 1 except it is no longer host-published.
- `gateway` (new, Day 2): `GET /`, `/healthz` (gateway-process liveness
  only), `/readyz` (a real, bounded HTTP check against `app`), and
  `/upstream/info` (a real, bounded HTTP call to `app:8080/info`,
  returned wrapped) — the only host-published service, on `127.0.0.1`
  only. No arbitrary-URL proxying: the upstream is fixed at startup, not
  derived from any incoming request. See
  [docs/compose-platform.md](docs/compose-platform.md).
- A digest-pinned, non-root (`10001:10001`) Dockerfile building one image
  that runs either role (`python3 -m app` or `python3 -m gateway`)
  directly as PID 1 in exec form, with a per-role stdlib-only
  `HEALTHCHECK`.
- A two-service `compose.yaml` (`app`, `gateway`) — `app` not
  host-published, `gateway` the sole host-published service on
  `127.0.0.1` — both with `read_only: true`, `cap_drop: [ALL]`,
  `security_opt: [no-new-privileges:true]`, and `depends_on: app:
  condition: service_healthy`.
- A recursively-correct `.dockerignore`, proven (not just asserted) to
  reject nested `__pycache__`/`.pyc` content.
- `unittest`-based tests (including gateway coverage), project-specific
  source/Dockerfile/Compose validators, a real-image smoke test, a
  real Compose-stack integration test, and a runtime security verifier
  that distinguishes source configuration, Docker-runtime inspection,
  and kernel/process-level proof, including an automated `docker
  stop`/SIGTERM lifecycle check (see
  [docs/security.md](docs/security.md)).

See [docs/roadmap.md](docs/roadmap.md) for the full seven-day arc — only
Days 1-2 are implemented; everything else is explicitly planned, not
built.

## Prerequisites

- Docker Engine with Compose v2 (`docker compose`), working without
  `sudo`.
- Python 3.11+ (for running tests/scripts locally; the container itself
  only needs Docker).
- GNU Make (optional — every `make` target is a thin wrapper around a
  `python3`/`docker` command you can also run directly).

## Build / test / run

```bash
make test             # unittest suite (app/ + gateway/)
make lint              # project-specific source validator (app/, gateway/)
make dockerfile-check    # project-specific Dockerfile validator
make compose-check         # project-specific Compose structural validator
make quality                 # test + lint + dockerfile-check + compose-check

make build                     # docker build, tagged maops-docker-platform:<VERSION>
make inspect                     # docker image inspect / ls / history
make smoke                         # real-image container smoke test (app role)
make security-check                  # hardened-runtime security verification
make compose-test                      # real Compose-stack integration test
make release-check                       # quality + build + inspect + smoke + security-check + compose-test

docker compose up -d                       # run the stack locally
curl http://localhost:8080/readyz            # via the gateway (loopback-only;
                                               #  neither service has curl/wget)
curl http://localhost:8080/upstream/info
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
app/                     # stdlib-only Python HTTP workload (Day 1 backend)
gateway/                 # stdlib-only Python gateway (Day 2, sole host-facing service)
docker/app/Dockerfile    # hardened, non-root, digest-pinned image, both roles
compose.yaml             # two-service hardened Compose stack (app, gateway)
tests/                   # unittest suite (app/ + gateway/)
scripts/lint/            # project-specific source + Dockerfile validators
scripts/compose/         # project-specific Compose structural + integration checks
scripts/smoke/           # real-image container smoke test
scripts/verify/          # runtime security verification
docs/                    # architecture, security, compose platform, roadmap
.claude/                 # project agents, skills, and guidance
VERSION                  # single authoritative version source
```

## Current version

`0.2.0` (see `VERSION`) — Day 2 of 7.

## Seven-day roadmap (high level)

| Day | Theme |
|---|---|
| 1 | Secure container foundation |
| 2 | Compose multi-service topology *(this release)* |
| 3 | Networking, configuration, volumes, persistence |
| 4 | Build/image security and reproducibility |
| 5 | Health, reliability, resources, observability |
| 6 | CI/CD, integration, release engineering |
| 7 | Hardening, reviews, showcase -> v1.0.0 |

Full detail: [docs/roadmap.md](docs/roadmap.md).

## License

MIT — see [LICENSE](LICENSE).
