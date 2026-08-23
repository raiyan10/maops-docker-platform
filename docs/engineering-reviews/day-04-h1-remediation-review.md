# Day 4 H-1 Remediation Review — v0.4.0 (FOCUSED, POST-REMEDIATION)

Repository: `maops-docker-platform`
Branch: `feature/day-4-build-security-reproducibility`
Target: `v0.4.0`
Reviewer: independent, focused H-1 remediation reviewer (review only — no
implementation file was modified; no commit/push/tag/release was
performed).
Date: 2026-08-23.

Scope: this is **not** a new full Day 4 review. It independently verifies
one specific claim — that the previously upheld High finding H-1
("role-aware healthcheck dispatch had no real runtime discriminating
power," `day-04-release-readiness.md` §3/§20) has been genuinely fixed.
Nothing in this report was accepted on the implementation report's word:
every claim was independently reproduced from real code reads, a live
`make release-check` run, and a hand-built, disposable 3x3 container
matrix started outside of any project script.

---

## 1. Finding severity counts (this review)

| Severity | Count |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 1 |

- **M-1 (new, H-1-adjacent):** the three new `*RoleDiscriminationTests`
  classes (`tests/test_healthcheck.py`,
  `tests/test_gateway_healthcheck.py`, `tests/test_state_healthcheck.py`)
  cover correct-role/wrong-role/missing-role bodies but not the adjacent
  malformed-input space the review brief specifically asked to challenge:
  wrong `status` value, malformed JSON, non-dict JSON (list/string/
  number), empty body, or a non-200 HTTP status paired with an otherwise
  correct body. This review independently exercised all of these against
  the real `check()` functions (§3) and every one is correctly rejected
  today — this is a coverage gap on already-correct code, not a defect,
  consistent with how the prior final review (§16/§19 of
  `day-04-release-readiness.md`) classified structurally identical gaps
  elsewhere in this codebase. Non-blocking.
- **L-1 (new, H-1-adjacent):** `docs/roadmap.md:222-224` and
  `docs/compose-platform.md:230-233` still describe Day 3 finding A-2's
  closure purely as "`check_kernel_readonly_write_fails`'s probe is now
  role-aware (dispatches to the right module *name*)" — the exact
  mechanism the prior final review proved does **not** by itself close
  A-2. The claim is no longer false (A-2 is genuinely closed now, §17),
  but these two documents were not updated during H-1 remediation to
  mention the actual closing mechanism (the `/healthz` `role` field +
  `EXPECTED_ROLE` check), unlike `docs/security.md`, which was properly
  and fully updated with a dedicated "Role-aware liveness (Day 4 —
  closes finding H-1)" section. Cosmetic/non-blocking.

---

## 2. Files changed by remediation

Git history on this branch has zero commits (still `bfdc9e4`, the Day 3
tip — see §15), so there is no commit boundary to diff against; the
working tree already contained the full, previously-reviewed Day 4 diff
before this remediation pass. This review isolated the H-1-specific
delta by (a) reading every file the review brief named, (b) diffing each
against `main` and confirming the H-1-shaped hunk within it, and (c)
cross-checking exact per-file unit-test counts against the prior final
review's own recorded table (`day-04-release-readiness.md` §7) to find
which files' test counts actually changed since that review.

**Confirmed H-1-remediation-scoped changes:**

