# Day 1 Independent Test-Quality Review

**Repository**: maops-docker-platform
**Branch**: feature/day-1-container-foundation
**Target**: v0.1.0
**Review date**: 2026-08-18
**Reviewer**: independent test-quality review session (review only; no
implementation files modified; this is the only file created by this
review)

**Scope note**: this review is scoped to *test and validation quality* —
whether the project's automated checks (unittest suite, source validator,
Dockerfile validator, smoke test, security checker) genuinely exercise
produced behavior and would catch real regressions. It is a companion to,
and independently re-verifies rather than trusts, `docs/engineering-
reviews/day-01-security-review.md` (the architecture/security review).
Where that review already reproduced a claim (base image digest, capability
state, image size, Compose lifecycle), this review does not repeat that
work and instead focuses on the *test harness itself*: would it catch a
regression, not just "does the current tree pass."

---

## 1. Executive summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 3 |
| Low | 4 |

**Verdict: RELEASE-READY for v0.1.0, with three Medium-severity test-coverage
gaps recommended for a near-term follow-up (not release blockers).** All
required commands pass with the exact numbers the security review reported
(34/34 tests, 9/9 Dockerfile checks, 20/20 security checks). Unit test
quality is genuinely strong — real HTTP requests against a real running
server, dynamic ports throughout, no mocks bypassing implementation, no
shared mutable state, order-independent. Adversarial fault-injection against
the smoke test (5 independently built broken images: wrong version, missing
`/info` key, broken `/healthz`, root runtime, nonexistent tag) confirmed it
genuinely fails for every meaningful fault tested, with reliable cleanup in
every case. The three Medium findings are all *coverage gaps*, not
correctness bugs: no automated test exercises `app/healthcheck.py`
directly or the Dockerfile's exact `HEALTHCHECK` invocation form; no
automated test exercises PID 1 / SIGTERM / `docker stop` lifecycle; and no
automated test exercises Compose-level hardening invariants end-to-end. All
three are presently mitigated by manual review (as documented in the
security review) rather than by any `make` target.

---

## 2. Required commands — results

| Command | Result |
|---|---|
| `make test` | **PASS** — 34/34 tests, `OK` (also re-run twice back-to-back: order-independent, no shared-state flakiness) |
| `make lint` | **PASS** — `check_source.py: OK (6 file(s) scanned under app/)` |
| `make dockerfile-check` | **PASS** — `check_dockerfile.py: OK (9 checks passed)` |
| `make quality` | **PASS** — test + lint + dockerfile-check, all green |
| `make build` | **PASS** — clean `--no-cache` build against the pinned digest |
| `make smoke` | **PASS** — re-run twice; `smoke: PASS` both times |
| `make security-check` | **PASS** — `security_check: PASS (20/20 checks passed)` |
| `docker compose config` | **PASS** — valid, one service, resolves environment/healthcheck/hardening flags correctly |

All numbers match `day-01-security-review.md` exactly. No discrepancy
found between the two independent review sessions' command output.

---

## 3. Unit test quality

Read every test in `tests/test_config.py`, `tests/test_server.py`,
`tests/test_version.py` in full (34 tests total).

**Genuine exercise of produced behavior, not tautologies or mocks:**

- `tests/test_server.py` starts the **real** `ThreadingHTTPServer` via
  `build_server()` on an OS-assigned loopback port (`port=0`) in a
  background thread, and every test makes a **real** `http.client`
  request over a real TCP socket and parses the **real** JSON response.
  Nothing is mocked or stubbed — this is full black-box exercise of
  `app/server.py`'s actual routing, dispatch, and error-handling code
  paths.
- `tests/test_config.py` calls `load_config()`/`parse_port()` directly
  with real input strings (not pre-validated fixtures) and asserts on
  real `ValueError` behavior for every boundary documented in `app/
  config.py` (empty, whitespace-only, non-integer, float-like, `0`,
  negative, `65536`, far-above-max) plus the two valid boundaries (`1`,
  `65535`). This is a complete, non-tautological boundary partition of
  `MIN_PORT`/`MAX_PORT`.
- `tests/test_version.py` reads the real repository `VERSION` file
  independently in the test itself (not hardcoding `"0.1.0"` alone) and
  cross-checks it against `get_version()`, then uses `unittest.mock.
  patch.object` (a real monkeypatch of the module-level `_VERSION_FILE`
  `Path`, not a mock of `get_version()` itself) to prove the value is
  re-read fresh on every call rather than cached at import time — a
  genuinely meaningful behavioral assertion, and the patch is correctly
  scoped with a context manager so it self-reverts.

**Checklist coverage verified present and behaviorally real:**

