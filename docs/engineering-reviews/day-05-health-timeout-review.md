# Day 5 Health + Timeout Review — v0.5.0

Repository: `maops-docker-platform`
Branch: `feature/day-5-health-reliability-resources`
Target: `v0.5.0`
Reviewer: independent HEALTH + TIMEOUT reviewer (review only — no
implementation file was modified; no commit/push/tag/release was
performed).
Date: 2026-08-25.

## Scope

Independent verification of: `/healthz`/`/readyz` semantics across
`state`→`app`→`gateway`; the Day 4 H-1 role-discrimination regression;
the Day 3 A-6 cross-hop timeout-stacking closure; the Day 5 timeout
hierarchy's config-load-time validation (bool/zero/negative/NaN/
Infinity/wrong-type/excessive/broken-hierarchy adversarial cases); real
stalled-dependency behavior and recovery, reproduced against real Docker
(not simulated); and documentation accuracy. Nothing here was accepted on
the implementation report's word — every claim below was independently
re-derived from a fresh source read, the project's own unit tests, and
live Docker runs performed by this review.

## Files reviewed

`config/platform.json`, `app/config.py`, `app/platform_config.py`,
`app/server.py`, `app/healthcheck.py`, `gateway/config.py`,
`gateway/platform_config.py`, `gateway/server.py`,
`gateway/healthcheck.py`, `state/server.py`, `state/healthcheck.py`,
`compose.yaml`, `scripts/reliability/reliability_check.py` (all 1008
lines), `scripts/compose/compose_integration.py` (role-discrimination
matrix + healthchecks), `scripts/compose/check_compose.py` (resource/
restart/grace-period structural checks), `tests/test_gateway_platform_config.py`,
`tests/test_app_platform_config.py`, `tests/test_gateway_server.py`
(`UpstreamTimeoutTests`), `tests/test_server.py` (app-role tests),
`tests/test_reliability_check.py`, `docs/reliability.md`,
`docs/configuration.md`, `docs/compose-platform.md`, `docs/persistence.md`,
`docs/roadmap.md`, `README.md`, `Makefile`.

## Tests/probes actually run

1. `python3 -m unittest discover -s tests` — **359/359 passed**.
2. `python3 scripts/compose/check_compose.py` — **17/17 structural checks
   passed** against the rendered Compose config (matches the roadmap's
   claimed "14 → 17" count).
3. Rebuilt the release image from current, uncommitted source
   (`make build`) before any Docker-based verification — the pre-existing
   `maops-docker-platform:0.5.0` image on this host predated the latest
   source edits (image built 2026-08-23 17:03; `gateway/platform_config.py`
   etc. last modified 2026-08-24) and would have been stale evidence.
4. `python3 scripts/compose/compose_integration.py` against real Docker —
   **58/58 checks passed**, including a live re-run of
   `check_role_discrimination_matrix()` (the H-1 3x3 proof).
5. `python3 scripts/reliability/reliability_check.py` (the production
   Day 5 mechanism, run twice conceptually — once blocked by an
   environment artifact, §"Environment note" below, once clean) against
   real Docker — **32/32 checks passed**, including the real
   `docker pause state` A-6 adversarial proof, the real kernel OOM-kill
   transient-crash automatic-recovery proof, the real persistent-failure
   bounded-retry-exhaustion proof, and the real intentional-stop-does-
   not-auto-restart proof.
6. A custom 5-trial repeated-pause timing probe (written for this review,
   reusing `reliability_check.py`'s own helper functions — brings the
   stack up once, then pauses/measures/unpauses `state` five times in a
   row) to specifically check the A-6 measurement for flakiness.
7. Direct Python REPL probes of `gateway/platform_config.py`'s
   `_validate_timeout_hierarchy` at the exact numeric boundaries (all
   three fields simultaneously at their individual maxima; a
   1e-7-second-margin config) to verify the invariant holds under
   adversarial edge conditions, not just the values the existing unit
   tests happen to use.
