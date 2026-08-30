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

## Day 7 additions (final hardening, production readiness, v1.0.0 preparation)

Release-*candidate* preparation only — no runtime redesign, and no
`v1.0.0` tag/GitHub Release created yet (that follows independent review,
exactly as `v0.6.0` did). `VERSION` bumped `0.6.0` -> `1.0.0`.

- **Runtime security-patch lifecycle tripwire**: `scripts/security/
  patch_lifecycle_check.py` (`make patch-lifecycle-check`) independently
  `docker pull`s the exact pinned Distroless base (derived from
  `docker/app/Dockerfile`'s own `FROM` text, never a duplicated constant)
  and proves whether `security/runtime-patches.lock`'s emergency
  `libssl3t64` overlay is still required, now redundant, or has drifted
  from its own documented rationale — using real Debian version-
  comparison semantics (`scripts/security/debian_version.py`). See
  [docs/build-security.md](docs/build-security.md).
- **Consumer-verifiable release checksums**: `scripts/release/
  prepare_release_bundle.py` (`make release-bundle`) stages a flat,
  basename-only release bundle and independently proves the real,
  unmodified `sha256sum -c SHA256SUMS` succeeds against it — closing a
  real defect found in the published `v0.6.0` release (its `SHA256SUMS`
  referenced CI-internal paths a normal flat download couldn't verify
  against).
- **Hardened post-restart cgroup-v2 race classifier**:
  `reliability_check.py`'s transient-failure classifier now recognizes a
  second, newly evidenced real GitHub Actions failure signature
  (`memory.max` disappearing, alongside the original
  `cgroup.controllers`), via a deliberately narrow, explicitly enumerated
  filename allowlist plus real path-context/ENOENT-semantics matching.
- **Historical debt sweep**: every still-relevant Low/Medium engineering-
  review finding from Days 1-6 was reviewed and adjudicated (closed,
  accepted, or explicitly still open) — including materially closing a
  Day 4 finding that `image_audit.py`'s base-digest cross-check was
  partially tautological. See
  [docs/production-readiness.md](docs/production-readiness.md).

## Day 6 additions (CI/CD and release engineering)

- **A GitHub Actions delivery plane** layered on top of the unchanged
  runtime plane: `.github/workflows/ci.yml` runs `make quality` (now
  including a new `workflow-check` gate) then the full `make
  release-check` on every pull request and every push to `main`;
  `.github/workflows/release.yml` implements a safe, non-publishing
  release-candidate dry run (`workflow_dispatch`) and a controlled,
  tag-triggered (`push: tags: v*.*.*`) GitHub Release publication with
  main-history verification, least-privilege permissions split by job, and
  every action pinned to an immutable commit SHA. See
  [docs/ci-cd.md](docs/ci-cd.md).
- **No container registry publication** — the GitHub Release (with its
  attached SBOM, vulnerability report, and checksums) is Day 6's entire
  delivery destination; no `docker push`/GHCR/Docker Hub configuration
  exists anywhere in this repository.
- Two new repository-owned validators: `scripts/ci/check_workflows.py`
  (`make workflow-check`) statically audits the committed workflow files;
  `scripts/release/check_release_context.py` validates `VERSION`/tag
  format, tag-vs-`VERSION` equality, release-notes presence, and
  main-history ancestry.
- Closed the Day 5 final adjudication's carried-forward test/harness
  findings (3 Medium + 6 Low) — see
  [docs/releases/v0.6.0.md](docs/releases/v0.6.0.md).
- Runtime architecture unchanged: still exactly `gateway -> app -> state`,
  three services, two networks, one named volume, one application image.
- **Emergency Debian-security overlay**: `make release-check`'s
  unweakened vulnerability policy caught a real, fixable HIGH finding
  (CVE-2026-14456, `libssl3t64`) that the pinned Distroless digest had not
  yet picked up upstream. A checksum-pinned, official Debian Security
  package overlay (a new `security-patch` build stage) fixes it without
  migrating the runtime base or weakening the policy — see
  [docs/build-security.md](docs/build-security.md) and
  [docs/supply-chain.md](docs/supply-chain.md), and
  `security/runtime-patches.lock` for the exact pin.

## Day 5 additions (health, reliability, resource controls)

- **Liveness vs. readiness formalized as a platform-wide contract**:
  `/healthz` is local-process liveness only on all three services and
  never calls a dependency (the Day 4 H-1 role-aware fix is unchanged);
  `/readyz` is honestly chained (`state` -> `app` -> `gateway`, each layer
  genuinely depending on the one below). See
  [docs/reliability.md](docs/reliability.md).
- **Closed Day 3 finding A-6** (cross-hop timeout stacking): the old
  single, shared `dependency_timeout_seconds` field is replaced by an
  explicit two-hop timeout budget in `config/platform.json`
  (`state_dependency_timeout_seconds` for `app`'s inner hop,
  `gateway_upstream_timeout_seconds` for `gateway`'s outer hop,
  `timeout_safety_margin_seconds` for the required headroom between
  them), with the invariant `outer > inner + margin` enforced at
  config-load time. Proven against a real stalled dependency
  (`docker pause state`, not a mock) — the external caller's request
  completes with a controlled failure inside the outer budget, never a
  raw hang or an `inner + outer` serial wait.
- **Explicit, reviewable resource controls** on all three services —
  `cpus: 0.50`, `mem_limit: 128m`, `pids_limit: 64` — the non-Swarm
  Compose fields a plain `docker compose up` actually applies as real
  Docker `HostConfig` values, plus a bounded `restart: on-failure:3`
  policy and a `stop_grace_period: 10s`.
- A new `scripts/reliability/reliability_check.py` (`make
  reliability-check`, now part of `make release-check`) proves all of the
  above against real Docker behavior: resource limits and restart policy
  applied to real containers, a real `docker pause`/`unpause` adversarial
  A-6 proof, a real kernel-initiated OOM-kill (a genuine SIGKILL,
  deliberately not `docker kill`/`docker stop` — see
  [docs/reliability.md](docs/reliability.md) for why those are exempted
  from the restart-policy engine) crash with automatic (never manual)
  bounded restart and persisted-state survival, a real intentional-stop-
  does-not-auto-restart proof, and `app`-down/`gateway`-down failure
  isolation — without duplicating anything `make compose-test` already
  proves.
- `scripts/compose/check_compose.py` gained three new structural checks
  (resource limits, restart policy, `stop_grace_period`), 14 -> 17.

## Day 4 additions (build/image security and reproducibility)

- **Distroless runtime**: the release image's final stage is
  `gcr.io/distroless/python3-debian13:nonroot` — no shell, no package
  manager, no `pip`/`setuptools`. The originally planned
  `python:3.13-slim` runtime was rejected (4 unfixed CRITICAL `perl-base`
  findings); Distroless was adopted after real vulnerability scanning,
  runtime testing, and reproducibility re-verification all passed against
  it. See [docs/build-security.md](docs/build-security.md).
- **Two-stage build** (a third, build-time-only `security-patch` stage
  was added Day 6 — see below): a digest-pinned `python:3.13-slim`
  builder stage (filesystem preparation only) feeding the digest-pinned
  Distroless final stage — the builder's own toolchain never enters the
  release image.
- A deterministic BuildKit/buildx release build (`make build`) — two
  independent, clean builds from the identical source tree produce a
  **byte-identical image ID**, proven by `make reproducibility-check`
  (re-verified after the Distroless migration).
- Application source is root-owned in the image (image-level
  immutability), independent of and in addition to `compose.yaml`'s
  runtime `read_only: true` — proven with a real write attempt against a
  container started with *no* hardening flags at all.
- A project-specific release-image policy audit (`make image-audit`),
  now including Distroless-specific proofs: shell absence, package-
  manager absence, pip/setuptools absence, and the expected
  `/usr/bin/python3.13` interpreter.
- Real SBOM generation (Syft, SPDX JSON — `make sbom`/`sbom-check`) and
  real vulnerability scanning (Trivy, JSON, with an explicit
  Critical/fixable-High policy — `make vuln-scan`) for the exact release
  image; both scanners pinned by exact digest, neither ever given the
  Docker socket. See [docs/build-security.md](docs/build-security.md) and
  [docs/supply-chain.md](docs/supply-chain.md) — the Distroless-based
  release image's vulnerability policy genuinely **passes** (Critical=0,
  fixable High=0), reported alongside the 15 unfixed-High findings that
  remain (non-blocking under policy).
