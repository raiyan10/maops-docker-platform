# Day 6 Integration/Runtime-Parity Review — v0.6.0

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering`, PR #6, target `v0.6.0`
Reviewer: independent compose-platform-engineer (review only — no
implementation file, workflow, compose.yaml, or doc was modified; nothing
committed, pushed, merged, tagged, or dispatched).
Date: 2026-08-26.

## Scope

Whether Day 6's CI/CD delivery-plane work (`.github/workflows/*`, the
`security-patch` Dockerfile stage, Makefile orchestration) preserved every
runtime/integration invariant established through Day 5: exact 3-service
topology and `state -> app -> gateway` chain direction, the two-network
isolation model, the `state_data` volume, single-image/three-roles,
resource limits, restart policy, health/readiness separation, and the
Day 5 timeout-hierarchy proof — and whether GitHub-hosted-runner-specific
workarounds leaked into production/local runtime behavior. Findings are
cross-checked against real GitHub Actions evidence (`gh run view` on runs
32938805880, 32960673438, 32967457379) and against a fresh local run of
`make compose-check`, `make compose-test`, and `make reliability-check`
on this machine's own Docker Engine 29.7.2 / Compose v5.4.0, in addition
to reading source.

## Files reviewed

`compose.yaml`, `docker/app/Dockerfile`, `config/platform.json`,
`.github/workflows/ci.yml`, `.github/workflows/release.yml`,
`scripts/compose/check_compose.py`, `scripts/compose/compose_integration.py`,
`scripts/reliability/reliability_check.py`, `scripts/verify/security_check.py`
(healthcheck-module-selection functions only), `Makefile`,
`tests/test_check_compose.py`, `tests/test_reliability_check.py`,
`docs/compose-platform.md`, `docs/networking.md`, `docs/configuration.md`,
`docs/persistence.md`, `docs/reliability.md`, `docs/ci-cd.md`,
`docs/engineering-reviews/day-05-release-readiness.md`,
`docs/engineering-reviews/day-06-bootstrap-readiness.md` (context only).

---

## 1. Topology parity (Day 3-5 vs. Day 6)

`compose.yaml` is byte-for-byte the Day 5 topology with only the
`${VERSION:-0.6.0}` fallback literal bumped (`compose.yaml:52,89,129`,
matching `VERSION` = `0.6.0`). Exactly three services (`state`, `app`,
`gateway`), chain direction `state -> app -> gateway` via
`depends_on: condition: service_healthy` (`compose.yaml:120-122,165-167`)
unchanged. `check_compose.py`'s `check_service_set`/
`check_dependency_conditions` and `compose_integration.py`'s live
`docker inspect StartedAt` vs. dependency-first-healthy-time ordering
proof both re-confirm this against the rendered config and a real stack
(local re-run below). **No topology drift — Info, not a finding.**

## 2. Network topology and isolation

`edge` (`gateway`+`app`) / `backend` (`app`+`state`, `internal: true`)
unchanged (`compose.yaml:169-172`). `check_gateway_state_isolation`
(`check_compose.py:315-324`) and the real DNS-resolution-failure proof in
`compose_integration.py` (in both directions) are unmodified by Day 6.
Locally re-run: `network 'backend' real docker network inspect
Internal==True`, `network 'edge' ... Internal==False`, and both
`gateway->state`/`state->gateway` DNS resolutions failed as expected
(full output captured in this session). **No regression.**

## 3. Volume/persistence findings

`state_data:/data` mount (`compose.yaml:60-63`) and `state`'s
`read_only: true` are unchanged. The Dockerfile's `security-patch` stage
does not touch `/data` at all — its `COPY --from=security-patch` targets
are exclusively under `/usr/lib`, `/usr/share`, `/var/lib/dpkg/status.d`
(`docker/app/Dockerfile:179-185`), none of which intersect the
`/data` mount point prepared by the `builder` stage
(`docker/app/Dockerfile:46,169`). Locally re-run `compose-test`
reconfirms `/data` write+cleanup succeeds while the rootfs write is
rejected on the same container (`[D] state's /data mount accepts a real
write despite read-only rootfs` alongside `[D] attempted write to
read-only rootfs fails` for the same container). **No regression.**

## 4. Compose-mounted config findings

`config/platform.json` is unchanged in shape (`schema_version`,
`platform_name`, the three timeout fields, `state_filename` — no secret
material) and still mounted read-only into all three services via the
top-level `configs:` object (`compose.yaml:64-66,101-103,146-148,177-179`).
`check_config_object` (`check_compose.py:380-410`) and the real `[C]+[D]`
rejected-write proof in `compose_integration.py` are untouched by Day 6.
Locally reconfirmed (`platform config mount rejects a real write ([C]+[D]):
Mounts.RW=False write_exit=1` on all three services). **No regression.**

## 5. Compose config validation findings

`docker compose config` and `scripts/compose/check_compose.py` both pass
locally (17/17 structural checks, `VERSION=0.6.0`). No Day 6 change
touches `check_compose.py`'s logic; the `${VERSION:-0.6.0}` fallback
literal is consistent everywhere it appears in `compose.yaml`, and
`check_version_fallback_defaults` (`check_compose.py:94-116`) would catch
drift if it existed. **No regression.**

## 6. Security restriction findings

`read_only: true` / `cap_drop: [ALL]` / `security_opt:
[no-new-privileges:true]` unchanged on all three services. The Day 6
`security-patch` build stage (`docker/app/Dockerfile:70-99`) is
build-time-only: it never enters the final `FROM` (line 121) and its own
shell/`dpkg-deb` tooling is not copied into the runtime image — only
plain files are `COPY --from=security-patch`'d (lines 179-185). Verified
these copied files preserve root ownership (`COPY --from`'s documented
default, matching every other application-source `COPY` in the same
Dockerfile at lines 165-168, and confirmed empirically true for this
Dockerfile's own prior application-source copies per the Dockerfile's own
comment at lines 158-162) — so the overlay does not introduce any
non-root-writable or UID-10001-owned file into `/usr/lib`,
`/usr/share`, or `/var/lib/dpkg/status.d`, and does not reintroduce any
mutable state independent of Compose's `read_only: true`. The final
stage remains shell-less/package-manager-less Distroless; no new
capability, `--privileged`, socket mount, or host-network/PID exception
was introduced anywhere in `compose.yaml` or the Dockerfile. **No
regression — Info: worth confirming in a future day that
`image_audit.py`/`scripts/lint/check_dockerfile.py` (already stated by
the Dockerfile's own comments to cross-check `security/runtime-
patches.lock`) continue to be exercised every time the pinned Debian
Security snapshot URL/SHA256 is next rotated, since this overlay's
correctness depends entirely on that pin staying accurate — not
something this review can re-verify without also re-fetching the
upstream archive.**

## 7. Health dependency and startup-ordering findings

`healthcheck:` blocks (`compose.yaml:77-82,114-119,159-164`) and
`depends_on: condition: service_healthy` in both directions are
unchanged by Day 6. Local `compose-test` re-run reconfirmed real
timestamp-based ordering: `state first healthy at 12:48:43.640813, app
started at 12:48:43.918833` and `app first healthy at 12:48:49.840719,
gateway started at 12:48:50.203449` — both dependents start strictly
after their dependency's first-healthy timestamp, not merely eventually
converging to healthy. **No regression.**

## 8. Lifecycle findings (up/functional/down, resource cleanliness)

Local `make compose-test` reached 58/58 PASS including the 3x3
role-discrimination matrix, network isolation, startup ordering, and the
rootfs/`\`/data\`` write proofs; teardown left no `maops-compose-*`
container/network/volume behind (verified via filtered `docker ps -a`
`docker network ls` `docker volume ls`, all empty after the run). Local
`make reliability-check` reached 32/32 PASS, reproducing the exact same
scenario sequence (pause/unpause `state`, transient OOM-kill +
automatic recovery, persistent OOM-kill + bounded exhaustion + operator
recovery, intentional-stop-does-not-restart, app-down / gateway-down
degradation and recovery) and also left zero `maops-reliability-*`
leftovers. **No regression — both scripts remain real, adversarial,
self-cleaning integration proofs, not YAML-only checks.**

## 9. Resource limit / restart policy / stop_grace_period findings

`cpus: 0.50` / `mem_limit: 128m` / `pids_limit: 64` / `restart:
on-failure:3` / `stop_grace_period: 10s` unchanged on all three services
(`compose.yaml:72-76,109-113,154-158`). Local reliability-check
reconfirmed real `HostConfig` values (`cpus=0.5 memory=134217728
pids_limit=64` for all three containers), cgroup v2 corroboration where
available (`memory.max=134217728 pids.max=64 cpu.max="50000 100000"` on
all three, matching the declared `cpus: 0.50` ratio), `RestartPolicy
{Name=on-failure MaximumRetryCount=3}` on all three, and
`Config.StopTimeout=10` on all three. Both the genuine kernel OOM-kill
(SIGKILL) restart path and the `docker stop`-does-not-restart path were
independently reproduced locally with matching results to the CI run
(`RestartCount before=0 after=1` for the transient crash; `before=1
after=3, OOMKilled=True, Running=False` for the persistent-failure
exhaustion; `stayed_stopped=True, restart_count before=0 after=0` for the
intentional stop). **No regression.**

## 10. Timeout-hierarchy (A-6) findings

`config/platform.json`'s `state_dependency_timeout_seconds=2.0`,
`gateway_upstream_timeout_seconds=5.0`,
`timeout_safety_margin_seconds=1.0` are unchanged; the invariant
`5.0 > 2.0 + 1.0` continues to hold and is enforced at config-load time
(`gateway/platform_config.py`, exercised via `check_timeout_hierarchy_config`
in `reliability_check.py:449-477`). The real `docker pause state`
adversarial proof was reproduced locally: the external `/state` request
completed in `elapsed=2.01s` (inside the outer 5.0s budget, never a raw
hang, never `inner+outer=7.0s` stacked serially), tightly governed by the
configured inner timeout (`expected_band=[1.50s, 2.50s]`), while
`app`'s/`gateway`'s own `/healthz` (`exec_healthcheck`) stayed passing
throughout and only `/readyz` degraded to 503. Liveness is not
dependency-aware. **No regression.**

## 11. Compose-under-CI findings (Day 6)

`.github/workflows/ci.yml`'s `release-policy` job and
`.github/workflows/release.yml`'s `validate` job both run the identical
`make release-check` target (`ci.yml:99-104`, `release.yml:98-100`),
which is defined once in the `Makefile`
(`release-check: quality build inspect image-audit smoke security-check
compose-test reliability-check reproducibility-check
supply-chain-check`) — no hand-rolled subset of `docker compose`
commands exists in either workflow, and neither workflow skips
`compose-test`/`reliability-check` "to save CI time." Verified directly
against run **32967457379** (current HEAD, PASS): the `release-policy`
job's own log contains `compose_integration: PASS (58/58 inspection
checks passed)` and `reliability_check: PASS (32/32 reliability checks
passed)`, with the full pause/OOM-kill/restart-policy/timeout-hierarchy
evidence lines present verbatim (matching this review's own local
re-run). Confirmed run **32960673438**'s failure was exactly the
documented transient GitHub-runner cgroup/runc race
(`runc did not terminate successfully: exit status 1: openat2
.../cgroup.controllers: no such file or directory`) and not a
weakening or masking of any check — the fix
(`_is_transient_cgroup_update_race`, `reliability_check.py:526-543`) is a
narrowly-scoped three-fragment classifier applied only to this script's
own `docker update` retry loop (`update_container_resources_verified`),
never touching `compose.yaml`, `check_compose.py`, or any production
runtime path; it independently re-verifies via `docker inspect` on every
branch and re-raises on any other error immediately, with no blanket
retry-on-failure behavior. No Docker Engine installation step exists in
either workflow (confirmed — the runner's own pre-installed Engine +
Compose v2 plugin is used as-is), and the `docker-container` Buildx
builder change (`ci.yml:83-96`) is scoped only to the deterministic-build
export mechanism, not the topology/hardening/network/volume/resource
checks under this review's scope. **No Day 6 regression against this
file's own scope.**

## 12. Day 7+ fitness notes (observations only)

- The `security-patch` Dockerfile stage is a reasonable emergency
  overlay pattern but is architecturally a one-off — if a second
  CVE-driven package overlay were ever needed on a different package, the
  current structure would require a near-duplicate stage rather than a
  parameterized one. Not a defect at today's single-package scope; worth
  a design note if this pattern needs to repeat before Distroless's own
  upstream catches up.
- `reliability_check.py` has now grown past 1250 lines and covers
  resource limits, restart policy, stop_grace_period, the A-6 pause
  proof, and two independent crash scenarios in one linear `main()`.
  It remains readable today, but a Day 7 addition of further adversarial
  scenarios (e.g. a second dependency type, TLS handshake failures) would
  benefit from extracting each scenario into a named function the way
  `check_resource_limits_applied`/`check_restart_policy_applied`/etc.
  already are, rather than growing the inline `try` block in `main()`
  further. Flagged for awareness, not for action now.
- `compose.yaml`'s narrative comments (Day 3/Day 5 headers) are still
  accurate and have not been allowed to silently drift as later days
  added fields — this discipline should continue as Day 7 hardening
  potentially touches the same file.

## 13. Recommended remediation order

No Critical or High findings from the integration/runtime-parity lens.
No remediation is blocking this review's scope.

1. (Low, non-blocking) Confirm `scripts/lint/check_dockerfile.py` and
   `scripts/build/image_audit.py` genuinely cross-check
   `security/runtime-patches.lock`'s pinned URL/SHA256 against the
   Dockerfile's `security-patch` stage on every CI run (stated by the
   Dockerfile's own comments; this review read but did not independently
   re-derive the cross-check logic in those two scripts, which are
   outside this agent's assigned file list) — recommend the
   container-security-reviewer or release-engineer agent's review
   confirm this explicitly if it has not already.
2. (Info) Consider, in a future day, extracting `reliability_check.py`'s
   `main()` scenarios into named functions purely for long-term
   readability — no correctness impact today.

---

## Verdict

**APPROVE** — from the compose-platform-engineer / integration-parity
lens, Day 6's CI/CD delivery-plane changes (Buildx builder workaround,
`security-patch` Dockerfile stage, cgroup-update-race retry logic,
`ci.yml`/`release.yml` orchestration) introduced zero drift against every
Day 3-5 runtime/integration invariant in scope: topology, network
isolation, volume/persistence, config mounting, hardening flags, health/
readiness/ordering, resource limits, restart policy, and the A-6
timeout-hierarchy proof are all still genuinely enforced against real
Docker behavior, both on GitHub-hosted runners (confirmed via `gh run
view` on the actual CI logs) and independently reproduced on this
reviewer's own local Docker Engine. The one historical CI failure
relevant to this scope (run 32960673438) was a real, narrowly-diagnosed,
narrowly-fixed GitHub-runner-specific transient condition confined
entirely to the test harness's own retry logic, never leaking into
production/local runtime semantics or weakening any check.

No PRE-TAG conditions from this review. (See §13 item 1 for a
recommended cross-check outside this agent's own file scope, not a
blocking condition.)