8. All Docker-based probes and script runs used `/usr/bin/docker`
   (confirmed native to WSL2) placed first on `PATH`; every temporary
   Compose project this review created (or that `reliability_check.py`/
   `compose_integration.py` created on this review's behalf) was
   confirmed fully cleaned up (`docker ps -a`/`network ls`/`volume ls`
   filtered by the relevant `maops-reliability-*`/`maops-compose-*`
   prefix — empty after every run). The user's own pre-existing,
   independently-running `maops-docker-platform` dev stack (default
   project name, unrelated to this review) was left untouched throughout.

## Health/readiness findings

`/healthz` in all three services (`app/server.py:91-96`,
`gateway/server.py:105-110`, `state/server.py:53-58`) is a pure, local,
constant-time handler — no `_call_state`/`_call_upstream`/storage read
anywhere in the function body. `/readyz` genuinely chains:
`state`'s `/readyz` does a real (non-mutating) store read
(`state/server.py:61-69`); `app`'s `/readyz` makes a real, bounded HTTP
call to `state`'s `/readyz` (`app/server.py:99-108`); `gateway`'s
`/readyz` makes a real, bounded HTTP call to `app`'s `/readyz`
(`gateway/server.py:113-122`) — `gateway` never contacts `state` directly
(confirmed unreachable at the network layer by `compose_integration.py`'s
`check_gateway_state_isolation`, re-run in this review). This is a
genuinely nested chain, not three independently-faked local checks.

Verified live against real Docker (`reliability_check.py`'s pause
scenario, this review's own run): while `state` is paused, `app`'s and
`gateway`'s own `/healthz` (probed via `docker exec ... {role}.healthcheck`
— kernel/process-level, not a possibly-cached HTTP response) both stayed
`200`; `app`'s own `/readyz` (probed from inside `app`'s own container)
and `gateway`'s own `/readyz` both correctly became `503`. This matches
the required matrix exactly:

| | `app` paused-dependency behavior | `gateway` paused-dependency behavior |
|---|---|---|
| `/healthz` | 200 (confirmed) | 200 (confirmed) |
| `/readyz` | 503 (confirmed) | 503 (confirmed) |

Recovery on unpause was automatic and confirmed: `state` returned to
Docker-`healthy`, `gateway`'s `/readyz` polled back to `200` with no
manual intervention, and the persisted counter value was unchanged
across the pause/unpause cycle.

## H-1 regression verdict: **CLOSED, no regression**

`check_role_discrimination_matrix()` (`compose_integration.py:319-358`)
was re-run against real, freshly-built Compose-managed containers in this
review and reproduced the full 3x3 matrix exactly as claimed: each of
`app.healthcheck`/`gateway.healthcheck`/`state.healthcheck` exits 0 only
against its own role's container and non-zero against the other two —
`state: app=FAIL, gateway=FAIL, state=PASS; app: app=PASS, gateway=FAIL,
state=FAIL; gateway: app=FAIL, gateway=PASS, state=FAIL`. Each
`_route_healthz` handler still hardcodes its own `role` literal
(`"app"`/`"gateway"`/`"state"`), and each `healthcheck.py`'s `check()`
still requires `payload.get("role") == EXPECTED_ROLE` in addition to
`status == "ok"` — this mechanism is byte-for-byte unchanged from the
Day 4 fix; Day 5 only reformalizes the liveness/readiness split around
it, exactly as `docs/reliability.md` claims. No regression.

## Timeout configuration findings

`config/platform.json` (verified, not assumed):
`state_dependency_timeout_seconds=2.0`, `gateway_upstream_timeout_seconds=5.0`,
`timeout_safety_margin_seconds=1.0`. Wiring confirmed correct and not
swapped: `app/config.py:92` sets `AppConfig.state_timeout_seconds` from
`platform_cfg.state_dependency_timeout_seconds`; `gateway/config.py:109`
sets `GatewayConfig.upstream_timeout_seconds` from
`platform_cfg.gateway_upstream_timeout_seconds`. The invariant
(`gateway_upstream_timeout_seconds > state_dependency_timeout_seconds +
timeout_safety_margin_seconds`) is enforced in
`gateway/platform_config.py:110-124` and re-checked independently by
`reliability_check.py`'s `check_timeout_hierarchy_config()` against the
real shipped file — both confirm `5.0 > 2.0 + 1.0`.

