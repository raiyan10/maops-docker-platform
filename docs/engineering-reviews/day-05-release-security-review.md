# Day 5 Release + Security + Regression Review — v0.5.0

Repository: `maops-docker-platform`
Branch: `feature/day-5-health-reliability-resources`
Target: `v0.5.0`
Reviewer: independent RELEASE + SECURITY + REGRESSION reviewer (review
only — no implementation file was modified; no commit/push/tag/release
was performed by this review).
Date: 2026-08-25.

## Scope and method

This is the fifth Day 5 review document in this directory. Four sibling
reviews already exist and were read in full before this review began:
`day-05-health-timeout-review.md` (health/timeout/A-6),
`day-05-resource-restart-review.md` (resource limits/restart policy),
`day-05-failure-recovery-review.md` (crash/recovery/persistence
scenarios), and `day-05-test-adversarial-review.md` (test-suite quality
and a hazard-list adversarial read of `reliability_check.py`). All four
independently reached PASS with only Low/Medium, non-blocking findings.
This review does not re-derive their scenario-level claims from scratch;
instead it independently **re-runs the entire release validation chain
end to end on this machine** (not merely trusting the prior runs), checks
the specific release/security/regression items in its own brief (exact
counts, VERSION consistency, Distroless digest continuity from Day 4,
vulnerability policy and current counts, agent/skill counts, Day 6+ scope
leakage, and whether any carried-forward finding has been misdescribed as
closed), and cross-references the four sibling reviews' findings rather
than duplicating their scenario-by-scenario proofs.

## Environment note (not a Day 5 finding, consistent with all four sibling reviews)

This sandbox's default `docker` on `PATH` (`~/.local/bin/docker`) is a
WSL2-interop shim that does not forward environment variables to
`docker.exe`. Resolved identically to every sibling review: `/usr/bin`
(the native WSL2 binary) placed first on `PATH`, plus
`WSLENV=GATEWAY_HOST_PORT:VERSION` exported for the full validation run
below.

## Full independent validation run

`make release-check` (which composes `quality` [`test`, `lint`,
`dockerfile-check`, `compose-check`] + `build` + `inspect` +
`image-audit` + `smoke` + `security-check` + `compose-test` +
`reliability-check` + `reproducibility-check` + `sbom` + `sbom-check` +
`vuln-scan`, then `docker compose config`) was run fresh, end to end, on
this machine (`WSLENV=GATEWAY_HOST_PORT:VERSION` exported, `/usr/bin`
first on `PATH` — the same environment workaround all four sibling
reviews document), from a completely clean container/image state for the
release image itself (`docker buildx build --no-cache`, two more
from-scratch builds inside `reproducibility-check`). It completed with
**exit code 0**. `make supply-chain-check` was then re-run standalone
(re-deriving `sbom`/`sbom-check`/`vuln-scan` a second, independent time,
with identical results) and `docker compose config` was independently
re-run and parsed with a fresh YAML load, separate from the copy embedded
in the `release-check` output. `git diff --check` was run both against
the working tree and against `main` — clean (exit 0, no output) in both
directions.

## 1. Exact counts — every one independently re-derived, not read off a report

| Check | Required | Independently observed | Source |
|---|---:|---:|---|
| Unit tests | 359 | **359** (`Ran 359 tests in 51.195s ... OK`) | `make test` (part of `make quality`) |
| Dockerfile checks | 10 | **10/10** | `make dockerfile-check` |
| Compose structural checks | 17 | **17/17** | `make compose-check` |
| Image-audit checks | 19 | **19/19** | `make image-audit` |
| Security checks | 22 | **22/22** | `make security-check` |
| Compose integration checks | 58 | **58/58** | `make compose-test` |
| Reliability checks | 32 | **32/32** | `make reliability-check` |

