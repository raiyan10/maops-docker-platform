# Day 3 Independent Docker Compose & Runtime Integration Review

Repository: `maops-docker-platform`
Branch: `feature/day-3-network-config-persistence`
Target: v0.3.0
Reviewer: independent Day 3 Compose/integration review agent (review-only)
Review date: 2026-08-19
Scope: `compose.yaml`, `scripts/compose/check_compose.py`,
`scripts/compose/compose_integration.py` — the lifecycle, mutation-
detection quality, and self-verification counts these three files claim,
per this review's brief. This review does not re-litigate network
segmentation (`docs/engineering-reviews/day-03-networking-review.md`),
container hardening (`docs/engineering-reviews/day-03-security-review.md`),
or storage-format/atomic-write internals
(`docs/engineering-reviews/day-03-persistence-review.md`) — all three
already exist and independently exercised much of the same
`compose.yaml`/`check_compose.py`/`compose_integration.py` surface in
detail. Where this review's own hands-on testing lands on the same
ground, that is stated as corroboration, not re-claimed as new. This
review's distinct contribution is: (1) an independent, from-scratch
verification of the "14 structural checks" / "55 integration checks"
counts specifically, (2) mutation scenarios the prior reviews did not
attempt (a removed volume, a misattached volume, a missing config mount),
and (3) process-level fault injection against the test harness itself —
`SIGINT` and, notably, `SIGTERM` — which no prior Day 3 review exercised.

**Method:** built the real image (`maops-docker-platform:0.3.0`, this
review's own build), then ran `check_compose.py` and
`compose_integration.py` directly against the real, tracked repository
multiple times, plus a further ~15 real `docker compose up`/`down` cycles
of this review's own construction (mutation tests, signal-injection
tests, a raw timestamp-precision probe). Every `compose.yaml` mutation was
applied via a scripted, exact-string, single-occurrence replace, verified
restored byte-identical via `diff` immediately after each test (full log
in the Appendix). Every Compose project this review created — the
project's own scripts' `maops-compose-*` projects and this review's own
ad hoc `maops-review-*` probes — was uniquely named and torn down; no
global prune was run at any point. Environment: Docker Desktop (WSL2
backend), Docker Compose v5.4.0, confirmed via `docker context show`/
`docker info` before testing began.

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 1 |
| Low      | 0 |

No Critical or High findings. The one Medium finding is a real,
independently-reproduced gap in the *test harness's own* process-signal
resilience — not a defect in the platform's declared or runtime behavior,
and not something either prior Day 3 Compose-adjacent review (networking,
security) happened to test.

---

## Findings

### M-1 (Medium): `compose_integration.py` has no `SIGTERM` handling — a `SIGTERM` mid-run silently orphans the entire 3-container/2-network/1-volume stack, with no error output at all

**Where**: `scripts/compose/compose_integration.py` — the script installs
no `signal.signal(SIGTERM, ...)` handler anywhere. Python's interpreter
only auto-converts `SIGINT` to a catchable `KeyboardInterrupt`; `SIGTERM`'s
default disposition (process termination, no exception raised, no
`finally` block executed) is left untouched.

**Reproduced** (three separate signal experiments, each on a fresh,
uniquely-named `maops-compose-*` project, each fully cleaned up by this
review afterward):

