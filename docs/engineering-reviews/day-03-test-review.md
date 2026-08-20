# Day 3 Test-Quality Review — v0.3.0

Independent reviewer. Review only — no implementation files modified.
Every count in this report was independently re-derived from the working
tree and from `git show 8dbec96:...` (the Day 2 merge commit), not taken
on the implementation report's word.

**Environment constraint:** the Docker CLI is not available in this
review sandbox (WSL2 distro without Docker Desktop's WSL integration
enabled). All pure-Python `unittest` suites below were executed directly
and repeatedly. The three Docker-dependent validators
(`security_check.py`, `container_smoke.py`, `compose_integration.py`)
and `check_compose.py` (which shells out to `docker compose config`)
could **not** be executed live and were reviewed by source inspection
only. This is called out again wherever it affects a verdict.

---

## 1. Count reconciliation

| | claimed | independently verified |
|---|---|---|
| Day 2 baseline | 78 | **78** ✓ (re-extracted `tests/`, `app/`, `gateway/` at commit `8dbec96` into an isolated tree, ran `python3 -m unittest discover`: `Ran 78 tests`) |
| Day 3 current total | 195 | **195** ✓ (ran twice against the working tree: `Ran 195 tests ... OK` both times, 46.4s and 44.9s) |
| Net-new | +117 | **+117** ✓ (195 − 78 = 117, and independently confirmed by summing the per-file deltas below, which also total 117) |

The implementation's `+117` claim is correct. No correction needed here.

### Per-file breakdown (exact, via `unittest.TestLoader().countTestCases()`, one subprocess per module)

| file | Day 2 | Day 3 | delta | status |
|---|---:|---:|---:|---|
| `tests/test_config.py` | 18 | 24 | +6 | existing, grown |
| `tests/test_gateway_config.py` | 18 | 20 | +2 | existing, grown |
| `tests/test_gateway_healthcheck.py` | 2 | 2 | +0 | existing, unchanged |
| `tests/test_gateway_server.py` | 20 | 26 | +6 | existing, grown |
| `tests/test_healthcheck.py` | 2 | 2 | +0 | existing, unchanged |
| `tests/test_server.py` | 15 | 23 | +8 | existing, grown |
| `tests/test_version.py` | 3 | 3 | +0 | existing, unchanged |
| `tests/test_app_platform_config.py` | — | 11 | +11 | **new** |
| `tests/test_gateway_platform_config.py` | — | 9 | +9 | **new** |
| `tests/test_state_config.py` | — | 18 | +18 | **new** |
| `tests/test_state_healthcheck.py` | — | 2 | +2 | **new** |
| `tests/test_state_platform_config.py` | — | 15 | +15 | **new** |
| `tests/test_state_server.py` | — | 21 | +21 | **new** |
| `tests/test_state_storage.py` | — | 19 | +19 | **new** |
| **Total** | **78** | **195** | **+117** | |

Existing-file growth: 22. New-file total: 95. 22 + 95 = 117 — reconciles exactly two independent ways.

### New-file count correction

The implementation reports "16 files added." Enumerating the actual
untracked (`??`) paths in `git status`, excluding `__pycache__` and
excluding the four sibling `day-03-*-review.md` files produced by the
*other* concurrent Day 3 reviewers (compose/networking/persistence/
security — not this implementation's own output), the real count is
**20 new files**:

```
app/platform_config.py
gateway/platform_config.py
state/__init__.py
state/__main__.py
state/config.py
state/healthcheck.py
state/platform_config.py
state/server.py
state/storage.py
config/platform.json
docs/configuration.md
docs/networking.md
docs/persistence.md
tests/test_app_platform_config.py
tests/test_gateway_platform_config.py
tests/test_state_config.py
tests/test_state_healthcheck.py
tests/test_state_platform_config.py
tests/test_state_server.py
tests/test_state_storage.py
```

**20, not 16** — the implementation's count is short by 4. (If the four
sibling review docs are counted as well, the true untracked total is 24,
short by 8.) Neither reading produces 16; the discrepancy could not be
attributed to a specific plausible sub-count from here, so it is reported
as a plain correction rather than a diagnosed miscounting mechanism.

---

## 2. Unit-test quality (app/gateway/state, all service+config layers)

