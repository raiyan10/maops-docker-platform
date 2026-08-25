# Day 5 Test + Adversarial Review — v0.5.0

Independent TEST + ADVERSARIAL reviewer. Review only — no implementation
file was modified by this review; this document is the only file it adds.

Scope, per the review brief: (1) whether the reported unit-test growth
(311 → 359) is *meaningfully* covering the specific Day 5 edge cases
named in the brief, not merely inflating a count; (2) an adversarial read
of `scripts/reliability/reliability_check.py` for the specific hazard
list in the brief (unsafe subprocess use, fixed-sleep assertions,
unbounded loops, weak deadlines, false-positive PASS conditions,
exception swallowing, cleanup/leak risk, environment dependence, ordering
assumptions, `RestartCount` semantics); (3) independent re-derivation of
every reported check count against this machine's real Docker, not taken
on the implementation's word.

Three prior Day 5 review docs already exist
(`day-05-health-timeout-review.md`, `day-05-resource-restart-review.md`,
`day-05-failure-recovery-review.md`) — these are implementation-side
correctness reviews of the *platform's runtime behavior*. This review is
narrower and complementary: it treats the test suite and the reliability
harness themselves as the object under review, not the platform behavior
they exercise. Where this review's own findings overlap with something
those three already found, it says so and does not double-count it.

**Environment note (reproduces the prior reviews' own I-1/environment
note independently):** this sandbox's default `docker` on `PATH`
(`~/.local/bin/docker`) is a WSL2-interop shim (`exec docker.exe "$@"`).
`docker compose`'s variable interpolation runs inside `docker.exe` on the
Windows side, and WSL only forwards environment variables into a
Windows-side process that are explicitly listed in `$WSLENV` — so
`reliability_check.py`'s `env["GATEWAY_HOST_PORT"] = "0"` (and
`env["VERSION"]`) were silently dropped through this shim. First attempt
through the shim failed exactly as the prior reviews describe: the
dynamic-port request silently fell back to the compose-file default
(`8080`) and collided with this machine's own already-running dev stack
on that port (`Bind for 127.0.0.1:8080 failed: port is already
allocated`). Confirmed root cause with a minimal reproduction
(`GATEWAY_HOST_PORT=12345 docker compose config` still rendered
`published: "8080"` through the shim; the same command through
`/usr/bin/docker`, the native WSL2 binary, rendered `published: "12345"`
correctly). Resolved for this review by placing `/usr/bin/docker` first
on `PATH` for the Docker-integration runs below, exactly as the prior
reviews did. **Not a Day 5 code defect** — `reliability_check.py`
correctly builds and passes its `env` dict via `subprocess.run(...,
env=env)`, which is exactly right for a normal Docker CLI; the failure is
this specific machine's shim, already disclosed as I-1 in
`day-05-health-timeout-review.md`.

---

## 1. Count reconciliation — every count independently re-run, not taken on report

| check | claimed | independently verified |
|---|---|---|
| Unit tests | 359 (from 311) | **359** ✓ (`make test`: `Ran 359 tests ... OK`, 51.9s) |
| `check_compose.py` structural checks | — | **17/17** ✓ (`python3 scripts/compose/check_compose.py` via `/usr/bin/docker`: `OK (17 structural checks passed ...)`) |
| `compose_integration.py` inspection checks | 58 (per `day-05-health-timeout-review.md`) | **58/58** ✓ (live run via `/usr/bin/docker`: `compose_integration: PASS (58/58 inspection checks passed)`; project cleaned up — no leftover `maops-compose-*` container/network/volume) |
| `security_check.py` checks | — | **22/22** ✓ (live run: `security_check: PASS (22/22 checks passed)`) |
| `scripts/build/image_audit.py` checks | — | **19/19** ✓ (live run: `image_audit: PASS (19/19 checks passed)`) |
| `reliability_check.py` checks | 32/32 (per `day-05-failure-recovery-review.md`, `day-05-resource-restart-review.md`) | **32/32** ✓ (live run, see §2 below; project cleaned up — no leftover `maops-reliability-*` container/network/volume) |

### Unit-test delta, per touched file (accounts for the full +48)

