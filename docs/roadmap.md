# Roadmap

Seven-day portfolio arc. Only Days 1-6 are implemented; everything under
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

## Day 3 — Networking, configuration, volumes, persistence (v0.3.0, implemented)

- A new `state/` package (stdlib-only, no third-party dependency, no
  Flask/Redis/PostgreSQL/SQLite) added alongside `app/` and `gateway/` in
  the same release image: `GET /`, `/healthz` (liveness only), `/readyz`
  (a real, non-mutating storage-readiness check), `GET /state`, and
  `POST /state/increment` — a durably persisted monotonic counter under a
  fixed `/data` mount, with atomic fsync'd writes and strict corrupted-
  state rejection (never silently coerced). See `docs/persistence.md`.
- `compose.yaml` now declares exactly three services in a health-gated
  chain: `state` (no dependency) -> `app` (`depends_on: state: condition:
  service_healthy`) -> `gateway` (`depends_on: app: condition:
  service_healthy`, unchanged from Day 2). `app` becomes the only service
  allowed to talk to `state`; `gateway` forwards `GET /state`/`POST
  /state/increment` to `app`'s identical paths, never to `state` directly.
- Two explicit Compose networks replace the Day 2 implicit default:
  `edge` (`gateway` + `app`) and `backend` (`app` + `state`,
  `internal: true`). `gateway` and `state` share no network — proven at
  runtime via real DNS-resolution-failure checks in both directions, not
  merely declared. See `docs/networking.md`.
- A named Compose volume (`state_data`, mounted at `/data` in `state`
  only) provides real persistence across container recreation and a full
  `compose down`/`up` cycle (volume retained) — proven, not asserted, by
  `scripts/compose/compose_integration.py`. `state` keeps
  `read_only: true` like every other service; `/data` is its only
  writable path, proven at both [C] and [D] evidence tiers.
  `docker/app/Dockerfile` pre-creates `/data` owned by `10001:10001` so a
  freshly created volume works for the non-root process without running
  as root or `chmod 777`.
- A new top-level Compose `configs:` object (`config/platform.json`,
  non-secret, mounted read-only into all three services at
  `/etc/maops/platform.json`) demonstrates runtime configuration outside
  the image — `dependency_timeout_seconds` (app's/gateway's bounded call
  to their dependency) and `state_filename` (state's persisted-file name,
  validated as a bare filename, never an arbitrary path) can change after
  a container recreation with no image rebuild. See
  `docs/configuration.md` for the full mechanism taxonomy (Compose
  interpolation vs. environment vs. Compose-mounted config vs. secrets).