| File | Change |
|---|---|
| `app/server.py`, `gateway/server.py`, `state/server.py` | `_route_healthz()` now returns `{"status": "ok", "role": "<role>"}` instead of `{"status": "ok"}` |
| `app/healthcheck.py`, `gateway/healthcheck.py`, `state/healthcheck.py` | added `EXPECTED_ROLE` constant + `isinstance(payload, dict)` guard + `payload.get("role") == EXPECTED_ROLE` in `check()` |
| `tests/test_healthcheck.py` (+5), `tests/test_gateway_healthcheck.py` (+4), `tests/test_state_healthcheck.py` (+4) | new `*RoleDiscriminationTests` classes against a real stub `/healthz` server (not a mock of `check()` itself) |
| `tests/test_server.py`, `tests/test_gateway_server.py`, `tests/test_state_server.py` | existing `HealthzEndpointTests` body assertion updated in place to expect the `role` field (no count change — confirmed against prior table) |
| `scripts/compose/compose_integration.py` | new `check_role_discrimination_matrix()` (real 3x3 proof against live containers), wired into `main()`, hard-fails the run on any mismatch |
| `tests/test_compose_integration.py` | new `CheckRoleDiscriminationMatrixTests` (+3) — explicitly documented as the Docker-free logic-only counterpart to the real proof, not a substitute for it |
| `scripts/verify/security_check.py` | `check_kernel_readonly_write_fails()` gained a `role` parameter threaded from each container's own name, so the "service kept serving" half probes that container's own healthcheck module (this dispatch-by-name mechanism itself pre-dates this remediation per the prior review; only the fact that dispatch now has real discriminating power is new, via the `healthcheck.py` changes above) |
| `docs/security.md` | new, fully-detailed "Role-aware liveness (Day 4 — closes finding H-1)" section |

**Scope-creep check:** every other file in the working-tree diff
(Distroless migration, `PYTHON_BIN` absolute-path changes across
`security_check.py`/`compose_integration.py`/`container_smoke.py`, SBOM/
Trivy tooling, multi-role smoke test, `schema_version` bool-bypass in
`platform_config.py`) was independently confirmed **unchanged since the
prior final review** by comparing this session's live per-file unit-test
counts against `day-04-release-readiness.md` §7's table — e.g.
`test_app_platform_config.py`, `test_check_dockerfile.py`,
`test_check_sbom.py`, `test_security_check.py` all still show the exact
same counts as before (13, 21, 10, 10 respectively). **No opportunistic
Medium/Low fix was bundled into this remediation.** The one exception
worth naming honestly: `image_audit.py`'s tautological base-digest check
(prior M-1) and its zero-test-coverage gap (prior Medium) are both still
present exactly as before — confirmed untouched.

---

## 3. `/healthz` contract (independently verified against real containers)

Verified live against three hand-started, disposable, fully-hardened
(`--read-only --cap-drop ALL --security-opt no-new-privileges:true`)
containers of `maops-docker-platform:0.4.0`, not `compose_integration.py`'s
own run:

| Role | Body (`GET`) | Status | Content-Type | Content-Length |
|---|---|---:|---|---:|
| app | `{"role": "app", "status": "ok"}` | 200 | `application/json` | 31 (exact) |
| gateway | `{"role": "gateway", "status": "ok"}` | 200 | `application/json` | 35 (exact) |
| state | `{"role": "state", "status": "ok"}` | 200 | `application/json` | 33 (exact) |

- `HEAD /healthz` returns the identical status/headers with a genuinely
  empty body (`BODY_READ_LEN=0`) for all three roles.
- No hostname, environment variable, IP address, PID, or config/secret
  value appears anywhere in the body — only `status` and `role`.
- `/healthz` was independently confirmed to still never call a
  dependency: `app`/`gateway`'s `_route_healthz()` bodies contain no
  reference to `_call_state`/`_call_upstream`, unlike `_route_readyz()`
  immediately below each in the same file. Readiness semantics
  (`/readyz`) are byte-for-byte unchanged.

**Verdict: local-process-liveness contract intact, role field is the
only addition, no leakage.**

---

## 4. Healthcheck parser verdict

Read `app/healthcheck.py`, `gateway/healthcheck.py`, `state/healthcheck.py`
directly: each defines its own `EXPECTED_ROLE` and accepts a response
only when `isinstance(payload, dict)` **and** `payload.get("status") ==
"ok"` **and** `payload.get("role") == EXPECTED_ROLE`.