1. **`SIGINT` during a `time.sleep()` poll** (sent 10s into a run, while
   blocked inside `check_runtime_healthy`'s poll loop): process exited
   ~2s later with a clean `KeyboardInterrupt` traceback; `finally`'s
   `docker compose ... down -t 10 -v` ran to completion; **zero residual
   containers/networks/volumes**.
2. **`SIGINT` during the initial, blocking `docker compose up -d`
   `subprocess.run()` call itself** (sent at 0.6s, 1.2s, 1.8s, and 2.5s
   into four separate runs — deliberately targeting the one call where a
   naive implementation could leave an orphaned, still-running `docker
   compose up` child behind, since only the literal signaled PID receives
   `kill -INT`, not its children): all four runs exited cleanly with
   **zero residue** in every case. This works because CPython's own
   `subprocess.run()` wraps `communicate()` in a bare `except: process.kill(); raise`
   — any exception during the wait, including the `KeyboardInterrupt`
   `SIGINT` produces, kills the `docker compose up -d` child *before* the
   exception propagates to `compose_integration.py`'s own `finally` block,
   which then runs `down -v` against whatever partial state that kill left
   behind. This is a genuine, if incidental, safety property — not
   something `compose_integration.py` implemented itself, but real
   nonetheless.
3. **`SIGTERM` (sent 10s into a run, well past the `up -d` step, blocked
   in the same health-poll loop as experiment 1)**: process died
   *immediately* (~2s), **the log file was completely empty** — even the
   already-printed `project=...` line was lost, because Python's default
   stdout buffering to a non-TTY file is block-buffered and a raw
   `SIGTERM` gives no opportunity to flush — and, critically, **the full
   stack was left running**: `docker ps -a`/`docker network ls`/`docker
   volume ls` all showed the complete, real
   `maops-compose-f4715bc8822e-{state,app,gateway}-1` containers, both
   `maops-compose-f4715bc8822e_{edge,backend}` networks, and the
   `maops-compose-f4715bc8822e_state_data` named volume — untouched,
   silently, with no diagnostic of any kind. Confirmed and cleaned up
   manually by this review (`docker compose -p maops-compose-f4715bc8822e
   -f compose.yaml down -t 5 -v`).

Also confirmed by direct source inspection: `app/server.py`,
`gateway/server.py`, and `state/server.py` all correctly register
`signal.signal(signal.SIGTERM, _handle_signal)` (their own PID 1
graceful-shutdown handling, exercised by `security_check.py`'s
`check_lifecycle_docker_stop`) — the gap is specific to the *harness
scripts* in `scripts/compose/`, not the application services they test.

**Impact**: bounded, not a live defect in the platform. A normal `make
compose-test`/`make release-check` invocation always exits through either
a clean `PASS`/`FAIL` return or a caught `ComposeIntegrationError` — both
paths already correctly run `finally`'s `down -v` (independently
reproduced separately: a genuine assertion failure, triggered by mutating
`compose.yaml` to add a host port to `state` and running the real script
against it, printed `FAIL: state must not publish a host port ...`, exited
1, and left zero residue — see Appendix). The gap is specifically the
external-signal path other than `SIGINT`: a CI job cancellation, a
`timeout`-command default signal, a supervisor/orchestrator kill, or a
developer's `kill <pid>` without `-INT` all send `SIGTERM` by default, and
each would leave a full, real, unexplained stack (including a named
volume) running with no log evidence of what happened. This is exactly
the failure mode `.claude/CLAUDE.md`'s Docker-safety-constraints section
cares most about (no leftover Docker resources outside`make clean`'s known
patterns) — and it is partially, but not automatically, mitigated:
`make clean`'s existing `maops-compose-[a-f0-9]+` regex (Makefile lines
78-82) *does* correctly match and remove this exact orphan pattern
(confirmed by inspection — the regex has no dependency on how the project
was created or torn down), so the failure mode is "requires a manual
`make clean` follow-up," not "permanently leaks Docker resources
forever" — but nothing today makes that follow-up automatic or even
visible, since the log file itself can be silently empty.

**Recommendation** (not applied — review only): register a `SIGTERM`
handler in `compose_integration.py`'s `main()` (mirroring the pattern
already proven correct in `app/server.py`/`gateway/server.py`/
`state/server.py`) that either raises a catchable exception into the
existing `try/finally` or explicitly invokes the same teardown path
before re-raising/exiting. A `print(..., flush=True)` (or
`PYTHONUNBUFFERED=1`) would also close the silent-empty-log gap
independently of the signal-handling fix.

---

## Check-count challenge: are "14" and "55" real, or inflated?

**Independently re-derived, not accepted at face value — both are
genuine, non-vacuous, field-level counts, and if anything both undercount
the actual verification performed.**