| Item | Covered | Note |
|---|---|---|
| `/` | Yes | full schema equality assertion, not just status code |
| `/healthz` | Yes | GET + HEAD (empty body, `Content-Length` present) |
| `/readyz` | Yes | |
| `/info` | Yes | exact key-set equality, safe-field-only assertion, arbitrary-env-leak assertion |
| `HEAD` | Yes | `/healthz` and `/does-not-exist` |
| `404` | Yes | GET and HEAD |
| unsupported methods | Yes | POST/PUT/DELETE on known paths, POST on unknown path (404-not-405 distinction) |
| Content-Type | Yes | asserted on `/` and `/healthz` |
| JSON validity | Yes | every test parses via `json.loads`, which itself fails loudly on malformed JSON |
| response schemas | Yes | exact dict/key-set equality throughout |
| `APP_HOST` | Yes | override + blank-falls-back-to-default |
| `APP_NAME` | Yes | override + blank-falls-back-to-default |
| valid `APP_PORT` | Yes | boundaries 1 and 65535 |
| malformed `APP_PORT` | Yes | non-integer, float-like |
| zero | Yes | |
| negative | Yes | |
| `>65535` | Yes | 65536 and 99999999 |
| whitespace handling | Yes | leading/trailing/tab/newline stripping on port; blank-string fallback on host/name |
| version loading | Yes | fresh-read-per-call, whitespace-stripped, real-file cross-check |
| safe `/info` | Yes | exact key-set; explicit no-`SECRET`/no-`PATH=` substring assertion |
| arbitrary environment not exposed | Yes | both at the config layer (`test_arbitrary_environment_is_not_consulted`) and the HTTP layer (`test_info_does_not_leak_arbitrary_environment`) |
| server configuration | Partial | see Finding L-4 below |

**No test-quality defects found in:**

- **Test-order dependence / shared mutable state**: each `ServerTestCase`
  subclass gets its own `setUp`/`tearDown` with a fresh server instance
  and a dynamically assigned port; `load_config()` tests pass an explicit
  `env` dict rather than mutating `os.environ`, so there is no real
  environment leakage between tests. Independently re-ran the full suite
  twice back-to-back — identical `OK` result both times.
- **Fixed ports / public network**: never observed — every test either
  uses `port=0` (server tests) or an explicit `env={}"` dict (config
  tests); nothing binds a fixed port or makes an external network call.
- **Sleeps as correctness mechanism**: none present in `tests/` — the
  server tests rely on `HTTPServer` being bound and listening
  synchronously before `serve_forever()` is invoked, which is real
  `socket.bind()`/`listen()` behavior, not a timing assumption.
- **Real `HOME` dependency / host-specific assumptions**: none — no test
  reads `HOME`, writes to a fixed path, or assumes a particular OS/locale.

### Findings

**L-4 (Low) — no test builds a server from a custom, non-default
`APP_HOST`/`APP_PORT` pair end-to-end via `load_config()`**

`tests/test_server.py`'s `ServerTestCase.setUp()` always constructs
`AppConfig` directly (`AppConfig(host="127.0.0.1", port=0, name="test-
app")`), never via `load_config(env={...})`. `tests/test_config.py`
separately tests `load_config()`'s parsing in isolation, but the two are
never composed: no test proves that an `APP_HOST`/`APP_PORT` value that
survives `load_config()` actually causes `build_server()` to bind to that
exact host/port. Risk is low — `AppConfig` is a plain frozen dataclass with
no logic between the two call sites — but it is the one item in the
checklist ("server configuration") not fully, end-to-end exercised.

**L-5 (Low) — `PATCH` method is implemented but never exercised by any test**

`app/server.py:122` defines `do_PATCH = _unsupported_method`, identical to
the tested `do_PUT`/`do_DELETE`/`do_POST`. No test sends a `PATCH` request.
Because all four unsupported-method handlers are the literal same function
object, the marginal risk of an undetected `PATCH`-specific regression is
minimal, but it is a checklist gap worth a one-line test for completeness.

---

## 4. Healthcheck regression protection

The implementation's documented prior defect: invoking the healthcheck
probe as a bare script (`python3 app/healthcheck.py`) instead of as a
package module (`python3 -m app.healthcheck`) causes a `ModuleNotFoundError`
on `from app.config import ...`, because a bare script's `sys.path[0]` is
the script's own directory, not `/app`.

**Independently reproduced the underlying defect** (real container, real
built image):

```
docker exec <container> python3 -m app.healthcheck        -> exit 0
docker exec <container> python3 app/healthcheck.py         -> exit 1
  Traceback (most recent call last):
    File "/app/app/healthcheck.py", line 14, in <module>
      from app.config import DEFAULT_PORT, load_config
  ModuleNotFoundError: No module named 'app'
```

Confirmed the current Dockerfile and `compose.yaml` both use the correct
module-invocation form (`CMD ["python3", "-m", "app.healthcheck"]`), so
**the fix itself is genuinely in place**. The question this section
actually investigates is whether a *future regression* back to the broken
form would be caught by anything automated.

**Tested each layer independently, using a temporary Dockerfile copy (real
repository files never modified) with the `HEALTHCHECK CMD` reverted to the
bare-script form, built under a separate throwaway image tag:**

| Layer | Would catch the regression? | Evidence |
|---|---|---|
| `tests/` (unittest) | **No** | Zero references to `healthcheck` anywhere under `tests/` (grep-confirmed). `app/healthcheck.py`'s `check()` function has no dedicated unit test at all — its only exercise is indirectly, inside a real container. |
| `make lint` (`check_source.py`) | N/A | Scans `app/` for forbidden constructs only; not applicable to invocation form. |
| `make dockerfile-check` | **No** | `check_healthcheck()` only asserts a `HEALTHCHECK` instruction exists and is not `NONE` — it never inspects the `CMD`'s argument list. Directly verified: ran `check_healthcheck()` against a Dockerfile text with `CMD ["python3", "app/healthcheck.py"]` substituted in — zero findings. |
| `make smoke` (`container_smoke.py`) | **No** | `wait_until_ready()` polls `/healthz` over a **direct HTTP connection**, entirely bypassing Docker's own `HEALTHCHECK` mechanism. Built a real image with the broken invocation form and ran `container_smoke.py`'s actual functions against it: smoke **passed** (`/healthz` reachable, UID correct) even though the image's own `HEALTHCHECK` was broken — because the HTTP server itself is unaffected by a broken `HEALTHCHECK CMD` line; they are independent subsystems. |
| `make security-check` | **Yes** | `check_runtime_healthy()` polls `docker inspect`'s `.State.Health.Status` (a `[C]` runtime-inspection check) to a 30s deadline, looking for `"healthy"`. Built and ran the broken-invocation image for real: `.State.Health.Status` progressed `starting` → `starting` (×4, one per 5–10s poll) → **`unhealthy`** at ~30s, with the container's actual health log showing the real `ModuleNotFoundError` traceback. Since the container can never reach `"healthy"` with a broken `HEALTHCHECK CMD`, this check deterministically fails regardless of exact timing — confirmed this is a reliable, not marginal, detector. |