- A multi-role chain smoke test (`state`+`app`+`gateway` from one image,
  without Compose) — every in-container probe across this project's
  tooling now execs the absolute `/usr/bin/python3.13` interpreter, never
  a shell, matching the Distroless runtime's own constraints.
- Closed six Day 3 review findings (`schema_version` boolean-bypass,
  role-aware read-only-write verification, a real `docker network
  inspect` proof, a stale doc reference, `SIGTERM` handling in the
  Compose integration harness, and a documentation clarification for
  cross-hop timeout stacking).

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
  that runs any of three roles (`/usr/bin/python3.13 -m app`, `-m
  gateway`, or `-m state`) directly as PID 1 in exec form, with a
  per-role stdlib-only `HEALTHCHECK`, and `/data` pre-created with
  correct non-root ownership so the `state_data` named volume works
  without running as root.
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
Days 1-6 are implemented; everything else is explicitly planned, not
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
make workflow-check           # project-specific GitHub Actions workflow policy validator
make quality                    # test + lint + dockerfile-check + compose-check + workflow-check

make build                     # deterministic BuildKit build, tagged maops-docker-platform:<VERSION>
make inspect                     # docker image inspect / ls / history
make image-audit                   # project-specific release-image policy audit
make smoke                           # real-image container smoke test (single-role + multi-role chain)
make security-check                    # hardened-runtime security verification
make compose-test                        # real Compose-stack integration test
make reliability-check                     # real resource/restart/timeout-hierarchy/failure-recovery proof
make reproducibility-check                   # independent two-build image-identity proof

