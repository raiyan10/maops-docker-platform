# Day 2 Test-Quality Review — MAOps Docker Platform v0.2.0

**Reviewer role:** independent Day 2 test-quality reviewer (review only —
no implementation changes made). **Scope:** all 78 unit tests under
`tests/`, and every project-specific validation script (`scripts/lint/`,
`scripts/smoke/`, `scripts/verify/`, `scripts/compose/`). Companion
documents `docs/engineering-reviews/day-02-compose-review.md` and
`docs/engineering-reviews/day-02-security-review.md` already performed
deep, adversarially-verified reviews of Compose structural/runtime
correctness and container security posture respectively; this review
cross-references their findings rather than re-deriving them, and adds
new evidence only where those reviews did not already adversarially test
something (most notably `check_lifecycle_docker_stop`'s actual
regression-catching behavior — see Finding V-1 below).

All evidence in this document was independently reproduced: `python3 -m
unittest discover` run directly (twice) and via `make test`/`make
quality`, `scripts/lint/check_dockerfile.py` and `scripts/lint/
check_source.py` probed in-process with synthetic adversarial inputs
(never by editing the tracked `Dockerfile` or `app`/`gateway` source),
and one real, disposable, uniquely-named Docker container built to
adversarially test `check_lifecycle_docker_stop`'s detection logic
against a deliberately broken SIGTERM handler.

---

## 1. Test-count reconciliation

The implementation report's claim — **"36 new gateway/healthcheck
tests"** against a **34 → 78** total — is **inaccurate**. The true
net-new count is **44**, and it is not confined to "gateway/healthcheck"
files.

### Ground truth, file-by-file (verified against Day 1 merge commit `27d8e9b`)

| File | Day 1 (`27d8e9b`) | Day 2 (current) | Δ | Note |
|---|---:|---:|---:|---|
| `tests/test_config.py` | 18 | 18 | 0 | byte-identical to Day 1 (`git diff` empty) |
| `tests/test_server.py` | 13 | 15 | **+2** | `test_patch_to_known_path_returns_405`, `test_custom_host_and_port_from_env_actually_bind` — Day 1 app-side follow-ups, **not gateway/healthcheck** |
| `tests/test_version.py` | 3 | 3 | 0 | 0 new tests, but 2 existing tests modified (see §4) |
| `tests/test_gateway_config.py` | — | 18 | **+18** | new |
| `tests/test_gateway_healthcheck.py` | — | 2 | **+2** | new |
| `tests/test_gateway_server.py` | — | 20 | **+20** | new |
| `tests/test_healthcheck.py` | — | 2 | **+2** | new — closes Day 1 **M-1**, tests **`app/healthcheck.py`**, not the gateway |
| **Total** | **34** | **78** | **+44** | |

Independently confirmed by running the suite: `Ran 78 tests ... OK` (see
§7).

### Why "36 new gateway/healthcheck tests" is wrong

- **Undercount.** Even the most generous reading — bucketing every file
  whose name contains "gateway" *or* "healthcheck" — totals **42**
  (`test_gateway_config.py` 18 + `test_gateway_healthcheck.py` 2 +
  `test_gateway_server.py` 20 + `test_healthcheck.py` 2), not 36. The
  narrower, gateway-only reading is 40. Neither matches the claimed 36;
  this review does not speculate about which specific tests the
  implementation report's author omitted to arrive at that number, only
  that it is short by 6–8 against every plausible bucketing.
- **Omission.** The claim's framing ("gateway/healthcheck") entirely
  leaves out the **2 new tests added to `tests/test_server.py`**
  (`test_patch_to_known_path_returns_405`,
  `test_custom_host_and_port_from_env_actually_bind`), which are real,
  legitimate new tests but belong to neither bucket — they are Day 1
  `app`-side test-review follow-ups (PATCH-method coverage, and an
  end-to-end `load_config()` → `build_server()` composition test). These
  bring the true total to 44, not 42, let alone 36.
- **Pre-existing precedent for count drift.** As an aside (not a Day 2
  defect): `docs/engineering-reviews/day-01-test-review.md` itself
  reported a `test_config.py: 17` / `test_server.py: 14` split that does
  not match the actual Day 1 merge commit (`test_config.py` was already
  18, `test_server.py` was 13) — the totals happened to both sum to 34,
  which is presumably how the discrepancy went unnoticed. This is
  mentioned only to establish that this class of error (a plausible-
  looking total masking a wrong per-file breakdown) has happened in this
  repository's own release evidence before, and is exactly the kind of
  claim this review exists to catch.