### Finding

**M-1 (Medium) — the healthcheck-invocation regression is caught only by
`make security-check`, not by `make test`, `make dockerfile-check`, or
`make smoke`**

- **Impact**: a developer running the fast inner-loop checks (`make
  test`, `make quality`, or even `make smoke` alone) after accidentally
  reverting the `HEALTHCHECK CMD` to a broken form would see every one of
  those pass, and would only discover the regression by running the
  slower `make security-check` (or `make release-check`, which includes
  it). The full release-check chain *does* catch it — this is a
  developer-inner-loop gap, not a release-gate gap.
- **Root cause**: `app/healthcheck.py`'s `check()` function has zero
  direct unit-test coverage (a fast, in-process `unittest` test could
  exercise it against a real loopback `AppConfig`-configured server, the
  same pattern `tests/test_server.py` already uses, without needing a
  container at all), and `check_dockerfile.py`'s `check_healthcheck()`
  does not assert on the `CMD`'s exact argument list.
- **Recommended fix** (not applied — review only): add a `tests/
  test_healthcheck.py` that starts a real loopback server (reusing the
  `ServerTestCase` pattern) and asserts `app.healthcheck.check()` returns
  `True`/`False` correctly for a reachable vs. unreachable port; optionally
  extend `check_dockerfile.py`'s `check_healthcheck()` to also assert the
  `CMD` argument list matches `["python3", "-m", "app.healthcheck"]`
  (or at minimum, is exec-form and starts with `-m`).

---

## 5. Signal / PID 1

**No automated test or script anywhere in the repository sends a real
`SIGTERM` to a container and asserts on exit code, exit status, or
timing.** Confirmed via repository-wide grep for `SIGTERM`/`signal`/
`docker stop`/`PID 1` across `tests/` and `scripts/`: zero matches outside
`app/server.py`'s own implementation and doc prose. `tests/test_server.py`
only ever calls `self.server.shutdown()` — a direct Python method call on
the `HTTPServer` object — never a real OS signal delivered to a real
process; this exercises the graceful-shutdown *logic* but not the
*signal-handling registration* (`signal.signal(SIGTERM, ...)`) at all.

**Independently reproduced the real behavior** (this review's own
container, not accepted from the security review's prior log):

```
docker exec <container> cat /proc/1/cmdline  -> python3 -m app
docker stop <container>                       -> wall time ~0.73s
docker inspect .State.ExitCode                -> 0
docker inspect .State.Status                  -> exited
docker logs <container>                       -> "received signal 15, shutting down"
                                                  "server stopped"
```

This confirms the *behavior* is genuinely correct and matches the security
review's independent finding (~0.62s in that session, ~0.73s in this one —
consistent run-to-run variance, both well inside Docker's 10s grace
window). The gap identified here is specifically about **automated
regression protection**, not about whether the current behavior is
correct.

### Finding

**M-2 (Medium) — no automated regression test for PID 1 / SIGTERM / clean
`docker stop` — and cleanup masking makes this worse**

- Both `container_smoke.py` and `security_check.py` clean up with
  `docker rm -f` (a **force-kill removal**, `SIGKILL`-equivalent),
  regardless of how the test itself ended. If a future change accidentally
  removed the `signal.signal(SIGTERM, ...)` registration in `app/
  server.py`, the *only* externally visible symptom would be that `docker
  stop` silently falls back to the full 10-second grace period before
  Docker issues `SIGKILL` — and since nothing in this repository ever
  calls `docker stop` (only `docker rm -f`), that regression would produce
  **no test failure anywhere**, in any `make` target, ever. It would only
  be caught by a human manually timing `docker stop` — exactly how both
  independent reviews (this one and the security review) currently verify
  it.
- **Scope note**: no documentation in this repository claims this is
  automated (`docs/architecture.md` describes the *behavior*, not test
  coverage for it), so this is a coverage gap, not a documentation
  overclaim.
- **Recommended fix** (not applied — review only): add a small script
  (parallel to `container_smoke.py`) that starts the real image, calls
  `docker stop` with a short explicit timeout, asserts `.State.ExitCode
  == 0` and wall-clock time well under the default grace period, and
  cleans up — this is a natural, low-cost addition to the existing
  `scripts/smoke/` or `scripts/verify/` pattern.

---

## 6. Source validator (`scripts/lint/check_source.py`)

Confirmed **genuinely AST-based**, not regex/substring: reads via
`ast.parse`, walks via `ast.walk`, and matches on `ast.Call`/`ast.Import`/
`ast.ImportFrom`/`ast.Attribute` node types. Independently confirmed a
`# eval(...)` comment or a `"eval("` string literal does **not** trip a
finding (by construction — `ast.parse` never sees comments, and string
literals are `ast.Constant` nodes, never matched by the `ast.Call`/
`ast.Name` patterns checked). This matches the docstring's specific claim
about comments/strings not trivially satisfying a *false* finding.

