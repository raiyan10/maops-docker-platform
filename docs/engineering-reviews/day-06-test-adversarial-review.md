# Day 6 Test + Adversarial Review — v0.6.0 (CI/CD and Release Engineering)

Independent TEST + ADVERSARIAL reviewer (`docker-test-engineer` role).
Review only — no implementation, test, workflow, or other doc file was
modified by this review; this document is the only file it adds. Branch
`feature/day-6-cicd-release-engineering`, PR #6, target `v0.6.0`.

Scope, per the review brief: workflow-policy test quality
(`scripts/ci/check_workflows.py`/`tests/test_check_workflows.py`),
release-context test quality
(`scripts/release/check_release_context.py`/`tests/test_check_release_context.py`),
adversarial re-derivation of Day 5's "3 Medium + 6 Low" closure claims,
Buildx-portability regression protection, the runtime-patch-lock and SBOM
supply-chain test suites (Day 6's emergency `libssl3t64` CVE-2026-14456
overlay), and — the highest-priority item — a line-by-line adversarial
read of `scripts/reliability/reliability_check.py`'s new
`_is_transient_cgroup_update_race()` classifier and its surrounding
bounded-retry/verified-resource-update machinery, cross-checked against
the three real GitHub Actions runs (`32938805880` FAILED,
`32960673438` FAILED, `32967457379` PASSED).

**Method note:** every count/claim below was independently re-derived —
`make test` was run fresh on this machine (591 tests, all passing, see
§1), and `gh run view --log-failed`/`gh run view --log` were used to read
the *actual* GitHub Actions log text for runs `32960673438` and
`32967457379` rather than trusting `docs/ci-cd.md`'s paraphrase of them
(§5). No file outside this report was modified; no workflow was
triggered; no destructive git operation was run.

---

## 1. Independently re-run test count

```
$ python3 -m unittest discover -s tests -p "test_*.py"
Ran 591 tests in 52.784s
OK
```