Independently adversarially challenged all three real `check()` functions
against a hand-rolled stub `/healthz` server (not the tracked test
suite — a throwaway script in this session's scratchpad, no tracked file
modified):

| Case | app | gateway | state | Expected |
|---|---|---|---|---|
| correct role | (tracked test) True | (tracked test) True | (tracked test) True | True |
| wrong role (both other roles) | False | False | False | False |
| missing role | False | False | False | False |
| wrong `status` value | False | False | False | False |
| malformed JSON | False | False | False | False |
| non-dict JSON (list) | False | False | False | False |
| non-dict JSON (string) | False | False | False | False |
| non-dict JSON (number) | False | False | False | False |
| empty body | False | False | False | False |
| HTTP 500 with otherwise-correct body | False | False | False | False |
| unreachable port (connection failure) | (tracked test) False | (tracked test) False | (tracked test) False | False |

**Every adversarial case correctly produces a failed healthcheck.**
Coverage gap noted in §1/M-1 — the malformed-input rows above are proven
correct by this review's own probe but have no dedicated regression test
in the tracked suite.

---

## 5. Unit-test reconciliation

Ran `python3 -m unittest discover` twice (deterministic, no flakiness):

```
Ran 311 tests in ~49-51s
OK
```

- Prior Day 4 final total (per `day-04-release-readiness.md` §7): **295**
- Current total: **311**
- Net-new: **+16**

Per-file delta, independently derived by running each test file in
isolation and diffing against the prior review's own recorded per-file
table — only four files changed count:

| File | Prior | Now | Delta |
|---|---:|---:|---:|
| `test_healthcheck.py` | 2 | 7 | +5 |
| `test_gateway_healthcheck.py` | 2 | 6 | +4 |
| `test_state_healthcheck.py` | 2 | 6 | +4 |
| `test_compose_integration.py` | 8 | 11 | +3 |
| **Total net-new** | | | **+16** |

All 16 net-new tests were read directly (§2). The 13 healthcheck tests
exercise the real `check()` function against a real loopback stub HTTP
server returning a caller-chosen body — genuine production
parsing/validation, not a re-assertion of the `EXPECTED_ROLE` constant.
The 3 `compose_integration.py` tests exercise
`check_role_discrimination_matrix()`'s own pass/fail aggregation logic
against a fake `run_docker`, and are explicitly, honestly docstring-labeled
as the "Docker-free" counterpart to the real proof that `make
compose-test` performs — this project does not claim the mocked test
alone proves the fix.

---

## 6. Real 3x3 matrix (mandatory, independently run)

Not taken from `compose_integration.py`'s printed PASS. This review built
three disposable containers directly with `docker run` (bare, outside any
project script), on a throwaway network
(`maops-h1review-net-4ce7ddfb6043`), each independently hardened
(`--read-only --cap-drop ALL --security-opt no-new-privileges:true`),
from the freshly-built `maops-docker-platform:0.4.0`
(`sha256:a2a90257...5967df`, §12), then ran all nine
`docker exec <container> /usr/bin/python3.13 -m <role>.healthcheck`
combinations directly:

| Target container (real role) | `app.healthcheck` | `gateway.healthcheck` | `state.healthcheck` |
|---|---:|---:|---:|
| **app** | **0** | 1 | 1 |
| **gateway** | 1 | **0** | 1 |
| **state** | 1 | 1 | **0** |

All nine exit codes match the required matrix exactly: each container's
own-role module exits 0; both other roles' modules exit non-zero, on
every container. **No wrong-role module exited zero.**

`make release-check`'s own `compose_integration.py` run (real
Compose-managed containers, independent of the above) reproduced the
identical result:

```
compose_integration: role-discrimination matrix: state: app=FAIL, gateway=FAIL, state=PASS;
app: app=PASS, gateway=FAIL, state=FAIL; gateway: app=FAIL, gateway=PASS, state=FAIL
compose_integration: PASS (58/58 inspection checks passed)
```