**Adversarial variants tested against the real checker function
(`check_file()`), 8 samples:**

| Adversarial sample | Result |
|---|---|
| `import os as o; o.system(...)` | **Bypassed** |
| `from os import system; system(...)` | **Bypassed** |
| `from os import system as run_cmd; run_cmd(...)` | **Bypassed** |
| `getattr(__builtins__, 'ev'+'al')(...)` | **Bypassed** |
| `__builtins__.__dict__['eval'](...)` | **Bypassed** |
| `importlib.import_module('subprocess').run(...)` | **Bypassed** |
| `subprocess.run('ls', **{'shell': True})` | Caught (via the `subprocess` import itself) |
| `subprocess.run('ls', shell=some_var)` | Caught (via the `subprocess` import itself) |

### Finding

**L-1 (Low) — single-hop aliasing bypasses the `os.system`/`os.popen` and
forbidden-import checks; the docstring's absolute phrasing overstates this**

- The `_check_call()` logic for `os.system`/`os.popen` requires the exact
  AST shape `ast.Attribute(value=ast.Name(id="os"), attr="system")` — a
  bare rename (`import os as o`) or a `from os import system` already
  defeats it, with no data-flow analysis needed at all (not even the kind
  of dynamic trick the docstring's "does not understand data flow"
  disclaimer is presumably guarding against).
  the import-based check has an
  analogous gap: it only inspects the literal module name syntax in
  `import`/`from` statements, so runtime module resolution
  (`importlib.import_module('subprocess')`) is invisible to it.