591 (from Day 5's independently-confirmed 359) — a genuine +232, not a
stale number. The bulk of the growth is exactly where the diffstat says
it should be: `tests/test_check_workflows.py` (538 lines, new),
`tests/test_check_release_context.py` (256 lines, new),
`tests/test_check_compose.py` (395 lines, new — this is M-C's closure),
`tests/test_runtime_patch_lock.py` (95 lines, new),
`tests/test_reliability_check.py` (+561/-diff, the transient-race and
`with_memory_shrink_restored` hardening), plus smaller additions to
`tests/test_check_sbom.py`, `tests/test_check_dockerfile.py`,
`tests/test_app_platform_config.py`, `tests/test_gateway_platform_config.py`,
`tests/test_server.py` (`git diff main...HEAD --stat -- tests/`
independently confirms this file list; no test file changed outside it).

No `pytest` import anywhere in the new/touched files (`grep -rn "^import pytest\|^from pytest" tests/` returns nothing); every new test class is a plain `unittest.TestCase`. No new test file uses a fixed external port, `time.sleep()` as a correctness assertion, or shared mutable module-level state — confirmed by direct read of every new/touched file (§3–§7 below cite specifics).

---

## 2. Day 5's "3 Medium + 6 Low" — closure independently re-verified, not taken on the PR's word

Per `docs/engineering-reviews/day-05-release-readiness.md` §5 ("Nine
unique Medium/Low findings total (3 Medium, 6 Low)"), the specific
findings and this review's own independent verification of each:

| ID | Finding | Where fixed (this branch) | Independently verified? |
|---|---|---|---|
| M-A | `with_memory_shrink_restored`'s restore failure only warned, never failed the check | `scripts/reliability/reliability_check.py:660-750` — restore now goes through `update_container_resources_verified()`; a failed/unverified restore raises `ReliabilityError` (never a warning-only path) | **CLOSED.** Read the code directly; `tests/test_reliability_check.py:617-674` (`test_restore_command_failure_raises_reliability_error`, `test_restore_verification_mismatch_raises_reliability_error`, `test_action_failure_and_restore_failure_precedence_and_diagnostics`) exercise exactly the three sub-cases the finding named. |
| M-B | No `app`-side analogue of `gateway`'s `UpstreamTimeoutTests` | `tests/test_server.py:192-213` (`StateTimeoutTests`, `state_delay_seconds = 0.5`) | **CLOSED.** Confirmed by direct read — the previously-unused fixture hook is now actually set to a nonzero value in a real subclass. |
| M-C | `check_compose.py`'s Day 5 structural checks (`check_resource_limits`, `check_restart_policy`, `check_stop_grace_period`, `_parse_cpus`/`_parse_bytes`/`_parse_duration_seconds`) had zero persisted unit tests | `tests/test_check_compose.py` (new, 395 lines) | **CLOSED.** All six named functions have dedicated test classes (`ParseCpusTests`, `ParseBytesTests`, `ParseDurationSecondsTests`, `CheckResourceLimitsTests`, `CheckRestartPolicyTests`, `CheckStopGracePeriodTests`) — confirmed via `grep -n "^class " tests/test_check_compose.py`. |
| L-1 (health-timeout) | `inner_governed` check used a loose `>= inner_timeout * 0.5` lower bound | `scripts/reliability/reliability_check.py:862-878` — tightened to a `[inner_timeout * LOWER_RATIO, inner_timeout * UPPER_RATIO]` band (±25%) | **CLOSED.** Read directly; the code comment cites the exact prior finding and the empirical 5-trial spread (1.75%) that justifies the new band width. |
| L-1 (resource-restart) | `check_resource_limits()` had no lower-bound floor (accepted e.g. `pids_limit: 1`) | `scripts/compose/check_compose.py:480-519` — tightened from a range check to EXACT equality (`abs(cpus - EXPECTED_CPUS) > TOLERANCE`, `mem_limit != EXPECTED_MEM_LIMIT_BYTES`, `pids_limit != EXPECTED_PIDS_LIMIT`) | **CLOSED.** `tests/test_check_compose.py::CheckResourceLimitsTests::test_cpus_below_target_fails`/`test_mem_limit_below_target_fails`/`test_pids_limit_below_target_fails` directly exercise the previously-unguarded below-target case. |
| L-1 (failure-recovery) | `RestartCount`'s "cumulative lifetime counter" framing didn't mention it resets on an explicit `docker start` | `docs/reliability.md:177-178` and surrounding section | **CLOSED** (documentation finding — confirmed the passage now exists and explicitly documents the Day 6 closure with a cross-reference back to the finding). |
| L-2 (failure-recovery) | `_TerminatedError`/SIGTERM mechanism in `reliability_check.py` had no Docker-free unit test (unlike the identical mechanism in `compose_integration.py`) | `tests/test_reliability_check.py:775-820` | **CLOSED.** `test_sigterm_raises_terminated_error`, `test_terminated_error_message_names_the_signal`, `test_terminated_error_is_reachable_through_try_finally` each send a real `os.kill(os.getpid(), signal.SIGTERM)` and assert the exception/`finally` behavior — mirrors `tests/test_compose_integration.py`'s pre-existing pattern exactly. |
| L-1 (test-adversarial) | No test asserted the inclusive upper timeout bound is *accepted* | `tests/test_app_platform_config.py:52-55`, `tests/test_gateway_platform_config.py:97-100,196,271` | **CLOSED.** `test_max_boundary_timeout_is_accepted`/`test_max_boundary_state_dependency_timeout_is_accepted`/`test_max_boundary_margin_is_accepted` all present. |
| L-2 (test-adversarial) | `gateway/platform_config.py`'s timeout fields had no `-Infinity` test (asymmetric with `app`'s) | `tests/test_gateway_platform_config.py:84-93,184-190,261-266` | **CLOSED.** `test_negative_infinity_timeout_is_rejected`, `test_negative_infinity_state_dependency_timeout_is_rejected`, `test_negative_infinity_margin_is_rejected` all present, for all three fields. |

**All nine of Day 5's carried-forward findings are genuinely closed, not
merely claimed closed** — every one was independently confirmed by
reading the actual diff (not the PR description) and, where a test
should exist, confirming the specific test class/method exists and
exercises the specific behavior the original finding named. No relabeling, no "closed by documentation" substituting for a real code fix where a code fix was expected (M-A, M-C) or vice versa (L-1 failure-recovery is correctly still a documentation-only fix, matching its own documentation-completeness category).

---

## 3. `scripts/ci/check_workflows.py` / `tests/test_check_workflows.py` — workflow-policy test quality

Read `scripts/ci/check_workflows.py` (517 lines) and
`tests/test_check_workflows.py` (538 lines) in full.

**Discriminating power — confirmed real, not merely file-parses-cleanly:**
every one of the eleven `check_*` functions in `CHECKS` has at least one
paired accept-good/reject-bad test in `tests/test_check_workflows.py`
(`NoPullRequestTargetTests`, `UsesPinnedToFullShaTests`,
`NoContinueOnErrorTests`, `NoManufacturedPassTests`,
`CiPermissionsReadOnlyTests`, `ReleasePermissionsScopedTests`,
`ManualDispatchCannotPublishTests`, `RequiredTriggersTests`,
`BuildxContainerBuilderBeforeReleaseCheckTests`,
`NoRegistryPublicationTests`, `NoDay7PlusToolingTests`) — each with a
deliberately mutated fixture derived from the shared `GOOD_CI`/
`GOOD_RELEASE`/`GOOD_CI_RELEASE_POLICY`/`GOOD_RELEASE_VALIDATE` fixtures,
not a single pass/fail run against the real committed files. This
directly satisfies the review brief's discriminating-power requirement.

**Explanatory-comment false-positive handling — confirmed correct and
tested:** `check_workflows.py:183-198`'s `_strip_comments()` is applied
uniformly to both files before any check runs, and
`tests/test_check_workflows.py:300-302`
(`test_explanatory_comment_mentioning_it_does_not_false_positive`)
directly proves a comment that *names* `pull_request_target` to explain
its absence does not trip `check_no_pull_request_target`. The mechanism
is shared by every other pattern-matching check
(`check_no_registry_publication`, `check_no_day7_plus_tooling`,
`check_no_continue_on_error`, `check_no_manufactured_pass`) and
`StripCommentsTests` independently unit-tests the stripping primitive
itself — adequate coverage of the shared mechanism without needing a
duplicate per-check test.

**Buildx portability regression protection — confirmed present and
itself unit-tested (this was the review brief's specific concern about a
silent regression back to the incompatible default `docker` driver):**
`check_buildx_container_builder_before_release_check()`
(`check_workflows.py:392-458`) statically enforces, by step order within
the job block, that both `ci.yml`'s `release-policy` job and
`release.yml`'s `validate` job (i) create a `docker-container`-driver
Buildx builder with `--use` **before** the `make release-check` step,
and (ii) remove it afterward with `if: always()`. Eight dedicated
negative tests
(`test_missing_release_policy_job_is_rejected`,
`test_missing_release_check_step_is_rejected`,
`test_missing_builder_creation_is_rejected`,
`test_builder_creation_missing_use_flag_is_rejected`,
`test_builder_creation_after_release_check_is_rejected`,
`test_missing_cleanup_step_is_rejected`,
`test_cleanup_step_missing_always_is_rejected`) each independently
confirm the check rejects one specific way this exact regression could
reappear. **A future PR that reverted `ci.yml`/`release.yml` to the
default Buildx driver (deleting the builder-creation step) would fail
`make workflow-check` immediately, at the cheap `quality` gate, before
ever reaching a real GitHub Actions run.** This directly answers the
brief's "could this silently break again" question: no, not without also
breaking a committed, real test.

**Self-reference / determinism proof:**
`MainDeterminismTests::test_real_committed_workflows_pass_every_check`
independently re-parses the *real* `.github/workflows/*.yml` files (not
fixtures) and confirms zero findings — genuinely exercises the same code
path `make workflow-check` runs in CI.
`test_main_reads_no_github_environment_variables` is a static
source-scan (`"os.environ"`/`"os.getenv"`/`"import os"` all absent from
the module source) proving the self-reference claim in the module's own
docstring (line 15-22) rather than merely asserting it.

**Info:** `check_no_manufactured_pass`'s `\|\|\s*true\b` regex and
`check_no_continue_on_error`'s regex both operate only on the two
workflow YAML files, not `Makefile` — correctly scoped, since
`Makefile:153,158`'s `docker compose ... down ... || true` (cleanup
best-effort in `make clean`) is legitimate and outside this checker's
declared scope (`.github/workflows/*.yml` only, per the module's own
docstring). Not a gap; flagged only so a future reader doesn't assume
`check_workflows.py` covers the Makefile too.

No Critical/High finding in this section.

---

## 4. `scripts/release/check_release_context.py` / `tests/test_check_release_context.py` — release-context test quality

Read both files in full (222 + 256 lines).

**Git-free by construction, confirmed both structurally and by
grep:** `validate_version_format`, `validate_tag_format`,
`tag_matches_version`, `validate_release_notes_exist`,
`validate_main_history` are all pure functions; `validate_main_history`
and `build_tag_context` both take an injectable `is_ancestor` callable
defaulting to `default_git_is_ancestor` (`check_release_context.py:107-135,155-180`).
`grep -n "subprocess\|git " tests/test_check_release_context.py` returns
nothing — confirmed no test in this file shells out to real `git`, and
`default_git_is_ancestor` itself is never referenced by name anywhere in
the test file (only via the injected-lambda substitute). This exactly
matches the brief's requirement.

**Failure-path coverage — genuinely exercised, not just the happy
path:** `ValidateVersionFormatTests` (6 cases: missing patch,
prerelease suffix, `v`-prefix, empty string, non-numeric component),
`ValidateTagFormatTests` (5 cases including uppercase `V`),
`TagMatchesVersionTests::test_version_mismatch_is_rejected` (the exact
`VERSION=0.6.0, tag=v0.5.0` example the brief names),
`ValidateMainHistoryTests::test_non_ancestor_commit_is_rejected` (the
not-on-main case), `BuildTagContextTests::test_commit_not_in_main_history_is_rejected`,
`test_version_tag_mismatch_is_rejected`,
`test_missing_release_notes_is_rejected_even_with_valid_ancestry`. This
is comprehensive coverage of every failure mode the brief asked about
(wrong VERSION, tag/VERSION mismatch, not-on-main, missing release
notes), each as its own dedicated negative test rather than a single
combined "bad case" test that would have weaker discriminating power.

**Real-repository cross-checks, appropriately scoped:**
`test_real_shipped_v0_6_0_notes_exist` and
`test_real_repository_dry_run_context_succeeds` cross-check against this
branch's own real `VERSION`/`docs/releases/v0.6.0.md` — these do read
real repository files (not git-free in the "no filesystem access" sense)
but never shell out to git or Docker, consistent with the module's own
"Docker-free, git-free" scope claim.

**One real gap found, Low severity:** `default_git_is_ancestor()`
(`check_release_context.py:107-121`) is never called by any automated
check at all in this repository — not by a unit test (correctly, per
scope) and not by any other script's own test suite either. The *only*
place it will ever run for the first time is a real tag push through
`.github/workflows/release.yml`, which per the task brief has not
happened yet (expected, not a defect — GitHub requires the
`workflow_dispatch`-bearing workflow to exist on `main` first). This
means the one piece of *real* git-adapter code in this entire Day 6
change has zero test coverage of any kind and zero real-world execution
evidence at the time of this review — a plain `subprocess.run(["git",
"merge-base", "--is-ancestor", ...])` call, so the risk is low (this is
one of the most standard git invocations there is), but it is worth
naming explicitly as an open item rather than silently assuming it will
work. See **L-3** below.

No Critical/High finding in this section.

---

## 5. `scripts/reliability/reliability_check.py`'s `_is_transient_cgroup_update_race()` — the highest-priority item

Read `reliability_check.py:480-750` in full (the entire Day 6 GitHub-finding remediation block) plus every corresponding test in
`tests/test_reliability_check.py` (`TransientCgroupUpdateRaceClassifierTests`,
`UpdateContainerResourcesVerifiedTests`, `WithMemoryShrinkRestoredTests`).

### 5.1 Ground-truth cross-check against the real GitHub Actions log

```
$ gh run view 32960673438 --log-failed | grep -i "cgroup.controllers\|runc did not terminate"
...reliability_check: FAIL: docker update (shrink memory) maops-reliability-fc75d503b248-state-1
failed: Error response from daemon: Cannot update container
cd65c1d62adfb4ed6f03f1d4c5a54d9bc912be2571089ab3a19adfa4c2d7badf:
runc did not terminate successfully: exit status 1: openat2
/sys/fs/cgroup/system.slice/docker-cd65c1d62adfb4ed6f03f1d4c5a54d9bc912be2571089ab3a19adfa4c2d7badf.scope/cgroup.controllers:
no such file or directory
```

This is **exactly** the text embedded in
`GITHUB_RUN_32960673438_TRANSIENT_STDERR`
(`tests/test_reliability_check.py:31-38`) — the fixture is not an
approximation or a paraphrase, it is the real log line (container ID
substituted with a distinct fake, immaterial to the classifier). The
classifier's own three-fragment match
(`"runc did not terminate successfully"`, `"cgroup.controllers"`,
`"no such file or directory"`) is confirmed to genuinely match this real
error, not a hypothetical one.

```
$ gh run view 32967457379 --log | grep "reliability_check:.*attempt"
reliability_check: shrank AND VERIFIED ...state-1's memory limit to 6m
  (... 1 attempt(s)) ...
reliability_check: restored AND VERIFIED ...state-1's memory limit to
  134217728 bytes (... 1 attempt(s))
reliability_check: PASS (32/32 reliability checks passed)
```

**Important, previously undocumented observation from this review's own
log inspection:** in the passing run (`32967457379`), both the shrink
and restore `docker update` calls succeeded on the **first** attempt —
the retry path was *not* exercised for real. This means the retry
mechanism's correctness in a genuine race, under genuine GitHub-hosted-
runner conditions, has so far only been proven **once** (the original
failure) and has not yet been proven to *recover* under real CI
conditions — only under the synthetic fixture replay in
`tests/test_reliability_check.py`. This is not a defect (races are, by
definition, not reliably reproducible on demand, and forcing one would
require flaky, timing-dependent test infrastructure this project
correctly avoids), but it is a real evidentiary gap worth naming rather
than silently treating "CI is green" as "the retry logic was proven
under fire." See **L-4** below — Low, not Medium, because the underlying
unit-test coverage of the retry state machine itself (§5.2) is thorough
and the fix is a narrowly-scoped, easily-reverted addition, not a
structural change to anything already proven correct.

### 5.2 Narrowness of `_is_transient_cgroup_update_race()`

`reliability_check.py:526-543`:

```python
def _is_transient_cgroup_update_race(stderr: str) -> bool:
    text = stderr or ""
    return (
        "runc did not terminate successfully" in text
        and "cgroup.controllers" in text
        and "no such file or directory" in text.lower()
    )
```

**Judgment: narrow enough.** Requiring all three fragments
conjunctively is the right design — any one fragment alone is either too
generic ("no such file or directory" is common to many unrelated,
genuinely fatal Docker/runc errors) or insufficiently specific to this
exact failure mode. `TransientCgroupUpdateRaceClassifierTests` directly
proves the conjunction is load-bearing, not decorative: nine negative
tests each remove or vary one part of the signature
(`test_generic_no_such_file_or_directory_is_not_transient`,
`test_runc_phrase_without_cgroup_controllers_is_not_transient`,
`test_cgroup_controllers_without_runc_phrase_is_not_transient`,
`test_permission_denied_is_not_transient`,
`test_invalid_memory_limit_is_not_transient`,
`test_invalid_argument_is_not_transient`,
`test_container_not_found_is_not_transient`,
`test_daemon_unavailable_is_not_transient`,
`test_unknown_flag_is_not_transient`,
`test_empty_stderr_is_not_transient`) — and one positive test
(`test_real_github_run_32960673438_error_is_classified_as_transient`)
uses the real log text, not a synthetic approximation. This is
substantively adversarial test design, not count-padding.

**Minor, Low-severity robustness note:** the classifier's case handling
is inconsistent — `.lower()` is applied only to the
`"no such file or directory"` fragment, not to
`"runc did not terminate successfully"` or `"cgroup.controllers"`. If a
future Docker Engine version emitted this exact error with different
capitalization in either of those two fragments (locale/version drift),
the classifier would silently stop matching and every subsequent
transient race would be treated as a hard failure (fail-safe, not
fail-open — this direction of drift is not a false-positive risk, only
a false-negative one that degrades back to "no retry", which is the
strictly safer failure mode). Not release-blocking. See **L-5**.

### 5.3 Walk-through of every specific adversarial scenario the brief named

| Scenario | Where handled | Where tested | Verdict |
|---|---|---|---|
| Unrelated errors fail immediately, no retry | `reliability_check.py:632-634` (`if not _is_transient_cgroup_update_race(stderr): raise ... immediately`) | `test_unrelated_error_fails_immediately_with_no_retry` asserts exactly 1 call, error text preserved | Correct |
| Retry deadline is bounded, cannot hang forever | `reliability_check.py:615,651-657` — `deadline = now() + deadline_seconds`; `remaining <= 0` raises | `test_transient_failures_continue_until_deadline_fails` uses an injected fake clock (`now`/`sleep`, no real wall-clock delay) and asserts `len(calls) < 40` (bounded, not a runaway loop) | Correct, and genuinely Docker-free/fast (no real `time.sleep` — confirmed by `grep -n "time\.sleep(" tests/test_reliability_check.py` returning nothing) |
| Successful `docker update` but wrong resulting `HostConfig` still fails | `reliability_check.py:622-630` — a reported-success mismatch raises immediately, is NOT retried | `test_success_but_hostconfig_mismatch_fails` — asserts exactly 2 calls (no retry) and the wrong values appear in the error message | Correct — the classifier only excuses the specific transient *command* error, never a wrong end-state |
| Already-applied-mid-race (non-zero exit, but the mutation actually landed) is detected and not double-retried | `reliability_check.py:636-649` — on a recognized transient error, `HostConfig` is re-inspected BEFORE any retry; a match returns immediately with a `"note"` field, no second `docker update` issued | `test_transient_error_but_already_applied_returns_without_extra_update` — asserts exactly 2 calls total (one failed update, one inspect) and `"note"` present in the result | Correct |
| Container disappearance during the race | `reliability_check.py:546-553` (`_inspect_host_config` raises `ReliabilityError` immediately on inspect failure — never retries against a possibly-gone container) | `test_container_disappears_during_retry_fails` — simulates "No such container" on the post-race inspect, asserts exactly 2 calls (no further retry attempted) | Correct |
| Action-failure + restore-failure precedence | `reliability_check.py:722-748` — restore failure is raised with the action's own exception attached as `__cause__` via `raise restore_exc from action_exc`; if restore succeeds, the action's own exception is re-raised unchanged | `test_action_failure_and_restore_failure_precedence_and_diagnostics` asserts both the restore-failure text AND the chained `__cause__` action exception are present; `test_action_failure_and_successful_restore_reraises_action_exception` proves the opposite ordering | Correct, and this is exactly the right precedence — a permanently misconfigured container is the more urgent operational fact, with no diagnostic information lost |
| Real Docker command unexpectedly failing elsewhere in the main flow | Every `run_docker`/`compose`/`docker_json` call site in the main scenario flow checks `returncode != 0` and raises with the real `stderr` attached (confirmed by direct read of `main()`'s body, unchanged from Day 5's already-adversarially-reviewed pattern) | Pre-existing coverage, re-confirmed still true | Correct |

**No false-positive-PASS path was found.** Every scenario the brief
asked this reviewer to specifically walk through resolves to a real,
independently-injectable-fixture-tested, deterministic behavior — not an
assumption, not a code-reading-only claim.

### 5.4 Could this retry logic mask a real, non-transient reliability regression?

Adversarially considered and rejected, for three independent reasons:

1. The classifier requires the **exact** three-fragment signature of
   this one specific `runc`/cgroup v2 race. A genuine, unrelated
   resource-limit-update regression (e.g. a real permissions problem, a
   real "invalid memory limit" from a bad value, a real daemon outage)
   produces different stderr text and is proven (§5.3, row 1) to fail
   immediately with zero retries.
2. Even when the exact transient signature is matched, the retry does
   **not** blindly re-issue `docker update` and trust its exit code — it
   independently re-`docker inspect`s and compares against the exact
   expected byte values every single time (§5.3, rows 3-4). A "recovery"
   that produced the wrong end state is not accepted as a pass.
3. The retry is bounded (10s default, §5.3 row 2) and every attempt
   count is visible in the emitted `CheckResult`/log line (e.g. "2
   attempt(s)") — a real, worsening regression that needed many retries
   to eventually succeed would still show up as visibly anomalous
   evidence in the run's own printed output, not be silently absorbed.

**Overall judgment on `_is_transient_cgroup_update_race()`: narrow
enough, correctly scoped to the exact real GitHub finding it was written
for, does not swallow unrelated failures, and does not substitute a
"succeeded eventually" claim for the exact verified end-state check that
already existed.** The one real, not-yet-closed gap is evidentiary, not
structural: the retry path's *real*-CI-triggered correctness has been
proven once by necessity (the original failure) and has not yet been
proven to recover live under a second real race (§5.1) — this is an
acceptable, disclosed residual risk given the alternative (deliberately
engineering a flaky test to reproduce a kernel race on demand) is worse.

No Critical or High finding in this section.

---

## 6. Runtime-patch-lock and SBOM supply-chain test quality (Day 6 CVE-2026-14456 overlay)

Read `scripts/security/runtime_patch_lock.py` (91 lines),
`tests/test_runtime_patch_lock.py` (95 lines), `scripts/security/check_sbom.py`
(177 lines), `tests/test_check_sbom.py` (160 lines), and the relevant
`docker/app/Dockerfile` `security-patch` stage (lines 48-107) plus
`scripts/lint/check_dockerfile.py`'s cross-check of it.

**`runtime_patch_lock.py` — confirmed shape-only, correctly so:**
`tests/test_runtime_patch_lock.py` verifies the lock file's KEY=value
shape, required-key completeness, SHA256 well-formedness (64 hex chars),
and HTTPS-only URL — but does **not** (and should not, per the review
brief's Docker-free constraint) verify the pinned SHA256 against a real
download. That real verification happens at a different, correct layer:
`docker/app/Dockerfile:81-83`'s `ADD --checksum=sha256:...` is a real
BuildKit-enforced content check — the build itself fails if the
downloaded `.deb`'s SHA256 doesn't match, independently of any Python
test. This is the right division of labor (build-time cryptographic
enforcement vs. test-time shape validation), and
`test_real_repository_lock_file_parses_and_pins_libssl` cross-checks the
*real* committed `security/runtime-patches.lock` parses cleanly.

**Cross-check between the lock file and the Dockerfile's hardcoded
values — confirmed real, not just claimed:** the Dockerfile's own
`ADD --checksum=` line hardcodes the URL/digest as literal text (it does
not read the lock file at build time — Distroless/BuildKit `ADD` cannot
do that), so a drift between the lock file and the Dockerfile would
otherwise go undetected. `scripts/lint/check_dockerfile.py`'s
`check_only_approved_remote_add`-family function
(`check_dockerfile.py:349-413`) independently loads
`runtime_patch_lock.py` and compares the Dockerfile's literal
`ADD --checksum=`/URL against the lock file's `LIBSSL_DEB_SHA256`/
`LIBSSL_URL`, and `tests/test_check_dockerfile.py:289-299`
(`test_...` variants tampering with `LIBSSL_DEB_SHA256`/`LIBSSL_URL`/the
missing-checksum case) prove this cross-check actually rejects a
mismatch, not merely that it runs without erroring. This closes exactly
the "overlay silently drifts from its own lock file" risk.

**SBOM patch-version validation — confirmed present and exactly matching
the brief's concern:** `check_sbom.py:112-136` loads
`runtime_patch_lock.py` and asserts the SBOM's own `libssl3t64` package
entry's `versionInfo` includes the lock file's `LIBSSL_VERSION`
(`3.5.7-1~deb13u2`), not the pre-patch vulnerable version. Critically,
`tests/test_check_sbom.py::test_stale_libssl_version_is_rejected`
directly proves the exact failure mode the brief asked about: **if the
overlay silently failed to apply (Syft's SBOM still reports the
vulnerable pre-patch version) but the build otherwise proceeded, this
check fails the build.** `test_missing_libssl_package_is_rejected`
covers the complementary case (the package vanishing from the SBOM
entirely). Both are real, deliberately-broken-fixture tests, not
assertions against the one already-good real SBOM.

No Critical/High finding in this section.

---

## 7. Findings

### L-3 (Low): `default_git_is_ancestor()` has zero test coverage and zero real-world execution evidence at review time

`scripts/release/check_release_context.py:107-121` is the one function
in this entire Day 6 branch that will run real `git` for the first time
against a real tag event, and by design (correctly) no unit test
exercises it. Per the task's own framing this is expected — the real tag
push and the `workflow_dispatch` dry run genuinely have not happened yet
because the workflow must land on `main` first. Not release-blocking for
merging this PR, but **should be exercised for real via the
`workflow_dispatch` dry run before the first real tag push**, precisely
because that is the only way this one function's real invocation will
ever be validated before it matters. See PRE-TAG condition #1.

### L-4 (Low): the transient-race retry path has not yet been proven to recover under a real, second GitHub-hosted-runner occurrence

Independently confirmed via `gh run view 32967457379 --log`
(§5.1): the passing run's `docker update` calls both succeeded on the
first attempt (`1 attempt(s)`), meaning the bounded-retry recovery logic
introduced to fix run `32960673438` has so far only been exercised
against synthetic fixtures (`tests/test_reliability_check.py`), not
against a second real occurrence of the race it was written for. The
underlying unit-test coverage of the retry state machine itself is
thorough (§5.3), and deliberately engineering a flaky test to force a
second real kernel race on demand would trade this small evidentiary gap
for a worse problem (a genuinely flaky CI suite), so this is correctly
left as a disclosed residual risk rather than "fixed" — but a future
regression reviewer should not assume run `32967457379`'s green status
constitutes live proof the retry itself works; it only proves the retry
*wasn't needed* that time.

### L-5 (Low): `_is_transient_cgroup_update_race()`'s case-normalization is inconsistent across its three required fragments

`reliability_check.py:538-542` applies `.lower()` only to the
`"no such file or directory"` comparison, not to
`"runc did not terminate successfully"` or `"cgroup.controllers"`. A
future Docker Engine version that changed capitalization in either of
those two fragments would silently stop matching. This is a fail-safe
direction (degrades to "treat as non-transient, fail immediately" — the
strictly safer outcome, not a false-positive-PASS risk), so it is Low,
not Medium. Recommend normalizing all three comparisons to `.lower()`
for consistency, purely for future-proofing against upstream wording
drift, not because current behavior is wrong.

### Info: workflow-policy and release-context validators are both genuinely test-driven, not merely re-run against the one good real file

Both `tests/test_check_workflows.py` and
`tests/test_check_release_context.py` construct deliberately-mutated
fixture variants of good baseline text/context for essentially every
policy branch, closing exactly the "single pass/fail run against the
real committed files" risk the review brief called out by name. No
further action needed.

### Info: `make clean`'s `|| true` cleanup pattern is correctly out of `check_workflows.py`'s declared scope

`Makefile:153,158` — confirmed this is intentional best-effort cleanup
in a non-gate context, and `check_workflows.py` only ever reads the two
workflow YAML files, never the Makefile, so there is no false-negative
risk here. Recorded for completeness only.

**No Critical or High finding in this review.**

---

## 8. Overall verdict

**APPROVE WITH CONDITIONS**

The test suite for this Day 6 change is substantively adversarial where
it matters most: the workflow-policy and release-context validators both
have genuine accept-good/reject-bad discriminating power rather than
single-run rubber-stamping; the Day 5 "3 Medium + 6 Low" carry-forward
findings are all independently confirmed genuinely closed (§2), not
relabeled or quietly dropped; the Buildx-portability regression that
caused the first real CI failure now has its own static, unit-tested
regression guard that would catch a silent reversion (§3); the supply-
chain (`runtime_patch_lock.py`/`check_sbom.py`) tests correctly separate
build-time cryptographic enforcement from test-time shape/consistency
validation and specifically prove the "overlay silently fails to apply"
failure mode is caught (§6); and — the highest-priority item —
`_is_transient_cgroup_update_race()` is a narrow, three-fragment
conjunctive classifier, cross-checked against the real GitHub Actions log
text it was written for, that does not swallow unrelated Docker errors,
does not accept a wrong post-update end state, does not retry against a
disappeared container, and correctly surfaces restore-failure precedence
over action-failure with the action's exception preserved as a chained
cause (§5). No Critical or High finding was found anywhere in this
review's scope.

The conditions below are all Low-severity, non-structural, and do not
block merging this PR — but should be satisfied before the first real
`v0.6.0` tag push, since two of them concern code paths this review
could not itself exercise for real (no tag has been pushed yet) and the
third is a one-line robustness hardening with no current behavioral
impact.

### PRE-TAG conditions

1. **Run the `workflow_dispatch` dry run at least once against `main`
   before the first real `v0.6.0` tag push**, once `release.yml` has
   landed on the default branch (this is the documented, expected
   sequencing, not a defect — see the task framing). This is the only
   way `default_git_is_ancestor()` (L-3) — the one real-`git` adapter
   this branch adds, deliberately untested by design — gets any
   execution evidence before it matters for a real release.
2. **Treat GitHub Actions run `32967457379`'s green status as proof the
   retry mechanism was correctly built, not as proof the retry mechanism
   has been exercised live** (L-4). If a future run's log shows a
   `docker update`/restore step taking more than 1 attempt, capture that
   log excerpt as the first real confirming evidence the fix works under
   fire, and reference it from `docs/ci-cd.md` alongside the existing
   `32960673438` failure record.
3. Optionally (not blocking), normalize
   `_is_transient_cgroup_update_race()`'s three fragment comparisons to
   consistently use `.lower()` (L-5) for resilience against future Docker
   Engine wording drift — current behavior is correct against every
   observed real error string, so this is future-proofing, not a fix.

TEST-ADVERSARIAL REVIEW: APPROVE WITH CONDITIONS
