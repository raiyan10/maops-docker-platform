# Day 4 Test-Quality Review — v0.4.0

Independent reviewer. Review only — no implementation files modified.
Every count below was independently re-derived by running
`python3 -m unittest discover` against the working tree and, for the
Day 3 baseline, against an isolated `git worktree` at commit `bfdc9e4`
(the current HEAD — all Day 4 work is uncommitted), not taken on either
implementation report's word.

**Environment note:** unlike the Day 3 review (no Docker available),
Docker is available in this sandbox. The release image was built once
(`docker buildx build ... -t maops-docker-platform:0.4.0`) and
`scripts/build/image_audit.py` / `scripts/verify/security_check.py` were
both run live to end. The two-independent-build reproducibility proof
(`scripts/build/reproducibility_check.py`, ~2 full `--no-cache` builds)
and the live Trivy scan (`scripts/security/vuln_scan.py`, first-run
vulnerability-DB download) were **not** executed — both are multi-minute
Docker-integration jobs whose own correctness is a release-readiness
question, not a test-quality one; they are assessed here by source
reading only, same as the Day 3 review's treatment of its Docker-gated
scripts. This is called out again wherever it affects a verdict.

An earlier accident during this review is disclosed for transparency:
a `git checkout bfdc9e4 -- .` run to build an isolated Day 3 baseline
briefly overwrote the working tree with Day 3 content. It was caught
immediately (before any test was run against it) and fully reverted via
`git stash pop` of a pre-emptive `git stash -u` taken beforehand; `git
status` after the revert matched the pre-checkout state exactly (58
files, same diff stat) and the fast suite re-ran at 295/295 to confirm.
No working-tree content was lost. The baseline was instead obtained
safely via `git worktree add --detach <tmp> bfdc9e4`, which never touches
the primary working tree.

---

## 1. Count reconciliation

| | claimed | independently verified |
|---|---|---|
| Day 3 baseline | 195 | **195** ✓ (`git worktree add --detach` at `bfdc9e4`, ran `python3 -m unittest discover`: `Ran 195 tests ... OK`) |
| Earlier "slim" Day 4 total | 272 | **not independently verifiable** — see below |
| Final (Distroless) Day 4 total | 295 | **295** ✓ (ran twice back-to-back against the working tree: `Ran 295 tests ... OK`, 45.96s then 46.43s; identical both times) |
| Day 3 → final Day 4 net-new | +100 | **+100** ✓ (295 − 195 = 100, independently confirmed by summing per-file deltas below, which also total 100) |

### The "slim → Distroless" migration delta is not recoverable

`git log`, `git reflog`, and `git stash list` were all checked: **no
commit, stash, or reflog entry corresponds to any intermediate "slim"
Day 4 state.** The current branch's only commit since Day 3 is still
`bfdc9e4` (the Day 3 release-evidence commit) — every Day 4 file,
including all new test files, exists only as uncommitted working-tree
content. The reported "272 tests" slim figure and the implied
272 → 295 (+23) migration delta therefore cannot be independently
verified from git history; there is no ref, stash, or on-disk snapshot
to diff against. This is reported as an information gap, not a
contradiction — the final total (295) and the Day 3 baseline (195) are
both independently confirmed, so the net-new figure that matters for
release purposes (+100) is solid regardless.

### Per-file breakdown (exact, one `unittest discover -p <file>` subprocess per module)

