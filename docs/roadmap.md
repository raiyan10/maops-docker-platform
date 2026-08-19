# Roadmap

Seven-day portfolio arc. Only Days 1-2 are implemented; everything under
a later day below is **planned, not implemented** — do not read a later
day's bullet list as describing current behavior.

## Day 1 — Secure container foundation (v0.1.0, implemented)

- A tiny, dependency-free Python stdlib HTTP workload (`app/`) exposing
  `GET /`, `/healthz`, `/readyz`, `/info` as deterministic JSON, with a
  controlled 404, a controlled unsupported-method (`405`/Allow header)
  response, HEAD support, and no traceback/environment disclosure.
- `docker/app/Dockerfile`: a digest-pinned `python:3.13-slim` base,
  dedicated non-root `10001:10001` user, exec-form `ENTRYPOINT` running
  the application directly as PID 1, a stdlib-only `HEALTHCHECK`, and OCI
  metadata (title/description/version/licenses — `source` deliberately
  omitted until a GitHub repository exists).
- `compose.yaml`: one hardened application service (`read_only: true`,
  `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`), explicit
  host port mapping, no premature multi-service topology.
- `.dockerignore` with genuinely recursive exclusion patterns, proven
  (not just asserted) to reject nested `__pycache__`/`.pyc` content via
  an actual nested-probe `--no-cache` build.
- `tests/` (stdlib `unittest`, no `pytest`) covering the HTTP surface,
  configuration validation (`APP_HOST`/`APP_PORT`/`APP_NAME`, including
  malformed/zero/negative/`>65535`/whitespace `APP_PORT` values), and
  version loading.
- `scripts/lint/check_source.py` and `scripts/lint/check_dockerfile.py` —
  small, honestly-scoped, project-specific validators (AST-based source
  checks; instruction-aware Dockerfile checks), not general-purpose
  scanners.
- `scripts/smoke/container_smoke.py` — boots the real, exact-version
  image in a throwaway container, verifies the HTTP surface and non-root
  runtime, always cleans up its own container.
- `scripts/verify/security_check.py` — runtime security verification
  distinguishing desired/source configuration, Docker runtime inspection,
  and kernel/process verification for every applicable control (see
  `docs/security.md`).
- `Makefile` (`help`, `test`, `lint`, `dockerfile-check`, `build`,
  `inspect`, `smoke`, `security-check`, `quality`, `release-check`,
  `clean`) with an image tag derived from `VERSION`, never `latest`.
- Five project-local agents and four project-local skills under
  `.claude/` for Docker architecture, container security, Compose
  platform engineering, Docker testing, and release engineering.

## Day 2 — Compose multi-service topology (v0.2.0, implemented)

- A new `gateway/` package (stdlib-only, no third-party dependency) added
  alongside `app/` in the same release image: `GET /`, `/healthz`
  (gateway-process liveness only), `/readyz` (a real, bounded HTTP check
  against `app`'s `/readyz`), `/upstream/info` (a real, bounded HTTP call
  to `app:8080/info`, returned wrapped, never an arbitrary-URL proxy —
  the upstream destination is fixed at startup from
  `UPSTREAM_HOST`/`UPSTREAM_PORT`, never influenced by an incoming
  request, which is what prevents SSRF-style abuse).
- `compose.yaml` now declares exactly two services: `app` (Day 1 backend,
  no host-published port, reachable only via Compose service-name
  discovery) and `gateway` (the sole host-published service, bound to
  `127.0.0.1` only, `depends_on: app: condition: service_healthy`). Both
  keep every Day 1 hardening property (`read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, non-root `10001:10001`). See
  `docs/compose-platform.md` for the full topology rationale.
- `docker/app/Dockerfile` now builds one image capable of running either
  role (`python3 -m app` or `python3 -m gateway`, both still exec-form
  PID 1, no shell wrapper), and adds an accurate
  `org.opencontainers.image.source` label now that the GitHub repository
  genuinely exists.
- `scripts/compose/check_compose.py` — a static, project-specific
  structural validator against `docker compose config`'s rendered output
  (service set, image/version, hardening flags, healthchecks,
  `depends_on`, no custom network, no named volume).
- `scripts/compose/compose_integration.py` — the runtime counterpart:
  brings up the real two-service stack, proves gateway→app
  service-discovery communication, exercises the app-stop/gateway-degrade
  and app-restart/gateway-recover scenario with bounded deadlines, and
  inspects the real Compose-created containers' hardening and PID 1
  identity. Closes Day 1 finding M-3 (no automated check previously
  exercised anything beyond `docker compose config`'s own syntax
  validity).
- `scripts/verify/security_check.py` gained an automated `docker
  stop`/SIGTERM lifecycle regression check (closes Day 1 finding M-2) and
  an exact `VERSION`-vs-image-label cross-check (closes the Day 1
  version-drift review finding, alongside `check_compose.py`'s own
  fallback-default drift check).
- `VERSION` bumped `0.1.0` → `0.2.0`; the Dockerfile's OCI version label
  is now derived from it via a build arg rather than a duplicated
  literal, and hardcoded version assertions were removed from tests where
  the value is obtainable from `VERSION` directly.
- Still no custom network, named volume, database, cache, message broker,
  reverse proxy, Compose secrets/configs, resource limits, or CI — all
  explicitly Day 3+ scope (see below).

## Day 3 — Networking, configuration, volumes, persistence (planned)

Compose-managed volumes, a real custom network topology (beyond Compose
defaults), and configuration/persistence patterns appropriate to
whatever Day 2 introduced.

## Day 4 — Build/image security and reproducibility (planned)

Vulnerability scanning, SBOM generation, and build-reproducibility
verification for the image(s) that exist by that point. Multi-platform
builds are a candidate but not committed.

## Day 5 — Health/reliability/resources/observability (planned)

Resource limits (CPU/memory), reliability patterns (restart policy
review, dependency health ordering), and first-pass observability beyond
the Day 1 `HEALTHCHECK`.

## Day 6 — CI/CD/integration/release engineering (planned)

A CI workflow, a container registry (GHCR and/or Docker Hub), and the
first automated end-to-end release pipeline. None of this exists on Day
1 — `.claude/CLAUDE.md` and every agent/skill in this repository is
explicit that no CI or registry configuration should be added before
this day.

## Day 7 — Hardening, reviews, showcase -> v1.0.0 (planned)

Final security/architecture review pass across the full seven-day build,
portfolio-facing documentation, and the `v1.0.0` tag/release.