Overall: **high**. Every server test file (`test_server.py`,
`test_gateway_server.py`, `test_state_server.py`) boots a real
`ThreadingHTTPServer`/`HTTPServer` on an OS-assigned loopback port
(`port=0`) and drives it over a real `http.client` connection — no
mocking of the code under test anywhere in these three files. Downstream
dependencies ("upstream"/"state") are either a real second loopback
server (`_FakeUpstreamHandler`/`_FakeStateHandler`, with a genuine
`time.sleep`-based delay knob for timeout tests) or a real closed port
obtained via bind-then-close (`_closed_port()`), producing a genuine
OS-level connection-refused condition rather than a simulated one. No
fixed ports, no shared global mutable state, and teardown is symmetric
(`shutdown()` + `server_close()` + `thread.join(timeout=5)`) in every
`tearDown`.

Environment mutation is confined to `unittest.mock.patch.dict("os.environ",
..., clear=False)` as a context manager (6 occurrences, all in the three
`test_*_healthcheck.py` files) — this guarantees restoration even on
assertion failure. No unguarded direct `os.environ[...] = ...` assignment
exists anywhere in `tests/`.

No tautologies were found in any server/healthcheck test file: assertions
compare real HTTP responses against literal expected values, never
against a locally re-derived "prediction" of the implementation's own
output.

### Confirmed asymmetry: gateway timeout is tested, app's identical path is not

`tests/test_gateway_server.py:263–280` (`UpstreamTimeoutTests`) is a
genuine, well-built regression test: a real stub server with
`upstream_delay_seconds=0.5` against a `GatewayConfig` with
`upstream_timeout_seconds=0.1`, asserting the real socket timeout
converts to a controlled `503`. This directly and concretely exercises
the "timeout value from platform config actually changes behavior"
property the review brief calls out — good, load-bearing evidence, not
a shallow "config dict has the right key" test.

`app/server.py`'s `_call_state()` (app/server.py:46–79) has the
*identical* mechanism — `http.client.HTTPConnection(..., timeout=config.state_timeout_seconds)`,
also sourced from platform config
(`app/config.py:92`, `state_timeout_seconds=platform_cfg.dependency_timeout_seconds`)
— but `tests/test_server.py` has **no equivalent test class**. Grepping
its class list (`RootEndpointTests`, `HealthzEndpointTests`,
`ReadyzSuccessTests`, `ReadyzStateNotReadyTests`,
`ReadyzStateUnavailableTests`, `InfoEndpointTests`,
`StateGetForwardingTests`, `StateGetUnavailableTests`,
`StateIncrementForwardingTests`, `StateIncrementUnavailableTests`,
`NotFoundTests`, `UnsupportedMethodTests`,
`EndToEndConfigurationTests`, `NoTracebackDisclosureTests`) confirms
there is no `*Timeout*` class and no `delay_seconds` fixture usage in
this file. The unreachable-dependency case (`StateGetUnavailableTests` /
`ReadyzStateUnavailableTests`) is tested via a closed port
(instant refusal), which is a different failure mode from a slow-but-open
connection actually exceeding the configured timeout. See §7 (missing
regressions).