| file | Day 3 | Day 4 final | delta | status |
|---|---:|---:|---:|---|
| `tests/test_app_platform_config.py` | 11 | 13 | +2 | existing, grown (schema_version true/false) |
| `tests/test_gateway_platform_config.py` | 9 | 11 | +2 | existing, grown (schema_version true/false) |
| `tests/test_state_platform_config.py` | 15 | 17 | +2 | existing, grown (schema_version true/false) |
| `tests/test_config.py` | 24 | 24 | +0 | unchanged |
| `tests/test_gateway_config.py` | 20 | 20 | +0 | unchanged |
| `tests/test_gateway_healthcheck.py` | 2 | 2 | +0 | unchanged |
| `tests/test_gateway_server.py` | 26 | 26 | +0 | unchanged |
| `tests/test_healthcheck.py` | 2 | 2 | +0 | unchanged |
| `tests/test_server.py` | 23 | 23 | +0 | unchanged |
| `tests/test_state_config.py` | 18 | 18 | +0 | unchanged |
| `tests/test_state_healthcheck.py` | 2 | 2 | +0 | unchanged |
| `tests/test_state_server.py` | 21 | 21 | +0 | unchanged |
| `tests/test_state_storage.py` | 19 | 19 | +0 | unchanged |
| `tests/test_version.py` | 3 | 3 | +0 | unchanged |
| `tests/test_check_dockerfile.py` | — | 21 | +21 | **new** |
| `tests/test_check_sbom.py` | — | 10 | +10 | **new** |
| `tests/test_check_source.py` | — | 9 | +9 | **new** |
| `tests/test_check_trivy_report.py` | — | 14 | +14 | **new** |
| `tests/test_compose_integration.py` | — | 8 | +8 | **new** |
| `tests/test_generate_sbom.py` | — | 2 | +2 | **new** |
| `tests/test_reproducibility_check.py` | — | 8 | +8 | **new** |
| `tests/test_scanner_lock.py` | — | 10 | +10 | **new** |
| `tests/test_security_check.py` | — | 10 | +10 | **new** |
| `tests/test_vuln_scan.py` | — | 2 | +2 | **new** |
| **Total** | **195** | **295** | **+100** | |

Existing-file growth: 6 (+2 in each of the three `platform_config` test
files, all `schema_version: true`/`false` closure tests). New-file
total: 94. 6 + 94 = 100 — reconciles exactly two independent ways
against the +100 headline figure.

---

## 2. Unit-test quality (new Day 4 files)

Overall: **high on hygiene, uneven on depth.** Every new test file
follows the established `importlib.util.spec_from_file_location` pattern
for loading `scripts/` modules (correctly documented as necessary since
`scripts/` is not an importable package), uses `tempfile.TemporaryDirectory()`
context managers exclusively (no bare `NamedTemporaryFile`/`mkstemp`
without cleanup anywhere), and never mutates `os.environ` directly — the
platform-config tests pass environment overrides as an explicit `env={}`
parameter rather than touching the real process environment, so there is
no leakage risk at all. No `time.sleep()` calls, no fixed ports, and no
`unittest.mock.patch(...).start()` left unpaired with `.stop()`/context-manager
teardown anywhere in the ten new files. The fast (Docker-free) suite ran
twice back-to-back at 295/295 with no observed order-dependence.

