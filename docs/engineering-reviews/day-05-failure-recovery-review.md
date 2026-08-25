# Day 5 Failure/Recovery/Persistence Review — v0.5.0

Repository: `maops-docker-platform`
Branch: `feature/day-5-health-reliability-resources`
Target: `v0.5.0`
Reviewer: independent FAILURE / RECOVERY / PERSISTENCE reviewer (review
only — no implementation file was modified; no commit/push/tag/release
was performed).
Date: 2026-08-25.

## Scope

Independent, adversarial verification of the three Day 5 crash/stop
lifecycle scenarios (transient failure, persistent failure, intentional
stop), the persistence guarantees across each, the state/app/gateway
failure-isolation matrix, and `reliability_check.py`'s own cleanup
discipline. Two sibling Day 5 reviews already exist in this directory
(`day-05-health-timeout-review.md`, `day-05-resource-restart-review.md`)
and already independently re-ran the full production script and manually
reproduced most of the individual mechanisms; nothing below was accepted
on their word either — every claim in this document was re-derived from
this review's own fresh source read and its own real Docker runs,
including two adversarial probes (a mid-run `SIGTERM` cleanup test, and
an isolated manual OOM-kill reproduction outside the script) that neither
prior review performed.

## Files reviewed

`scripts/reliability/reliability_check.py` (all 1008 lines),
`tests/test_reliability_check.py`, `docs/reliability.md`, `compose.yaml`,
`Makefile` (`reliability-check`, `release-check`, `clean` targets),
`README.md`, `docs/roadmap.md`, `tests/test_compose_integration.py`
(for its analogous `_TerminatedError`/SIGTERM unit tests, used as a
cross-check baseline).

## Environment note (not a Day 5 finding)

Same sandbox artifact the two sibling reviews already documented: the
default `docker` on `PATH` is a WSL2-interop shim that does not forward
environment variables to `docker.exe`. Resolved identically — `/usr/bin`
placed first on `PATH` (the native WSL2 binary) plus
`WSLENV=GATEWAY_HOST_PORT:VERSION` exported for every Docker-based probe
in this review.

## Tests/probes actually run

1. Confirmed the release image was stale relative to current uncommitted
   source (image `Created` 2026-08-23T11:03Z; `app/platform_config.py`,
   `gateway/platform_config.py`, `compose.yaml`, `config/platform.json`
   etc. all modified 2026-08-24) and ran `make build` before any
   Docker-based verification. The rebuild produced a **byte-identical**
   image digest (`sha256:c1b1183e...`) to what was already running —
   confirming the deterministic build round-trips correctly and that the
   already-running dev stack and this review's fresh build both reflect
   current source, not stale evidence.
2. `python3 -m unittest tests.test_reliability_check -v` — **28/28
   passed**.
3. `python3 scripts/reliability/reliability_check.py` (the production
   mechanism) against real Docker, full run — **32/32 checks passed**,
   full log captured and inspected line-by-line against every
   Scenario-A/B/C claim in the review brief (see "Scenario A" through
   "Scenario C" below).
4. Confirmed post-run cleanup: `docker ps -a` / `volume ls` / `network
   ls` filtered by `maops-reliability-*` — empty. The pre-existing,
   unrelated `maops-docker-platform` dev stack (default project,
   `127.0.0.1:8080`) was confirmed untouched (same containers, same
   uptime) before and after.
5. **New probe (neither sibling review performed this): a real mid-run
   `SIGTERM`.** Launched `reliability_check.py` in the background twice:
   - Run 1: `SIGTERM` sent ~1s after "state, app, gateway all reached
     Docker healthy state" (a benign point, before any mutation).
   - Run 2: `SIGTERM` sent immediately after "state ... paused (real
     stalled dependency)" — the most adversarial point available, since
     `state` is frozen mid-way through the A-6 scenario and `state_is_paused`
     must be true for the `finally` block's conditional unpause-before-down
     logic to matter at all.

   Both runs: exit code **143** (the script's own documented convention
   for a caught `_TerminatedError`), `_TerminatedError` correctly caught
   and printed as `TERMINATED:`, and — verified directly, not just
   assumed from a clean exit code — **zero** leftover
   `maops-reliability-*` containers/volumes/networks in either case, and
   the unrelated dev stack was confirmed untouched afterward both times.