**H-1's core regression is fixed, confirmed by two independent real-container runs (Compose-managed and bare `docker run`).**

---

## 7. Adversarial discrimination (role field is load-bearing)

Using a disposable, untracked stub-server script (not a tracked file):
`app.healthcheck` presented with `role="state"` and `role="gateway"` both
→ False; `gateway.healthcheck` presented with `role="app"` and
`role="state"` both → False; `state.healthcheck` presented with
`role="app"` and `role="gateway"` both → False. Removing the `role` key
entirely → False for all three. See the full table in §4. **The `role`
field is genuinely load-bearing, not decorative.**

---

## 8. Compose integration proof

`check_role_discrimination_matrix()` (`scripts/compose/compose_integration.py:319-358`)
independently read and confirmed to: iterate the three real
Compose-managed containers (`containers = {"state": ..., "app": ...,
"gateway": ...}`, populated from real `docker compose` container names,
`compose_integration.py:504`), and for each, `docker exec` all three real
healthcheck modules via `sc.run_docker(["exec", target_container,
PYTHON_BIN, "-m", probe_module])` — a real subprocess call against a real
running container, not a mock. A single `CheckResult` aggregates all nine
cells with a full per-cell diagnostic string; any exit-code mismatch
(`exited_zero != expected_zero`) is collected into `mismatches` and fails
the whole check, which the caller (`compose_integration.py:756-760`)
treats as a hard `ComposeIntegrationError`, aborting the run — **a
wrong-role success genuinely makes `compose-test` fail**, confirmed by
reading `main()`'s control flow (not merely inferred).

`make compose-test` (via `release-check`) reported **PASS — 58/58**
(prior final review's own count was 57/57 — the +1 is exactly this new
aggregated headline check, consistent with the +3 unit-test delta at the
mock-logic layer and the one new real-integration check at the runtime
layer).

---

## 9. Rootfs continuation check (role-aware)

Independently re-ran, for all three roles, against the same disposable
hand-started containers as §6:

| Role | Real rootfs write | Own-role healthcheck after | Wrong-role healthchecks after |
|---|---|---|---|
| app | rejected (`Read-only file system`) | exit 0 | gateway=1, state=1 |
| gateway | rejected (`Read-only file system`) | exit 0 | app=1, state=1 |
| state | rejected (`Read-only file system`) | exit 0 | app=1, gateway=1 |

This directly proves Day 3 A-2's originally-intended property end to end:
the rootfs write is genuinely rejected by the kernel, *that role's own*
service is proven still alive afterward, and — new, and the entire point
of H-1 — a *different* role's healthcheck against the same container
still correctly fails afterward too, closing the exact gap the prior
final review identified (a pure module-name dispatch with no real
discriminating power).

---

## 10. Startup ordering

Confirmed unchanged and still real via this session's own
`make release-check` log:

```
compose_integration: health-gated startup ordering proven (state -> app -> gateway)
```

`get_started_at()`/the dependency-ordering check
(`compose_integration.py:256-291`) still compares real
`docker inspect` timestamps (dependency's first-healthy time vs.
dependent's start time) — unaffected by the `/healthz` body change, since
Docker's own `HEALTHCHECK` machinery only inspects the probe's *exit
code*, never its JSON body.

---

## 11. Failure / recovery

Confirmed via the same `release-check` log (real Compose stack, `state`
stopped then restarted):

```
compose_integration: app and gateway processes remained alive after state was stopped
compose_integration: app's own /healthz liveness stayed healthy while state was down
compose_integration: gateway /readyz correctly degraded to not-ready (503 {'error': 'upstream unavailable', 'status': 'not-ready'})
compose_integration: gateway GET /state returns a controlled error while state is down (503 {'error': 'upstream unavailable'})
compose_integration: state became healthy again after restart
compose_integration: gateway /readyz recovered to ready (200 {'status': 'ready'})
```

`app`'s local `/healthz` liveness correctly stayed healthy (still
identifying role `app`) throughout — confirming the H-1 change did not
accidentally fold dependency-readiness into liveness. Readiness
degradation and recovery both behave exactly as designed.