All seven counts match the brief's expected totals exactly. This is the
second full independent re-derivation of the first six (the four sibling
reviews already collectively re-ran every one of them at least once); this
review adds nothing new to the count-reconciliation exercise except a
second confirmation from a completely fresh, from-scratch image build
(`--no-cache`, not reusing any layer from a prior review's build) rather
than the pre-existing on-disk image.

## 2. Topology — verified against `docker compose config`, not the source YAML

`docker compose config` was run standalone and its output parsed with a
fresh YAML load (independent of any check script in the repository):

- **Exactly 3 services**: `app`, `gateway`, `state`. Confirmed.
- **Exactly 2 networks**: `edge`, `backend`. Confirmed.
- **`backend.internal == True`**. Confirmed directly from the parsed
  rendered config (not merely the source `compose.yaml` literal), and
  independently corroborated by `compose_integration.py`'s own live
  `docker network inspect` proof (`backend.Internal=true, edge.Internal=false`).
- **Exactly 1 named volume**: `state_data`. Confirmed.
- **Only `gateway` has a `ports:` entry**, bound
  `host_ip: 127.0.0.1` (loopback, never `0.0.0.0`), `target: 8080`.
  `app` and `gateway` both have `ports: None` (not merely undeclared in
  source — absent from the fully rendered config). Confirmed both from
  the standalone parse and from `compose_integration.py`'s live
  `docker inspect` proof that `app`/`state` have no published host port
  and `gateway` is the sole host-published service.
- **One image, three roles**: all three services build from the same
  `docker/app/Dockerfile` and are tagged the identical
  `maops-docker-platform:0.5.0` in the rendered config; `image_audit.py`
  and `container_smoke.py`'s multi-role chain both independently confirm
  all three real containers run the exact same image ID
  (`sha256:c1b1183e7e28...`), differing only in `command:` (`-m state`
  / `-m app` / `-m gateway`). Confirmed.
- **Restart/grace-period policy, all three services**: `restart:
  on-failure:3`, `stop_grace_period: 10s` on `app`, `gateway`, `state`
  alike, confirmed both from the standalone rendered-config parse and
  from `reliability_check.py`'s real `HostConfig.RestartPolicy`/
  `Config.StopTimeout` proof against live containers (below).

## 3. Distroless runtime digest — unchanged from Day 4, independently verified

`docker/app/Dockerfile` has **zero diff** against `main` (`git diff main
--stat -- docker/app/Dockerfile` returns nothing) — Day 5 does not touch
the Dockerfile at all. Both pinned `FROM` digests match the Day 4-recorded
values exactly:

- Builder: `python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`
- Final runtime: `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`

`scripts/build/image_audit.py`'s `EXPECTED_FINAL_BASE_DIGEST` constant is
the identical value. The actual enforcement of this pin at build time
lives in `scripts/lint/check_dockerfile.py`'s `check_from()` (part of the
confirmed 10/10 `make dockerfile-check` checks above) — `image_audit.py`'s
own `check_final_base_is_approved_distroless()` remains the tautological
check already identified as Day 4 finding M-1 in
`day-04-image-security-review.md` (asserts `RootFS.Layers` is non-empty,
not that it actually matches the pinned digest); this is a **carried-forward,
not-fixed** Day 4 finding, correctly not claimed as closed anywhere in the
Day 5 docs reviewed (see §15 below) — see Finding L-1.

The release image ID produced by this review's own fresh, `--no-cache`
build (`sha256:c1b1183e7e28c540149cf0dcbf139b67a90f291327ed8837ae30614a3dd3ddcf`)
matches the pre-existing on-disk `maops-docker-platform:0.5.0` image
exactly, and matches both `reproducibility_check.py`'s Build A and Build B
image IDs (§9) — the base digest is unchanged, and the full derived image
is itself byte-for-byte reproducible on top of it.

## 4. Hardened runtime — [C] + [D] evidence, all three services

Independently confirmed from this review's own fresh `security-check` and
`reliability-check` runs, at the kernel/process level, not merely
declared configuration:

| Property | Evidence tier | Result |
|---|---|---|
| UID:GID 10001:10001 | [B] `Config.User` + [D] `/proc/1/status`-equivalent probe | `Config.User: '10001:10001'`; effective process `uid=10001 gid=10001` |
| Read-only rootfs | [C] `HostConfig.ReadonlyRootfs` + [D] real rejected write | `True`; real write attempt: `[Errno 30] Read-only file system`, service stayed healthy afterward |
| Zero capabilities | [C] `--cap-drop=ALL` + [D] real `/proc/1/status` capability sets | `CapEff=0000000000000000 CapPrm=0000000000000000 CapBnd=0000000000000000` |
| `NoNewPrivs` | [D] kernel flag read from inside the running container | `NoNewPrivs=1` |
| `/data` ownership (state only) | [AUDIT:image-policy] | owned `10001:10001`, writable by the runtime UID (the one deliberate exception) |