6. **New probe (neither sibling review performed this): an isolated
   manual reproduction of the transient-crash mechanism, outside the
   script**, specifically to independently verify kernel-vs-harness
   causation. Brought up a freshly-built, uniquely-named Compose stack
   (`maops-review-fr-<epoch>`, this review's own project) via a plain
   `docker compose up -d`, captured `state`'s PID/`RestartCount`/
   `OOMKilled` before, started a background `docker events --filter
   container=<id>` capture, ran the *exact* `oom_score_adj` + memory-
   pressure exec `reliability_check.py` itself uses, then re-inspected
   and stopped the events capture. See "Scenario A" below for the result
   and why it rules out a harness artifact. Cleaned up with `docker
   compose ... down -t 10 -v`; confirmed zero leftovers and the dev stack
   untouched.
7. Grepped the full script for every `["start", ...]`/`compose(...,
   ["start", ...])` call site (4 total: lines 853, 910, 940, 960) and
   confirmed by inspection that all four occur strictly *after* Scenario
   1 (the transient-crash automatic-recovery proof, which completes at
   line ~777) — none inside the authoritative transient-recovery code
   path. Grepped separately for `prune`/`docker rm`/`docker rmi` — none
   found (only the docstring's own prohibition text).
8. Read `Makefile`'s `clean` target's `maops-reliability-*` block
   (lines ~144-149) — scoped to its own name prefix, uses `docker compose
   ... down -t 5 -v` (not a prune), matches the pattern already used for
   `maops-compose-*`.

## Scenario A — Transient unexpected failure

All eleven claims in the review brief independently verified:

| # | Claim | Verified how |
|---|---|---|
| 1 | raises `/proc/1/oom_score_adj` to 1000 from inside `state` | read `transient_crash_source` (script) — reproduced verbatim in probe 6 |
| 2 | memory pressure from a disposable sibling `docker exec` process | same source; the exec'd process (not PID 1's own code) allocates ~4000×1MiB |
| 3 | kernel OOM subsystem kills PID 1 | **independently confirmed via probe 6**: a real `docker events` stream captured `oom` → `exec_die`(×2) → `die` (`exitCode:137`) → `start`, in that order, for the exact container. Docker only emits an `oom` event when the kernel's own cgroup OOM-killer subsystem fires — `docker kill`/`docker stop` never produce one (this is the same distinction the sibling resource-restart review's manual `docker kill` test independently confirmed from the opposite direction: no `oom` event, `RestartCount` stays 0). The presence of a genuine `oom` event immediately preceding `die` is direct, first-party evidence this is kernel-initiated, not a harness artifact simulating the same outward symptoms. |
| 4 | Docker `on-failure` automatically restarts `state` | `start` event followed `die` in the same captured sequence; no script or manual `docker start`/`compose start` was issued between them in either probe 6 or the full script run |
| 5 | `RestartCount` increases exactly once | probe 6: `RestartCount` `0`→`1`. Full script run: `RestartCount before=0 after=1`. Both match |
| 6 | `state` becomes healthy | `check_runtime_healthy` passed in the full run; probe 6's follow-up inspect showed `Health=starting` transitioning to healthy shortly after (consistent with the 5s `start_period`) |
| 7 | `app` readiness recovers | full run: `exec_local_readyz` on `app` returned `200 {'status': 'ready'}` |
| 8 | `gateway` readiness recovers | full run: `poll_gateway_readyz` converged to `{'status': 'ready'}` |
| 9 | persistent value unchanged | full run: `before_crash=0 after_recovery=0` |
| 10 | `gateway -> app -> state` works again | full run: `POST /state/increment` returned `200 {'value': 1}` |
| 11 | no `docker start` used | grepped every `start` call site in the script (4 total, all outside Scenario 1 — see probe 7) |