| file | Day 4 baseline | Day 5 (measured) | delta |
|---|---:|---:|---:|
| `tests/test_app_platform_config.py` | 13 | 16 | +3 |
| `tests/test_gateway_platform_config.py` | 11 | 27 | +16 |
| `tests/test_config.py` | 24 | 24 | +0 (rename-only diff, no new case) |
| `tests/test_gateway_config.py` | 20 | 21 | +1 |
| `tests/test_reliability_check.py` | — | 28 | +28 (new file) |
| **sum of deltas** | | | **+48** |

`git status` shows exactly these four modified test files plus this one
new file for the whole test suite; 311 + 48 = 359 matches the reported
total exactly, and no other test file's count needed to move. Count
claim: **confirmed**, arithmetically closed, not just plausible.

---

## 2. Live real-Docker `reliability_check.py` run (independently executed by this review)

Full run via `/usr/bin/docker` (native WSL2 binary) plus
`WSLENV=GATEWAY_HOST_PORT:VERSION` exported so the script's own env
overrides actually reach the compose interpolation step:

- All 32 checks **PASS**, matching the two prior reviews' own runs.
- `RestartCount` before/after independently observed:
  Scenario 1 (transient OOM crash): `before=0 after=1`. Scenario 2
  (persistent OOM, memory pinned to `6m`): `before=1 after=3`, **not**
  `before=1 after=4`. This directly confirms the script's own documented
  claim that `RestartCount` is a cumulative, lifetime counter for the
  container instance, not reset per crash episode, and that the bound
  assertion is correctly written against the absolute cap
  (`EXPECTED_RESTART_MAX_ATTEMPTS`), not a delta from an arbitrary
  baseline — the one place this could most plausibly have been gotten
  wrong.
- `docker stop` on `state` completed in `0.53s`, well inside the
  configured `10s` grace period, and did **not** trigger a restart
  (`restart_count before=0 after=0` across a bounded 3s settle-poll) —
  confirmed distinct from the two OOM-kill paths above, i.e. the
  intentional-stop-is-not-a-restart-trigger claim holds under a fresh,
  independent run, not just the implementation's own.
- Cleanup: `docker ps -a` / `network ls` / `volume ls` filtered for
  `maops-reliability-*` after the run returned nothing on all three —
  the `finally` block's `down -t 10 -v` genuinely removed everything it
  created, including on the success path (no interrupt was needed to
  prove this here since the two sibling reviews already adversarially
  interrupted it mid-run and confirmed cleanup still ran).

This corroborates, from a second independent execution, the two prior
reviews' own live runs — the 32/32 claim is not a stale or cherry-picked
number.

---

## 3. Meaningful-coverage review against the brief's checklist

Reviewed `tests/test_app_platform_config.py`,
`tests/test_gateway_platform_config.py`, and `tests/test_reliability_check.py`
line by line against every item the brief named.