Dependency-failure tests are otherwise solid: `test_readyz_reports_unavailable_when_state_unreachable`,
`test_state_get_returns_controlled_503_when_state_unreachable`,
`test_state_increment_returns_controlled_503_when_state_unreachable`
(and gateway's equivalents) all hit a real closed port and assert a
controlled `503`/`error` body rather than a leaked exception —
`NoTracebackDisclosureTests` / `test_error_responses_never_contain_traceback_markers`
in both files back this with an explicit "Traceback" absence assertion.

`tests/test_healthcheck.py` (app) is a small but genuinely useful pair:
one positive test against the real server, one negative test that
shuts the server down mid-test and asserts `healthcheck.check()`
correctly returns `False` — this is exactly the Day 1 M-1 regression
class (bare-script-invocation breakage) the module's own docstring says
it closes, and it is a real in-process HTTP round trip, not a mock.
`test_gateway_healthcheck.py`/`test_state_healthcheck.py` follow the
identical, correct pattern.

### No SSRF-style regression test exists (property holds by construction only)

Neither `app/config.py`'s `state_host`/`state_port` nor
`gateway/config.py`'s `upstream_host`/`upstream_port` can currently be
influenced by request data — both are frozen dataclass fields resolved
once at process/config-load time, and no route handler reads a header,
query string, or path segment to select a destination
(`app/server.py:46-79`, confirmed by reading every `_route_*` function).
That said, grepping `tests/test_server.py` and `tests/test_gateway_server.py`
for `X-Forwarded`, `Host:`, `spoof`, or any header/path-based
destination-override attempt returns **nothing**. The safety property is
real today, but it rests entirely on code review, not on an automated
regression test that would catch a future refactor accidentally
threading request data into `_call_state`/the upstream call. See §7.

---

## 3. Persistence / storage-test quality (`tests/test_state_storage.py`, 19 tests)

Overall: **high, with one real gap and one CI-portability landmine**.

- Genuine `tempfile.TemporaryDirectory()` isolation throughout; no fixed
  paths, no `$HOME` dependency, no shared state between tests, correct
  cleanup.
- Malformed-state coverage is thorough: invalid JSON, non-object JSON,
  wrong `schema_version`, missing `value`, negative `value`, non-integer
  `value`, and — correctly — a **boolean** `value`
  (`test_boolean_value_raises_corrupted`, explicitly guarding against
  Python's `bool` being an `int` subclass, with a docstring explaining
  why). This is the right pattern.
- `test_write_failure_does_not_leave_a_stray_tmp_file` forces an
  `OSError` via `chmod(0o500)` on the target directory. This only works
  because the review/dev environment runs as a non-root user (verified
  `uid=1000`). **If this suite is ever run as root** (common for
  container-based CI runners, which Day 6 will likely introduce), `chmod`
  restrictions are bypassed by root, the write would silently succeed,
  and `assertRaises(OSError)` would fail loudly rather than silently
  false-passing — not a silent hole, but a portability trap worth fixing
  before Day 6 wires this into CI.
- `test_partial_write_never_visible_to_a_reader` (lines 160–171) does
  **not** inject an actual interrupted/partial write — its own docstring
  admits it "simulates" the atomicity claim by running 10 successive
  increment+read cycles and checking each read parses cleanly. That
  proves happy-path self-consistency; it does not prove the write-tmp/
  fsync/`os.replace` sequence (`state/storage.py:94-111`, sound on
  inspection) actually survives a real crash mid-write (no test kills the
  process, truncates the tmp file, or interrupts between `fsync` and
  `os.replace`). The test's name promises more than it delivers.
- Concurrency: no test exercises concurrent writers to the same store —
  an honest absence rather than a false claim (no test name or docstring
  claims concurrency safety), so this is a coverage gap, not a
  misrepresentation.

---

## 4. Configuration-test quality (`platform_config.py` × 3, 35 tests total; `*_config.py` × 3)

Overall: **thorough on the axes each author thought of; one real
production bug slipped through all three modules identically.**

`test_app_platform_config.py` (11), `test_gateway_platform_config.py` (9),
`test_state_platform_config.py` (15), plus `test_config.py`/
`test_gateway_config.py`/`test_state_config.py`'s own platform-config
integration points, collectively cover: malformed JSON, non-object JSON,
missing/wrong-type fields, boundary values (zero/negative/above-max
timeout), path-traversal attempts in `state_filename` (`".."`, `"."`,
path separators), env-path override, explicit-path-wins-over-env
precedence, and default behavior when the config file is absent. No
over-mocking: every test calls the real loader against a real temp file;
none of them monkeypatch the function under test.
`test_arbitrary_environment_is_not_consulted` (present in all three
`*_config.py` files) is a genuinely useful negative test confirming each
`*Config` surfaces only its documented fields even when unrelated env
vars are present.

### Confirmed production bug: `schema_version` accepts booleans in all three loaders

`app/platform_config.py:84-88`, `gateway/platform_config.py:84-88`, and
`state/platform_config.py:84-88` all validate identically:

```python
schema_version = data.get("schema_version")
if schema_version != SCHEMA_VERSION:
    raise ValueError(...)
```

There is no `isinstance(schema_version, bool)` guard. Because
`True == 1` in Python, `{"schema_version": true, ...}` is silently
accepted as schema version 1 in **all three** modules — reproducible by
constructing such a file and calling `load_platform_config`. This is the
exact bug class the codebase explicitly guards against elsewhere —
`state/storage.py`'s `test_boolean_value_raises_corrupted` for the
counter `value` field, and each `*platform_config.py`'s own boolean guard
on `dependency_timeout_seconds` — but the guard was never extended to
`schema_version` itself. None of the 35 platform_config tests exercise
`schema_version: true`/`false`, so nothing catches it.

### Cross-module consistency and drift risk (as requested: "would tests catch drift?")

`app/platform_config.py` and `gateway/platform_config.py` are
**byte-identical** except for prose in their module docstrings (confirmed
by diff). Per the CLAUDE.md architecture, this duplication is
intentional (each service is stdlib-only with no shared library), but no
test compares the two modules against each other or runs a shared
fixture set against both — each test file only imports and asserts
against its own module. Concretely: **drift has already started.**
`test_app_platform_config.py` (11 tests) includes
`test_integer_timeout_is_accepted` and `test_string_timeout_is_rejected`
that `test_gateway_platform_config.py` (9 tests) does not, despite the
two source modules being identical today. If a future edit patched one
module's validation and not its twin, no test anywhere would flag the
inconsistency. `state/platform_config.py` legitimately validates
different fields (`platform_name`, `state_filename` vs. app/gateway's
`dependency_timeout_seconds`) per its own scope, which is not itself a
problem — the risk is specifically in the app/gateway pair, which are
supposed to be (and are) identical.

`config/platform.json` itself is well-formed, non-secret, and consistent
with what all three loaders expect
(`schema_version: 1, platform_name, dependency_timeout_seconds: 3.0,
state_filename: "state.json"`).

---

## 5. Validator quality (source-code audit; live execution not possible — no Docker in this sandbox)

**`scripts/lint/check_source.py`** — genuinely closes the carried-forward
L-1 finding. It is AST-based (not substring matching), tracks `os`
import aliases (`import os as x`) and `from os import system as x`
rebindings via a real module-level `Import`/`ImportFrom` walk, and flags
`shell=True` on any call. Scans `app/`, `gateway/`, `state/` only — never
`scripts/`/`tests/`, honestly scoped per its own docstring. This is solid
and its "closes L-1" claim checks out on inspection.

**`scripts/lint/check_dockerfile.py`** — line/instruction-aware (handles
continuations, comments), validates digest-pinned `FROM`, non-root
`USER`, exec-form `HEALTHCHECK` CMD, no `sudo`, no remote `ADD`, no
secret-looking `ARG`/`ENV` names, explicit `WORKDIR`, exec-form
runtime command. It hardcodes the expected HEALTHCHECK CMD to
`["python3", "-m", "app.healthcheck"]` — correct, because the single
image's baked-in Dockerfile `HEALTHCHECK` is only ever the `app` role's
default (the Dockerfile's own comment at line 61 acknowledges "each role
probes its own"); `gateway`/`state` correctly override this per-service
in `compose.yaml`, and `check_compose.py`'s `check_healthchecks()`
verifies each override independently. This is not a gap.

**`scripts/compose/check_compose.py`** — this is **not** stale from
Day 2; it genuinely validates the new Day 3 topology: exact network
membership per service (`check_network_membership`), an explicit,
separate assertion that gateway and state share no network
(`check_gateway_state_isolation`, deliberately not left implicit),
`backend: internal: true` / `edge` not internal
(`check_top_level_networks`), the named `state_data` volume mounted only
into `state` at `/data` (`check_state_volume`), and the `configs:`
mount for `platform.json` at `/etc/maops/platform.json` on all three
services (`check_config_object`). `check_upstream_targets()`
(lines 389-447) is a real widening of the Day 2 L-1 closure: it now
cross-checks **both** gateway's `UPSTREAM_HOST` and app's `STATE_HOST`
against the real service set, the real target port, and real shared
network membership — matching the Day 3 scope note precisely.

**`scripts/compose/compose_integration.py`** — the strongest single file
in this review, on inspection. `check_startup_ordering()` (lines
185-207) is a genuine timestamp proof: it compares the dependency's
first Docker-recorded `healthy` transition (`State.Health.Log`) against
the dependent's `State.StartedAt`, not merely "both eventually became
healthy" — this is precisely what Day 2 finding M-1 called out as
missing. `check_config_mount_readonly()` (lines 210-227) performs a real
`[C]+[D]` check: reads `Mounts[].RW` **and** attempts a real
`echo probe > /etc/maops/platform.json` inside the container, asserting
it is rejected — a genuine kernel-level proof, correctly implemented and
correctly generic across all three service roles. Network isolation
between gateway and state is proven by real DNS resolution attempts in
both directions inside the containers (`dns_resolves()`), not by
membership-set comparison alone.

### Confirmed defect: the reused `[D]` rootfs-write-fails check is not role-aware

`scripts/verify/security_check.py:357-377`
(`check_kernel_readonly_write_fails`) performs a real write-rejection
check, but its "service kept functioning" half is hardcoded:

```python
conn_check = run_docker(["exec", container_name, "python3", "-m", "app.healthcheck"])
still_serving = conn_check.returncode == 0
```

This function was written for `security_check.py`'s own `main()`, which
only ever boots the `app` role via a bare `docker run` — hardcoding
`app.healthcheck` was correct there. `compose_integration.py:526-544`,
however, **reuses this exact function generically** in a loop over all
three Compose-managed containers (`state`, `app`, `gateway`) at line 543:
`sc.check_kernel_readonly_write_fails(container, 0)`. Inside a `state` or
`gateway` container, no `app` process is listening (each container's
PID 1 is `python3 -m <role>`, confirmed by the very next check in the
same loop, `get_pid1_cmdline`), so `python3 -m app.healthcheck` will
always fail to connect and `still_serving` will always be `False` for
those two roles — making `check_kernel_readonly_write_fails` **always
report FAIL for `state` and `gateway`**, regardless of whether the
rootfs write was genuinely rejected. (The unused `port: int` parameter on
this function, never referenced in its body, is a secondary signal that
this function was not adapted for reuse.)

`compose-test` (this script) is wired into `make release-check`
(`Makefile:68`), so — if this finding holds on a live Docker run, which
this sandbox cannot confirm — `make release-check` cannot currently pass
this specific check for 2 of the 3 services. **This is reported as a
high-confidence static-analysis finding, not confirmed by execution**,
because Docker is unavailable in this review environment.

**`scripts/smoke/container_smoke.py`** — correctly and honestly scoped
(Day 3 docstring update explicitly notes it only ever exercises the
isolated `app` role via bare `docker run`, so `/readyz` is expected to
be `503`, not `ready`, and defers the full-chain proof to
`compose_integration.py`). Unique `maops-smoke-<uuid>` naming, dynamic
loopback port, `try/finally` cleanup — consistent with the project's
safety constraints.

**`scripts/verify/security_check.py`** (beyond the bug above) — unique
`maops-security-<uuid>` naming, `try/finally` cleanup, dynamic port,
`[A]/[B]/[C]/[D]` labeling is applied consistently and honestly (e.g.
`check_runtime_cap_drop_all` is correctly labeled `[C]`, never claimed
as kernel-enforced on its own).

---

## 6. Day 2 closure verdicts

| Finding | Verdict | Evidence |
|---|---|---|
| M-2: PID 1 / SIGTERM regression test | **CLOSED** | `check_lifecycle_docker_stop()` (security_check.py:394-427) issues a real `docker stop --time 10`, asserts `ExitCode == 0`, `Status == exited`, and `elapsed < 10s`. Code is sound on inspection; not re-executed live here (no Docker). |
| M-3 / M-1: Compose startup-ordering proof | **CLOSED** | `check_startup_ordering()` (compose_integration.py:185-207) is a genuine `dependency_healthy_at <= dependent_started_at` comparison using Docker's own recorded timestamps, not eventual-healthy polling. Not re-executed live here. |
| UPSTREAM_HOST-vs-real-service cross-check | **CLOSED** | `check_upstream_targets()` (check_compose.py:389-447) cross-checks both `UPSTREAM_HOST` (gateway) and `STATE_HOST` (app) against the real service set, port, and shared network — correctly widened per the Day 3 scope note, not merely carried forward unchanged. |
| Compose-managed [D] read-only-write proof | **PARTIALLY CLOSED — not functional for 2 of 3 services** | The mechanism is right in principle and `check_config_mount_readonly()` (the config-mount counterpart, written fresh for Day 3) is correctly generic. But the *reused* rootfs-write-fails check (`check_kernel_readonly_write_fails`, inherited from Day 1/2 `security_check.py`) is hardcoded to `app.healthcheck` and will always report FAIL for `state`/`gateway` when invoked from `compose_integration.py`. See §5. |
| `os.system`/`os.popen` alias detection | **CLOSED** | `check_source.py` tracks both `import os as x; x.system(...)` and `from os import system as x; x(...)` via a real AST import-alias walk, not literal substring matching. |

---

## 7. Flakiness assessment

**Pure-Python suite (195 tests, no Docker):**
- Ran twice back-to-back: `195/195 OK` both times (46.4s, 44.9s). Deterministic.
- No fixed ports anywhere in `tests/` (grepped for hardcoded non-zero
  `127.0.0.1` port literals — none found); every server-backed test file
  uses `port=0` / OS-assigned ports.
- `time.sleep()` appears exactly twice in `tests/`
  (`test_server.py:37`, `test_gateway_server.py:43`), both inside the
  shared fake-upstream/fake-state handler's deliberate `delay_seconds`
  knob used only by the timeout-conversion tests — a legitimate use, not
  an anti-pattern. One real risk: `UpstreamTimeoutTests` configures a
  `0.5s` delay against a `0.1s` timeout; on a sufficiently loaded CI
  runner this margin could theoretically compress. Not a problem
  observed in two local runs, but worth widening before Day 6 CI lands.
- No unguarded `os.environ[...]` mutation; all 6 occurrences use
  `patch.dict(..., clear=False)` as a context manager.
- `_closed_port()` (bind-then-close to get a genuinely free, unbound
  port) has an inherent, explicitly-documented, extremely-low-probability
  TOCTOU window — accepted as-is, correctly labeled in its own docstring
  rather than silently relied upon.
- No shared mutable module-level state, no order-dependence observed
  (each `TestCase` builds and tears down its own server/tempdir in
  `setUp`/`tearDown`).

**Docker-based scripts (not executable in this sandbox):** assessed by
code reading only.
- `compose_integration.py` uses `time.monotonic()` for all deadline
  arithmetic (`poll_until`, `poll_gateway_readyz`) — immune to wall-clock
  adjustment. Wall-clock timestamps (`State.StartedAt`,
  `State.Health.Log[].End`) are used only where the proof genuinely
  requires comparing two Docker-recorded events against each other,
  which is correct and unavoidable for that specific claim.
  `parse_docker_timestamp()` explicitly truncates (never rounds up)
  excess nanosecond digits Python's `datetime.fromisoformat` can't parse,
  keeping the ordering comparison conservative.
  Deadlines are generous (`UP_TIMEOUT_SECONDS=150`,
  `HEALTHY_DEADLINE_SECONDS=30`, `POLL_INTERVAL_SECONDS=0.5`).
- `dns_resolves()` (the gateway↔state isolation proof) execs
  `python3 -c "socket.gethostbyname(...)"` inside each container per
  direction — a real resolution attempt each time, not cached; no
  obvious flakiness source on inspection, but this project's own
  environment cannot confirm it against real Compose DNS.
- Teardown in all three Docker scripts is `try`/`finally`-guarded and
  uses unique, project-prefixed names (`maops-smoke-<uuid>`,
  `maops-security-<uuid>`, `maops-compose-<uuid>`), consistent with the
  project's safety constraints; `compose_integration.py`'s `down -v`
  is scoped to its own uniquely-named project, never a global operation.

---

## 8. Highest-value missing regressions

1. **`app` → `state` timeout-to-controlled-503 test**, mirroring
   `test_gateway_server.py`'s `UpstreamTimeoutTests` exactly. The
   mechanism (`app/server.py:_call_state`, timeout sourced from platform
   config) is structurally identical to gateway's already-tested one but
   has zero direct test coverage today.
2. **An explicit SSRF-style regression test** for both hops: send a
   request carrying a spoofed destination-looking header (e.g.
   `X-Forwarded-Host`) or path, and assert the real outbound call target
   is unaffected. Currently this safety property is proven only by code
   construction, not guarded by a test.
3. **`schema_version: true`/`false` rejection test**, plus the
   corresponding `isinstance(..., bool)` guard, applied identically to
   all three `platform_config.py` modules (see §4).
4. **A genuine interrupted/partial-write test** for `state/storage.py` —
   truncate or kill between the `fsync` and `os.replace` steps (or
   monkeypatch `os.replace` to raise after the tmp file is written) and
   assert the previous good state is still readable — replacing the
   current "10 successive good writes" proxy in
   `test_partial_write_never_visible_to_a_reader`.
5. **A cross-module consistency test** (shared fixture/parametrized test
   run against `app.platform_config`, `gateway.platform_config`, and
   `state.platform_config`'s common surface) to catch behavioral drift
   between the intentionally-duplicated app/gateway modules, which have
   already drifted in test coverage (§4) even though their source is
   still identical.
6. **A role-aware fix (or per-role parameterization) for
   `check_kernel_readonly_write_fails`**, so the Compose-managed `[D]`
   read-only-write proof can actually pass for `state`/`gateway`, not
   just `app` (see §5/§6).

---

## 9. Severity counts

| Severity | Count | Findings |
|---|---:|---|
| High | 2 | `schema_version` boolean-bypass in all 3 platform_config modules (§4); `check_kernel_readonly_write_fails` role-mismatch bug breaking the Compose `[D]` proof for state/gateway (§5/§6) |
| Medium | 2 | Missing app→state timeout regression test (§2/§7); no SSRF-style regression test for either hop (§2/§7) |
| Low | 3 | `chmod(0o500)`-as-root portability landmine in storage tests (§3); `test_partial_write_never_visible_to_a_reader` overclaims what it proves (§3); app/gateway platform_config test-coverage drift despite identical source (§4) |

---

## 10. Release blockers

- **Blocker:** the `schema_version` boolean-bypass (§4, High) is a
  reproducible correctness bug in production code across all three
  services, not merely a test gap — recommend fixing before treating
  v0.3.0 as release-ready.
- **Blocker (pending live confirmation):** the `check_kernel_readonly_write_fails`
  reuse bug (§5/§6, High) is wired into `make release-check` via
  `compose-test`. On the evidence available from source inspection, this
  check will fail every real run for the `state` and `gateway`
  containers. This could not be confirmed by execution in this sandbox
  (no Docker available) — **whoever runs `make release-check` with real
  Docker next should treat this as the first thing to verify**, since if
  confirmed it means the release gate cannot currently pass at all.
- Not blockers, but should land before Day 4: the two Medium missing-
  regression gaps (§8 items 1–2) and the Low findings (§8 items 4–5,
  and the chmod-as-root landmine before Day 6 CI work begins).

---

## 11. Final test-quality verdict

The Day 3 test suite is **substantially well-engineered** at the unit
level: real servers over real sockets on dynamic ports, no mocking of
code under test, symmetric setup/teardown, disciplined environment and
filesystem isolation, and thorough malformed-input coverage for both the
storage layer and the three platform-config loaders. The count
reconciliation the implementation reported (+117, matching Day 2's 78 →
Day 3's 195) is accurate at both the total and the per-file-delta level.

The suite's actual weaknesses are not in test *hygiene* (isolation,
flakiness, tautology) but in **coverage symmetry and a genuine production
bug the existing tests don't reach**: a boolean-vs-int validation gap
that exists identically in three duplicated modules and that the
project's own established pattern (guard against `bool`-as-`int`) should
have caught, an untested code path in `app` that mirrors a well-tested
one in `gateway`, and — most consequentially — a Day 2 closure claim
(`[D]` read-only-write proof extended to Compose-managed containers)
that, on source inspection, does not actually function for two of the
three services due to a validator-reuse bug, and which this review could
not confirm or refute by execution because Docker is unavailable in this
sandbox. Recommend fixing the schema_version bug and either fixing or
re-verifying the `check_kernel_readonly_write_fails` reuse bug against
real Docker before treating v0.3.0 as release-ready; everything else in
this report is quality-improvement, not a blocker.