All five hold on all three real, Compose-managed containers (`state`,
`app`, `gateway`), confirmed in both the `security-check` run (single
`app`-role container) and the `reliability-check`/`compose-test` runs
(all three roles simultaneously). No regression from Day 4's hardening
baseline.

## 5. Role-specific health / H-1 3×3 matrix

Live-reproduced in this review's own `compose-test` run, exact match to
all four sibling reviews and to the Day 4 H-1 remediation review's
original proof:

```
state: app=FAIL, gateway=FAIL, state=PASS
app: app=PASS, gateway=FAIL, state=FAIL
gateway: app=FAIL, gateway=PASS, state=FAIL
```

Each container's own healthcheck module accepts only its own role, on
every one of the 9 cells — no regression in the mechanism that closed Day
4 finding H-1.

## 6. Day 5 resource limits, restart policy, stop_grace_period — applied to real containers

Independently confirmed via this review's own `reliability-check` run,
`[C]` (`docker inspect ... HostConfig`) and, where available, `[D]`
(cgroup v2 files), on all three real containers:

- `cpus<=0.5, mem<=134217728B (128MiB), pids<=64` — `state`/`app`/`gateway`
  all measured at exactly `cpus=0.5 memory=134217728 pids_limit=64`.
  Cross-checked at the `[D]` cgroup v2 level: `memory.max='134217728'
  pids.max='64' cpu.max='50000 100000'` — identical on all three.
- `HostConfig.RestartPolicy == {Name: 'on-failure', MaximumRetryCount: 3}`
  on all three real containers.
- `Config.StopTimeout == 10` (seconds) on all three real containers,
  independently corroborated by a real `docker stop` completing in
  `0.53s`/`0.65s` (two separate measurements in this run), well inside the
  10s window.
- An intentional `docker stop` was independently reconfirmed to **not**
  trigger the `on-failure` restart policy (`stayed_stopped=True,
  restart_count before=0 after=0`).

## 7. A-6 timeout hierarchy — closed, independently reproduced

This review's own live `docker pause state` run against the real,
currently-shipped `config/platform.json`
(`state_dependency_timeout_seconds=2.0`,
`gateway_upstream_timeout_seconds=5.0`, `timeout_safety_margin_seconds=1.0`):
a state-dependent request through `gateway -> app -> state` completed in
**2.01s** — genuinely governed by the inner 2.0s timeout, comfortably
inside the outer 5.0s budget, with no raw traceback in the response body.
This matches, to the millisecond, the independent measurement in
`day-05-health-timeout-review.md`'s own full-script run (`2.01s`) and
sits within that review's own 5-trial repeated-probe range
(2.008s-2.035s). No evidence of flakiness or drift across this review's
own additional run. A-6 remains closed.

## 8. Real failure/recovery scenarios — re-confirmed live, not re-derived from source

This review's own fresh `reliability-check` run reproduced all three
lifecycle scenarios end to end, matching the failure-recovery review's
independently-derived claims exactly:

- **Transient failure** (real kernel OOM-kill, `/proc/1/oom_score_adj`
  maxed + sibling memory pressure, `mem_limit` never touched): `docker
  exec` returned `137` (the documented success signature), `state`
  automatically restarted and became healthy again with **no manual
  `docker start` anywhere in the path**, `gateway`'s `/readyz` recovered
  automatically, persisted value survived (`value=1` after a real
  increment).
- **Persistent failure** (`docker update --memory 6m --memory-swap 6m`,
  kept lowered): bounded retry exhaustion at exactly `RestartCount == 3`
  (`on-failure:3` — never more), memory limit restored via the `finally`
  block, then an explicit, clearly-labeled **operator** `docker compose
  start state` recovered the service — never described as automatic.
  Persisted value survived unchanged across the whole scenario
  (`before=1 after_recovery=1`).