**Independent PID-level proof (probe 6, beyond what the script itself
checks):** `state`'s own PID went from `28524` (pre-crash) to `29037`
(post-recovery) — a genuinely different OS process, not the same PID
surviving a signal it happened to ignore. This directly rules out the two
alternative "successful-looking but not actually a real crash" failure
modes a less careful implementation could fall into: (a) the exec'd
sibling silently failing without touching PID 1 at all (would leave the
PID unchanged and produce no `oom`/`die`/`start` triplet — not what was
observed), and (b) a scripted `docker restart` disguised as a crash
(would produce a `start` event with no preceding `oom`, and would
typically leave the exit code `0`, not `137`).

**This review's independent verdict on Scenario A: every claim holds,
confirmed by evidence this review generated itself outside the script
under test, not merely by re-running the project's own instrumentation.**

## Scenario B — Persistent failure

- **Bounded retry exhaustion, not automatic recovery**: independently
  confirmed via the full run's own printed evidence: `RestartCount
  before=1 after=3 (cap=3) OOMKilled=True Running=False`. The container
  is left `exited`/`Running=False` — this is not framed or observable as
  a recovery in any sense; `state`'s own persisted counter and readiness
  only return to normal *after* the script's separate, later, explicit
  `docker compose start state` call.
- **Retry cap actually reached**: `restart_count_after ==
  EXPECTED_RESTART_MAX_ATTEMPTS` (`3`), verified in the real run's
  output, matching `compose.yaml`'s declared `on-failure:3` exactly.
- **Documentation does not overclaim**: read `docs/reliability.md`,
  `README.md`, and `docs/roadmap.md`'s Day 5 sections in full. All three
  consistently distinguish "the policy retried automatically" from "the
  service recovered automatically," explicitly state the persistent
  scenario "does NOT prove the service comes back on its own," and label
  the subsequent restart as a deliberate **operator** action, not part of
  the automatic-recovery claim. No instance of unqualified "automatic
  recovery" language attached to the persistent-failure scenario was
  found anywhere in the reviewed docs.
- **Operator recovery happens only after the automatic retry proof has
  finished**: confirmed by direct code read
  (`reliability_check.py:841-853`) — `with_memory_shrink_restored(...,
  _wait_for_bounded_exhaustion)` runs the shrink, the bounded-exhaustion
  poll, the `RestartCount==3`/`OOMKilled`/`Running` assertion, and (via
  its own `finally`) the memory restore, all before that call returns;
  only *after* it returns does `compose(project, env, ["start", "state"],
  ...)` execute. The real run's log preserves this exact ordering
  (`SCENARIO 2 ... complete ... operator recovery next` prints
  immediately before the `docker compose start state` line's effects
  appear).

**New observation from this review (Info, not a defect — see Findings):**
the full run's log shows `RestartCount` was `3` at the end of Scenario 2,
but the very next check (`restart_count_before_stop`, read immediately
after the operator's `docker compose start state` and several successful
requests, right before the intentional-stop scenario) reads `0`. This
means an explicit `docker start`/`docker compose start` resets Docker's
`RestartCount` counter — confirmed directly from this review's own real
run output, not inferred. This does not contradict the sibling
resource-restart review's I-1 finding (that `RestartCount` does *not*
reset across purely *automatic* restarts within one continuous run — a
claim about the restart-policy engine's own bookkeeping, which this
review did not need to re-test) — it is a distinct, complementary fact
about what an *explicit* manual start does to that same counter. See
Finding L-1 below.

## Scenario C — Intentional stop

- `docker stop state` (via `sc.run_docker(["stop", ...])`) completed with
  `ExitCode=0` in `0.61s`, well inside the 10s `stop_grace_period` —
  confirmed in the real run.
- **Stays stopped**: polled over `STOP_SETTLE_WINDOW_SECONDS` (3.0s) —
  `stayed_stopped=True`, `RestartCount` unchanged (`0`→`0`, post-reset
  per the observation above) over that window. This is not merely a
  statistical "didn't happen to restart in time" result: `docker
  stop`/`docker kill` are structurally exempted from the `on-failure`
  restart-policy engine by Docker itself (the container is tagged as
  manually terminated at the moment of the stop, before the policy
  engine is ever consulted) — a fact the sibling resource-restart
  review independently confirmed with its own manual `docker kill` +
  13-40s bounded observation. A 3s settle window is therefore adequate
  given the exemption is structural, not a race that could resolve
  later.
- Recovery is via the script's own explicit, clearly-non-automatic
  `docker compose start state`, and the persisted value survived
  unchanged (`value=2` both sides) — confirmed in the real run.

## Persistence

Recorded and cross-checked before/after every relevant crash, matching
the real run's own printed values:

| Before | Event | After | Match |
|---|---|---|---|
| `0` (pre-pause) | `docker pause`/`unpause` | `0` | yes |
| `0` (pre-transient-crash, = post-pause) | kernel OOM-kill + automatic restart | `0`, then `1` after a real increment | yes |
| `1` (pre-persistent-failure) | bounded retry exhaustion + operator restart | `1`, then `2` after a real increment | yes |
| `2` (pre-intentional-stop) | `docker stop` + explicit `compose start` | `2` | yes |

No crash path in this platform ever loses or corrupts the persisted
counter — every recovery path (automatic, operator-driven, or explicit
restart) reconnects to the same named volume (`state_data`) rather than
recreating it, and this review's own real HTTP responses (not the
script's self-report) show the value only ever changing on an explicit
`POST /state/increment`, never as a side effect of any crash/recovery
step.

## Failure matrix

All three required cases exercised against real containers in the same
run, all confirmed correctly isolated:

| Unavailable service | `/healthz` of dependents | `/readyz` of dependents | Recovery |
|---|---|---|---|
| `state` (`docker pause`) | `app`/`gateway` stay `200` | `app`/`gateway` both `503` | automatic on `unpause` |
| `app` (`compose stop app`) | `gateway` stays `200` | `gateway` degrades to not-ready | recovers on `compose start app` |
| `gateway` (`compose stop gateway`) | n/a (gateway is the externally-reachable one) | `app`/`state` fully unaffected (both `docker exec` healthchecks still pass, `state` still `Running`) | recovers on `compose start gateway` |

The `gateway`-down case is the strongest isolation proof of the three:
`app` and `state` are proven to notice nothing at all (not even a
readiness degradation), matching the documented topology where `gateway`
is a pure edge/consumer of `app`, never a dependency of it.

## Cleanup

- `main()`'s outer `finally` (unpause-if-paused, then `compose ... down
  -t 10 -v`) was adversarially tested by this review with a real
  mid-run `SIGTERM` at two points, including the most adversarial one
  available (`state` frozen via `docker pause` at the moment of
  termination) — both times, teardown genuinely completed: zero leftover
  containers/volumes/networks, in both cases confirmed by direct
  inspection rather than trusting the script's own exit code.
- `with_memory_shrink_restored`'s inner `finally` (Scenario 2's memory
  restore) is unit-tested Docker-free for three failure shapes
  (`ReliabilityError` from the action, an unrelated `RuntimeError`, and a
  failed restore call itself) — re-run in this review (`28/28` including
  these). The sibling resource-restart review already adversarially
  probed the one real gap here (a restore failure being warning-only,
  not currently exploitable into a false PASS given today's numbers) —
  this review re-read that analysis, agrees with it, and does not
  duplicate it as a separate finding (see M-1 in
  `day-05-resource-restart-review.md`).
- No `prune`, no broad `docker rm`/`rmi`, confirmed by direct grep of the
  full script.
- `make clean`'s `maops-reliability-*` block is correctly scoped to its
  own deterministic name prefix and uses `down -v` per matched project,
  not a prune.
- The unrelated, pre-existing dev stack (`maops-docker-platform-*`,
  default project, `127.0.0.1:8080`) was confirmed untouched by every
  probe in this review, start to finish.

## Findings

### L-1 (Low — documentation completeness, not a defect): `RestartCount` resets on an explicit `docker start`/`compose start`, which the "cumulative, lifetime counter" framing doesn't mention

`docs/reliability.md` describes `RestartCount` as "a CUMULATIVE, lifetime
counter for this one container instance, not reset per crash episode."
This is accurate for the specific claim it's defending (that Scenario 2's
correct assertion is against the absolute cap, not a delta) and is
independently confirmed correct by the sibling resource-restart review's
4-round adversarial test. This review's own real run shows a second,
distinct fact the docs don't currently state: an *explicit* `docker
start`/`docker compose start` (as opposed to an automatic restart-policy
retry) resets the counter to `0` — observed directly (`3` at the end of
Scenario 2, `0` immediately after the operator's `compose start state`
and before the intentional-stop scenario). This has no effect on any
claim this project currently makes (Scenario 2's exhaustion assertion is
already complete and recorded before the reset happens; the
intentional-stop scenario's "no auto-restart" check is valid regardless
of which baseline it starts from), but it is a real gap in the "lifetime
counter" framing: a reader could take that phrase to mean the *cap
itself* is a true lifetime limit across any number of manual
interventions, when in fact an operator who restarts a container without
actually having fixed the underlying condition gets a **fresh** budget of
3 more automatic retries, not zero. Recommend qualifying the docs'
language (e.g. "cumulative across automatic restarts within one
continuous run of the container; an explicit manual start resets it").
Non-blocking.

### L-2 (Low — test-coverage gap, not a functional defect): `reliability_check.py`'s `_TerminatedError`/SIGTERM-cleanup mechanism has no Docker-free unit test, unlike the identical mechanism in `compose_integration.py`

`scripts/reliability/reliability_check.py` copies
`compose_integration.py`'s exact `_TerminatedError`/`_handle_sigterm`/
`_install_sigterm_handler()` pattern verbatim (confirmed by direct
comparison), but `tests/test_reliability_check.py` has no test analogous
to `tests/test_compose_integration.py`'s `SigtermHandlingTests` (a real
`os.kill(os.getpid(), SIGTERM)` sent to the test process itself, asserting
`_TerminatedError` is raised and that a `finally` block still runs) —
despite that exact test class already existing in this repository as a
template. This review independently exercised the real end-to-end
behavior twice against live Docker (see "Tests/probes actually run" #5)
and confirmed it works correctly both times, including at the most
adversarial point (mid-pause). Not release-blocking — the real-Docker
proof this review performed is strictly stronger evidence than a unit
test would be — but a fast, Docker-free regression test would catch a
future accidental removal/breakage of this exact mechanism in seconds
rather than only via an expensive real-Docker probe like the one this
review had to construct by hand. Recommend adding one, mirroring
`compose_integration.py`'s own test class.

### Info: Scenario A's kernel-causation claim independently re-derived, not merely re-observed

This review generated its own `docker events` capture and PID-before/after
comparison, outside `reliability_check.py`, specifically because a
`docker exec` returning non-zero and `RestartCount` incrementing are, on
their own, consistent with several different (including non-kernel)
mechanisms. The observed `oom → exec_die → exec_die → die(exitCode 137) →
start` event sequence and the PID change (`28524` → `29037`) are evidence
this review considers dispositive of genuine kernel causation, not merely
consistent with it. No finding — recorded to make explicit what "verify
this is genuinely kernel-induced, not a harness artifact" was checked
against.

## Release-blocker status

**No Critical or High findings. No data-loss condition, no false
automatic-recovery claim, and no unreliable-cleanup condition were found
that would invalidate the release.** Both Low findings are documentation/
test-coverage completeness items with real-Docker evidence already
covering the underlying behavior; neither affects a released artifact's
correctness.

## Final review verdict

Every claim in Scenario A (all eleven), Scenario B (bounded exhaustion,
cap reached, non-overclaiming docs, correctly-ordered operator recovery),
and Scenario C (stays stopped, structurally not just statistically) was
independently reproduced against real Docker by this review, including
two adversarial probes — a mid-run `SIGTERM` at the most hostile point
available, and an isolated manual reproduction of the OOM mechanism
outside the script under test — that go beyond what either sibling Day 5
review performed. Persistence held across every crash path tested, the
three-way failure matrix showed correct isolation in both directions
(dependency-down and edge-down), and cleanup left no trace under normal
completion, a benign interrupt, or the most adversarial interrupt this
review could construct. The two findings recorded are real but
non-blocking completeness gaps, not defects in the platform's actual
failure/recovery/persistence behavior.

FAILURE-RECOVERY REVIEW PASS