### Correct characterization of the 44

- **40** are genuinely new gateway coverage (`test_gateway_config.py`,
  `test_gateway_healthcheck.py`, `test_gateway_server.py`).
- **2** are a new *app*-side healthcheck unit test file
  (`test_healthcheck.py`), closing Day 1 finding **M-1** (`app/
  healthcheck.py::check()` previously had zero direct unit coverage) —
  this is a Day 1 follow-up closure that Day 2's own scope statement in
  `.claude/CLAUDE.md` does not even list (it names only M-2/M-3), so M-1
  being closed too is an unadvertised bonus, not a defect, but it does
  mean "gateway/healthcheck" as a label conflates two different pieces
  of work (new gateway feature tests vs. a Day 1 debt-closure test for
  the *existing* app).
- **2** are additions to the existing Day 1 `test_server.py`, unrelated
  to the gateway by name or subject.

---

## 2. Category-by-category assessment (as scoped by this review's brief)

| Category | Verdict |
|---|---|
| Gateway config tests (`test_gateway_config.py`, 18) | Solid coverage of `parse_port`/`load_config`; real gaps noted in Finding L-3/L-4 below (parity with app's parser tests; unvalidated `int()` quirks) |
| Gateway HTTP behavior (`test_gateway_server.py`, 20) | Real server, real loopback fake-upstream, real closed-port "unreachable" fixture — no mocking of `gateway.server`'s own dispatch logic. Strong. |
| Health/readiness semantics | `/healthz` proven to never touch upstream (`use_fake_upstream=False` + still 200) and `/readyz` proven to reflect upstream state in both directions. Correct and non-tautological. |
| Upstream failure handling | `ReadyzUpstreamUnavailableTests`, `UpstreamInfoUnreachableTests` — real connection-refused via a genuinely closed port, not a mock exception. Correct. |
| Malformed upstream response | `UpstreamInfoMalformedResponseTests` (non-JSON body), `UpstreamInfoNonDictResponseTests` (valid JSON, wrong type) — both real HTTP bodies from a real fake server. Correct, and distinguishes the two failure modes `_call_upstream` actually distinguishes in source. |
| Timeout behavior | `UpstreamTimeoutTests` uses a real 0.5s server-side delay against a patched 0.1s client timeout — a real, deterministic timeout, not a mocked `socket.timeout`. Confirmed the patch target (`gateway_server.UPSTREAM_TIMEOUT_SECONDS`) is correct given the `from ... import` binding. Not flaky by construction. |
| HEAD | Covered on `/healthz` (gateway) and `/does-not-exist` (both app and gateway); correctly asserts empty body + present `Content-Length`. |
| 404 / 405 / 503 | All three present with `Allow` header assertion on 405 and no-traceback assertions layered on top. Retained from Day 1 pattern, correctly extended to the gateway's own route table. |
| Safe metadata | `RootEndpointTests` on the gateway asserts an exact key set (`service`/`version`/`status`) — no env leakage possible by construction (gateway's `/` never touches `os.environ` beyond `load_config()`'s four named variables). App's own `InfoEndpointTests` (safe-fields, no-env-leak) retained unchanged. |
| App tests retained | Confirmed: `test_config.py` (18) and the pre-existing part of `test_server.py` are unmodified/untouched beyond the two additions in §1. No Day 1 coverage was deleted. |
| PATCH / end-to-end configuration Day 1 follow-ups | Confirmed present and real (§1) — the end-to-end test genuinely binds a free OS-assigned port, runs `load_config()` → `build_server()` → real thread → real HTTP GET, not a mock. |
| Docker HEALTHCHECK regression protection | `check_dockerfile.py`'s new exact-match `HEALTHCHECK CMD` check adversarially verified in §3 (V-2) — genuinely catches the M-1 bare-script regression. |
| Version drift tests | `test_version.py`'s two hardcoded `"0.1.0"` literals were removed in favor of reading the real `VERSION` file (see §4) — a real fix, not a new test, but directly responsive to the "version drift" framing of this review's brief. |
| PID 1 / SIGTERM automated validation | Not a `tests/` unittest — lives in `scripts/verify/security_check.py::check_lifecycle_docker_stop` (closing M-2) and is exercised again against Compose-managed containers by `compose_integration.py`. Adversarially verified in §3 (V-1): this review is the first to prove the check's detection logic actually fails a genuinely broken SIGTERM handler, rather than merely observing it pass under normal conditions. |
| Compose structural tests | `scripts/compose/check_compose.py`, 10 checks — already deeply, adversarially reviewed in `day-02-compose-review.md`. No new defects found on independent re-reading; cross-referenced, not re-litigated here. |
| Compose runtime integration tests | `scripts/compose/compose_integration.py`, 25 inspection checks — already deeply reviewed in `day-02-compose-review.md` (which itself found Medium finding M-1 there: `depends_on: condition: service_healthy` ordering is proven only statically, never at runtime). This review's independent reading concurs with that finding and does not add a new one on top of it. |

---

## 3. Adversarial challenges performed by this review

**V-1 (new evidence, not present in the companion security review) —
`check_lifecycle_docker_stop` genuinely catches a broken SIGTERM
handler, not just a working one.**

The security review's own text describes this check as "independently
observed to pass" under normal conditions — i.e. against the real,
correctly-implemented `app`/`gateway` signal handlers. Neither companion
review adversarially broke the handler to confirm the check's `passed =
exit_code == 0 and status == "exited" and elapsed < grace` logic actually
flags a regression. This review did:

```
docker run -d --name maops-adversarial-sigterm-test-<pid> \
  python:3.13-alpine sh -c 'trap "" TERM; sleep 300'
docker stop --time 3 maops-adversarial-sigterm-test-<pid>
```

Result: `ExitCode: 137`, `Status: exited`, elapsed ≈ 3.27s (≥ the 3s
grace given — Docker force-killed via SIGKILL after the grace period
elapsed, exactly as it would after the real 10s production grace period).
Under the check's actual condition, `exit_code == 0` is already `False`
(137 ≠ 0), so `passed` is unconditionally `False` regardless of the
elapsed-time term. **Confirmed: the check has real discriminating power,
not just a tautological pass.** Container removed immediately after
(`docker rm -f`); no other resource touched.

**V-2 — `check_dockerfile.py`'s HEALTHCHECK/FROM checks probed with 13
synthetic instruction-line variants** (never the tracked `Dockerfile`;
probed by calling `check_healthcheck()`/`check_from()` directly against
in-memory strings):

- Bare-script HEALTHCHECK form (`["python3", "app/healthcheck.py"]`,
  the exact Day 1 M-1 regression) → **caught**.
- Shell-form (non-array) `HEALTHCHECK CMD python3 -m app.healthcheck` →
  **caught**.
- Correct form, with/without extra whitespace, with leading
  `--interval`/`--timeout` flags → **all correctly pass**.
- Valid 64-hex-char digest → **passes**; 63-char, non-hex, and
  uppercase-prefix digests → **all correctly caught**; missing digest,
  `:latest` + digest, and wrong base-image policy → **all correctly
  caught**.

No false positive or false negative found in either check under these
13 variants.

**V-3 — `check_source.py`'s `os.system`/`os.popen` detection re-probed
against a synthetic fixture file** (not a tracked source file):

```python
import os as sneaky
sneaky.system("echo still-a-real-shell-exec")
```

Result: **not flagged** — confirming the single-hop import-aliasing
bypass the Day 1 test review already reported as **L-1** is still present
and unchanged (same code, only the scanned directory list grew to
include `gateway/`). This is not a new Day 2 regression — it is the same
gap, now reachable from a second directory where it happens not to
currently matter (`gateway/` contains no `os.system`/`os.popen` call at
all today). Recorded here as **L-1 (carried forward, scope widened)**.

**V-4 — `docker ps --filter` OR-vs-AND semantics in `make clean`'s
cleanup path**, tested empirically since a wrong assumption here would
silently under-clean:

```
docker ps -aq --filter 'name=^maops-smoke-' --filter 'name=^maops-security-'
```

against three synthetic containers (`maops-smoke-test1`,
`maops-security-test2`, `unrelated-container3`) confirmed the two
same-key filters combine as **OR** (both project containers matched, the
unrelated one did not) — `make clean`'s cleanup logic is correct, not a
finding.

---

## 4. Findings

### Medium

**M-1 — The implementation report's test-count claim is materially
wrong and must not be used as release evidence as-is.**
- **What:** "36 new gateway/healthcheck tests" against the true 44
  net-new tests (§1). Understates the real total by 6–8 under every
  reading, and its own framing ("gateway/healthcheck") omits the 2 new
  Day 1 `app`-side follow-up tests in `test_server.py` entirely.
- **Why it matters:** this repository's stated philosophy
  (`.claude/CLAUDE.md`) is that security/quality claims are always
  backed by evidence and labeled honestly; a test-count claim is the
  simplest possible piece of release evidence, and it is the one this
  review found to be wrong. If left uncorrected in whatever document
  the implementation report becomes part of, it would misrepresent both
  the scale and the composition of Day 2's test investment.
- **Fix:** correct the claim to "44 new tests: 40 gateway-specific (config/
  healthcheck/server), 2 closing Day 1 finding M-1 for the app's own
  healthcheck, and 2 closing Day 1 app-side PATCH/end-to-end-config
  gaps" — or simply cite the table in §1.
- **Not a release blocker** for v0.2.0 itself (the tests exist, pass,
  and are individually sound — this is a documentation-accuracy finding,
  not a code defect).

### Low

**L-1 — `os.system`/`os.popen` detection bypassed by import aliasing
(carried forward from Day 1, scope now includes `gateway/`).** See V-3.
Unchanged code; not a new Day 2 regression; gateway source does not
currently exercise the gap. Already reported as Day 1 finding L-1 in
`day-01-test-review.md`; recorded here only because this review's brief
asked for false-negative coverage of `scripts/lint/` and the scanned
scope changed.

**L-2 — Tautological "arbitrary environment is not consulted" test,
now duplicated into the gateway config tests.**
`tests/test_gateway_config.py::LoadConfigTests::
test_arbitrary_environment_is_not_consulted` (and its pre-existing Day 1
twin in `test_config.py`) only asserts
`vars(config).keys() == {expected field names}` after loading config
from an environment containing `SECRET_TOKEN`/`AWS_SECRET_ACCESS_KEY`.
Because `GatewayConfig`/`AppConfig` are `@dataclass(frozen=True)` with a
fixed, hardcoded field list, this assertion is guaranteed to hold
regardless of whether any given environment variable was actually read —
it would not catch a hypothetical bug where, say, `config.name` were
populated from `SECRET_TOKEN` instead of always being `DEFAULT_NAME` (the
test never asserts individual field *values* here at all, unlike the
adjacent `test_blank_hosts_fall_back_to_defaults`, which does). The test
name promises more than the assertion delivers. Not a security issue in
practice (both `load_config()` implementations are simple enough to read
and are genuinely narrow), but the test itself does not prove what it
claims to prove, and this weakness was just copy-pasted into a second
file rather than caught.

**L-3 — Coverage-parity gap between `test_config.py`'s and
`test_gateway_config.py`'s `ParsePortTests`.**
App's `ParsePortTests` (11 tests) covers `whitespace_only_is_rejected`,
`float_like_value_is_rejected`, and `far_above_max_is_rejected`. Gateway's
`ParsePortTests` (9 tests) covers none of these three, despite
`gateway/config.py::parse_port` being a separate, independently
maintained implementation of the same contract (not shared code) —
gateway does add one test app's version lacks
(`test_error_message_names_the_variable`). Verified `gateway.config.
parse_port` currently handles all three cases identically to the app's
(rejects `"   "`, rejects `"3.14"`, rejects out-of-range magnitudes) —
so there is no live defect today, but the three cases are unproven for
gateway's own code path, which is exactly the kind of gap that survives
a future edit to one implementation and not the other.

**L-4 — `parse_port` accepts Python `int()` quirks that look like
malformed input, untested in both app and gateway suites.**
Confirmed empirically: `int("8_080") == 8080` (digit-group underscore)
and `int("+80") == 80` (explicit sign) both parse successfully through
`parse_port` in both `app/config.py` and `gateway/config.py`. Neither
`test_malformed_value_is_rejected` test exercises either form. Cosmetic/
low-risk (an operator would have to intentionally or accidentally type a
Python-style numeric literal into an env var), but it means
"malformed_value_is_rejected" coverage is narrower than its name implies
in both modules.

**L-5 — TOCTOU free-port pattern (`_closed_port()` / bind-then-close
probes), now used by 5 new gateway test classes.**
`tests/test_gateway_server.py::_closed_port()` and the free-port probe in
`tests/test_server.py::EndToEndConfigurationTests` both bind a loopback
socket, read the OS-assigned port, and immediately close it to obtain a
"currently free" port for a fixture — an inherent, explicitly
self-documented ("an accepted, extremely-low-risk pattern") race between
`close()` and reuse by an unrelated process on the same host. This is a
pre-existing Day 1 pattern (the same trick appears in `scripts/smoke/
container_smoke.py`, unchanged), not a new Day 2 problem, but Day 2
duplicates it into 5 more test classes
(`ReadyzUpstreamUnavailableTests`, `UpstreamInfoUnreachableTests`,
`NotFoundTests`, `UnsupportedMethodTests`, `NoTracebackDisclosureTests`,
all via `use_fake_upstream=False`). No flake observed across multiple
repeated full-suite runs in this review (§7). Recorded as a known,
accepted, low-probability risk that widens in surface area, not as a
new class of bug.

### Informational (no action needed)

- `test_version.py`'s two `"0.1.0"` hardcoded literals were replaced
  with a read of the real `VERSION` file — a genuine version-drift-risk
  fix (Day 1's tests would otherwise have required a manual edit on
  every version bump, and would have silently kept comparing against a
  stale literal if `get_version()` broke in a way that still returned a
  constant string). Zero net-new tests from this change; flagged here
  only because "version drift tests" was explicitly in this review's
  scope.
- `check_lifecycle_docker_stop` (M-2) and `check_dockerfile.py`'s
  HEALTHCHECK/digest checks (M-1, Dockerfile side) both survived
  adversarial mutation with zero false positives/negatives (§3, V-1/V-2).
- `make clean`'s multi-`--filter` cleanup logic behaves as OR (correct),
  independently confirmed empirically rather than assumed (§3, V-4).

---

## 5. What this review did **not** re-litigate

- Compose structural/runtime correctness beyond confirming no new defect
  on independent reading — see `day-02-compose-review.md`, including its
  own Medium finding (M-1 there: `depends_on` ordering is proven only
  statically).
- Container security posture (capabilities, read-only rootfs enforcement
  at the kernel level, etc.) — see `day-02-security-review.md`, including
  its own Medium finding (M-1 there: read-only rootfs is proven at [C]
  but not independently at [D] by the automated suite for
  Compose-managed containers).
- `scripts/smoke/container_smoke.py` — unmodified since Day 1
  (confirmed via `git status`/`git diff`), already reviewed in
  `day-01-test-review.md` ("strongest-verified component in the
  repository"); no new adversarial testing performed here since nothing
  changed.

---

## 6. Reliability / repeated-run evidence

- `python3 -m unittest discover -s tests -t .` run independently twice:
  **78/78 pass both times** (19.8s, 20.2s) — no order-dependence or
  shared-state flakiness observed.
- `make test` and `make quality` (test + lint + dockerfile-check +
  compose-check) both run to completion: **78/78 tests, `check_source.py:
  OK (11 files)`, `check_dockerfile.py: OK (9 checks)`,
  `check_compose.py: OK (10 structural checks, version=0.2.0)`.**
- No arbitrary/unbounded sleeps found in any new test or validator —
  every wait is either a bounded poll loop (`compose_integration.py`,
  `security_check.py`'s `check_runtime_healthy`) or a small, deterministic,
  purpose-built delay paired with a matching timeout
  (`UpstreamTimeoutTests`' 0.5s fake-upstream delay vs. a patched 0.1s
  client timeout).
- Every new test class with a real server/container follows the
  established `setUp`/`tearDown` (or `try`/`finally`) teardown-on-both-
  paths pattern; `compose_integration.py`'s `finally: down_result = ...`
  and `security_check.py`'s `finally: cleanup(container_name)` both run
  regardless of which check raised. No cleanup-failure path found that
  would leak a container or Compose project on a mid-run exception.

---

## 7. Release verdict

**Test-quality verdict for v0.2.0: READY, with one Medium
documentation-accuracy correction required before the implementation
report (or anything derived from it) is used as release evidence.**

- 78/78 tests pass, reproducibly, across independent re-runs.
- No Critical or High test-quality findings. No false positive or false
  negative surfaced in `check_dockerfile.py`, `check_source.py`
  (beyond the pre-existing, unchanged L-1), or `check_lifecycle_
  docker_stop` under adversarial synthetic inputs.
- The one Medium finding (M-1: incorrect "36 new gateway/healthcheck
  tests" claim) is a reporting-accuracy problem, not evidence of a
  missing or broken test — the underlying 44 new tests are real, pass,
  and are (with the Low findings above) individually sound.
- The Low findings (L-1 through L-5) are all either pre-existing/carried
  forward from Day 1, cosmetic coverage-parity gaps against an already-
  correct implementation, or an accepted low-probability race pattern
  already in use elsewhere in the repository — none block v0.2.0.

**Recommendation:** correct the test-count claim (§1/M-1) before this
implementation report is cited as release evidence; the Low findings can
be addressed opportunistically (e.g. alongside future gateway config
changes) without blocking release.