Adversarial validation, independently confirmed (existing unit tests
re-run + fresh boundary probes by this review):

| Case | Result |
|---|---|
| `bool` for any timeout field | rejected — `isinstance(value, bool)` checked before the numeric branch in `_validate_timeout` (both modules) |
| `bool` for `schema_version` | rejected — explicit `isinstance(schema_version, bool)` check |
| `0` | rejected — `0 < value` is strict |
| negative | rejected |
| `NaN` | rejected — `math.isfinite` |
| `Infinity`/`-Infinity` | rejected — `math.isfinite` (Python's `json` module otherwise accepts these) |
| wrong type (string, etc.) | rejected |
| excessive (above per-field max: 30s inner, 60s outer, 30s margin) | rejected |
| broken hierarchy (equal, less-than, inner-alone-greater) | rejected — existing `TimeoutHierarchyInvariantTests` cover all three shapes |
| all three fields simultaneously at their individual maxima (30/30/60) | **this review's own new probe** — correctly rejected (`60.0` is not `> 60.0`), confirming the per-field ceilings can't be combined to silently defeat the invariant |
| margin of `1e-7`s above the strict minimum | **this review's own new probe** — accepted (by design: the invariant is a strict mathematical inequality; the *practical* safety margin is whatever the operator configures in `timeout_safety_margin_seconds`, not an implicit floor beyond it). Not a defect — flagged as Info below. |

The bool-as-int-subtype bypass specifically cannot occur: `_validate_timeout`
rejects `bool` before it ever reaches the `(0, max]` range check in both
`app/platform_config.py` and `gateway/platform_config.py`, and this is
independently unit-tested (`test_boolean_timeout_is_rejected`,
`test_schema_version_true_is_rejected`/`_false_is_rejected` in both
modules' test files).

## Real A-6 reproduction

Used the production mechanism as instructed: `scripts/reliability/
reliability_check.py`'s real `docker pause state` scenario (a genuine
stalled dependency — the frozen cgroup accepts a new TCP handshake into
its listen backlog but never completes the HTTP exchange, exactly the
condition the timeout hierarchy exists to bound), run twice by this
review (once via the full script, once via a repeated 5-trial standalone
probe built for this review), against freshly rebuilt containers from
current source.

## Measured timing

Full-script run: `state`-dependent request through `gateway -> app ->
state` completed in **2.01s** while `state` was paused (`inner=2.0s,
outer=5.0s, margin=1.0s`) — status `503`, no `"Traceback"` in the body.

Repeated 5-trial probe (built for this review specifically to check for
flakiness, per the review brief's instruction not to trust a single
machine-local pass):

| Trial | Elapsed |
|---|---|
| 0 | 2.015s |
| 1 | 2.013s |
| 2 | 2.008s |
| 3 | 2.008s |
| 4 | 2.035s |

Range: 2.008s–2.035s (spread of 27ms) — tightly clustered around the
configured 2.0s inner timeout, with **~3.0s of empirical slack** before
the 5.0s outer budget on this host (Docker Desktop / WSL2). The
configured `timeout_safety_margin_seconds` floor is only 1.0s, but the
*shipped defaults*' actual behavioral margin is 3x that; the observed
jitter (tens of milliseconds) is nowhere close to threatening either
bound. No evidence of the gateway's own outer timeout ever firing instead
of the inner one across 6 total real trials (1 full-script + 5 probe).

Confirmed against real evidence:
- `state` really becomes non-responsive — the TCP handshake completes
  (cgroup freezer, not a network-level block) but the HTTP response never
  arrives, matching the documented mechanism.
- `app` stops waiting at the inner budget — `app`'s own in-container
  `/readyz` returned `503` and the end-to-end elapsed time (~2.01-2.035s)
  matches `state_dependency_timeout_seconds` (2.0s), not
  `gateway_upstream_timeout_seconds` (5.0s) or their sum (7.0s).
- The gateway received a controlled failure well before its own outer
  budget — confirmed `bounded_by_outer` (elapsed < 5.0s) across every
  trial.
- Total latency is bounded — never observed above 2.035s, nowhere near a
  hang.
- No raw traceback leaked — confirmed (`"Traceback" not in
  state_text`) in every trial.
- Liveness remained local — `app`/`gateway` `/healthz` stayed `200`
  throughout via kernel/process-level `docker exec` probes, never via a
  possibly-stale HTTP cache.
- Readiness degraded — confirmed `503` on both `app`'s and `gateway`'s
  `/readyz`.
- Readiness recovered — confirmed automatic return to `200` on unpause,
  with the persisted counter unchanged.

## Recovery result

All three recovery paths were independently reproduced against real
Docker in this review's own run of `reliability_check.py`:
- **Pause/unpause** (A-6 scenario): automatic, `state` back to
  Docker-`healthy`, `gateway` `/readyz` back to `200`, value unchanged.
- **Transient crash** (real kernel OOM-kill via `state`'s own
  `/proc/1/oom_score_adj`, `mem_limit` never touched, `docker kill`/
  `docker stop`/internal `os.kill(1, SIGKILL)` all independently
  confirmed *not* to trigger `on-failure` by the script's own documented
  prior experiments): fully automatic, `RestartCount` advanced by exactly
  1, no manual `docker start` anywhere, value preserved, full chain
  functional again (increment succeeded).
- **Persistent failure** (memory limit lowered and kept lowered):
  `on-failure:3` retried automatically up to exactly the configured cap
  (cumulative lifetime `RestartCount` 1→3, correctly *not* reset per
  episode) and then correctly stopped — explicitly *not* claimed as
  automatic recovery; explicit operator `docker compose start state`
  required and succeeded, value preserved.
- **Intentional stop**: `docker stop state` exited cleanly (`ExitCode=0`,
  0.60s, well inside the 10s grace period) and did **not** trigger
  `on-failure` (`RestartCount` unchanged over the settle window) — the
  correct distinction from the transient/persistent crash cases above.

## Adversarial cases

Covered above (config validation table) and via direct boundary probes
this review constructed independently of the existing test suite. One
asymmetry found between `app`'s and `gateway`'s *unit-level* (Docker-free)
test suites is recorded as M-1 below — this is a test-coverage gap, not a
runtime defect; the real-Docker proof (`reliability_check.py`) already
covers the missing scenario end-to-end.

## Findings table

| ID | Severity | Category | Summary |
|---|---|---|---|
| M-1 | Medium | test-coverage | `tests/test_server.py` (the `app` role's Docker-free unit suite) has no fast unit test analogous to `tests/test_gateway_server.py::UpstreamTimeoutTests` proving `state_timeout_seconds` actually bounds a slow `state` response and converts it to a controlled `503` — even though `ServerTestCase`/`_FakeStateHandler` already expose a working `state_delay_seconds` hook (`tests/test_server.py:81,86`) that no subclass ever sets to a nonzero value. `app` is the *inner* hop A-6 depends on; gateway's outer-hop equivalent has this exact test. Not release-blocking (the real-Docker `docker pause` proof in `reliability_check.py` already covers this scenario end-to-end and was independently re-run by this review), but should be closed before Day 6 so a future regression in `app`'s own timeout wiring is caught by `make test` in seconds rather than only by the multi-minute Docker-based `make reliability-check`. |
| L-1 | Low | test-rigor | `reliability_check.py:619`'s `inner_governed` check (`state_request_elapsed >= inner_timeout * 0.5`) is a loose lower bound (1.0s here) that doesn't tightly bind the measured latency to the *specific* configured inner timeout (2.0s) — a hypothetical future bug that made `app` apply a much larger effective timeout (anywhere up to just under the 5.0s outer budget) would still pass both this check and `bounded_by_outer`. This review's own repeated measurement (2.008s-2.035s across 5 trials) shows the real behavior is in fact tightly correlated with the configured value today, so this is a structural test-rigor gap, not a sign of an actual defect. Non-blocking. |
| I-1 | Info | environment | Not a Day 5 code defect: this sandbox's default `docker` on `PATH` (`~/.local/bin/docker`) is a WSL-interop shim (`exec docker.exe "$@"`) that does **not** forward the shell's environment variables into `docker.exe`/Compose interpolation — confirmed via a minimal reproduction (`GATEWAY_HOST_PORT=7777`/a generic `TESTVAR` both silently fell back to their Compose-file defaults through the shim, but resolved correctly through `/usr/bin/docker`). This caused `reliability_check.py`'s first run in this review to fail immediately (`GATEWAY_HOST_PORT=0`'s dynamic-port request silently became the literal default `8080`, colliding with the user's own already-running dev stack on that port) — resolved by placing `/usr/bin/docker` first on `PATH`, exactly as this review's own instructions anticipated. Recorded for awareness only, since Day 6 CI tooling will need `docker`/`docker compose` invocations to resolve to an env-var-forwarding CLI, not this shim. |
| I-2 | Info | design | `timeout_safety_margin_seconds` enforces a strict mathematical inequality (`outer > inner + margin`) with no implicit floor beyond whatever the operator sets — this review confirmed a margin of `1e-7`s independently passes validation. This is consistent with the field being an explicit, operator-controlled setting (not a hidden constant), and the *shipped* defaults provide a healthy real-world buffer (measured ~3.0s of slack vs. ~30ms of observed jitter), so this is not a defect in the shipped configuration — only a reminder that an operator who shrinks the margin near zero is trading away the jitter tolerance this review measured, not that the mechanism itself is broken. |
| I-3 | Info | test-coverage | `tests/test_gateway_platform_config.py` has no explicit `-Infinity` rejection test for `gateway_upstream_timeout_seconds` (only `NaN` and `+Infinity`), unlike `tests/test_app_platform_config.py`, which has `test_negative_infinity_timeout_is_rejected`. The shared `math.isfinite()` check in both modules already rejects `-Infinity` correctly (verified), so this is a pure test-symmetry nit with zero functional risk. |

## A-6 final status: **CLOSED**

The invariant is enforced at config-load time (not merely documented),
cannot be bypassed by any of the adversarial input classes tested
(including two boundary cases this review added beyond the existing
suite), and the real-world behavior it's supposed to guarantee was
independently reproduced against genuine Docker containers six separate
times (once via the production script, five times via a dedicated
repeated-trial probe) with tight, low-variance timing that stayed
comfortably inside the configured outer budget and was clearly governed
by the inner one. No raw hangs, no `inner + outer` stacking, no leaked
tracebacks, in any trial.

## Release-blocker status

**No Critical or High findings.** No finding in this review invalidates
a core Day 5 reliability claim. M-1 is a real, concrete gap but sits at
the unit-test layer only — the property it's missing a fast regression
test for is already proven correct by real-Docker evidence gathered
independently in this review. Nothing here is RELEASE-BLOCKING.

## Final review verdict

Liveness/readiness semantics, the H-1 regression guard, and the A-6
timeout-hierarchy closure are all genuinely implemented and hold up under
independent, real-Docker adversarial reproduction — not just under the
implementation's own claimed proof. Documentation
(`docs/reliability.md`, `docs/configuration.md`, `docs/compose-platform.md`,
`docs/persistence.md`, `docs/roadmap.md`, `README.md`) accurately
reflects the verified behavior, including cross-references to test class
names and check counts that this review confirmed actually exist/match.
The one Medium finding is a test-coverage asymmetry with no observed
functional impact, backed by real-Docker evidence that the underlying
behavior is correct.

**HEALTH-TIMEOUT REVIEW PASS**