| item | where | verdict |
|---|---|---|
| timeout numeric parsing (int accepted) | `test_app_platform_config.py::test_integer_timeout_is_accepted` | covered |
| bool rejection | both platform-config test files, for every numeric field **and** `schema_version` (`true`/`false` both tested separately — not just "truthy") | covered, thoroughly |
| zero | both files, all three fields (`gateway_upstream_timeout_seconds`, `state_dependency_timeout_seconds`, `timeout_safety_margin_seconds`) | covered |
| negative | both files, all three fields | covered |
| NaN | both files | covered |
| Infinity | both files (`+Infinity`); `app` also separately tests `-Infinity` | covered (gateway's own file does not separately test `-Infinity`, but the shared `_validate_timeout` implementation is identical code — see §4 L-2) |
| maximum bounds | only the **above**-max case is tested (e.g. `999` rejected); no test asserts a value **exactly at** the inclusive upper bound (`MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS=30.0`, `MAX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS=60.0`, `MAX_TIMEOUT_SAFETY_MARGIN_SECONDS=30.0`) is accepted, even though the validator's own comparison (`0 < value <= max_value`) is inclusive | **gap — see L-1** |
| timeout hierarchy | `TimeoutHierarchyInvariantTests` (6 cases: defaults satisfy it, outer>inner+margin accepted, outer==inner+margin rejected — proves strict `>` not `>=`, outer<inner+margin rejected, outer<inner alone rejected, error message names all three fields) | covered, thoroughly — this is the strongest part of the new suite |
| safety margin | `TimeoutSafetyMarginFieldTests` (zero/negative/bool/above-max rejected, valid accepted) | covered |
| resource validation | `reliability_check.py`'s own `check_resource_limits_applied()` (Docker-runtime version): 6 fixture tests (all-pass, `CpuQuota`/`CpuPeriod` fallback shape, missing CPU, missing memory, missing PID, permissive drift beyond target) — meaningfully covered. `check_compose.py`'s structural `check_resource_limits()` (rendered-config version): **zero** persisted unit tests — see §4 M-1 | **split verdict — see M-1** |
| restart-policy validation | `reliability_check.py`'s `check_restart_policy_applied()`: 5 fixture tests (`on-failure:3` passes, `always` fails, `unless-stopped` fails, unbounded `on-failure:0` fails, missing policy fails) — meaningfully covered. `check_compose.py`'s structural `check_restart_policy()`: **zero** persisted unit tests | **split verdict — see M-1** |
| deadline/polling helper behavior | `PollUntilTests` (3 cases: returns predicate value once true, raises `ReliabilityError` after a bounded deadline — itself asserted `<5s` so the test can't hang, error message names the description) | covered adequately |
| malformed healthcheck input | pre-existing (`tests/test_healthcheck.py`, `tests/test_gateway_healthcheck.py`, `tests/test_state_healthcheck.py`), untouched this branch — in scope, still passing, not a Day 5 regression | covered (pre-existing) |
| wrong/missing role | pre-existing `tests/test_security_check.py::HealthcheckModuleForRoleTests` (`test_unknown_role_is_rejected`, `test_all_three_roles_map_to_distinct_modules`), untouched this branch | covered (pre-existing) |
| resource restoration under exceptions | `WithMemoryShrinkRestoredTests::test_restores_original_values_on_success` | covered |
| restoration when wrapped action raises `ReliabilityError` | `test_restores_original_values_even_when_action_raises` — asserts the restore call fires with the exact captured original values, exception still propagates | covered, exactly what the brief asked for |
| restoration when it raises another exception | `test_restores_original_values_even_when_action_raises_unexpected_exception` (a bare `RuntimeError`, proving the `finally` isn't narrowed to `except ReliabilityError`) | covered, exactly what the brief asked for |

**Overall:** the new tests are not count-padding. The timeout-hierarchy
and bool/NaN/Infinity/zero/negative numeric-validation coverage in
particular is genuinely thorough and adversarially minded (the
`schema_version: true`/`false` cases, the strict-`>`-not-`>=` hierarchy
boundary case, and the two-exception-type restore-cleanup proof are all
the kind of case a less careful pass would have skipped). The one
consistent gap is that everything Docker-runtime-facing
(`reliability_check.py`'s own `_applied` checks) got careful fixture
tests, while the parallel Compose-rendered-config-facing logic added to
`scripts/compose/check_compose.py` this same day got none at all — see
§4 M-1.

---

## 4. Adversarial review of `scripts/reliability/reliability_check.py`

Checked against every item in the brief's hazard list:

- **`shell=True`/`os.system`/`os.popen`/unsafe dynamic command
  construction**: none present. Every subprocess invocation is
  `subprocess.run([...], ...)` with a list argv (`compose()`,
  `run_docker` via the reused `security_check` module, the two `docker
  exec ... python3 -c <literal>` calls). The two inline Python sources
  exec'd inside containers (`_LOCAL_READYZ_SOURCE`,
  `_CGROUP_PROBE_SOURCE`, `transient_crash_source`) are fixed string
  literals with only a numeric `timeout` value ever interpolated via
  `.format()` — no request- or container-derived string is ever
  concatenated into a command or source string. Confirmed clean.
- **Fixed sleeps used as assertions**: none. Every wait is either
  `poll_until()` (a real `time.monotonic()`-bounded predicate loop) or
  the one explicit bounded settle-window
  (`STOP_SETTLE_WINDOW_SECONDS=3.0` for the intentional-stop-does-not-
  restart proof) — which is not a sleep-as-assertion but a deliberate
  "stays negative for a bounded observation window" pattern, correctly
  distinguished in the module's own docstring.
- **Unbounded loops**: none — `poll_until()` and the settle-window loop
  both terminate on `time.monotonic()` deadlines with no fallback
  infinite branch.
- **Weak deadline logic**: deadlines are generous but not vacuous
  (`CRASH_RECOVERY_DEADLINE_SECONDS=60.0` for two separate real OOM-kill
  scenarios that each need real container reboot time under this
  project's own resource limits) — confirmed empirically fast in
  practice (§2, full run well under the deadlines).
- **Stale container IDs**: containers are created once per run
  (deterministic `{project}-{service}-1` names from a fresh
  `uuid.uuid4()`-suffixed project) and never recreated by name mid-run —
  `docker compose start`/`stop` operate on the same container instance
  throughout, so no ID ever goes stale mid-script.
- **Race conditions**: the gateway host port is explicitly **re-fetched**
  (`get_actual_gateway_host_port`) after the gateway-restart scenario
  rather than assumed to survive a restart unchanged — a defensive
  choice, not a race. `RestartCount`/`is_running` are always read fresh
  immediately before each assertion, never cached across a wait.
- **False-positive PASS conditions** — walked through the brief's exact
  list:
  - *state never actually crashed*: `restart_count_before_transient_crash`
    is captured **before** the crash-inducing `docker exec`, and the
    poll condition is `count > restart_count_before_transient_crash`
    (strictly greater, not merely "count is truthy") — a no-op crash
    attempt would time out `poll_until` and raise, not silently pass.
  - *RestartCount does not increase*: same mechanism; Scenario 2 asserts
    exact equality against the absolute configured cap
    (`restart_count_after == EXPECTED_RESTART_MAX_ATTEMPTS`), independently
    reproduced in §2.
  - *readiness never degraded / never recovered*: both directions go
    through `poll_gateway_readyz(expect_ready=...)`, which raises
    `ReliabilityError` on deadline rather than returning a default —
    there is no code path where "never converged" reads as success.
  - *persistent state changed*: every recovery point re-reads `/state`
    and asserts `payload.get("value") == <captured baseline>`, raising
    on mismatch — confirmed live in §2 across both crash scenarios.
  - *resource restoration failed*: `with_memory_shrink_restored()`'s
    `finally` block only logs a `stderr` WARNING on a failed restore —
    it does not raise and does not append a `CheckResult`. This is the
    same gap `day-05-resource-restart-review.md` already found and
    labeled M-1 there (with its own adversarial reproduction showing it
    is not currently exploitable given this project's exact `6m` shrink
    target vs. `state`'s real footprint). Referenced here, not
    re-counted as a new finding — see §5.
  - *a Docker command unexpectedly failed*: every `docker`/`compose` call
    in the main path checks `returncode != 0` and raises
    `ReliabilityError` with the real `stderr` attached; none of the
    `run_docker`/`compose`/`docker_json` call sites in the main flow
    swallow a nonzero exit silently. The only two places a Docker command
    failure is *not* fatal are both intentional and explicitly logged: the
    `unpause` call in the outer `finally` (cleanup-path best-effort, by
    design, matching `compose_integration.py`'s own convention) and the
    memory-restore warning above.
- **Exception swallowing**: no bare `except:`/`except Exception: pass`
  anywhere in the file. The two narrow handlers in `main()`
  (`ReliabilityError`, `_TerminatedError`) both print the full detail and
  return a nonzero/143 exit code; anything else propagates uncaught (through
  the `finally` cleanup) as a real traceback and nonzero exit — correct,
  since a truly unexpected exception should fail loudly, not be coerced
  into a tidy `CheckResult`.
- **Cleanup failures / resource leaks**: `finally` always runs `compose
  down -t 10 -v` for the run's own project, and separately unpauses
  `state` first if a pause was left active by an earlier exception
  (`state_is_paused` flag). Independently confirmed empty
  `maops-reliability-*` container/network/volume listings after this
  review's own live run (§2).
- **Accidental dependence on existing developer resources**: none found
  in the script itself — it never reads or assumes another
  project's container/network/volume. The one real dependence this
  review surfaced (colliding with an already-running dev stack on port
  `8080`) is caused entirely by the local WSL/`docker.exe` shim silently
  discarding the script's own `GATEWAY_HOST_PORT=0` override before it
  ever reaches Compose — already covered in the Environment note above
  and already disclosed as I-1 in the prior reviews.
- **Test-ordering assumptions**: `tests/test_reliability_check.py` loads
  a fresh module instance per test class via `setUp()`
  (`load_reliability_check()` re-executes the file from disk each time),
  and every fixture (`_fake_sc`, `_spy_sc`) is constructed fresh per test
  method with no shared mutable module-level state — no cross-test
  ordering dependency found.
- **Cumulative `RestartCount` assumptions**: correctly handled — see the
  live-run confirmation in §2 (`before=1 after=3`, not `before=1
  after=4`), and the code comment at
  `reliability_check.py:811-819` explicitly documents why the delta form
  would have been wrong.
- **Docker Desktop-specific assumptions**: the cgroup v2 probe
  (`check_cgroup_v2_resource_limits`) is explicitly documented and coded
  as best-effort — an unavailable path is reported, not silently treated
  as failing or passing (`any_available` gates whether the "cgroup v2 not
  available" note is emitted, and only an actually-*wrong* value ever
  fails the check). The OOM-kill mechanism itself
  (`oom_score_adj` + memory pressure from a sibling `exec`) relies on
  standard Linux kernel/cgroup v2 behavior, not any Docker-Desktop-only
  feature, and the module docstring records that `docker
  kill`/`docker stop`/same-namespace-signal alternatives were tried and
  rejected by direct experiment first — this is exactly the kind of
  "verified, not assumed" approach the review brief is checking for.

No Critical or High finding surfaced in this section.

---

## 5. Findings

### M-1 (Medium): Day 5's new `check_compose.py` structural checks (`check_resource_limits`, `check_restart_policy`, `check_stop_grace_period`, and their parsing helpers `_parse_cpus`/`_parse_bytes`/`_parse_duration_seconds`) have zero persisted unit-test coverage

Confirmed by direct search: no file under `tests/` references any of
these six names (`tests/test_reliability_check.py` only exercises the
*differently-named*, Docker-runtime-facing siblings —
`check_resource_limits_applied` etc. — which are well covered; see §3).
There is no `tests/test_check_compose.py` at all, and there never has
been one for any day of this project (`check_compose.py` has always been
exercised only against the real rendered `compose.yaml`, not fixture
unit-tested) — so this is a continuation of a pre-existing project
pattern, not a new regression introduced this day, but the pattern
happens to bite hardest on exactly the two named-in-the-brief items
(*resource validation*, *restart-policy validation*) because this is
where the newest, most parsing-heavy logic landed.

Concretely untested by any repeatable, committed test: `_parse_duration_seconds()`'s
Go-duration-string regex path (`"1h30m"`, `"0s"`, an empty string, a
bare integer at the 3600-second nanosecond/second disambiguation
boundary), `check_restart_policy()`'s string-parsing of a malformed
retry count (`"on-failure:abc"`, `"on-failure"` with no colon), and
`check_resource_limits()`'s permissive-drift-beyond-target branch for
`mem_limit`/`pids_limit` specifically (only the CPU drift case has any
coverage at all, and that coverage is `day-05-resource-restart-review.md`'s
own ad hoc, not-committed reviewer probe — see that review's own L-1,
which found the real logic gap this test gap would have caught).
`day-05-resource-restart-review.md` independently verified the
*current* behavior against 19 hand-run adversarial cases and found it
correct except for L-1's lower-bound gap — but none of that verification
persists in the repository; a future regression in any of these six
functions would not be caught by `make test`, only by a human re-running
the same ad hoc probe or by the (much later, much more expensive)
`make reliability-check` gate.

**Recommend**: a `tests/test_check_compose.py` exercising
`check_resource_limits`/`check_restart_policy`/`check_stop_grace_period`/
the three parsing helpers against fabricated `config` dicts, mirroring
the fixture style `tests/test_reliability_check.py` already established
for the Docker-runtime-facing siblings. Not release-blocking — the
underlying logic is independently confirmed correct today (§2, plus
`day-05-resource-restart-review.md`'s probe), and the real compose.yaml
is re-validated by this same logic on every `make compose-check`/`make
quality` run — but it should close before Day 6 so this class of
regression is caught at the cheapest gate rather than not at all.

### L-1 (Low): No test asserts the inclusive upper timeout bound is *accepted*

Both `tests/test_app_platform_config.py` and
`tests/test_gateway_platform_config.py` test that a value **above** each
field's `MAX_*` constant is rejected, but neither tests that a value
**exactly equal to** the max (which the validator's own `0 < value <=
max_value` treats as valid) is accepted. A future off-by-one regression
that flipped `<=` to `<` would not be caught by the current suite.
Recommend one boundary-inclusive test per field
(`state_dependency_timeout_seconds == 30.0`, `gateway_upstream_timeout_seconds
== 60.0`, `timeout_safety_margin_seconds == 30.0`, each accepted).

### L-2 (Low): `gateway/platform_config.py`'s own timeout field does not get a `-Infinity` test, unlike `app/platform_config.py`'s

`tests/test_app_platform_config.py::test_negative_infinity_timeout_is_rejected`
has no analogue in `tests/test_gateway_platform_config.py` for
`gateway_upstream_timeout_seconds`, `state_dependency_timeout_seconds`, or
`timeout_safety_margin_seconds` — only `+Infinity` is tested there. Both
modules share the identical `_validate_timeout` shape
(`math.isfinite()` rejects both signs uniformly), so this is not a
behavioral gap today, only a coverage asymmetry between two
independently-maintained files meant to mirror each other. Low severity,
cheap to close (three one-line test additions).

### Info: Two findings already tracked by prior Day 5 reviews, not re-counted here

- `with_memory_shrink_restored()`'s warning-only (not raise, not
  `CheckResult`-recorded) restore-failure path — already `M-1` in
  `day-05-resource-restart-review.md`, independently reproduced there
  with a real forced-failure experiment. This review's own read of the
  code and its tests (§3, §4) reaches the identical conclusion
  independently; not re-scored here to avoid double-counting the same
  finding across sibling reviews.
- `tests/test_server.py` has no fast unit test analogous to
  `tests/test_gateway_server.py::UpstreamTimeoutTests` proving `app`'s
  own inner-hop `state_timeout_seconds` converts a slow `state` into a
  controlled `503` — already `M-1` in `day-05-health-timeout-review.md`.
  Independently re-confirmed present here (`state_delay_seconds` hook
  exists in `tests/test_server.py`'s shared fixture but no subclass ever
  sets it nonzero, while the gateway analogue exists and passes).

### Info: WSL/`docker.exe` shim environment note

Already disclosed as I-1 in `day-05-health-timeout-review.md` and as an
"Environment note" in the other two Day 5 reviews; independently
reproduced by this review from scratch (see the Environment note above)
with its own minimal repro rather than taken on the prior reviews' word.
Not a code defect and not scored.

---

## 6. Verdict

The reported unit-test growth (311 → 359, +48) is independently
confirmed exact, not merely plausible, and the new tests are
substantively adversarial where they matter most — the Day 5
timeout-hierarchy invariant (the actual closure of Day 3 finding A-6) has
the most thorough coverage in the entire new suite, including the
strict-inequality boundary case a shallower pass would have missed, and
the resource-restoration-under-exception tests cover exactly the two
exception shapes (`ReliabilityError` and an arbitrary other exception)
the brief asked about. `scripts/reliability/reliability_check.py` itself
passed a from-scratch adversarial read against the brief's full hazard
list with no Critical or High finding: no unsafe subprocess construction,
no fixed-sleep assertions, no unbounded loops, correct handling of the
cumulative (not per-episode) `RestartCount` semantics — independently
re-verified against a live real-Docker run in this review (§2) — and no
identified false-positive-PASS path among the six scenarios the brief
named, other than the one already-tracked, already-adversarially-tested
M-1 restoration-warning gap from `day-05-resource-restart-review.md`.

The one Medium finding this review adds (M-1 above) is a real,
independently-confirmed gap — the newest, most parsing-heavy Day 5 logic
in `check_compose.py` has no persisted regression test at all — but it
is a continuation of this project's pre-existing `check_compose.py`
testing pattern rather than a new regression, the underlying logic is
independently confirmed correct today by both this review's own live run
and a sibling review's ad hoc probe, and it is caught one gate later
(`make reliability-check`) regardless. Non-blocking.

TEST-ADVERSARIAL REVIEW PASS