- `scripts/compose/compose_integration.py` gained a genuine
  timestamp-based health-gated startup-ordering proof (closes Day 2
  finding M-1, day-02-compose-review.md: the prior script only polled
  each container to eventually-healthy independently, never proving
  `gateway` didn't start before `app` was actually healthy) and a real
  [D] rejected-write proof for every Compose-managed container's rootfs
  (closes Day 2 finding M-1/L-2, day-02-security-review.md /
  day-02-compose-review.md: previously only asserted at [C]).
- `scripts/compose/check_compose.py` gained a real cross-check that
  `UPSTREAM_HOST`/`STATE_HOST` both name a real service in the compose
  file *and* share a network with the consumer (closes Day 2 finding L-1,
  day-02-compose-review.md), plus the full set of Day 3 network/volume/
  config structural invariants.
- `scripts/lint/check_source.py` now also scans `state/`, and closes the
  carried-forward Day 1/2 finding (L-1, day-01/02-test-review.md) that
  `os.system`/`os.popen` detection could be bypassed by a single-hop
  import alias.
- `VERSION` bumped `0.2.0` → `0.3.0`; the same version-consistency design
  (image tag, OCI label, Compose image references, raw fallback literals)
  extends to the three-service topology with no new duplicated version
  literal.
- Still no CPU/memory resource limits, restart-policy reliability
  engineering, CI, or container registry — all explicitly Day 4+ scope
  (see below).

## Day 4 — Build/image security and reproducibility (v0.4.0, implemented)

- **Runtime decision**: the originally planned `python:3.13-slim` runtime
  was rejected after a real Trivy scan found 4 unfixed CRITICAL
  `perl-base` findings (with no newer base digest available to resolve
  them). `gcr.io/distroless/python3-debian13:nonroot` was adopted in its
  place — same Python 3.13/Debian 13 family, no shell, no package
  manager, no `perl-base` — and independently re-verified (real
  vulnerability scan: 0 CRITICAL, 0 fixable HIGH; full runtime testing;
  exact reproducibility) before adoption. `docker/app/Dockerfile` is now
  a two-stage build: a digest-pinned `python:3.13-slim` builder
  (filesystem preparation only) feeding the digest-pinned Distroless
  final stage. See `docs/build-security.md`.
- Every in-container probe across this project's tooling
  (`scripts/verify/security_check.py`, `scripts/build/image_audit.py`,
  `scripts/smoke/container_smoke.py`, `scripts/compose/
  compose_integration.py`) now execs the absolute `/usr/bin/python3.13`
  interpreter directly — never a shell, never a coreutils binary
  (`sh`, `cat`, `id`, `find`, `stat`) — since the Distroless final
  runtime has none of those.
- A deliberate BuildKit/buildx-based deterministic release build
  (`docker buildx build --output type=docker,rewrite-timestamp=true`,
  `SOURCE_DATE_EPOCH` derived from the current commit timestamp, never
  the wall clock) — two independent, clean, `--no-cache` builds from the
  identical source tree produce a **byte-identical image ID**, verified
  directly and independently corroborated by RootFS diff-ID equality,
  Config/OCI-label equality, and a normalized content-addressed
  filesystem manifest of `/app`
  (`scripts/build/reproducibility_check.py`, `make reproducibility-check`).
  See `docs/build-security.md`.
- Image-level immutability: application source (`app/`, `gateway/`,
  `state/`, `VERSION`) is now root-owned in the built image, not owned by
  the non-root `10001:10001` runtime user — proven with a real attempted
  write against a container started with *no* hardening flags at all
  (not even `--read-only`), independent of and in addition to
  `compose.yaml`'s runtime `read_only: true`. `/data` remains the one
  deliberate exception, still writable by `10001:10001`.
- `scripts/build/image_audit.py` (`make image-audit`) — a project-
  specific release-image policy audit: exact tag/version, non-root
  `Config.User`, a truthful OCI source label (cross-checked against the
  real `git remote`), entrypoint/default command, all three service
  packages present, `/data` ownership, the image-level immutability
  proof, absence of repository-only/secret-shaped/setuid-setgid/
  world-writable content, and (Day 4 Distroless-specific) real proof of
  shell absence, package-manager absence, pip/setuptools absence, and the
  expected `/usr/bin/python3.13` interpreter.
- Real SBOM generation (Syft, SPDX JSON) and real vulnerability scanning
  (Trivy, JSON) for the exact release image — both scanners pinned by
  exact digest (`security/scanners.lock`), scanning a `docker save`
  archive with the Docker socket never mounted into either scanner
  container. `scripts/security/generate_sbom.py`/`check_sbom.py`
  (`make sbom`/`sbom-check`) and `scripts/security/vuln_scan.py`/
  `check_trivy_report.py` (`make vuln-scan`, `make supply-chain-check`)
  enforce an explicit vulnerability policy (any CRITICAL, or any HIGH
  with a fix available, fails the gate — no `.trivyignore`, no
  manufactured exceptions), unweakened by the runtime migration. See
  `docs/supply-chain.md`: the Distroless-based release image genuinely
  **passes** vulnerability policy (Critical=0, fixable High=0), reported
  alongside 15 unfixed-HIGH findings that remain (non-blocking under
  policy) — the historical `python:3.13-slim` failure is preserved as the
  documented reason the runtime changed, not as this release's result.
- `scripts/smoke/container_smoke.py` gained a multi-role chain smoke test
  (`state`+`app`+`gateway` from the one image, on a throwaway Docker
  network, without Compose), closing the Day 3 Low finding that smoke
  testing only ever covered the `app` role.
- Closed six Day 3 review findings: A-1 (`schema_version` boolean-bypass
  in all three `platform_config.py` modules — `True == 1` in Python), A-2
  (`check_kernel_readonly_write_fails`'s "service kept serving" probe now
  dispatches to each container's own healthcheck module by name, but the
  property that actually closes A-2 is the Day 4 H-1 fix itself — each
  `/healthz` now carries a `role` field and each healthcheck module
  rejects a wrong-role response, so the role-aware *dispatch* has real
  discriminating power rather than merely selecting a same-shaped probe;
  see `docs/security.md`'s "Role-aware liveness" section), A-3 (a real, live
  `docker network inspect` proof for `backend`/`edge`'s `Internal` flag
  was added to `compose_integration.py`, closing the doc/automation gap),
  A-4 (`docs/compose-platform.md`'s stale `UPSTREAM_TIMEOUT_SECONDS`
  reference corrected to `dependency_timeout_seconds`), A-5
  (`compose_integration.py` now installs a real `SIGTERM` handler so a
  mid-run termination still reaches its `finally` teardown, plus
  line-buffered stdout so diagnostic output is never silently lost), and
  a documentation-only clarification for A-6 (cross-hop timeout
  stacking — the deeper reliability-engineering fix is deliberately left
  to Day 5).
- `VERSION` bumped `0.3.0` → `0.4.0`; the same version-consistency chain
  extends with no new duplicated version literal.
- Still no CI, no container registry, no cryptographic build provenance/
  attestation/signing, no resource limits, no restart-policy engineering
  — all explicitly Day 5+ scope (see below).

## Day 5 — Health, reliability, resource controls (v0.5.0, implemented)

- Liveness (`/healthz`) vs. readiness (`/readyz`) formalized as an
  explicit, platform-wide contract rather than an implicit per-service
  convention: liveness is local-process-only and never calls a
  dependency; readiness is honestly chained (`state` -> `app` -> `gateway`,
  each layer's readiness genuinely depends on the one below, never
  independently faked). The Day 4 H-1 role-aware `/healthz` fix is
  unchanged and still regression-proven by the real 3x3 healthcheck
  matrix in `compose_integration.py`.
- Closed Day 3 finding A-6 (cross-hop timeout stacking): `config/
  platform.json`'s single, ambiguous `dependency_timeout_seconds` field is
  replaced by an explicit, named two-hop budget -
  `state_dependency_timeout_seconds` (app's inner hop),
  `gateway_upstream_timeout_seconds` (gateway's outer hop), and
  `timeout_safety_margin_seconds` - with the required invariant
  (`outer > inner + margin`) enforced at config-*load* time by
  `gateway/platform_config.py`, not merely documented. Proven against a
  real stalled dependency (`docker pause state`, not a mock): a
  state-dependent request through `gateway -> app -> state` returns a
  controlled failure inside the configured outer budget, genuinely
  governed by the inner timeout, never a raw hang or an `inner + outer`
  serial wait. See `docs/reliability.md`.
- `compose.yaml` now declares explicit, reviewable Compose resource
  limits (`cpus: 0.50`, `mem_limit: 128m`, `pids_limit: 64`) and a bounded
  restart policy (`restart: on-failure:3`) plus `stop_grace_period: 10s`
  on all three services - the non-Swarm Compose fields a plain
  `docker compose up` actually applies as real Docker `HostConfig` values,
  not a Swarm-only `deploy.resources.limits` block ordinary Compose
  ignores.
- `scripts/compose/check_compose.py` gained three new structural checks
  (resource limits present/bounded, restart policy present/bounded,
  stop_grace_period present/bounded) against the rendered Compose config,
  bringing the total from 14 to 17.
- A new `scripts/reliability/reliability_check.py` (`make
  reliability-check`), wired into `make release-check`, is the dedicated
  runtime home for all of the above plus real crash/restart/intentional-
  stop/graceful-shutdown proofs, kept as three deliberately distinct
  lifecycle scenarios (see `docs/reliability.md`): a **transient** real
  kernel-initiated OOM-kill on `state` (PID 1's own `/proc/1/oom_score_adj`
  maxed from inside, `mem_limit` never touched - deliberately not
  `docker kill`/`docker stop`, both confirmed exempted from the
  restart-policy engine, and not an internal `os.kill(1, SIGKILL)` either,
  confirmed blocked by the kernel's PID-namespace init-signal-immunity
  rule) triggers exactly one automatic restart with no manual
  `docker start`/`stop`/`kill` anywhere in the proof, and `app`/`gateway`
  readiness and the persisted counter all recover automatically; a
  **persistent** OOM condition (the memory limit itself lowered and kept
  lowered) proves the bound instead - `on-failure:3` retries automatically
  up to exactly 3 times and correctly stops, requiring an explicit
  operator restart, never described as automatic recovery; an
  **intentional** `docker stop` is proven to *not* trigger the restart
  policy at all, and completes cleanly within the grace period; stopping
  `app` degrades only `gateway`'s readiness (never its liveness), and
  stopping `gateway` leaves `app`/`state` completely unaffected. This
  script does not duplicate anything `compose_integration.py` already
  proved (topology, DNS, network segmentation, persistence, config
  mounting, runtime hardening, the H-1 matrix, startup ordering, or the
  existing `state`-stop/degrade/recover scenario) - see
  `docs/reliability.md` and `docs/compose-platform.md`'s "Day 5
  additions" section for the exact division of ownership.
- `VERSION` bumped `0.4.0` → `0.5.0`; the same version-consistency chain
  extends with no new duplicated version literal.
- Still no CI, no container registry, no cryptographic build provenance/
  attestation/signing, no metrics/tracing/log-aggregation observability
  stack - all explicitly Day 6+ scope (see below) or explicitly out of
  this release's scope (see `docs/reliability.md`'s own boundary
  section).

## Day 6 — CI/CD, integration, release engineering (v0.6.0, implemented)

A GitHub Actions delivery plane layered on top of the unchanged Days 1-5
runtime plane — see `docs/ci-cd.md` for the full design.

- **`.github/workflows/ci.yml`**: runs on every `pull_request` targeting
  `main` and every `push` to `main`. Two jobs — `quality` (fast,
  Docker-free: `make quality`, now including a new `workflow-check` gate)
  fails first and cheaply; `release-policy` (`needs: quality`) runs the
  full authoritative `make release-check` against the GitHub-hosted
  Ubuntu runner's own pre-installed Docker Engine + Compose v2 plugin (no
  Docker Engine installation step added). Least privilege throughout
  (`permissions: contents: read`, no PR run ever receives a secret or a
  write-scoped token), `pull_request_target` never used, obsolete runs for
  the same PR/ref cancelled automatically.
- **`.github/workflows/release.yml`**: `push: tags: v*.*.*` is the real
  release event; `workflow_dispatch` on `main` is a safe, structurally
  non-publishing release-candidate dry run (the `publish` job's own `if:`
  condition can only ever be satisfied by a real tag push - see
  `docs/ci-cd.md`). Real-tag publication additionally proves the tagged
  commit belongs to `main`'s history (`git merge-base --is-ancestor`) and
  that the tag exactly matches `VERSION`, before creating a GitHub Release
  (GitHub CLI, `contents: write` scoped to only that one job) with the
  release image's SBOM, Trivy vulnerability report, and a `SHA256SUMS`
  file attached. Tags/releases are never moved, rewritten, or overwritten.
- **No container registry publication** - this was an earlier draft's
  assumption for this day and is explicitly corrected here: the GitHub
  Release (with its attached security/release evidence) is Day 6's entire
  delivery destination. No `docker login`/`docker push`, no GHCR/Docker
  Hub/ECR/ACR configuration, and no registry credential exists anywhere in
  this repository - `scripts/ci/check_workflows.py` statically enforces
  this absence. A container registry remains out of this project's scope
  for the full seven-day arc unless a future scope decision explicitly
  adds one.
- Two new repository-owned, Docker-free-testable validation scripts:
  `scripts/ci/check_workflows.py` (`make workflow-check`) statically
  audits the two committed workflow files for the security/integrity
  invariants above; `scripts/release/check_release_context.py` validates
  `VERSION`/tag format, tag-vs-`VERSION` equality, release-notes presence,
  and main-history ancestry, with pure logic separated from the one real
  `git` call it needs (never `shell=True`, argv-only).
- Closed the Day 5 final adjudication's carried-forward test/harness
  findings (3 Medium + 6 Low) now that automated gates are authoritative -
  see `docs/releases/v0.6.0.md` for the itemized closure list. The seven
  untouched Day 4 carried-forward findings remain open at their previously
  adjudicated severity.
- `VERSION` bumped `0.5.0` → `0.6.0`; the same version-consistency chain
  extends with no new duplicated version literal.
- Runtime plane unchanged: still exactly `gateway -> app -> state`, three
  services, two networks, one named volume, one application image. Still
  no Cosign/SLSA/provenance attestation, no Kubernetes/Helm/Argo CD, no
  Prometheus/Grafana/OpenTelemetry, no Terraform/Ansible - all explicitly
  Day 7+ scope or out of this project's scope entirely (see below).
- **Emergency Debian-security overlay** (in-scope hotfix, not a new
  day): `make release-check`'s unweakened vulnerability policy caught a
  real, fixable HIGH finding (CVE-2026-14456, `libssl3t64`) after the
  pinned Distroless digest lagged an already-published Debian Security
  fix. Remediated with a narrow, checksum-pinned Debian-security package
  overlay (a new `security-patch` build stage in `docker/app/Dockerfile`,
  pinned via `security/runtime-patches.lock`) rather than a base-image
  migration or a policy weakening - see `docs/build-security.md` and
  `docs/supply-chain.md`. The Distroless base digest and the runtime
  topology above are both unchanged.

## Day 7 — Hardening, reviews, showcase -> v1.0.0 (implemented)

Final hardening and production-readiness pass across the full seven-day
build — no runtime redesign: the same three services, one image, two
networks, one named volume, and hardening properties Days 1-6 already
established. `VERSION` bumped `0.6.0` -> `1.0.0`. See
`docs/production-readiness.md` (the implementation-time debt ledger and
the final production-readiness contract) and
`docs/releases/v1.0.0.md` (release-candidate notes — this is
release-*candidate* preparation; the `v1.0.0` tag/GitHub Release
themselves follow only after independent review, exactly as `v0.6.0`
did).

Three Day-6-carried Medium findings closed:

- **Runtime security-patch lifecycle** (`scripts/security/
  patch_lifecycle_check.py`, `make patch-lifecycle-check`) — a real
  tripwire that independently `docker pull`s the exact pinned Distroless
  final base (derived from `docker/app/Dockerfile`'s own FROM text via
  `scripts/security/base_image_ref.py`, never a duplicated digest
  constant) and inspects its real, currently-shipped `libssl3t64`
  version against `security/runtime-patches.lock`'s own recorded
  vulnerable/patched versions, using genuine Debian version-comparison
  semantics (`scripts/security/debian_version.py`). Distinguishes
  "overlay still required", "overlay now redundant" (fails loudly rather
  than silently continuing to trust a stale overlay), "evidence could not
  be established" (fails rather than assuming), and "the lock's own
  recorded rationale has drifted from reality" (fails, prompting a lock
  update) — four real, independently testable outcomes, not a tautology.
  Integrated into `make release-check`.