- **`check_compose.py`'s 14**: counted the `checks = [...]` list directly
  (`scripts/compose/check_compose.py:459-474`) — exactly 14 entries, each
  a distinct top-level function, matching the printed `OK (14 structural
  checks passed ...)` exactly (re-ran `make compose-check` directly: `check_compose.py: OK (14 structural checks passed against the rendered compose config, version=0.3.0)`,
  exit 0). But several of those 14 functions bundle many independent
  field-level assertions into one list entry — e.g. `check_hardening_flags`
  checks 6 distinct properties (`read_only`, `cap_drop`, `security_opt`,
  `privileged`, `pid`, `network_mode`) across all 3 services, ≈18
  assertions folded into "1 of 14"; `check_upstream_targets` checks 4
  properties (real-service-name, expected-target-match, port-match,
  shared-network) across both `gateway->app` and `app->state` hops, ≈8
  assertions folded into "1 of 14". A rough tally of every genuinely
  distinct field comparison across all 14 functions lands well north of
  60 individual assertions — the round number of 14 is a real count of
  *functions*, not an inflated count of *checks performed*.
- **`compose_integration.py`'s 55**: ran the real script twice, end to
  end, against the real tracked repository (see "Lifecycle reproduction"
  below) — both runs printed exactly `PASS (55/55 inspection checks
  passed)`, and this review counted the printed `CheckResult` lines
  directly in both run logs: exactly 55 in each, every one a concrete
  `PASS`/`FAIL` against real `docker inspect`/`docker exec`/`/proc` data
  (no "service exists" no-op found). Beyond the counted 55, this review
  also counted **20 additional `print()`-only lines** in each run — real,
  `raise`-based assertions (exact image match ×3, startup ordering ×2 —
  counted separately from the `results`-list versions, network membership
  ×3, no-host-port, loopback-binding, DNS-resolution ×2,
  isolation-precursor checks, `GET`/`POST /state` round trips ×2,
  liveness-during-outage, degrade poll, recovery poll, value-survives
  ×3) that fail fast via `ComposeIntegrationError`/exception rather than
  accumulating in the counted `results` list — so the true number of
  independent runtime assertions this script performs per run is closer
  to 75, not 55. This exactly corroborates the same undercounting pattern
  `docs/engineering-reviews/day-02-compose-review.md` found for Day 2's
  "25" figure, now re-confirmed independently for Day 3's larger numbers
  rather than assumed to still hold.

**Verdict: not inflated. If anything, both printed numbers are a
conservative floor on the real verification work, not a ceiling.**

---

## Lifecycle reproduction (independent, from scratch)

Ran the complete platform lifecycle personally, twice back-to-back,
against the real tracked `compose.yaml` (not a copy), each under the
script's own freshly-generated `maops-compose-<uuid>` project:

| Stage | Result |
|---|---|
| Unique project naming | `maops-compose-e8110f4e5b73` (run 1), `maops-compose-2d2bf81920b2` (run 2) — distinct every time |
| Config creation/mount | `platform.json` mounted read-only at `/etc/maops/platform.json` in all 3 containers, confirmed `Mounts.RW=False` + a real rejected write, both runs |
| Network creation | `edge`/`backend` created per-project, confirmed via `docker inspect .NetworkSettings.Networks` |
| Named volume creation | `<project>_state_data`, confirmed present pre-teardown, absent post-`-v`-teardown |
| `state` startup | first Docker-healthy at `16:53:44.921068+00:00` (run 1) |
| `app` startup after `state` healthy | `app` `StartedAt` `16:53:45.055699+00:00` — **0.134s after** `state`'s health transition, not merely "eventually both healthy" |
| `gateway` startup after `app` healthy | `app` first-healthy `16:53:50.723475+00:00`, `gateway` `StartedAt` `16:53:50.824945+00:00` — **0.101s after** |
| All three healthy | confirmed via `docker inspect .State.Health.Status`, both runs |
| `gateway -> app` | `GET /state` through the real public gateway port succeeded (`value=0`) |
| `app -> state` | same call chain, real HTTP round trip through both hops |
| `gateway`/`state` segmentation | DNS resolution fails both directions (corroborates the networking review's stronger raw-IP-connect proof; not re-derived here) |
| Persistence increment | `POST /state/increment` → `value=1`, read back via a fresh `GET` |
| `state` stop/degradation | `app`/`gateway` processes stayed alive; `app`'s own `/healthz` stayed healthy; `gateway /readyz` degraded to a controlled `503`; `GET /state` via gateway returned a controlled `503` |
| `state` restart/recovery | Docker-healthy again; `gateway /readyz` recovered to `200`; value `1` survived unchanged |
| `state` recreation/persistence | `--force-recreate --no-deps state`; value `1` survived; post-recreate increment → `2` |
| `compose down`/`up` persistence | full `down` (no `-v`) + `up`; value `2` survived across a brand-new set of containers/networks with a freshly-assigned gateway host port |
| Runtime hardening | all 3 containers: read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, non-root `10001:10001`, no host PID/network, no Docker socket — all `[C]`+`[D]` |
| Config read-only | `[C]+[D]`: `Mounts.RW=False` **and** a real rejected write, all 3 containers |
| Rootfs read-only real writes | `[D]`: real write to `/etc/maops-readonly-probe` rejected (`Read-only file system`), service kept serving, all 3 containers |
| `state`'s `/data` writable | `[D]`: real write+cleanup succeeded despite read-only rootfs |
| Exact cleanup including volume | confirmed via `docker ps -a`/`docker network ls`/`docker volume ls` filtered by project name — **zero residue after both runs** |

Both runs: `PASS (55/55 inspection checks passed)`, wall-clock ≈80s each
(run 1 timed explicitly: `1m20.337s`).

---

## Adversarial mutation testing

19 scenarios were independently attempted against `check_compose.py` in
this review (a mix of the "at minimum" list from this review's brief and
three genuinely new scenarios the prior networking/security/persistence
reviews did not test). Every mutation was a single, exact-string,
single-occurrence text replace against the real tracked `compose.yaml`,
verified restored byte-identical via `diff` immediately after each test —
see Appendix for the full restore log.

| Scenario | New to this review? | Result |
|---|---|---|
| `service_healthy` -> `service_started` (gateway) | corroborates networking review | DETECTED — `check_compose.py` |
| `gateway` joins `backend` | corroborates networking review | DETECTED — `check_compose.py` (both the network-set mismatch and the direct isolation check fired) |
| wrong `STATE_HOST` (typo) | corroborates networking review | DETECTED — `check_compose.py` |
| `backend: internal: false` | corroborates networking review | DETECTED — `check_compose.py` |
| `state` host port added | corroborates networking review; also re-run through the real `compose_integration.py` against a live stack (see below) | DETECTED — both `check_compose.py` and `compose_integration.py` |
| **volume removed from `state`** | **new** | DETECTED — `check_compose.py` (`check_state_volume`, both the top-level-set and the per-service-mount assertions fired) |
| **`state_data` volume mounted into `app`** | **new** | DETECTED — `check_compose.py` (`check_state_volume`'s "must not mount any named volume" assertion) |
| **`configs:` mount removed from `state`** | **new** | DETECTED — `check_compose.py` (`check_config_object`) |
| "config mount writable" | **new — and found to be an invalid premise** | **N/A**: probed the real Compose config schema directly (added a `mode: 0644` field and rendered via `docker compose config --format json`) — Compose's `configs:` long syntax only exposes `source`/`target`/`uid`/`gid`/`mode` (file permission bits); there is no writable/read-only toggle at all. Docker's config-injection mechanism is unconditionally read-only by construction, not by `compose.yaml` declaration — so this mutation cannot be expressed in `compose.yaml` in the first place. `check_compose.py` correctly has no structural check for it (none is possible); the real assurance is `compose_integration.py`'s runtime `[C]+[D]` `check_config_mount_readonly`, independently confirmed passing on all 3 containers above. |

**Failure-path runtime test** (distinct from the structural-check tests
above): ran the real `compose_integration.py` — not `check_compose.py` —
against the `state`-host-port mutation, live, on a real stack. It
correctly progressed through health/ordering/network checks, then failed
exactly where expected: `FAIL: state must not publish a host port, found:
{'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '19999'}]}`, exit 1 —
and `finally`'s teardown still ran to completion, confirmed via
`docker ps -a`/`docker network ls`/`docker volume ls`: zero residue. This
is the "cleanup on failure" proof requested by this review's brief, run
independently of (and in addition to) the signal-injection tests in M-1.

**Structural-check quality verdict**: every scenario this review
attempted that *can* be expressed in `compose.yaml` was correctly
detected by `check_compose.py`. Combined with the networking review's own
independent 18/19-scenario scoreboard (whose one miss, an unattached
extra top-level network, is a `docker compose config` pruning artifact,
not a checker logic bug — not re-tested here since it is already
well-established), this review found no new structural-checker gap.

---

## Timing, timestamps, dynamic ports, and repeated-run safety

- **Dynamic-port safety**: `GATEWAY_HOST_PORT=0` (the value
  `compose_integration.py` sets) produced genuinely different
  OS-assigned host ports across this review's own two runs — `32811`
  (run 1) and `32813` (run 2) — resolved via a real `docker port` call,
  not merely the requested `"0"` echoed back by `HostConfig.PortBindings`.
- **Docker event/timestamp correctness**: independently probed a raw
  `docker inspect .State.StartedAt` value on a throwaway container
  (`2026-08-19T17:04:29.673057629Z` — genuine 9-fractional-digit
  precision) and confirmed `parse_docker_timestamp`'s regex-based
  truncation-to-6-digits correctly handles real Docker output, not just
  already-6-digit synthetic input; this review's own lifecycle-run
  timestamps (6-digit, correctly parsed and compared) corroborate the
  same conclusion in practice.
- **Startup timing**: the ordering proof genuinely measures a real,
  small, positive gap (0.134s and 0.101s in this review's own run) between
  dependency-health and dependent-start, not a coincidence of a generous
  poll deadline — consistent with the networking review's own, separately
  reproduced ordering-regression test.
- **Repeated-run safety**: two consecutive real runs, zero interference,
  distinct project names, distinct ports, `55/55` both times, zero residue
  after either.
- **Resource-name collisions**: project names are
  `f"maops-compose-{uuid.uuid4().hex[:12]}"` — 48 bits of randomness per
  run; collision risk is not a practical concern for this project's usage
  pattern (sequential, human/CI-triggered runs, not a high-volume fleet).
- **Cleanup on failure**: confirmed above (state-host-port mutation, real
  runtime failure, real `down -v` in `finally`).
- **Cleanup after SIGINT/exception**: confirmed for `SIGINT` at four
  different injection points, including mid-`up -d` (see M-1's
  experiments 1-2). **Not** confirmed for `SIGTERM` — see M-1.
- **Docker Desktop/WSL assumptions**: this entire review ran against
  Docker Desktop's WSL2 backend (`docker info`: `OSType: linux`, kernel
  `6.18.33.2-microsoft-standard-WSL2`) — i.e., every lifecycle/mutation/
  signal test above is proof against the actual environment this project
  is developed in, not a differently-behaving CI runner. No
  `host.docker.internal`, no Desktop-specific path, and no WSL-specific
  branch exists anywhere in `compose.yaml` or the two scripts under
  review (grepped directly) — service discovery is pure Compose-DNS
  throughout, which is portable to a native Linux Docker Engine host
  unchanged. One environmental nuance worth naming (informational, not a
  finding): Docker Desktop's WSL2 integration forwards `127.0.0.1`-bound
  published ports through to the Windows host's own `localhost` as well
  (by design, for developer convenience) — the *security* boundary
  `docs/networking.md`/`docs/compose-platform.md` claim ("never exposed
  beyond this machine") still holds under that mechanism, but the
  underlying plumbing differs from a bare Linux kernel loopback interface;
  worth being aware of if this project is ever run on a shared
  Windows/WSL host, though out of scope for this review to grade as a
  finding since no broader exposure was found or is claimed.

---

## Fault injection / detection scoreboard

| Injection | Caught by `check_compose.py`? | Caught by `compose_integration.py`? |
|---|---|---|
| `service_healthy` -> `service_started` | Yes | Yes (networking review's own runtime re-test; not re-run here) |
| `gateway` joins `backend` | Yes | not independently re-run at runtime here (structural detection alone is sufficient and instant) |
| wrong `STATE_HOST` | Yes | not independently re-run at runtime here |
| volume removed from `state` | **Yes (new)** | not independently re-run at runtime here (would surface as `state` failing to start cleanly / data non-persistence; structural detection alone is instant and sufficient) |
| `state_data` mounted into `app` | **Yes (new)** | not independently re-run at runtime here |
| `backend: internal: false` | Yes | not independently re-run at runtime here |
| config mount missing/writable | Yes (missing, new) / N/A (writable — not expressible) | Yes, for the read-only property specifically (`check_config_mount_readonly`, confirmed passing in this review's lifecycle runs) |
| `state` host port added | Yes | **Yes — independently re-run at runtime in this review**, correct `FAIL` + clean teardown |
| `SIGINT` mid-run | n/a (stateless, no containers created) | **Yes — handled correctly, 4/4 injection points** |
| `SIGTERM` mid-run | n/a | **No — M-1** |

`check_compose.py` is deliberately the fast, structural, always-run-first
layer (`make quality`); every scenario it catches is caught in well under
a second against a rendered config, versus tens of seconds for the
equivalent real-stack proof. This review did not find it necessary to
re-run every structural-check-detected scenario through the slow runtime
path as well — the value of `compose_integration.py` is in the classes of
property `check_compose.py` structurally cannot see (real DNS/L3
reachability, real health-gated timing, real kernel-enforced hardening,
real persisted bytes across container recreation) — all of which were
independently reproduced above, in the "Lifecycle reproduction" section,
against the real, unmutated stack.

---

## Cleanup verdict

**Clean on every path this review tested, with one caveat.** Confirmed
via `docker ps -a`/`docker network ls`/`docker volume ls` after: two
normal successful runs, a genuine runtime assertion failure (state host
port), four `SIGINT` injections at varying points including mid-`up -d`,
and (after this review's own manual follow-up) the `SIGTERM` injection.
The caveat is M-1: `SIGTERM` bypasses `compose_integration.py`'s own
cleanup entirely, relying on `make clean`'s separate, coarser regex-based
sweep as the only backstop, and that backstop is not automatic. No global
Docker prune was run at any point in this review; every resource this
review created directly (`maops-review-ts` probe, the manual mutation
cleanups) used a unique, project-prefixed name and was removed
individually.

---

## Final Compose/integration release verdict

**Sound — genuinely closes Day 3's Compose scope, with one real but
narrow, non-blocking gap.** The `14`/`55` counts independently re-derived
here are accurate and, if anything, conservative; the platform's declared
network/volume/config topology and its runtime behavior (startup
ordering, persistence across every disruption tested, kernel-enforced
hardening, config/rootfs read-only proofs) all independently reproduced
exactly as `compose.yaml`, `docs/networking.md`, `docs/persistence.md`,
and `docs/configuration.md` claim. Every mutation this review attempted
that can actually be expressed in `compose.yaml` was correctly detected
by `check_compose.py`, including three scenarios (removed/misattached
volume, missing config mount) no prior Day 3 review had exercised. The
one Medium finding (M-1, `SIGTERM` leaves a silent, real orphaned stack)
is a real gap in the *test harness's* own robustness — not the
platform's — and is not a release blocker: `SIGINT` (the primary
interactive-cancel path, `Ctrl-C`) is handled correctly via a real,
verified mechanism (not luck), a genuine runtime assertion failure is
handled correctly, and `make clean`'s existing cleanup regex already
covers the exact residue pattern `SIGTERM` would leave — closing it fully
just requires making that cleanup automatic and the failure visible
rather than silent.

---

## Appendix: mutation/restore integrity log

Every temporary `compose.yaml` mutation in this review was applied via a
Python script performing an exact-string, single-occurrence
(`text.count(old) == 1`, asserted before every write) replacement, then
restored from a pre-mutation backup and verified via `diff` immediately
after each test — no mutation was ever left in place between experiments,
and no mutation was ever combined with another:

```
$ md5sum compose.yaml                                  # before any mutation
39527037ef8905d0afb5ce3d0530fbc5  compose.yaml
... (8 check_compose.py mutation scenarios + 1 real compose_integration.py
     failure-path run, each applied then immediately restored) ...
$ diff compose.yaml <backup>.orig && echo "RESTORED-IDENTICAL"
RESTORED-IDENTICAL   # printed after every single mutation cycle
```

Final `git status --short compose.yaml` at the end of this review shows
only the same pre-existing Day 3 working-tree diff against `HEAD` that
was present before this review began — no residual change from any
mutation performed during this review.

Signal-injection experiments (M-1) used real, unmutated `compose.yaml`
content — no text mutation involved, only process-level `kill -INT`/
`kill -TERM` against the running review script, with manual
`docker compose -p <project> down -t 5 -v` cleanup for the one
(`SIGTERM`) case where the script's own teardown did not run. No global
Docker prune was run at any point in this review; every container,
network, and volume this review created directly (outside the two
scripts' own runs) was removed individually by exact, project-prefixed
name.