No outright tautologies were found (no test recomputing the
implementation's own logic and comparing it to itself). Over-mocking is
mostly absent; where mocking is used (`test_compose_integration.py`'s
`SimpleNamespace`-based fake `sc` module, `test_generate_sbom.py`/
`test_vuln_scan.py`'s `subprocess.run` argv capture,
`test_security_check.py`'s `run_docker` mock) it is scoped exactly to
what each file's own docstring says it tests — pure parsing/dispatch
logic or a specific isolation property — with real Docker-integration
behavior explicitly deferred to `make` targets, consistent with
`.claude/CLAUDE.md`'s division of responsibility.

The unevenness is in **depth of individual-check coverage**, detailed
per-file below (§3–§6). Several files test only the "happy path plus a
representative failure" for logic that has more than two failure modes,
and two files (`test_generate_sbom.py`, `test_vuln_scan.py`) test only
one narrow property (Docker-socket isolation) and nothing else about
their production module — no `main()` exercise, no `SbomGenerationError`/
`VulnScanError` path, no missing-output-file path. This is a repeated,
deliberate pattern across all four Docker-orchestrating Day 4 scripts
(`image_audit.py`, `reproducibility_check.py`, `generate_sbom.py`,
`vuln_scan.py`) — each keeps its own orchestration logic (the `main()`
sequencing, the `try`/`finally` cleanup, the exit-code contract) entirely
outside the `unittest` suite, exercised only by a real `make` invocation
against real Docker. This is an intentional, documented scoping choice
(every affected docstring says so explicitly), not an oversight, but it
means a broken orchestration path in any of these four scripts has **zero
automated regression protection** in the fast suite — only a human
running `make image-audit`/`make reproducibility-check`/`make sbom`/
`make vuln-scan` and reading the output would catch it.

---

## 3. Dockerfile-validator test quality (`test_check_dockerfile.py`, 21 tests)

Strong on the two-stage/digest-pinning axis, genuinely thin elsewhere.
`check_from()` gets 7 dedicated tests covering both stages'
digest-pinning, wrong-digest, non-digest-pinned, `:latest`, and missing
`AS builder` — good discriminating-power coverage of the highest-value
check. `check_no_run_in_final_stage()` (3 tests, including the shellless
final-stage regression) and `check_exec_form_runtime_command()`/
`check_healthcheck()` (the bare-`python3` regression guards, explicitly
called out as Distroless-migration risks in both the implementation and
this checker's own docstring) are each directly and adversarially tested.

**Five of the module's ten checks have no dedicated rejection test at
all** — `check_no_sudo()`, `check_no_remote_add()`, `check_no_secret_vars()`,
`check_workdir()`, and `check_no_privileged_concepts()` are only ever
exercised through `FullValidDockerfileIntegrationTest`, which asserts the
*valid* fixture produces zero findings from all ten checks combined. That
proves each function doesn't false-positive on good input; it does
**not** prove any of the five actually flags bad input — if
`check_no_sudo()` were accidentally reduced to `return []`
unconditionally, no test in this file would fail. `check_user()` also has
no "no USER instruction at all" test (only the explicit-`root` case), and
the `DIGEST_PATTERN` "malformed-but-present" branch (a digest string of
the wrong length or non-hex, distinct from "digest missing entirely") is
untested — only the "missing `@digest`" and "well-formed but wrong-value
digest" paths are covered.

Live-verified: `python3 scripts/lint/check_dockerfile.py` against the
real `docker/app/Dockerfile` reports `OK (10 checks passed)`, confirming
the checker's happy-path and the fixture module's `VALID_DOCKERFILE`
constant both genuinely match production.

---

## 4. Distroless-test quality / image-audit coverage (`scripts/build/image_audit.py`, 543 lines, **0 dedicated unit tests**)

This is the most consequential finding in this review. `image_audit.py`
is the single file that owns nearly every Distroless-regression check the
review brief asks about by name — wrong final base, no-shell, no-package-manager,
no-pip, expected Python executable, `/data` ownership, source
immutability — and it has **no `tests/test_image_audit.py`** (confirmed:
`grep -rl "image_audit" tests/` returns nothing). Every one of its
checks, including the pure-Python, Docker-free `get_git_remote_source_url()`
regex-normalization helper (git@host:path → https://host/path — genuinely
testable without Docker) and the `SECRET_SHAPED_NAME_PATTERN` regex, is
exercised only by a real `make image-audit` run against a real built
image. There is no fast-suite regression protection for any of it.

This gap is not merely theoretical — it hid a real, confirmed defect:

### Confirmed defect: `check_final_base_is_approved_distroless()` does not check the approved digest

The module defines `EXPECTED_FINAL_BASE_DIGEST` and
`EXPECTED_FINAL_BASE_REPO` (image_audit.py:64–65) and the function's own
docstring (lines 382–388) claims it "cross-checks docker/app/Dockerfile's
own approved-digest pin ... against what the built image's RootFS
actually resolves to via `docker image inspect`. Proves the release image
was really built FROM the approved Distroless digest, not merely that
the Dockerfile text claims it was." **The implementation does not do
this.** `grep -n "EXPECTED_FINAL_BASE_DIGEST\|EXPECTED_FINAL_BASE_REPO"`
shows both constants are defined and never referenced anywhere else in
the file. The function body (image_audit.py:388–394) only runs
`docker image inspect <image> --format {{json .RootFS.Layers}}` and
checks the command succeeded with non-empty output — this is true for
*any* inspectable image regardless of its actual base. Confirmed live:
running `python3 scripts/build/image_audit.py` against the real,
correctly-built release image produces
`[AUDIT:image-policy] PASS image RootFS is inspectable (base-layer presence sanity check): [...]`
— note the check's own printed name is the honest, narrower one
("sanity check"); only the function name and docstring overclaim. This
specific check would pass identically even if the final stage's base
image digest were silently swapped for an unapproved one — the exact
"wrong final base" Distroless regression this review was asked to verify
detection for is **not** detected at the image-inspection [B] evidence
tier by this function.

This does not mean "wrong final base" is undetected project-wide:
`scripts/lint/check_dockerfile.py`'s `check_from()` performs the real
digest comparison against the same `EXPECTED_FINAL_DIGEST`/`EXPECTED_FINAL_REPO`
constants at the Dockerfile-*source* [A] evidence tier, and that path
**is** well-tested (`test_wrong_final_digest_is_rejected`, §3). But the
project's own stated philosophy (`.claude/CLAUDE.md`'s [A]/[B]/[C]/[D]
distinction) is that a source-level claim is not the same evidence as an
image-level proof that the built artifact actually matches it —
`image_audit.py`'s docstring explicitly promises the stronger [B] proof
and does not deliver it, and because the function has zero unit tests,
this went uncaught.

The other 18 `image_audit.py` checks were confirmed live to work
correctly against the real image (`image_audit: PASS (19/19 checks
passed)`, full output captured during this review) — the no-shell,
no-package-manager, no-pip, `/data` ownership, and source-immutability
checks all did genuinely exec real probes and got real (correct)
results. The concern here is regression protection going forward, not
today's correctness: none of these 19 checks, including the 18 that work
correctly today, would fail loudly in CI if a future edit broke their
logic — only a human reading `make image-audit` output would notice.

---

## 5. Reproducibility-test quality (`test_reproducibility_check.py`, 8 tests)

Good design: the manifest-algorithm tests exercise the exact production
`_MANIFEST_SCRIPT` source (not a reimplementation) via local subprocess
execution against disposable temp directories, using the same
`MAOPS_MANIFEST_ROOT` override the production script's container-side
invocation uses — a genuine test of the real code path.
`ComputeSourceDateEpochTests` correctly proves the epoch source is
`git log`-derived and explicitly proves `time.time()` is never called
(via a positive mock-assertion, not merely "the test happened to pass").

Coverage against the axes the review brief names — **content, mode, uid,
gid, symlink**:

| axis | tested? |
|---|---|
| content | ✓ `test_manifest_detects_differing_file_content` |
| mode | ✓ `test_manifest_detects_differing_file_mode` |
| symlink | ✓ `test_manifest_records_symlink_target` |
| mtime exclusion | ✓ `test_manifest_excludes_mtime_identical_content_different_mtime_matches` |
| **uid** | **not tested** |
| **gid** | **not tested** |

No test constructs two manifests differing only in file owner/group and
asserts they compare unequal. This is a real gap against a property the
production `_MANIFEST_SCRIPT` explicitly records (`st.st_uid`/`st.st_gid`
are both captured per entry) and the review brief explicitly asked to be
verified — the manifest-equality logic (a plain Python dict/list
comparison) is simple enough that the omission is low-risk in practice,
but it is untested, and the most plausible reason (constructing two files
with genuinely different owners requires `chown` privileges this
non-root local test environment doesn't have) is not stated anywhere in
the test file, so a reader can't distinguish "deliberately out of scope"
from "overlooked."

The two-independent-build integration proof itself
(`reproducibility_check.py`'s `main()` — the actual image-ID/RootFS/Config/manifest
four-way comparison) was not executed in this review (see environment
note); it was reviewed by source inspection only.

---

## 6. Supply-chain policy-test quality

### `check_trivy_report.py` / `test_check_trivy_report.py` (14 tests) — excellent

All five policy scenarios the review brief names by name are directly,
individually tested, plus a combined discriminating test and full-`main()`
CLI exercise:

| required scenario | test |
|---|---|
| Critical blocks | `test_any_critical_finding_fails_policy` |
| fixable High blocks | `test_fixable_high_finding_fails_policy` |
| unfixed High does not block | `test_unfixed_high_finding_is_reported_but_non_blocking` |
| malformed JSON blocks | `test_malformed_json_is_rejected` |
| wrong report structure blocks | `test_missing_schema_version_is_rejected`, `test_missing_results_is_rejected` |

`test_mixed_report_discriminates_each_bucket_correctly` is a genuine
discriminating-power test (five findings of different severities in one
synthetic report, asserting each lands in exactly the right bucket, not
just that the overall pass/fail is correct), and `MainCliTests` proves
the exit-code contract end-to-end through `main()` against real temp
files — this is the one Day 4 script whose orchestration entry point
*is* unit-tested, in contrast to §2/§4's pattern.

### `check_sbom.py` / `test_check_sbom.py` (10 tests) — strong

Covers malformed JSON, missing SPDX marker, empty package inventory,
missing/present Python-identity signal (including the Distroless-specific
lenient `python3.13-minimal`/`libpython3.13-stdlib` dpkg-name match, with
its own dedicated discriminating-power test distinguishing it from the
Debian-slim-specific literal "python" name), version-in-filename
mismatch, missing Syft tool-reference, local-workstation-path leakage,
and secret-shaped-string leakage. No gaps of note.

### `scanner_lock.py` / `test_scanner_lock.py` (10 tests) — strong

Comprehensive parser validation (bare tag, `:latest`, short digest,
non-hex digest, malformed line, duplicate key) plus a test that the real
shipped `security/scanners.lock` itself parses cleanly and both
`SYFT_IMAGE`/`TRIVY_IMAGE` resolve to plausible image names — a good
"the fixture and reality agree" check in the same spirit as
`check_dockerfile.py`'s `FullValidDockerfileIntegrationTest`.

### `generate_sbom.py` / `test_generate_sbom.py` (2 tests) and `vuln_scan.py` / `test_vuln_scan.py` (2 tests) — thin, narrowly scoped by design

Both files test exactly one property each: no Docker-socket mount, and
the image/report archive mount is read-only. Both are genuinely
important properties (this is the project's socket-isolation guarantee)
and both are tested by inspecting real captured argv rather than trusting
the code's own claim, which is the right technique. But — consistent with
§2/§4's pattern — neither file has any test for `main()`'s own
orchestration: `docker_save()`'s failure path, `SbomGenerationError`/
`VulnScanError` propagation, the "expected output not found" branch, or
(for `vuln_scan.py`) the handoff into `check_trivy_report.py`'s
`validate_report()`/`evaluate_policy()`. A regression in any of that
sequencing logic would only surface via a real `make sbom`/`make
vuln-scan` run.

---

## 7. Day 3 closure verdicts

| Finding | Verdict | Evidence |
|---|---|---|
| `schema_version` true/false rejection | **CLOSED, verified** | All three `platform_config.py` modules (`app/`, `gateway/`, `state/`, each lines 84–88) use `isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION` — a real fix, not a partial one. All three corresponding test files carry `test_schema_version_true_is_rejected`/`test_schema_version_false_is_rejected` (the +2/+2/+2 delta accounting for all six of the platform_config growth tests in §1), each with a docstring explaining the Python `bool`-is-`int`-subclass rationale. This closes the exact High finding the Day 3 test review (§4/§9) reported. |
| Role-aware rootfs-continuation healthcheck | **CLOSED, verified** | `healthcheck_module_for_role()` (security_check.py:409–425) is a small, deliberately pure dispatch function with its own 5-test `HealthcheckModuleForRoleTests` class. `check_kernel_readonly_write_fails()` now takes a `role` parameter and dispatches through it; `CheckKernelReadonlyWriteFailsDispatchTests` (5 tests) proves via a mocked `run_docker` that each role's probe genuinely execs *that* role's healthcheck module (not a hardcoded `app.healthcheck`) — the exact discriminating property the Day 3 High finding required, and it also proves the Distroless migration kept the probe shell-free (`test_probe_argv_never_invokes_a_shell`). This closes the Day 3 High finding cleanly; live execution of `security_check.py` (which only exercises the `app` role) additionally confirms the `app`-role path works end to end against the real image. |
| Network `Internal` runtime proof | **CLOSED at the unit-test tier; live re-verification not performed this review** | `check_network_internal_flag()`'s pure parsing/comparison logic (compose_integration.py) has 5 dedicated tests via a `SimpleNamespace`-mocked `sc.run_docker`, including a discriminating-power test (`test_mismatched_internal_flag_fails`) and a project-name-scoping test (`test_uses_project_prefixed_full_network_name`). This is Docker-free by design (real `docker network inspect` behavior is `make compose-test`'s job); this review did not bring up the full Compose stack to re-confirm the real-network path, matching the Day 3 review's treatment of the same script. |
| SIGTERM integration-harness cleanup | **CLOSED, verified, adversarial** | `SigtermHandlerTests` (3 tests) sends a **real** `signal.SIGTERM` to the test process itself via `os.kill(os.getpid(), ...)` — not merely asserting `signal.signal()` was called — and proves the handler converts it into a catchable `_TerminatedError`, including a direct proof that a SIGTERM raised mid-`try` still executes its `finally` block (the exact property `compose_integration.py`'s own cleanup depends on, and the exact gap a bare-default SIGTERM disposition would have). `self.addCleanup(signal.signal, signal.SIGTERM, signal.SIG_DFL)` correctly restores the process-global signal disposition after each test — no leakage into other tests. |

---

## 8. Distroless regression-detection audit (per review-brief checklist)

| regression to detect | detected by | verdict |
|---|---|---|
| wrong final base | `check_dockerfile.py:check_from()` [A, source] — well-tested (§3) | **detected at [A] only.** `image_audit.py`'s intended [B] image-level proof (`check_final_base_is_approved_distroless`) does not actually perform the comparison — see §4. |
| mutable-only final FROM (no digest) | `check_dockerfile.py:_check_digest_pinned_from()`, shared code path with the builder-stage case, which **is** directly tested (`test_non_digest_pinned_from_is_rejected`) | detected, but only via the builder-stage test exercising shared logic — no test removes the digest specifically from the *final* stage's FROM while leaving the builder's intact. Low risk given the shared implementation, but not directly proven. |
| wrong Python entrypoint | `check_dockerfile.py:check_exec_form_runtime_command()` — directly tested (`test_bare_python3_entrypoint_is_rejected`) | **detected, well-tested.** |
| shell accidentally present where audited | `image_audit.py:check_no_shell()` — real exec-attempt proof, confirmed working live; **0 unit tests** | works today (confirmed by execution); no fast-suite regression protection (§4). |
| runtime package manager accidentally present | `image_audit.py:check_no_package_manager()` — same pattern | works today (confirmed by execution); no fast-suite regression protection (§4). |
| UID/GID drift | `image_audit.py:check_data_directory()` (image-level, `/data` only) + `security_check.py:check_kernel_effective_uid_gid()` (process-level, unit-tested indirectly via role dispatch, not UID value itself) | `/data` UID/GID drift: image-level only, 0 unit tests. Process-effective UID/GID: confirmed live (`uid=10001 gid=10001`), but no synthetic-fixture unit test constructs a "wrong UID" case — both checks rely entirely on live-Docker execution for regression protection. |
| `/data` ownership drift | `image_audit.py:check_data_directory()` | same as above — 0 unit tests, works today by live confirmation. |
| shell-dependent probe reintroduction | `security_check.py`/`image_audit.py`'s `PYTHON_BIN` absolute-interpreter convention, with a dedicated regression test (`test_probe_argv_never_invokes_a_shell`, §7) in `security_check.py` only — `image_audit.py` has no equivalent unit test, though every one of its own probes was written using the same `exec_python()`/absolute-path convention and confirmed shell-free by live execution. | **partially detected**: `security_check.py`'s probes are unit-guarded against this regression; `image_audit.py`'s probes are not, relying on live execution and code-pattern consistency only. |

---

## 9. Flakiness assessment

**Fast (Docker-free) suite, 295 tests:**
- Ran twice back-to-back: `295/295 OK` both times (45.96s, 46.43s — a
  third run was in progress when this review's own tool-call timeout cut
  it off mid-run, not a suite failure). Deterministic.
- No fixed ports, no `time.sleep()`, no unrestored `os.environ` mutation,
  no bare `patch(...).start()` anywhere in the ten new Day 4 test files
  (§2).
- No shared mutable module-level state observed; every test builds its
  own temp directory/module load in `setUp`/inline.

**Docker-dependent scripts (this review had Docker available, unlike Day 3):**
- `docker buildx build` (single build, no `--no-cache`): completed
  cleanly in a few seconds (cached layers from a fresh checkout still
  built without incident).
- `python3 scripts/build/image_audit.py`: ran to completion,
  `19/19 checks passed`, no timeouts, no flaky probe.
- `python3 scripts/verify/security_check.py`: ran to completion,
  `22/22 checks passed`, including the real `docker stop` lifecycle
  check (`elapsed=0.92s`, well inside its 10s grace period) and the
  shell-free `[D]` read-only-write-rejection probe. No flakiness
  observed in a single run.
- **Not executed this review** (multi-minute, judged out of scope for a
  test-quality review — see environment note): `reproducibility_check.py`'s
  two-independent-`--no-cache`-build proof, and `vuln_scan.py`'s live
  Trivy scan (first-run vulnerability-DB download can itself take
  several minutes and is a known source of one-off network-dependent
  flakiness the script's own docstring already discusses honestly for
  *result* variance, not build variance). Assessed by source reading
  only: both use `uuid.uuid4().hex[:12]`-suffixed disposable tags/names
  and unconditional `try`/`finally` cleanup, consistent with the
  project's safety constraints; `reproducibility_check.py`'s
  `SOURCE_DATE_EPOCH` is derived from `git log -1 --format=%ct` with an
  explicit fixed-zero fallback (never `time.time()`, and this specific
  property does have its own direct unit test, §5) which is the
  correct anti-flakiness design for a reproducibility proof.
- Scanner cache/artifact paths (`generate_sbom.py`'s `scratch_dir`,
  `vuln_scan.py`'s Trivy `cache_dir`): both are created fresh per-run
  inside a `tempfile.TemporaryDirectory()`, so no cross-run cache-path
  collision or leftover-state flakiness risk on inspection; not
  exercised live this review.
- Compose teardown (`compose_integration.py`): not exercised live this
  review (would require bringing up the full three-service stack); its
  SIGTERM-handling logic is unit-tested (§7) and its `down -v` scoping
  to a unique `-p maops-compose-<uuid>` project name was already
  reviewed favorably in the Day 3 test review and is unchanged by Day 4.

---

## 10. Highest-value missing regressions

1. **`os.system`/`os.popen` alias-bypass test is missing from
   `test_check_source.py`.** The review brief explicitly asks to verify
   the Day 3 L-1 alias-closure regression protection remains — it does
   not, in test form. `check_source.py`'s `_collect_os_aliases()`
   (lines 80–104) still contains the real alias-tracking logic (`import
   os as x; x.system(...)`, `from os import system as x; x(...)`), but
   the new `test_check_source.py` only tests the *direct*, non-aliased
   case (`test_os_system_forbidden_for_tooling_even_with_subprocess_present`,
   using a literal `import os; os.system(...)`). No test anywhere in the
   suite constructs an aliased/rebound `os.system` call and asserts it
   is still caught. If a future refactor of `_collect_os_aliases()`
   silently broke the alias-tracking (e.g. simplified back to literal
   `os.system(...)` matching, exactly the original L-1 bug), nothing in
   the fast suite would fail.
2. **`image_audit.py`'s `check_final_base_is_approved_distroless()`
   should either implement the digest comparison its name/docstring
   promise, or be renamed/re-scoped to match what it actually checks**
   (§4) — and either way needs its own unit test once it does something
   deterministic to test. This is the review's one confirmed production
   defect, not merely a coverage gap.
3. **A `tests/test_image_audit.py` file covering the module's
   Docker-free logic** — at minimum `get_git_remote_source_url()`'s
   git-URL normalization (SSH-form → HTTPS-form, several branches, zero
   coverage) and the `SECRET_SHAPED_NAME_PATTERN`/`_PINNED_REFERENCE_PATTERN`-style
   regex matching used by `check_no_secret_or_key_shaped_files()`. This
   would not require Docker and would close the largest single
   test-coverage hole in the Day 4 scope (§4).
4. **Five untested `check_dockerfile.py` rejection paths**
   (`check_no_sudo`, `check_no_remote_add`, `check_no_secret_vars`,
   `check_workdir`, `check_no_privileged_concepts`) — each needs one
   direct "bad input is rejected" test, mirroring the pattern already
   used for the other five checks (§3).
5. **A uid/gid-divergence test for the reproducibility manifest**
   (§5) — even a monkeypatched `os.lstat`/synthetic-entry-dict test
   (rather than a real `chown`, which needs root) would close this
   specific gap against the review brief's explicit ask.

---

## 11. Severity counts

| Severity | Count | Findings |
|---|---:|---|
| High | 2 | `image_audit.py`'s `check_final_base_is_approved_distroless()` does not perform its documented digest comparison — confirmed by execution, not merely by reading (§4/§8); `os.system`/`os.popen` alias-bypass (Day 3 L-1) has zero regression-test coverage despite the review brief explicitly asking it be verified (§10.1) |
| Medium | 3 | `image_audit.py` (543 lines, the primary owner of the Distroless-regression checks this review was asked to audit) has zero unit tests of any kind (§4/§8); five of `check_dockerfile.py`'s ten checks have no dedicated rejection test (§3/§10.4); reproducibility manifest's uid/gid-divergence detection is untested against an explicit review-brief ask (§5/§10.5) |
| Low | 3 | `generate_sbom.py`/`vuln_scan.py` test only the socket-isolation property, not `main()`'s own error/orchestration paths (§6); the "slim → Distroless" 272-test migration delta is not independently recoverable from git history (§1, informational, not a defect); the `DIGEST_PATTERN` malformed-format branch in `check_dockerfile.py` is untested (§3) |

---

## 12. Release blockers

- **Not a release blocker in the traditional sense, but a documentation/
  correctness issue that should be fixed before this checker is relied
  on:** `check_final_base_is_approved_distroless()` (§4, High) currently
  provides no actual protection against a swapped final-base-image
  digest at the image-inspection tier — the Dockerfile-source-level
  check (`check_dockerfile.py`) does still catch this today, so the
  release image itself is not at risk from this specific gap, but
  `image_audit.py`'s own stated purpose for this check is not being
  fulfilled and the constants backing it are dead code. Recommend fixing
  before treating `make image-audit`'s "PASS" as meaning what its
  docstring says it means.
- **Not a release blocker, but a real regression-protection gap:** the
  L-1 alias-closure has no test (§10.1, High) — the underlying
  production logic is unchanged from Day 3 and this review found no
  evidence it has regressed, only that nothing would catch it if it did.
- Not blockers: the Medium/Low findings above are coverage gaps in newly
  added, currently-correct code (confirmed live for `image_audit.py`'s
  other 18 checks and `security_check.py`'s full 22), not evidence of
  an existing defect in the release image.

---

## 13. Final test-quality verdict

The Day 4 test suite's count claims are accurate: 195 → 295 (+100) is
independently verified at both the total and per-file-delta level, and
the earlier "slim" 272-test intermediate figure, while not independently
verifiable (no git history captures it), does not affect the two figures
that matter for release purposes.

The suite is **strong where it was built out deliberately**: the
vulnerability-policy engine (`check_trivy_report.py`, 14 tests) is the
best-tested file in this review — every policy branch the brief named is
directly, individually proven, plus a genuine discriminating-power test
and a full `main()` CLI exercise. The SBOM validator, scanner-lock
parser, Dockerfile digest-pinning logic, the SIGTERM-handling adversarial
regression, and the role-aware healthcheck-dispatch fix are all
similarly well-built, with real discriminating-power tests rather than
happy-path-only coverage.

The suite's actual weakness mirrors the Day 3 review's own finding
pattern: **the newest, most security-critical file has the least
protection.** `image_audit.py` — the 543-line module carrying nearly
every Distroless-specific regression check this review's brief asked
about by name (wrong base, no shell, no package manager, UID/GID drift,
`/data` ownership) — has no unit tests at all, and that absence directly
allowed a real defect (`check_final_base_is_approved_distroless()`'s
unused digest constants) to ship undetected. Live execution against the
real, correctly-built release image confirms every one of `image_audit.py`'s
19 checks passes today, and confirms `security_check.py`'s full 22
checks pass today — the release image itself is not shown to be at risk
by anything in this review. But "passes today, live, by hand" and "is
regression-tested" are the [C]/[D]-evidence-tier distinction this
project's own philosophy insists on elsewhere, and `image_audit.py`
currently only has the former. Recommend closing the
`check_final_base_is_approved_distroless()` defect and adding
`tests/test_image_audit.py` for its Docker-free logic before treating
the Distroless-migration test coverage as complete; the remaining
Medium/Low findings are quality-improvement, not blockers.