---

## 12. Distroless / security regression check

Independently re-verified directly against the disposable containers
(§6), not merely re-quoted from the log:

| Property | Result |
|---|---|
| UID:GID | `10001:10001` |
| CapEff / CapPrm / CapBnd | all `0000000000000000` |
| NoNewPrivs | `1` |
| PID 1 cmdline | `/usr/bin/python3.13 -m app` (absolute interpreter, no shell/wrapper) |
| `/bin/sh` present | absent (`exec: "/bin/sh": stat /bin/sh: no such file or directory`) |
| `/var/run/docker.sock` | absent |
| Source immutability (`/app/app/server.py` write) | rejected, `PermissionError: [Errno 13] Permission denied` |

**No regression from the H-1 change.** All hardening properties this
review's own review-image build carries are identical to what the prior
final review already independently confirmed.

---

## 13. Required gates

All run for real, this session, against a live Docker daemon
(`/usr/bin/docker`, server `29.7.2`), via a single `make release-check`
invocation (internally sequences `quality` + `build` + `inspect` +
`image-audit` + `smoke` + `security-check` + `compose-test` +
`reproducibility-check` + `sbom` + `sbom-check` + `vuln-scan`, per
`Makefile:116`):

| Gate | Result |
|---|---|
| `make test` | **PASS — 311/311** |
| `make lint` | PASS (no `Error` in log) |
| `make dockerfile-check` | PASS |
| `make compose-check` | PASS |
| `make quality` | PASS |
| `make build` | PASS |
| `make image-audit` | **PASS — 19/19** |
| `make smoke` | **PASS** — `smoke: single-role (app) PASS`, `smoke: multi-role chain PASS` |
| `make security-check` | **PASS — 22/22** |
| `make compose-test` | **PASS — 58/58** |
| `make reproducibility-check` | **PASS — STRONG** (§14) |
| `make sbom` | PASS |
| `make sbom-check` | PASS |
| `make vuln-scan` | PASS (§14 vuln policy) |
| `make supply-chain-check` | PASS (composite) |
| `make release-check` | **PASS, exit 0**, full end-to-end run |
| `docker compose config` | PASS, exit 0, clean 3-service/2-network/1-volume/1-config render |

Two benign log artifacts independently confirmed non-failures: a
"Traceback" at log line ~218 is `BaseHTTPServer`'s own stderr noise from
a deliberately-disconnecting client in
`test_upstream_timeout_converts_to_controlled_503` (standard library
behavior, not a test failure — the test result immediately after reads
`ok`); the `CRITICAL=1`/`HIGH` lines at log lines ~76-83 are
`test_check_trivy_report.py`'s own synthetic-fixture policy-violation
test, not the real scan (the real scan's own numbers are reported
separately at §14 and are `CRITICAL=0`).

**Cleanup result:** post-run, `docker ps -a --filter name=maops-` and
`docker network ls --filter name=maops-` are both empty; no leftover
`maops-repro-*`/`maops-smoke-*` images or containers found. This review's
own disposable H-1 verification containers/network (§6/§9) were removed
in this session as well — confirmed empty post-cleanup.

---

## 14. Vulnerability policy

Fresh Trivy scan from this session's own `make release-check` run
(`aquasec/trivy:0.74.0@sha256:62b1e65e...c1969`, pinned):

| Severity | Count | Policy |
|---|---:|---|
| Critical | **0** | any → FAIL (not triggered) |
| High, fixable | **0** | any → FAIL (not triggered) |
| High, unfixed | 15 | reported, non-blocking |
| Medium | 44 | reported, non-blocking |
| Low | 51 | reported, non-blocking |
| Unknown | 12 | reported, non-blocking |