- **Intentional stop**: `docker stop` exited cleanly (`ExitCode=0`,
  `0.65s`, well inside the 10s grace period) and did **not** trigger
  `on-failure` (`restart_count before=0 after=0`); recovered via an
  explicit `docker compose start`, persisted value unchanged (`value=2`).
- **`app`-down / `gateway`-down isolation**: `gateway`'s `/healthz` stayed
  `200` while `app` was stopped, `/readyz` correctly degraded and
  recovered; `app` and `state` were both confirmed completely unaffected
  (`app.healthcheck` + `state.healthcheck` both still `PASS`) while
  `gateway` was stopped, and the full chain recovered after restart.

Full run: **32/32 PASS**. All disposable resources (`maops-reliability-*`
containers/network/volume) confirmed removed after the run; the
pre-existing, unrelated dev stack (`maops-docker-platform-*`, default
project, `127.0.0.1:8080`) was confirmed untouched throughout (same three
containers, same "Up 2 hours" uptime, before and after this review's
entire validation run).

## 9. Reproducibility — STRONG, independently re-confirmed

`reproducibility_check.py`, run fresh by this review (two more
`--no-cache` builds, on top of the one already performed for `make
build`/`make inspect`/`make image-audit`/`make smoke` above — three
independent from-scratch builds total in this one review session, all
producing the identical image ID):

| Check | Result |
|---|---|
| Exact image ID equality (Build A == Build B) | **PASS** — both `sha256:c1b1183e7e28c540149cf0dcbf139b67a90f291327ed8837ae30614a3dd3ddcf`, and identical to this review's separate `make build` output |
| RootFS diff-ID equality | **PASS** |
| Config/OCI-label equality | **PASS** |
| Normalized filesystem manifest equality | **PASS** (24 entries) |

All four independent equality axes pass — **STRONG** evidence level,
matching the label `reproducibility_check.py` itself prints and matching
the Day 4 baseline's own STRONG classification. No regression.

## 10. Vulnerability policy — unweakened, current counts independently verified

`scripts/security/check_trivy_report.py::evaluate_policy()` implements
exactly the required policy, confirmed by direct source read:

- any `CRITICAL` finding -> `FAIL`
- any `HIGH` finding **with** a `FixedVersion` -> `FAIL`
- `HIGH` findings **without** a fix -> reported prominently, non-blocking
- lower severities -> reported, never a release blocker

No `.trivyignore` exists anywhere in the tree (confirmed by directory
listing); no suppression mechanism of any kind found in
`vuln_scan.py`/`check_trivy_report.py`.

This review ran `vuln_scan.py` (via Trivy `0.74.0`, pinned by exact digest
`sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969` —
the identical pin `security/scanners.lock` records, confirmed unmodified
by this branch via `git diff main -- security/scanners.lock`, empty)
**twice** in this session (once inside `make release-check`, once again
standalone via `make supply-chain-check`), against a fresh `docker save`
archive of the exact release image, never the live daemon or Docker
socket. Both runs produced identical results:

| Severity | Count | Policy outcome |
|---|---:|---|
| CRITICAL | **0** | PASS (any would FAIL) |
| HIGH, fixable | **0** | PASS (any would FAIL) |
| HIGH, unfixed | **15** | reported, non-blocking |
| MEDIUM | 52 | reported, non-blocking |
| LOW | 51 | reported, non-blocking |
| UNKNOWN | 6 | reported, non-blocking |