make sbom                                    # generate SPDX JSON SBOM (Syft)
make sbom-check                                # validate the generated SBOM
make vuln-scan                                   # generate Trivy JSON + enforce vulnerability policy
make supply-chain-check                            # sbom + sbom-check + vuln-scan

make release-check   # quality + build + inspect + image-audit + smoke + security-check +
                      #   compose-test + reliability-check + reproducibility-check +
                      #   supply-chain-check (sbom + sbom-check + vuln-scan)

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
proofs, [docs/persistence.md](docs/persistence.md) for the volume/
read-only-rootfs interaction, [docs/build-security.md](docs/build-security.md)
for deterministic builds and image-level immutability,
[docs/supply-chain.md](docs/supply-chain.md) for SBOM generation,
vulnerability scanning, and this project's explicit vulnerability policy,
and [docs/reliability.md](docs/reliability.md) for the liveness/readiness
contract, the Day 5 timeout-hierarchy design that closes Day 3 finding
A-6, resource controls, and the real crash/restart/pause failure-recovery
proofs. [docs/ci-cd.md](docs/ci-cd.md) documents the Day 6 delivery plane
that now automates every gate above on every pull request and push.

## Repository structure

```
app/                     # stdlib-only Python HTTP workload (Day 1 backend)
gateway/                 # stdlib-only Python gateway (Day 2, sole host-facing service)
state/                   # stdlib-only Python persistence service (Day 3)
config/platform.json     # non-secret, Compose-mounted runtime configuration (Day 3, timeout fields updated Day 5)
security/scanners.lock   # digest-pinned Syft/Trivy scanner references (Day 4)
security/runtime-patches.lock  # checksum-pinned emergency Debian-security package overlay (Day 6)
docker/app/Dockerfile    # 3-stage: slim builder -> security-patch overlay -> Distroless final runtime, non-root, all three roles
compose.yaml             # three-service hardened Compose stack (state -> app -> gateway, resource/restart-bounded, Day 5)
.github/workflows/       # CI + release delivery plane (Day 6) - see docs/ci-cd.md
tests/                   # unittest suite (app/ + gateway/ + state/ + Day 4/5/6 tooling)
scripts/lint/            # project-specific source + Dockerfile validators
scripts/compose/         # project-specific Compose structural + integration checks
scripts/smoke/           # real-image container smoke test (single-role + multi-role)
scripts/verify/          # runtime security verification
scripts/build/           # deterministic-build reproducibility proof + image policy audit (Day 4)
scripts/security/        # SBOM generation/validation + vulnerability scan/policy (Day 4);
                         #   runtime patch-lock parsing + patch-lifecycle tripwire + Debian
                         #   version comparison (Day 7)
scripts/reliability/     # real resource/restart/timeout-hierarchy/failure-recovery proof (Day 5)
scripts/ci/              # GitHub Actions workflow policy validator (Day 6)
scripts/release/         # release-context (VERSION/tag/history) validator (Day 6);
                         #   consumer-verifiable release-bundle staging (Day 7)
docs/releases/           # version-specific GitHub Release notes (Day 6+)
artifacts/               # generated SBOM/vulnerability-report output (git-ignored)
release-bundle/          # flat, consumer-verifiable release bundle output (Day 7, git-ignored)
docs/                    # architecture, security, networking, configuration,
                         #   persistence, compose platform, build security,
                         #   supply chain, reliability, ci-cd, roadmap,
                         #   production readiness (Day 7)
.claude/                 # project agents, skills, and guidance
VERSION                  # single authoritative version source
```

## Current version

`1.0.0` (see `VERSION`) — Day 7 of 7, release-candidate preparation. See
[docs/releases/v1.0.0.md](docs/releases/v1.0.0.md) and
[docs/production-readiness.md](docs/production-readiness.md).

## Seven-day roadmap (high level)

| Day | Theme |
|---|---|
| 1 | Secure container foundation |
| 2 | Compose multi-service topology |
| 3 | Networking, configuration, volumes, persistence |
| 4 | Build/image security and reproducibility |
| 5 | Health, reliability, resource controls |
| 6 | CI/CD, integration, release engineering |
| 7 | Hardening, reviews, showcase -> v1.0.0 *(this release)* |

Full detail: [docs/roadmap.md](docs/roadmap.md).

## License

MIT — see [LICENSE](LICENSE).