All 15 unfixed-High findings attribute to Debian 13 "trixie" system
packages (`libpython3.13-*`, `python3.13-*`, `libssl3t64`,
`libncursesw6`, `libtinfo6`) — none to this project's own `app`/
`gateway`/`state` code. **Vulnerability policy: PASS, not weakened by
H-1.**

---

## 15. Reproducibility

`make reproducibility-check` (part of `release-check`), this session:

- Build A ID: `sha256:a2a90257323610357e9d6a54d74f732d28abb44671ff8dd087373a6dda5967df`
- Build B ID: `sha256:a2a90257323610357e9d6a54d74f732d28abb44671ff8dd087373a6dda5967df`
- Exact image ID equality: **PASS**
- RootFS diff-ID equality: **PASS**
- Config/OCI-label equality: **PASS**
- Normalized filesystem manifest (24 entries): **PASS**
- Evidence level: **STRONG**

This ID is, correctly, **different** from the prior final review's build
(`sha256:c0b5a441cc6b787ec24fb1877459bc337b0ff513eb581a5f3c076fa87896c6a6`)
— expected and required, since H-1 changed real application source
(`_route_healthz()` bodies) in all three role modules. This review does
**not** treat that difference as a failure; the relevant proof is Build A
== Build B within *this* session's snapshot, which holds exactly.

---

## 16. Current image ID is pre-commit (tracked, not a new finding)

`git log -1 --format=%H` still resolves to `bfdc9e4` (the Day 3 tip); the
working tree still shows the full uncommitted Day 4 diff (68
modified/untracked paths per `git status --short`, consistent with the
prior review's M-3). `SOURCE_DATE_EPOCH` is therefore still anchored to
`bfdc9e4`, not to a commit containing this H-1 fix — the image ID
recorded in §15 is not yet the identity the eventual tagged release will
carry. This is the prior review's already-tracked M-3, not a new finding
from this pass, and this review takes no position beyond confirming the
condition is unchanged.

---

## 17. Day 3 finding A-2 adjudication

**Is A-2 now genuinely CLOSED? YES.**

Both required conditions independently verified:

- **A. correct role → correct module dispatch:** `healthcheck_module_for_role()`
  (`scripts/verify/security_check.py`) and `check_role_discrimination_matrix()`
  (`scripts/compose/compose_integration.py`) both correctly select
  `state.healthcheck`/`app.healthcheck`/`gateway.healthcheck` by
  container role name — unchanged from before, already correct.
- **B. wrong role → wrong module actually fails at runtime:**
  independently reproduced live, twice, against real containers (§6 —
  this review's own bare `docker run` matrix; and the real Compose-managed
  matrix inside `make release-check`'s own `compose_integration.py` run),
  plus the rootfs-continuation proof (§9) showing the wrong-role failure
  holds even after a real rootfs write attempt. All nine cells of the
  real matrix are correct in both independent runs. **No wrong-role
  module exited zero anywhere this review checked.**

This is the exact condition the prior final review found false
(`day-04-release-readiness.md` §3: "all three exit 0" against a real
`state`-role container) and is now the exact condition found true.

---

## 18. Release blocker status

No Critical or High finding survives this focused review. The one
previously-upheld release blocker (H-1) is independently confirmed fixed
by two separate live-container 3x3 matrix runs, an adversarial
role/malformed-input probe against all three real parser functions, a
role-aware rootfs-continuation proof, and a full green `make
release-check`. The two findings this review does raise (§1: M-1 test
coverage gap on malformed-input paths; L-1: two stale-but-not-false doc
passages) are both non-blocking and narrower in scope than anything that
would justify withholding release readiness on H-1's account.

This review does not re-adjudicate M-3 (uncommitted tree, §16) or any
other previously-accepted Medium/Low finding from
`day-04-release-readiness.md` — those remain exactly as that report left
them, carried forward, not reopened here.

---

**H-1 REMEDIATION VERIFIED**
