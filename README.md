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

## Day 1 + Day 2 + Day 3 functionality

- `app`: `GET /`, `/healthz`, `/readyz`, `/info`, `GET /state`, `POST
  /state/increment` — deterministic JSON endpoints, `application/json`
  content type, HEAD support, controlled 404 and unsupported-method (405)
  responses, no traceback or environment disclosure. `/readyz` is now
  dependency-aware (a real, bounded call to `state`'s own `/readyz`);
  `/state`/`/state/increment` forward to `state`'s identical paths.
- `gateway` (Day 2): `GET /`, `/healthz` (gateway-process liveness only),
  `/readyz` (a real, bounded HTTP check against `app`), `/upstream/info`
  (a real, bounded HTTP call to `app:8080/info`, returned wrapped), and
  (Day 3) `GET /state`/`POST /state/increment` (forwarded to `app`'s
  identical paths) — the only host-published service, on `127.0.0.1`
  only. No arbitrary-URL proxying: every upstream is fixed at startup,
  never derived from any incoming request.
- `state` (new, Day 3): `GET /`, `/healthz`, `/readyz` (a real,
  non-mutating storage-readiness check), `GET /state`, `POST
  /state/increment` — a durably persisted monotonic counter under a named
  Compose volume, with atomic fsync'd writes and strict corrupted-state
  rejection. `app` is the only service allowed to reach it. See
  [docs/compose-platform.md](docs/compose-platform.md) and
  [docs/persistence.md](docs/persistence.md).
- Two explicit Compose networks (`edge`: `gateway`+`app`; `backend`:
  `app`+`state`, `internal: true`) replace Day 2's implicit default —
  `gateway` and `state` share no network at all, proven at runtime via
  real DNS-resolution-failure checks in both directions. See
  [docs/networking.md](docs/networking.md).
- A non-secret, Compose-mounted `config/platform.json` (read-only,
  proven at both [C]/[D] evidence tiers) demonstrates runtime
  configuration outside the image — see
  [docs/configuration.md](docs/configuration.md).
- A digest-pinned, non-root (`10001:10001`) Dockerfile building one image
  that runs any of three roles (`python3 -m app`, `-m gateway`, or `-m
  state`) directly as PID 1 in exec form, with a per-role stdlib-only
  `HEALTHCHECK`, and `/data` pre-created with correct non-root ownership
  so the `state_data` named volume works without running as root.
- A three-service `compose.yaml` (`state` -> `app` -> `gateway`,
  health-gated in that order) — `app`/`state` not host-published,
  `gateway` the sole host-published service on `127.0.0.1` — all three
  with `read_only: true`, `cap_drop: [ALL]`,
  `security_opt: [no-new-privileges:true]`.
- A recursively-correct `.dockerignore`, proven (not just asserted) to
  reject nested `__pycache__`/`.pyc` content.
- `unittest`-based tests (including gateway and state coverage),
  project-specific source/Dockerfile/Compose validators (now including a
  real network/upstream-target cross-check), a real-image smoke test, a
  real Compose-stack integration test (now including a genuine
  timestamp-based startup-ordering proof and real network-isolation/
  persistence proofs), and a runtime security verifier that distinguishes
  source configuration, Docker-runtime inspection, and kernel/
  process-level proof, including an automated `docker stop`/SIGTERM
  lifecycle check (see [docs/security.md](docs/security.md)).

See [docs/roadmap.md](docs/roadmap.md) for the full seven-day arc — only
Days 1-3 are implemented; everything else is explicitly planned, not
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
make test             # unittest suite (app/ + gateway/ + state/)
make lint              # project-specific source validator (app/, gateway/, state/)
make dockerfile-check    # project-specific Dockerfile validator
make compose-check         # project-specific Compose structural validator
make quality                 # test + lint + dockerfile-check + compose-check

make build                     # docker build, tagged maops-docker-platform:<VERSION>
make inspect                     # docker image inspect / ls / history
make smoke                         # real-image container smoke test (app role)
make security-check                  # hardened-runtime security verification
make compose-test                      # real Compose-stack integration test
make release-check                       # quality + build + inspect + smoke + security-check + compose-test

docker compose up -d                       # run the stack locally (state -> app -> gateway)
curl http://localhost:8080/readyz            # via the gateway (loopback-only;
                                               #  no service has curl/wget)
curl http://localhost:8080/state
curl -X POST http://localhost:8080/state/increment
docker compose down                          # volume retained; add -v to remove it too
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
image content leakage, secrets, healthcheck),
[docs/architecture.md](docs/architecture.md) for the application/
container boundary and PID 1 process model,
[docs/networking.md](docs/networking.md) for the network segmentation
proofs, and [docs/persistence.md](docs/persistence.md) for the volume/
read-only-rootfs interaction.

## Repository structure

```
app/                     # stdlib-only Python HTTP workload (Day 1 backend)
gateway/                 # stdlib-only Python gateway (Day 2, sole host-facing service)
state/                   # stdlib-only Python persistence service (Day 3)
config/platform.json     # non-secret, Compose-mounted runtime configuration (Day 3)
docker/app/Dockerfile    # hardened, non-root, digest-pinned image, all three roles
compose.yaml             # three-service hardened Compose stack (state -> app -> gateway)
tests/                   # unittest suite (app/ + gateway/ + state/)
scripts/lint/            # project-specific source + Dockerfile validators
scripts/compose/         # project-specific Compose structural + integration checks
scripts/smoke/           # real-image container smoke test
scripts/verify/          # runtime security verification
docs/                    # architecture, security, networking, configuration,
                         #   persistence, compose platform, roadmap
.claude/                 # project agents, skills, and guidance
VERSION                  # single authoritative version source
```

## Current version

`0.3.0` (see `VERSION`) — Day 3 of 7.

## Seven-day roadmap (high level)

| Day | Theme |
|---|---|
| 1 | Secure container foundation |
| 2 | Compose multi-service topology |
| 3 | Networking, configuration, volumes, persistence *(this release)* |
| 4 | Build/image security and reproducibility |
| 5 | Health, reliability, resources, observability |
| 6 | CI/CD, integration, release engineering |
| 7 | Hardening, reviews, showcase -> v1.0.0 |

Full detail: [docs/roadmap.md](docs/roadmap.md).

## License

MIT — see [LICENSE](LICENSE).