The unfixed-High **count** (15) matches the Day 4 baseline
(`day-04-release-readiness-final.md`, §12: "Critical 0 / High fixable 0 /
High unfixed 15") exactly, though the *specific* CVE IDs differ (e.g.
`CVE-2026-11940`, `CVE-2026-15308`, `CVE-2026-7210` now appear where Day
4's scan reported a different set) — expected and correctly disclosed by
the tool itself (`vuln_scan.py`'s own printed note: "vulnerability
*results* are time-varying (live Trivy DB); the image itself is
deterministic ... but a later scan of this exact image may report
different CVEs"). All 15 unfixed-High findings attribute to Debian 13
"trixie" base-OS packages (`libpython3.13-*`, `python3.13-*`, `libssl3t64`,
`libncursesw6`/`libtinfo6`) with no vendor fix currently published — same
package families as the Day 4 baseline. No CRITICAL, no fixable HIGH: the
release-blocking policy passes on its own terms, not because the policy
was weakened.

## 11. Agents and skills

- **5 Claude agents** confirmed (`.claude/agents/*.md`):
  `compose-platform-engineer`, `container-security-reviewer`,
  `docker-architect`, `docker-test-engineer`, `release-engineer`.
- **4 Claude skills** confirmed (`.claude/skills/*/`):
  `compose-validation`, `container-security-validation`,
  `docker-build-validation`, `release-readiness`. Matches
  `.claude/CLAUDE.md`'s own inventory exactly; no new agent or skill
  directory was added or removed this branch (`git diff main --stat`
  shows only content edits to the five existing agent files and three of
  the four existing skill files — `docker-build-validation/SKILL.md` is
  untouched this branch).

## 12. Day 6+ scope-leakage check

Repository-wide grep for `.github/workflows`, CI/CD implementation,
registry publication, Cosign, SLSA, Kubernetes, Prometheus, Grafana, and
an OpenTelemetry collector — confirmed clean:

- **No `.github/` directory exists at all** (`ls -la .github` ->
  "no .github directory").
- Every other hit is either (a) a docstring/doc passage in
  `docs/build-security.md`/`docs/roadmap.md`/`docs/reliability.md`
  explicitly stating these are **not** implemented and are Day 6+ scope,
  or (b) historical text inside prior (Day 1-4) review documents
  discussing what earlier scope-boundary greps found clean at the time —
  none of it inside `app/`, `gateway/`, `state/`, `scripts/`,
  `compose.yaml`, or the `Makefile`.
- `docs/roadmap.md`'s own Day 5 section (§ "Day 5 — Health, reliability,
  resource controls") explicitly states: "Still no CI, no container
  registry, no cryptographic build provenance/attestation/signing, no
  metrics/tracing/log-aggregation observability stack." `docs/reliability.md`'s
  own "What Day 5 deliberately does not cover" section independently
  states the same boundary from the implementation side.

No scope leakage found. Day 5 stays inside its declared boundary.

## 13. `git diff --check`

Run both against the working tree (`git diff --check`) and against
`main` (`git diff main --check`) — **both clean, exit 0, no output**. No
trailing-whitespace or conflict-marker regressions introduced by this
branch.

## 14. Documentation consistency

`README.md`, `docs/roadmap.md`, `docs/reliability.md`, `docs/security.md`,
and `docs/compose-platform.md` were cross-read for internal consistency
against every count/claim independently re-verified above (359 tests,
17/58/19/22/32 check counts, the A-6 2.0s/5.0s/1.0s figures, the
`state_dependency_timeout_seconds`/`gateway_upstream_timeout_seconds`/
`timeout_safety_margin_seconds` field names, the `cpus`/`mem_limit`/
`pids_limit`/`restart`/`stop_grace_period` values, and the H-1 3x3 matrix
description). No discrepancy found between any doc and this review's own
independently-measured evidence. `config/platform.json`'s live values
(`2.0`/`5.0`/`1.0`) match `docs/reliability.md`'s worked example exactly.

## 15. Carried-forward Medium/Low findings — not misdescribed as closed

Cross-checked every Day 4 carried-forward finding listed in
`day-04-release-readiness-final.md` §15 against current repository state
and against every Day 5 document (`docs/reliability.md`,
`docs/roadmap.md`'s Day 5 section, this directory's four sibling Day 5
reviews) for any claim that one of them is now closed:

| Day 4 carried-forward finding | Still present/open? | Misdescribed as closed anywhere in Day 5 docs? |
|---|---|---|
| `image_audit.py` base-check evidence-quality gap (tautological `check_final_base_is_approved_distroless`) | Yes — confirmed still tautological by direct source read (§3 above) | No |
| `image_audit.py` missing unit tests | Yes — `grep -rl image_audit tests/` still returns nothing | No |
| `image_audit.py` source-immutability probe scoped to `app/` only | Not independently re-probed this session (unchanged code, not this review's scope) | No |
| `check_trivy_report.py` malformed-report-shape/severity-case gaps | Not independently re-probed this session (unchanged code) | No |
| `check_dockerfile.py` untested branches | Not independently re-probed this session (unchanged code) | No |
| Reproducibility manifest's untested uid/gid axis | Not independently re-probed this session (unchanged code) | No |
| M-1 (H-1-remediation): `*RoleDiscriminationTests` malformed-input gap | Not independently re-probed this session (unchanged code) | No |
| L-1 (H-1-remediation): `docs/roadmap.md`/`docs/compose-platform.md` doc-wording gap re: A-2's closing mechanism | Current text of both files (unchanged since commit `403d609`) already names the closing mechanism (the `/healthz` `role` field + `EXPECTED_ROLE` check) and cross-references `docs/security.md`'s "Role-aware liveness" section — this finding appears to already be satisfied by the existing text, though no adjudication document has explicitly recorded it as closed. **Info, not a Day 5 concern either way** — flagged for a future adjudication pass to reconcile, not a Day 5 regression. | No — not claimed closed anywhere; if anything, current text is more complete than the finding's own snapshot described |

None of the eight Day 4 carried-forward items is claimed closed by any
Day 5 document. All eight remain honestly out of scope for this release
(none is Day 5 work), consistent with `.claude/CLAUDE.md`'s instruction
not to implement later-day scope early or retroactively re-litigate
settled findings.

Also cross-checked the four sibling Day 5 reviews' own findings
(M-1/L-1/L-2 in `day-05-health-timeout-review.md`; I-1/M-1/L-1 in
`day-05-resource-restart-review.md`; L-1/L-2 in
`day-05-failure-recovery-review.md`; M-1/L-1/L-2 in
`day-05-test-adversarial-review.md`) — none of them is described as
closed or fixed anywhere in `docs/reliability.md`, `docs/roadmap.md`, or
`README.md`; all remain open, non-blocking, and correctly un-actioned in
the shipped documentation. No misrepresentation found.

## 16. Cleanup — this review's own resource discipline

Every disposable resource this review created or triggered (`make build`'s
throwaway `.cache/build/` tarball, three `--no-cache` builds' worth of
`maops-repro-*`/`maops-smoke-*`/`maops-security-*`/`maops-image-audit-*`/
`maops-compose-*`/`maops-reliability-*` containers/networks/volumes, two
`docker save` archives from the two `vuln_scan.py` invocations) was
confirmed fully removed after the run:

```
docker ps -a --filter 'name=^maops-reliability-'  -> empty
docker ps -a --filter 'name=^maops-repro-'         -> empty
docker ps -a --filter 'name=^maops-compose-'       -> empty
docker ps -a --filter 'name=^maops-smoke-'/'security-'/'image-audit-' -> empty
docker images --filter 'reference=maops-repro-*'   -> empty
docker network ls --filter 'name=^maops-'          -> only the pre-existing dev
                                                       stack's own edge/backend
                                                       networks (not review-created)
docker volume ls --filter 'name=maops-reliability' -> empty
docker volume ls --filter 'name=maops-compose'     -> empty
```

The pre-existing, unrelated dev stack (`maops-docker-platform-{app,gateway,state}-1`,
default project, `127.0.0.1:8080`) was confirmed running the same three
containers with the same "Up 2 hours" uptime both before and after this
review's entire validation run — never touched, never restarted. Only
`artifacts/sbom/` and `artifacts/security/` gained new (git-ignored,
intentionally-persisted-per-CLAUDE.md) generated output; nothing under
version control was modified by this review except the creation of this
one document. No global prune used at any point.

## Findings

### L-1 (Low — carried forward from Day 4, not a Day 5 regression): `image_audit.py`'s Distroless base-digest check remains tautological

`check_final_base_is_approved_distroless()` still only asserts
`RootFS.Layers` is non-empty rather than actually comparing against
`EXPECTED_FINAL_BASE_DIGEST` — independently re-confirmed by direct source
read this session (§3). This is the identical Day 4 finding M-1 from
`day-04-image-security-review.md`, carried forward and honestly disclosed
as still-open in `day-04-release-readiness-final.md` §15. Not a Day 5
regression (the file is untouched by this branch), not release-blocking
(the real enforcement lives in `check_dockerfile.py::check_from()`,
confirmed correct and exercised as part of the passing 10/10
`dockerfile-check` result). Recorded here only because this review's
brief specifically asked whether carried-forward findings remain honestly
described — this one does.

### Info: Day 4 L-1 (doc-wording gap re: A-2's closing mechanism) appears already satisfied by current text

`day-04-release-readiness-final.md` §15 carries forward a finding that
`docs/roadmap.md`/`docs/compose-platform.md` "omit the actual closing
mechanism" for Day 3 finding A-2. Independently re-read this session: both
files' current text (unchanged since commit `403d609`, i.e. predating even
that adjudication review) already states the real closing mechanism (the
Day 4 H-1 `/healthz` `role` field + `EXPECTED_ROLE` check) and
cross-references `docs/security.md`. This is not a Day 5 concern in
either direction — no Day 5 document claims this finding closed, and no
Day 5 document repeats the stale gap either. Recorded only for a future
adjudication pass to reconcile the tracking, not as a finding against this
release.

### Info: Trivy unfixed-High CVE identities shifted since Day 4, count unchanged

The exact CVE IDs behind the 15 unfixed-High findings differ from Day 4's
scan (expected, per `vuln_scan.py`'s own disclosed time-varying-database
caveat — the image is deterministic, the vulnerability database is not).
The count (15) and the affected package families (Debian 13 "trixie"
system libraries: `libpython3.13-*`, `python3.13-*`, `libssl3t64`,
`libncursesw6`/`libtinfo6`) are unchanged in character from the Day 4
baseline. Not a regression, not a new release risk — recorded because the
review brief specifically asked for current, not assumed, vulnerability
counts.

## Release-blocker status

**None.** Every exact count in the brief was independently re-derived and
matches exactly. VERSION=0.5.0 is consistent everywhere it appears. The
Compose topology (3 services, 2 networks, `backend.internal=true`, 1 named
volume, loopback-only single host-published port, one image/three roles)
matches the brief precisely, verified against the rendered
`docker compose config` output, not merely the source YAML. The Distroless
runtime digest is byte-identical to Day 4's pin, and the derived release
image is independently reproducible (STRONG, all four equality axes) from
three separate from-scratch builds performed in this one review session.
UID/GID 10001:10001, read-only rootfs, zero capabilities, and
`NoNewPrivs` are all proven at the kernel/process ([D]) level, not merely
declared. The H-1 3x3 matrix, the Day 5 resource/restart/grace-period
controls, and the A-6 timeout hierarchy are all proven against real,
live Docker containers in this session, not read off a prior report. The
vulnerability policy is unweakened (no `.trivyignore`, no suppression
mechanism found) and currently passes on its own real terms: 0 Critical,
0 fixable High, 15 unfixed High (all Debian-base, no vendor fix
available) — an honest, non-blocking disclosure, not a gap. No Day 6+
scope (CI, registry, Cosign, SLSA, Kubernetes, Prometheus, Grafana,
OpenTelemetry) has leaked into this branch. The agent/skill inventory
(5/4) matches `.claude/CLAUDE.md` exactly. Every carried-forward Day 4
finding remains honestly described as open, not misrepresented as closed,
anywhere in the Day 5 documentation reviewed. The one finding this review
adds (L-1) is a Day 4 carried-forward item re-confirmed still open, not a
new or Day-5-introduced defect.

## Final review verdict

This review independently re-ran the entire release validation chain end
to end — `make release-check` (quality, three separate from-scratch image
builds, image-audit, smoke, security-check, compose-test,
reliability-check, reproducibility-check, sbom, sbom-check, vuln-scan),
plus a standalone `make supply-chain-check`, a standalone
`docker compose config`, and `git diff --check` against both the working
tree and `main` — on this machine, from a cold image cache, and every
exact count, every hardening property, every reliability scenario, the
A-6 timing, and the reproducibility/vulnerability evidence matched the
brief and the four sibling Day 5 reviews' own independent findings
exactly. No Critical or High finding. No scope leakage. No misdescribed
carried-forward finding. All review-created Docker resources were
confirmed fully cleaned up, and the pre-existing, unrelated dev stack was
left untouched throughout.

RELEASE-SECURITY REVIEW PASS