- **Impact**: low in practice — `app/` is a small, first-party,
  human-reviewed stdlib-only codebase (confirmed via `docs/security.md`
  and this review's own reading of all 6 files), and this checker is
  explicitly scoped as "not a general-purpose static security scanner." No
  actual instance of any bypass pattern exists in the current codebase.
- **Recommended fix** (not applied — review only): either tighten the
  docstring's specific phrasing about `os.system`/`os.popen` to note it
  matches only the direct `os.<attr>(...)` spelling, or extend
  `_check_call` to also flag any `ast.ImportFrom(module="os")` whose
  names include `system`/`popen` regardless of local alias — a small,
  still-AST-based (not regex) enhancement consistent with the checker's
  existing design.

No other gap found; the checker's `shell=True` detection correctly handles
both a literal-`True` keyword argument (its documented scope) and
correctly does *not* false-positive on `shell=some_variable` (it only
flags `ast.Constant` values, so a non-literal `shell=` argument is
silently not flagged either way — consistent, not a false positive, but
worth noting it only catches the literal-constant case, which is exactly
what its docstring claims).

---

## 7. Dockerfile validator (`scripts/lint/check_dockerfile.py`)

Confirmed **line/instruction-aware**, not naive substring matching on the
raw file: `parse_instructions()` explicitly joins backslash line
continuations, strips full-line comments before instruction detection,
and normalizes instruction case (`parts[0].upper()`) before any check
runs. Verified positively — all of the following correctly still pass
after normalization (not bypasses, confirmations that the parser is
robust):

- Mixed-case instructions (`FrOm`, `user`) — correctly recognized as
  `FROM`/`USER`.
- A full-line comment mentioning `:latest` immediately before a real,
  valid pinned `FROM` — correctly ignored, does not trip a finding.
- A backslash-continued multi-line `HEALTHCHECK` (the real Dockerfile's
  own actual form) — correctly joined and recognized as present.
- Trailing whitespace on a `USER` value — correctly stripped.
- `check_user()`/`check_from()`/`check_healthcheck()` correctly use only
  the **last** matching instruction when multiple appear (Docker's own
  semantics for `USER`/`FROM`/`HEALTHCHECK` — only the final one is
  effective at runtime), confirmed with a synthetic multi-`USER` and a
  synthetic multi-stage (`FROM ... AS build` then a second `FROM`) sample.

**Challenged all 9 checks with adversarial and benign-edge variants**
(21 samples run against the real check functions). Two genuine gaps found:

### Findings

**L-2 (Low) — `check_from()`'s digest-pin check validates only substring
presence of `@sha256:`, not actual digest format**

- `FROM python:3.13-slim@sha256:abc`, `FROM python:3.13-slim@sha256:` (empty),
  and `FROM python:3.13-slim@sha256:not-hex-at-all!!` all **pass** the
  "digest-pinned" check — the logic is `"@sha256:" not in image_ref`, with
  no validation that what follows is 64 hex characters.
- **Impact is low in practice**: independently confirmed Docker itself
  rejects a malformed digest at the reference-parsing stage, before any
  registry call — `docker pull python:3.13-slim@sha256:abc` →
  `invalid reference format`. So a genuinely malformed digest could never
  survive an actual `make build` regardless of this gap; the checker's own
  claim just doesn't independently verify what Docker itself already
  enforces.
- **Recommended fix** (not applied — review only): tighten the digest
  check to `re.fullmatch(r"[0-9a-f]{64}", digest_part)` for precision, or
  soften the docstring's phrasing to "requires the `@sha256:` marker
  (relies on `docker build` itself to reject a malformed digest value)."

**L-3 (Low) — `check_no_sudo()`/`check_no_privileged_concepts()` are
substring/regex checks over the full instruction argument text, not
shell-token-aware — false positives on benign content are possible**

- `RUN echo 'we do not use sudo'` is flagged as `"sudo usage found"` even
  though it contains no actual `sudo` invocation — the word `sudo`
  appears only inside an echoed string literal. `\bsudo\b` correctly
  avoids matching inside a *larger identifier* (`pseudo-package` correctly
  passes), but does not distinguish a real command from a quoted string
  argument to `echo`.
- **Impact**: none currently — no such `RUN` line exists in the real
  Dockerfile (confirmed by re-running the actual checker: `OK`). This is a
  false-positive risk for future edits, not a security gap (the failure
  mode is over-blocking, not under-blocking a real `sudo` invocation via a
  literal `sudo ...` command, which is still correctly caught).
- **Recommended fix** (not applied — review only): none required for Day
  1; worth a one-line docstring caveat if this checker's scope grows.

Every other check (`check_no_remote_add`, `check_no_secret_vars`,
`check_workdir`, `check_exec_form_runtime_command`) behaved exactly as
documented against every adversarial sample tried, including the
correctly-honest "-looking" phrasing in `check_no_secret_vars`'s own
finding message (`ENV MY_APP_SECRETS_DIR=/x` is flagged, and the message
says "secret-bearing-**looking**", not "is a secret" — an intentional,
honestly-worded heuristic, not a bug).

---

## 8. Smoke test (`scripts/smoke/container_smoke.py`)

**Proved it fails for every meaningful fault in scope**, by independently
building 5 separate broken images (each in an isolated temp build
context, none touching real repository files) and running the actual
`container_smoke.py` functions (not a re-implementation) against each:

| Fault injected | Result |
|---|---|
| `VERSION` file baked into the image reads `9.9.9` instead of `0.1.0` | **FAIL**: `/info version mismatch: expected '0.1.0', got '9.9.9'` |
| `/info` route missing the `python_version` key | **FAIL**: `/info unexpected keys: dict_keys(['host', 'name', 'port', 'version'])` |
| `/healthz` route returns `{"status": "not-ok"}` instead of `"ok"` | **FAIL** (via timeout): `container did not become ready within 6s: /healthz not ready yet: 200 {'status': 'not-ok'}` |
| Dockerfile `USER` reverted to `root` | **FAIL**: `container is running as root (uid 0)` |
| Nonexistent image tag | **FAIL**: `docker run failed: ... pull access denied / no such image` |

All five failures are specific, actionable messages (not a generic
crash), and **cleanup was confirmed reliable in every case** — no leftover
`maops-review-smoketest-*` containers after any of the five induced
failures (`docker ps -a` filter confirmed empty after each run).

**Other properties confirmed:**

- **Exact-version, no `latest` ambiguity**: `read_version()` reads the
  real `VERSION` file and builds `f"maops-docker-platform:{version}"` —
  never a bare `:latest` tag anywhere in the script.
- **Bounded deadline**: `wait_until_ready()` uses a `time.monotonic()`
  deadline (30s) with a 0.5s poll interval — not an unbounded retry loop,
  and not a fixed `sleep()` used as the pass/fail signal.
- **Dynamic/local host port**: `-p 127.0.0.1::8080` binds to loopback only
  on an OS-assigned host port (`docker port` is queried after the fact) —
  no fixed port, no public-network exposure.
- **Cleanup on both success and failure**: the entire lifecycle is inside
  `try/except SmokeTestError/finally: cleanup(container_name)`, and
  `cleanup()` always runs regardless of which branch triggered.
- **Unique naming**: `f"maops-smoke-{uuid.uuid4().hex[:12]}"` — 48 bits of
  randomness per run, no realistic collision risk across repeated or
  parallel runs.

No findings in this section — this is the strongest-verified script in
the repository (see §13).

---

## 9. Security test quality (`scripts/verify/security_check.py`)

Confirmed all 20 checks are correctly labeled by evidence tier
(`[A]`/`[B]`/`[C]`/`[D]`), and independently re-verified the specific
items called out in the review scope:

- **Recursive cache exclusion**: independently created a **fresh** two-
  location probe (`app/__pycache__/review_probe.pyc` and
  `app/nested/deep/__pycache__/review_probe.pyc`, 2 levels deep, distinct
  from any prior review's probe), ran a real `docker build --no-cache`,
  and scanned the resulting image with `docker run --rm --entrypoint find
  ... -iname '*.pyc' -o -iname '__pycache__'`: **zero matches**. Probes
  removed and confirmed absent afterward.
- **Recursive image scanner is itself tested against nested synthetic
  content**: `regression_prove_recursive_detection()` builds a synthetic
  `__pycache__` 3 directories deep in an isolated temp tree, unrelated to
  the real image, and asserts `recursive_find_forbidden_bytecode()` (which
  uses `Path.rglob("*")`, genuinely recursive) catches it. Re-ran
  independently: `PASS`, matching output confirmed.
- **Actual rootfs write failure**: `check_kernel_readonly_write_fails()`
  executes a real `sh -c 'echo probe > /etc/maops-readonly-probe'` inside
  the running hardened container and checks the real exit code (this
  review's own run: `exit=2`, `Read-only file system`), then re-probes
  `/healthz` to confirm the service kept serving. This is a real attempted
  action, correctly labeled `[D]`, not an inference from `HostConfig`.
- **Actual `CapEff`/`CapBnd`/`NoNewPrivs` state**: `read_proc_1_status()`
  reads `/proc/1/status` inside the container via `docker exec`, not
  `docker inspect`'s `HostConfig` — correctly `[D]`, independently
  re-confirmed (`CapEff=0000000000000000` etc., `NoNewPrivs=1`).
- **UID/GID, host PID, host networking, privileged state, Docker socket**:
  all present as `[C]`/`[D]` checks reading real `docker inspect`/`docker
  exec` output, no check found asserting a `[D]`-level claim from only
  `[C]`-level evidence.
- **Image file leakage**: `check_image_content_recursive()` does a real
  `docker cp` of `/app` out of a live container and recursively scans the
  extracted tree — bounded to that extracted tree only (never `/proc`,
  `/sys`, `/dev`, confirmed by reading the code: `root.rglob("*")` where
  `root` is the temp extraction directory).
- **Health state**: `[C]`-tier, polls real `.State.Health.Status`.

**Evidence tier matches reality throughout — no `[C]`-only claim presented
as `[D]`-level enforcement proof, and no `[D]` check found that was
actually only reading `HostConfig`.**

**Induced two real failures against this script's own logic (not a
simulation) to test its failure-reporting and cleanup discipline:**

1. **Pre-container failure** (pointed `read_version()` at a nonexistent
   image tag): reproduced the security review's own **L-1** finding
   independently — `check_image_user()` raises an uncaught `RuntimeError`
   with a raw traceback rather than the script's own controlled `FAIL`
   summary, because the pre-container checks (`check_image_user`,
   `check_image_healthcheck`, `check_image_labels`,
   `regression_prove_recursive_detection`) run before the `try/finally`
   block. **Confirmed no container is created or leaked** in this path
   (`docker ps -a` filter empty afterward) — this independently confirms
   L-1's specific claim that the gap is a reporting-consistency issue, not
   a cleanup defect.
2. **Mid-run failure** (built a real image with `USER root`, ran the full
   `security_check.py` `main()` against it): produced a correct, complete
   controlled failure report — `[B] FAIL image Config.User is 10001:10001:
   'root'` and `[D] FAIL effective process UID:GID is 10001:10001: uid=0
   gid=0`, all 18 other checks still ran and reported correctly,
   `security_check: FAIL (2/20 checks failed)`, and the container was
   cleanly removed via the `finally` block (`docker ps -a` filter empty
   afterward). This is strong evidence the `try/finally` cleanup path is
   reliable under a genuine, non-synthetic failure, not just a clean run.

No new findings beyond confirming L-1 from the prior security review still
holds and does not leak resources.

---

## 10. Recursive known-defect tests

Already covered in detail in §9 — restated as its own verdict per the
review scope's structure: **confirmed independently, with a fresh probe
distinct from any prior session's**, that both (a) the real `.dockerignore`
+ build pipeline excludes a nested (2-level-deep) `__pycache__`, and (b)
`security_check.py`'s own `recursive_find_forbidden_bytecode()` is tested
against a synthetic 3-level-deep fixture inside the script itself, not
merely asserted to work by reading the code. Both hold.

---

## 11. Cleanup / failure-path reliability

Recorded unrelated Docker resources before and after this entire review
session: **zero unrelated resources were ever affected.** Every container
this review created used a unique, project-consistent, uuid-suffixed name
(`maops-review-*`, `maops-smoke-*`, `maops-security-*`) and every temp
image was explicitly `docker rmi`'d after use. Confirmed at the end of the
session: `docker ps -a --filter name=maops-` → empty; no stray `maops-
review-*`/`:temp`/`99.99.*` images remain.

Induced controlled failures at three different points (smoke: nonexistent
tag; smoke: root-user image; security-check: nonexistent tag;
security-check: root-user image) — **cleanup succeeded in every case**,
including the one known-inconsistent-reporting path (L-1) where cleanup
was never actually at risk since it triggers before any container exists.

No global prune was run at any point (verified by reviewing every command
in this transcript); no command matched `docker system/container/image/
volume prune`.

---

## 12. Compose test coverage

**No automated test exercises Compose-level invariants.** Confirmed via
repository-wide search: `docker compose` appears only in `Makefile`'s
`release-check` target (`docker compose config`, which renders and prints
configuration with **no assertions** — a non-zero exit from `docker
compose config` would fail the `make` target, but a *valid* config that
happens to have dropped `read_only: true` or `cap_drop: [ALL]` would still
print cleanly and pass). No script anywhere runs `docker compose up`,
inspects the resulting container, and asserts on any of: one service,
`read_only`, `cap_drop: [ALL]`, `no-new-privileges`, non-host network,
non-host PID, absence of a Docker-socket mount, or health status. All
verification of these properties via Compose specifically (as opposed to
via `security_check.py`'s direct `docker run`, which never goes through
Compose at all) has so far been performed manually, once, by the security
review session (§13 of `day-01-security-review.md`).

**Assessed risk, not defaulting to High severity per the review scope's
own instruction:** `compose.yaml`'s hardening flags (`read_only`,
`cap_drop`, `security_opt`) are textually identical to the flags
`security_check.py` already passes to its own `docker run` and verifies at
all four evidence tiers — so the *mechanism* (Docker enforcing these flags)
is thoroughly regression-tested; what's specifically untested is that
`compose.yaml` itself continues to *declare* the same flags correctly, a
much narrower drift risk (e.g., a future edit accidentally deleting a line
from `compose.yaml`). The `compose-validation` skill under `.claude/
skills/` exists specifically to cover this via a documented, reusable,
human/agent-invoked procedure rather than a `make`-target automated test —
consistent with `CLAUDE.md`'s framing of skills as growing procedures, not
CI. Given that framing, and that Day 1 makes no CI claim, this is assessed
as a **Medium**, not High, gap: real and worth closing, but presently
mitigated by an existing (manual) procedure rather than entirely absent.

### Finding

**M-3 (Medium) — no automated regression test for Compose-declared
hardening invariants**

- **Recommended fix** (not applied — review only): a small script
  (`scripts/verify/compose_check.py` or an extension of
  `security_check.py`) that runs `docker compose up -d`, inspects the
  Compose-managed container the same way `security_check.py` already
  inspects its own `docker run` container, and tears down via `docker
  compose down` — reusing nearly all of `security_check.py`'s existing
  `[C]`/`[D]` check functions against a Compose-launched container instead
  of a directly-`docker run` one. This is a natural Day 2+ extension
  (`compose-validation` skill is explicitly scoped to grow from a
  one-service check into this) rather than a Day 1 gap requiring urgent
  action.

---

## 13. Flakiness assessment

Assessed each documented flakiness risk:

- **Dynamic port race**: none observed. Both test suite (`port=0`,
  OS-assigned) and container scripts (`-p 127.0.0.1::8080`, host port
  queried via `docker port` after `docker run` returns) avoid any window
  where a port is guessed rather than read back from the OS/Docker.
- **Deadlines**: `container_smoke.py` (30s) and `security_check.py`'s
  health poll (30s) are generous relative to observed real timings
  (healthy within ~1–8s in every run this session). No deadline was
  observed to be marginal in any of the ~15 real container runs performed
  during this review.
- **Sleeps**: every polling loop uses `time.monotonic()` deadline + fixed
  poll interval as a *rate limiter*, never a `sleep()` used as the actual
  pass/fail signal.
- **Docker startup polling / container-name collisions**: `uuid.uuid4().
  hex[:12]` naming gives no realistic collision risk; repeated-run safety
  was directly exercised (ran `make smoke` twice back-to-back, `make test`
  twice back-to-back, and `security_check.py`'s logic against 2 different
  broken images plus the real image in the same session) — every run
  independently succeeded/failed correctly with no interference between
  runs.
- **Cleanup race windows**: none found — every `finally`/equivalent block
  runs `docker rm -f` synchronously before the script returns.
- **Docker Desktop/WSL assumptions**: this review's environment is itself
  WSL2 (`Linux 6.18... microsoft-standard-WSL2`) — every command in this
  review, including the induced-failure and probe-based tests, ran
  successfully in this exact environment, so no WSL-specific flakiness was
  observed in practice (as opposed to theorized).

**No flakiness found in this session.** This is not a guarantee against
rare timing issues on a heavily loaded CI runner (Day 1 has no CI yet, so
this hasn't been stress-tested under contention), but nothing in the
design (fixed sleeps, guessed ports, non-monotonic deadlines) creates an
inherent flakiness risk.

---

## 14. Summary of findings

| ID | Severity | Area | One-line summary |
|---|---|---|---|
| M-1 | Medium | Healthcheck regression | Only `make security-check` (not `make test`/`make smoke`) catches a reverted `HEALTHCHECK` invocation form; `app/healthcheck.py` has zero direct unit-test coverage |
| M-2 | Medium | PID 1 / signal | Zero automated test for SIGTERM/`docker stop`/exit-code lifecycle; force-kill cleanup in existing scripts would mask a regression entirely |
| M-3 | Medium | Compose coverage | No automated test exercises Compose-declared hardening invariants end-to-end; currently manual-only, mitigated by an existing skill, not a `make` target |
| L-1 | Low | Source validator | `os.system`/`os.popen`/forbidden-import checks bypassed by trivial import aliasing; no current instance in `app/` |
| L-2 | Low | Dockerfile validator | Digest-pin check validates substring presence only, not hex format; backstopped by Docker's own reference-parsing rejection |
| L-3 | Low | Dockerfile validator | `sudo`/privileged checks are substring-over-argument-text; false-positive risk on benign `RUN echo` content, no current instance |
| L-4 | Low | Unit tests | No end-to-end test composes `load_config()` output into `build_server()` for a custom host/port |
| L-5 | Low | Unit tests | `PATCH` method implemented but never exercised (shares code path with tested methods, minimal risk) |

No Critical or High findings.

---

## 15. Strongest three test areas

1. **Smoke-test fault-injection coverage (§8).** Five independently built,
   genuinely broken images (wrong version, missing schema key, broken
   health endpoint, root runtime, nonexistent tag) were run through the
   real `container_smoke.py` functions, not a re-implementation, and every
   one produced a specific, correct failure with reliable cleanup. This is
   the single most thoroughly and successfully adversarially-tested
   component in the repository.
2. **`security_check.py`'s evidence-tier discipline under real induced
   failure (§9).** Beyond re-confirming the `[A]`/`[B]`/`[C]`/`[D]`
   labeling is accurate (matching the security review's separate finding),
   this review specifically proved the script produces a correct,
   complete, non-crashing failure report and reliably cleans up its
   container when handed a genuinely broken image mid-run — not just when
   everything passes.
3. **Unit test realism (§3).** `tests/test_server.py` never mocks the
   HTTP layer — it runs the real `ThreadingHTTPServer` and makes real
   socket connections — and `tests/test_config.py`'s boundary coverage of
   `parse_port()` is a genuinely complete partition of the documented
   valid range, not a token sample of "one good, one bad" values.

---

## 16. Highest-value missing regressions

In priority order:

1. **A direct unit test for `app/healthcheck.py::check()`** (M-1) — the
   cheapest fix here (pure in-process `unittest`, no container needed,
   following the existing `ServerTestCase` pattern exactly) closes the
   single gap most likely to bite a fast inner dev loop.
2. **An automated SIGTERM/`docker stop` lifecycle check** (M-2) — natural
   fit as a new function alongside `container_smoke.py` or
   `security_check.py`'s existing container-lifecycle helpers.
3. **A Compose-driven variant of `security_check.py`'s existing `[C]`/`[D]`
   checks** (M-3) — most of the check logic already exists and is proven
   correct; it just needs to run against a `docker compose up`-launched
   container instead of (or in addition to) a directly-`docker run`
   one. This is explicitly the growth path the `compose-validation` skill
   already anticipates.

---

## 17. Release blockers

**None.** All three Medium findings are coverage gaps in the test harness
itself, not defects in application, Dockerfile, or Compose behavior — every
underlying property they fail to *regression-protect* (correct healthcheck
invocation, graceful SIGTERM shutdown, Compose hardening flags) was
independently verified to be **currently correct** by this review (§4, §5)
and by the companion security review (§4, §13 of `day-01-security-
review.md`). They represent risk to *future* changes, not to the v0.1.0
artifact as it exists today.

---

## 18. Final test-quality verdict

**Test-quality verdict: PASS, with three Medium-priority coverage gaps
recommended for near-term follow-up.** The test suite that exists is
genuinely high-quality — real behavior exercised, no tautologies, no
implementation-bypassing mocks, no shared state, no fixed ports, no
sleep-based correctness, verified order-independent and repeat-run-safe.
The validators and smoke/security scripts were adversarially challenged
with 30+ synthetic samples and 7 real broken-image builds across this
review, and every fault tested that the scripts document themselves as
covering was correctly caught. The gaps found are precisely the three
areas explicitly flagged as historically manual-only in the companion
security review (healthcheck invocation form, PID 1/signal lifecycle,
Compose-level enforcement) — this review adds concrete, reproduced
evidence of *why* each is currently uncaught by automation, and a
low-cost, pattern-consistent fix for each, without treating any of them
as a release blocker for v0.1.0.

---

## Required summary block

- **Exact test count**: 34 tests (`tests/test_config.py`: 17,
  `tests/test_server.py`: 14, `tests/test_version.py`: 3)
- **Failures / skips**: 0 failures, 0 errors, 0 skips — `OK`, confirmed on
  two independent back-to-back runs
- **Source-validator verdict**: genuinely AST-based as documented; correct
  on comments/strings never producing false findings; one Low-severity gap
  (single-hop import aliasing bypasses `os.system`/`os.popen` checks — L-1)
- **Dockerfile-validator verdict**: genuinely line/instruction-aware
  (case, whitespace, continuations, comments handled correctly); two
  Low-severity gaps (digest format not validated beyond substring
  presence — L-2; substring-based `sudo`/privileged checks can false-
  positive on benign content — L-3)
- **Healthcheck-regression verdict**: the fix is genuinely in place and
  independently reproduced; a future regression to the broken form would
  be caught only by `make security-check` (~30s, via real `[C]`-tier
  health-status polling), not by `make test`/`make smoke` — Medium
  finding M-1
- **Smoke-test quality**: strongest-verified component in the repository
  — 5/5 independently constructed real faults correctly caught with
  reliable cleanup in every case; no findings
- **Security-test quality**: all 20 checks' evidence tiers independently
  confirmed accurate; cleanup and failure-reporting confirmed reliable
  under two independently induced real failures (one pre-container, one
  mid-run); no new findings beyond independently reconfirming the
  security review's own L-1
- **PID1/lifecycle quality**: behavior itself is correct and independently
  reproduced (~0.73s graceful `docker stop`, exit code 0) but has zero
  automated regression protection anywhere in the repository — Medium
  finding M-2
- **Recursive-regression quality**: independently reproduced with a fresh,
  distinct 2-level-deep probe and a real `--no-cache` build; the scanner's
  own synthetic 3-level-deep regression self-test independently re-run and
  confirmed passing
- **Cleanup reliability**: 100% across every induced failure and every
  normal run in this session (7 broken-image builds, 2 induced
  security-check failures, 2 induced smoke failures); zero unrelated
  Docker resources ever affected; zero global prune calls used
- **Flakiness assessment**: none observed across ~15 real container runs
  in this session, including repeated back-to-back runs of `make test` and
  `make smoke`; no fixed ports, no sleep-based correctness, no name
  collisions
- **Strongest three test areas**: (1) smoke-test fault injection, (2)
  security-check's evidence-tier discipline under real induced failure,
  (3) unit-test realism (real sockets, complete boundary partitions)
- **Highest-value missing regressions**: (1) direct unit test for
  `app/healthcheck.py::check()`, (2) automated SIGTERM/`docker stop`
  lifecycle check, (3) Compose-driven variant of `security_check.py`'s
  existing `[C]`/`[D]` checks
- **Release blockers**: none — all findings are coverage gaps against
  currently-correct behavior, not defects in v0.1.0 itself
- **Final test-quality verdict**: **PASS** for v0.1.0, with three Medium
  and five Low findings recommended as near-term follow-up work, none of
  which block release