- **Release-consumer `SHA256SUMS` layout** (`scripts/release/
  prepare_release_bundle.py`, `make release-bundle`, DAY6-POST-M1) — the
  real `v0.6.0` release shipped a `SHA256SUMS` referencing CI
  workspace-relative paths (`release-evidence/sbom/...`), which a
  consumer downloading the flat GitHub Release assets could not verify
  with an unmodified `sha256sum -c SHA256SUMS`. This script stages a
  flat, basename-only bundle and independently proves the real,
  unmodified command succeeds against it — with real, discriminating
  tests for a missing/renamed/tampered asset, a duplicate basename, and a
  hand-tampered manifest referencing a path-traversal/nested-CI-path
  entry. `release.yml`'s `publish` job now attaches `release-bundle/*`
  verbatim instead of re-deriving checksums inline.
- **Post-restart cgroup-v2 race classifier** (`scripts/reliability/
  reliability_check.py::_is_transient_cgroup_update_race`, DAY6-POST-M2)
  — hardened, conservatively, to also accept the newly evidenced
  `memory.max` disappearance variant (GitHub run `33059581018`) alongside
  the original `cgroup.controllers` variant (GitHub run `32960673438`),
  via a deliberately narrow, explicitly enumerated accepted-filename set
  plus a real `openat2 <path>: no such file or directory` match requiring
  genuine cgroup-hierarchy path context — never a broad "any
  cgroup-shaped filename" wildcard. Unrelated missing files, unrelated
  runc failures, permission/daemon/argument errors, and the bounded
  monotonic retry deadline are all unchanged.

Also materially closed, in the same session, a still-open Day 4 finding
this Day 7 scope's own "historical debt sweep" directive named
explicitly: `scripts/build/image_audit.py`'s `check_final_base_is_
approved_distroless` was partially tautological (it only asserted the
built image's `RootFS` was inspectable and non-empty, never actually
comparing it against anything). It now independently `docker pull`s the
same pinned base `patch_lifecycle_check.py` derives, and asserts that
base's own `RootFS.Layers` is a genuine ordered prefix of the built
release image's own layers — real evidence, with new Docker-free unit
coverage (`tests/test_image_audit.py`) the function never had before.

See `docs/production-readiness.md` for the full implementation-time debt
ledger (every other historical Low/Medium finding, adjudicated CLOSED /
ACCEPTED / SUPERSEDED / OUT OF SCOPE) and the final production-readiness
contract `make release-check` now composes end to end.
