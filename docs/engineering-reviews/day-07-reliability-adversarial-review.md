# Day 7 / v1.0.0 Reliability and Adversarial Testing Review (Independent)

**Reviewer scope**: independent review of the CURRENT UNCOMMITTED working
tree on `feature/day-7-final-hardening-production-readiness`, focused on
`scripts/reliability/reliability_check.py`, `tests/test_reliability_check.py`,
`docs/reliability.md`, `docs/ci-cd.md`, and the Day 7 attempt to close the
Day 6 post-restart cgroup-race Medium finding (`DAY6-POST-M2`). This review
does not read `docs/engineering-reviews/day-07-container-security-review.md`
or `docs/engineering-reviews/day-07-platform-architecture-review.md`, per
instruction. No implementation code was modified; nothing was
committed/pushed/tagged.

**Verification performed** (read-only / non-destructive):

- Full read of `scripts/reliability/reliability_check.py` (1328 lines) and
  `tests/test_reliability_check.py` (890 lines), plus their Day 7 diffs
  (`git diff`).
- Ran the Docker-free unit suite: `python3 -m unittest tests.test_reliability_check -v`
  — **65/65 tests passed**.
- Ran the **real** Docker integration script against the already-built
  `maops-docker-platform:1.0.0` image: `python3 scripts/reliability/reliability_check.py`
  — **32/32 real-Docker checks passed, exit code 0**, in a single run,
  taking the full Scenario 1 (transient OOM) and Scenario 2 (persistent
  OOM/bounded exhaustion) path to completion.
- Confirmed post-run cleanliness: `docker ps -a`, `docker network ls`,
  `docker volume ls` filtered on `maops-reliability-` all returned empty —
  no leaked resources.
- Cross-checked `docs/reliability.md`, `docs/ci-cd.md`, and
  `docs/production-readiness.md` against the actual code for staleness.
- Confirmed `.github/workflows/ci.yml`'s `release-policy` job (which runs
  `make release-check`, which runs `make reliability-check`) has no
  `continue-on-error`/`|| true` masking around this step.

---

## 1. Core reliability contract — status

All of the following were independently confirmed with **real** Docker/
kernel evidence from the run performed during this review, not merely
trusted from source/docstrings:

| Property | Evidence observed this session |
|---|---|
| Liveness vs. readiness separation | `app`/`gateway` `.healthcheck` modules stayed exit-0 while `state` was paused; `/readyz` on both degraded to 503 |
| Dependency-aware readiness | `gateway`'s `/readyz` and `app`'s own in-container `/readyz` both flipped to `503` while `state` was paused, and recovered to `200`/`{"status":"ready"}` after unpause |
| Timeout hierarchy (A-6) | Real pause-induced request completed in `elapsed=2.01s` against `inner_timeout=2.0s`, `outer_timeout` band `[1.50s, 2.50s]` — inner-governed, not outer-stacked |
| CPU/memory/PID limits | `HostConfig` exact match (`cpus=0.5`, `memory=134217728`, `pids_limit=64`) on all three real containers; cgroup v2 `memory.max`/`pids.max`/`cpu.max` files independently corroborated the same values from inside each container |
| `on-failure:3` | `RestartCount` progressed `0 -> 1` (Scenario 1, transient) then `1 -> 3` (Scenario 2, persistent), never further |
| `stop_grace_period=10s` | `docker stop` exited cleanly (`ExitCode=0`) in `0.65s`, well inside the 10s grace window |
| Graceful SIGTERM | Confirmed by the above clean `ExitCode=0` stop, plus `tests/test_reliability_check.py::SigtermHandlingTests` proving `reliability_check.py`'s **own** mid-run SIGTERM handling via a real `os.kill(os.getpid(), SIGTERM)` |
| Persistent state survival | Value `0 -> 1` (Scenario 1) `-> 2` (Scenario 2 + operator recovery) tracked correctly across pause/unpause, transient crash, persistent-failure exhaustion, and intentional stop/start |
| Transient dependency pause (A-6) | Real `docker pause`/`unpause` on `state`; controlled `503`, no hang, no raw traceback |
| Transient PID 1 failure (Scenario 1) | Real kernel OOM-kill via `oom_score_adj`-biased memory pressure from inside the container (never `docker kill`/`stop`), `exec` returncode `137` (kill signature), exactly one automatic restart |
| Automatic restart | `RestartCount` advanced with **no** `docker start`/`compose start` call anywhere in the automatic-recovery path |
| Persistent failure / exhaustion / operator recovery (Scenario 2) | `RestartCount` hit the absolute cap `3` (cumulative lifetime, not per-episode — confirmed `before=1, after=3`), `OOMKilled=True`, `Running=False`; only *after* that bound was proven did the script issue an explicit `compose start state` |
| Intentional stop exemption | `docker stop` (real) confirmed to leave `RestartCount` unchanged and the container `Running=False` for the full 3s settle window — restart-policy engine correctly does not fire |

No mocked-only evidence was found substituting for any of the above —
every one of these properties is backed by a real `docker`/kernel
observation from the run performed in this session, matching what the
script's own comments claim.

---

## 2. Day 7 cgroup-race classifier review (`_is_transient_cgroup_update_race`)

Reviewed `scripts/reliability/reliability_check.py:531-807` in full, plus
`tests/test_reliability_check.py:270-819`.

**Classifier logic** (`_is_transient_cgroup_update_race`, lines 562-600)
requires, in order, ALL of:

1. literal substring `"runc did not terminate successfully"` present;
2. a regex match of `openat2\s+(?P<path>\S+):\s*no such file or directory`
   (case-insensitive on the ENOENT wording only) — real ENOENT-on-`openat2`
   semantics, not a bare "no such file or directory" search;
3. `"/cgroup/"` present in the captured path (real cgroup-hierarchy
   context); and
4. the path's basename in `_TRANSIENT_CGROUP_RACE_ACCEPTED_FILENAMES =
   frozenset({"cgroup.controllers", "memory.max"})` — a small, explicitly
   enumerated, non-wildcard set.

This satisfies every sub-requirement in the task brief:

- **Both evidenced variants recognized**: `cgroup.controllers` (GitHub run
  `32960673438`) and `memory.max` (GitHub run `33059581018`) both pass
  `test_real_github_run_..._is_classified_as_transient`.
- **runc/cgroup context required, not "any error"**: confirmed via
  `test_runc_phrase_without_cgroup_controllers_is_not_transient` and
  `test_cgroup_controllers_without_runc_phrase_is_not_transient` — either
  fragment alone is rejected.
- **ENOENT/no-such-file specifically required**:
  `test_openat2_without_enoent_wording_is_not_transient` proves a real
  `openat2 .../memory.max: permission denied` (same accepted filename,
  same path context, same runc phrase) is rejected because it isn't
  ENOENT.
- **Restricted filenames, not a wildcard**:
  `test_unrelated_cgroup_controller_filename_is_deliberately_not_transient`
  proves an otherwise byte-identical error naming `pids.max` is rejected.
- **Does not retry unrelated runc failures / unrelated missing files /
  permission failures / invalid memory values / daemon-unavailable
  failures**: all covered by dedicated negative tests
  (`test_generic_no_such_file_or_directory_is_not_transient`,
  `test_permission_denied_is_not_transient`,
  `test_invalid_memory_limit_is_not_transient`,
  `test_invalid_argument_is_not_transient`,
  `test_container_not_found_is_not_transient`,
  `test_daemon_unavailable_is_not_transient`,
  `test_unknown_flag_is_not_transient`, `test_empty_stderr_is_not_transient`).
- **Monotonic bounded deadline, not wall-clock**: `update_container_resources_verified`
  (lines 613-714) computes `deadline = now() + deadline_seconds` once, with
  `now` defaulting to `time.monotonic` (immune to `time.time()`-style clock
  adjustment) and injectable for tests.
- **Bounded retry interval, not busy-looping**: `sleep(min(retry_interval_seconds, remaining))`
  (line 714) — a real bounded sleep between attempts, verified deterministically
  via injected fake `now`/`sleep` in every retry test (no real wall-clock
  sleep anywhere in the unit suite).
- **Real `HostConfig` re-verification after retry**: every success path
  (first-try, transient-then-success, already-applied-despite-nonzero-exit)
  re-inspects `HostConfig.Memory`/`HostConfig.MemorySwap` and only returns
  on an exact match — never inferred from exit code (`_verified()`, lines
  665-669; exercised by tests A, B, B2, C, G).
- **"Already applied after nonzero-but-transient" handled safely and
  idempotently**: `test_transient_error_but_already_applied_returns_without_extra_update`
  proves no redundant `docker update` is issued once the retry-check
  `inspect` shows the target values already landed.
- **Action/restore failure precedence preserved**: `with_memory_shrink_restored`
  (lines 717-807) raises the restore failure with the action's own
  exception chained as `__cause__` when both fail
  (`test_action_failure_and_restore_failure_precedence_and_diagnostics`,
  `test_action_failure_with_restore_retry_exhaustion_preserves_precedence`);
  a successful restore re-raises the action's own exception unchanged
  (`test_action_failure_and_successful_restore_reraises_action_exception`,
  including a bare `RuntimeError`, not just `ReliabilityError`, via
  `test_restores_original_values_even_when_action_raises_unexpected_exception`).
- **No infinite retry loop possible**: traced the exact loop
  (`update_container_resources_verified`, lines 675-714) — every iteration
  either returns, raises immediately (non-retryable error or a
  disappeared-container `inspect` failure), or checks `remaining = deadline
  - now(); if remaining <= 0: raise` before sleeping. The deadline is fixed
  once at entry and only consulted, never extended. `test_transient_failures_continue_until_deadline_fails`
  confirms bounded call count (`assertLess(len(calls), 40)`) and
  `assertGreaterEqual(now(), 2.0)` against the fake clock.

**Real Docker discriminating power**: the real run performed in this
session (§ above) shrank and restored `state`'s memory limit in **exactly
1 attempt each** (no transient race encountered locally — consistent with
the code's own documented claim that this race is GitHub-hosted-runner-
specific and not reproducible against local Docker Desktop). This means
the retry path itself, while unit-tested thoroughly, has **not** been
exercised end-to-end against a real, live recurrence of either cgroup
race variant since the Day 7 fix landed — see Finding M2 below.

---

## 3. Real Docker scenarios — discriminating power after the Day 7 change

Scenario 1 and Scenario 2 remain meaningfully discriminating:

- Scenario 1 uses a genuine kernel OOM-kill (own-process `oom_score_adj`
  maxed from inside the container, then real memory pressure against the
  *unmodified* `mem_limit`), confirmed by `exec` returncode `137` and
  exactly one `RestartCount` increment — never `docker kill`/`docker stop`,
  never a manufactured `RestartCount`.
- Scenario 2 lowers the real memory limit (`docker update --memory 6m`)
  and keeps it lowered across every restart attempt via
  `with_memory_shrink_restored`, independently confirming
  `RestartCount == 3` (absolute cap, correctly treated as a cumulative
  lifetime counter, not per-episode — the code and the real run both
  confirm `before=1, after=3`, not `before=1, after=4`), `OOMKilled=True`,
  and `Running=False` before any `compose start` is issued.
- No test in `tests/test_reliability_check.py` manufactures `RestartCount`
  artificially, and no scenario substitutes `docker kill`/`docker stop`
  for the genuine kernel-triggered OOM path — confirmed by direct reading
  of `scripts/reliability/reliability_check.py:964-1198` and the absence
  of any such call in that block.
- The Day 7 retry-classifier widening (accepting `memory.max` in addition
  to `cgroup.controllers`) does **not** loosen either scenario's own
  pass/fail assertions — `_wait_for_bounded_exhaustion`'s bound check
  (`restart_count_after == EXPECTED_RESTART_MAX_ATTEMPTS and ... OOMKilled
  is True and not Running`) and Scenario 1's `transient_crash_recovered`
  check are both untouched by the Day 7 diff and still require exact
  values, not a loosened range.

---

## 4. Test quality

- **No tautological assertions found** in the reviewed classes — every
  positive test also has a matching negative counterpart (see §2).
- **Retry path has extensive negative cases** (11 rejection tests in
  `TransientCgroupUpdateRaceClassifierTests` alone).
- **No timing flakiness**: every retry/deadline test uses an injected
  fake `now`/`sleep` pair (`_fake_clock`); `PollUntilTests` uses short
  real deadlines (0.2s-5s) purely to prove bounded termination, not as a
  correctness assertion on exact timing.
- **No shell-outs to real Docker anywhere in `tests/test_reliability_check.py`**
  — confirmed by grep for `subprocess`/`docker compose`/`Popen`/`run_docker(\[.*docker`/`shutil.which`:
  zero matches. All 65 tests are genuinely Docker-free, matching the
  `_fake_sc()` pattern shared with `tests/test_compose_integration.py`.
- **No hidden exception swallowing**: `with_memory_shrink_restored`
  explicitly documents and tests that it never swallows an exception —
  confirmed by the precedence tests in §2.
- **No manufactured green output**: the real run performed this session
  independently reproduced every claimed real-Docker property (RestartCount
  progression, OOMKilled, ExitCode, elapsed timing bands) without any
  script-side shortcut.
- **Environment-sensitive assumptions are explicitly and correctly
  hedged**: `check_cgroup_v2_resource_limits` documents and tests
  (`test_unavailable_paths_do_not_fail`) that missing cgroup v2 files are
  reported, not silently passed *or* failed — a deliberate, tested
  best-effort design, not an unstated assumption.
- **Untested error precedence**: not found — precedence is directly
  tested (§2).
- **Cleanup weakness found** — see Finding M1 below: a real gap in the
  paused-container teardown-safety guarantee that this project's own
  skill documentation explicitly calls out as required.

---

## Findings

```
ID: DAY7-REL-M1
Severity: Medium
Title: A failed `docker unpause` during the A-6 pause proof is masked, defeating the "always unpause before down -v" teardown guarantee
Evidence: scripts/reliability/reliability_check.py:945-949 —
    finally:
        unpause_result = sc.run_docker(["unpause", state_container])
        state_is_paused = False
        if unpause_result.returncode != 0:
            print(f"reliability_check: WARNING: docker unpause {state_container} failed: ...", file=sys.stderr)
  `state_is_paused` is set to `False` unconditionally, regardless of
  whether `unpause_result.returncode` actually indicates success. The
  outer teardown `finally` block (lines 1305-1310) only re-attempts an
  unpause `if state_is_paused:` (line 1306) — but since the inner block
  already cleared the flag even on a failed unpause, the outer `finally`
  will never retry, and `compose down -t 10 -v` (line 1308) will be
  issued against a container that may still be paused. This is exactly
  the risk `.claude/skills/compose-validation/SKILL.md`'s Day 5 section
  and this review's own brief explicitly call out: "a paused container
  can otherwise make teardown hang or behave unexpectedly." No test in
  `tests/test_reliability_check.py` exercises a failed-unpause path (no
  `test_...unpause_failure...` exists), so this gap is untested as well
  as unguarded.
Impact: If a real `docker unpause` transiently fails (daemon load,
  network blip on Docker Desktop, etc.), the script's own teardown may
  attempt to `compose down -v` a paused `state` container, risking a
  hung or degraded teardown and a leaked `maops-reliability-<uuid>`
  Compose project/volume — a direct violation of this repository's own
  "unique name + guaranteed cleanup" convention (CLAUDE.md "Docker
  safety constraints"). Not observed in the real run performed this
  session (unpause always succeeded on the first attempt), so likelihood
  is low, but the code path exists and is unguarded/untested.
Required remediation: Only clear `state_is_paused` when
  `unpause_result.returncode == 0`; on failure, retry the unpause (bounded)
  or leave the flag `True` so the outer `finally` gets a second attempt
  before `compose down -v` runs. Add a Docker-free unit test (a `sc` fake
  whose first `unpause` call fails) proving the outer teardown still
  attempts a second unpause.
Release-blocking: NO
```

```
ID: DAY7-REL-M2
Severity: Medium
Title: DAY6-POST-M2 is recorded as fully "CLOSED" without any real live-Docker confirmation of the new `memory.max` retry path actually firing and succeeding post-fix
Evidence: docs/production-readiness.md lines ~116-149 and its
  disposition table ("DAY6-POST-M2 (cgroup classifier narrowness) |
  CLOSED THIS SESSION") cite only: (a) the classifier code change and (b)
  new unit tests using synthetic fixture text derived from the real log
  strings. The Day 6 remediation checklist for this finding
  (docs/engineering-reviews/day-06-post-release-verification.md §7.2)
  explicitly required "add/retain real Docker reliability evidence" as
  one of its closure conditions — distinct from the unit-test requirement
  listed separately. The real, live `reliability_check.py` run performed
  during this review (§1/§2 above) completed both the Scenario-2 shrink
  and restore `docker update` calls in exactly 1 attempt each — the
  transient cgroup/runc race was NOT encountered, consistent with the
  code's own comment (scripts/reliability/reliability_check.py:1009-1011,
  "Local Docker Desktop succeeds on the first `docker update` attempt")
  that this race is a GitHub-hosted-runner-specific phenomenon. No commit
  in this working tree, and no evidence file reviewed, shows a real CI
  run since this fix landed that actually encountered the `memory.max`
  (or `cgroup.controllers`) race and was successfully retried by the new
  code.
Impact: The classifier's *logic* is genuinely and conservatively correct
  (§2 above; this is real, high-confidence evidence). But "CLOSED" as
  recorded conflates "the code is right and well unit-tested" with "the
  fix has been proven against a real recurrence of the failure it exists
  to fix" — the latter is unverifiable on demand (the race does not
  reproduce locally) and has not yet happened for the `memory.max`
  variant specifically. Declaring full closure risks a false sense of
  confidence if the classifier's path-context/openat2/ENOENT assumptions
  turn out to be even slightly wrong on the next real occurrence (e.g. a
  future GitHub runner emitting a differently-worded runc error for the
  same underlying race) — the only guard against that residual risk is
  the CI run that eventually re-triggers it, which has not happened yet
  for this exact fix.
Required remediation: Downgrade the disposition-table wording from
  unqualified "CLOSED" to something honestly reflecting the evidence tier
  actually available, e.g. "code-level CLOSED, pending first live
  re-occurrence confirmation" — consistent with this project's own
  [A]/[B]/[C]/[D] evidence-tier philosophy (a synthetic-fixture unit test
  is closer to a source-level [A]/[B] proof than a [D] real-occurrence
  proof). Track the next real CI encounter of either signature as the
  actual closing evidence, and cite the specific `gh run view` output
  when it occurs (mirroring how the original `32960673438` closure was
  documented in Day 6).
Release-blocking: NO
```

```
ID: DAY7-REL-L1
Severity: Low
Title: docs/reliability.md and docs/ci-cd.md's dedicated cgroup-race sections are stale relative to the Day 7 classifier change
Evidence: docs/reliability.md:333-349 and docs/ci-cd.md:166-234 both still
  describe the classifier as requiring only
  `"runc did not terminate successfully"`, `"cgroup.controllers"`, and
  `"no such file or directory"` (the Day 6 design) — neither mentions the
  `memory.max` variant, the `openat2`-regex/ENOENT requirement, the
  `/cgroup/`-path-context requirement, or the enumerated
  `_TRANSIENT_CGROUP_RACE_ACCEPTED_FILENAMES` set the Day 7 code diff
  actually introduced (scripts/reliability/reliability_check.py:550-600).
  `docs/ci-cd.md` was itself modified elsewhere in this same working-tree
  diff (release-bundle/checksum sections), so this is not an
  untouched-file oversight but a section that was skipped.
  `docs/production-readiness.md` §1.3 does correctly describe the Day 7
  change, so the information exists — it is just not propagated to the
  two docs that are supposed to be the authoritative design references
  for this exact mechanism.
Impact: A reader following `docs/reliability.md`/`docs/ci-cd.md` (the
  documents this repository's own README/roadmap point to as the
  authoritative reliability/CI design references) would not learn that a
  second failure signature is now handled, or the more precise
  path/ENOENT semantics now enforced — purely a documentation-accuracy
  gap, no functional impact.
Required remediation: Update both sections to describe the current
  `_is_transient_cgroup_update_race` design (both accepted filenames, the
  `openat2`/ENOENT/path-context requirements) and cross-reference
  DAY6-POST-M2/`docs/production-readiness.md` §1.3.
Release-blocking: NO
```

```
ID: DAY7-REL-I1
Severity: Informational
Title: Real end-to-end reliability_check.py run independently reproduced this session
Evidence: `python3 scripts/reliability/reliability_check.py` executed
  against the real `maops-docker-platform:1.0.0` image during this
  review completed with "reliability_check: PASS (32/32 reliability
  checks passed)", exit code 0. Key real observations captured in the
  run log: `RestartCount before=0 after=1` (Scenario 1, transient),
  `RestartCount before=1 after=3 ... OOMKilled=True Running=False`
  (Scenario 2, persistent/bounded), `docker stop` `ExitCode=0
  elapsed=0.65s`, an intentional-stop `restart_count before=0 after=0`
  (unchanged), and the A-6 pause/timeout proof at
  `elapsed=2.01s inner_timeout=2.0s expected_band=[1.50s, 2.50s]`.
  Post-run `docker ps -a`/`network ls`/`volume ls` filtered on
  `maops-reliability-` were all empty.
Impact: None (positive finding) — corroborates that the Day 7 changes
  did not regress any of the Day 5/6 real-Docker reliability proofs, and
  that teardown is clean under the (only) path actually exercised this
  session (no mid-run failure was injected, so this run does not by
  itself close Finding M1 above).
Required remediation: None.
Release-blocking: NO
```

```
ID: DAY7-REL-I2
Severity: Informational
Title: Classifier and retry-helper unit coverage fully satisfies the Day 7 hardening brief's own checklist
Evidence: `python3 -m unittest tests.test_reliability_check -v` — 65/65
  tests passed, 0 failures/errors, 0 shell-outs to `docker`/`subprocess`
  anywhere in the file (confirmed by grep). Deterministic fake-clock
  retry tests (`_fake_clock()` helper, used throughout
  `UpdateContainerResourcesVerifiedTests`/`WithMemoryShrinkRestoredTests`)
  and 11 dedicated negative-discrimination tests in
  `TransientCgroupUpdateRaceClassifierTests` were all independently
  re-verified by reading their assertions and re-running the suite.
Impact: None (positive finding).
Required remediation: None.
Release-blocking: NO
```

---

## Final Verdict

**APPROVE WITH CONDITIONS**

Total findings by severity: Critical: 0, High: 0, Medium: 2, Low: 1,
Informational: 2.

**Conditions for full approval** (none release-blocking individually, but
both should be addressed before this branch is treated as final v1.0.0
evidence):

1. Fix the paused-container teardown-masking bug (`DAY7-REL-M1`) — a
   one-line logic fix (only clear `state_is_paused` on a verified
   successful unpause) plus one new Docker-free unit test.
2. Correct the DAY6-POST-M2 disposition wording (`DAY7-REL-M2`) to
   distinguish "classifier logic is correct and thoroughly unit-tested"
   (true, and independently confirmed by this review) from "proven
   against a real live recurrence of the `memory.max` race" (not yet
   true — no such recurrence has been observed since the fix landed).
3. Refresh `docs/reliability.md`/`docs/ci-cd.md` (`DAY7-REL-L1`) to match
   the actual Day 7 classifier design already correctly described in
   `docs/production-readiness.md`.

**Day 6 post-restart cgroup-race Medium finding (DAY6-POST-M2): PARTIALLY
CLOSED.**

Reasoning: the classifier code itself is a genuine, high-quality, and
correctly conservative fix — it recognizes both real evidenced failure
signatures (`cgroup.controllers` and `memory.max`), requires real
`openat2`/ENOENT semantics and cgroup-path context rather than a loose
substring match, uses an explicitly enumerated (non-wildcard) filename
set, preserves a monotonic bounded deadline and bounded retry interval,
preserves mandatory post-update `HostConfig` re-verification, handles the
already-applied-despite-nonzero-exit case idempotently, and preserves
correct action/restore failure precedence with proper exception chaining
— all independently confirmed against the code and against a full,
passing 65-test Docker-free unit suite (§2 of this review). This is
real, high-confidence evidence that the *fix is correctly built*.

However, the Day 6 remediation checklist for this finding explicitly
required "add/retain real Docker reliability evidence," and the only
real-Docker evidence available for the `memory.max` variant specifically
is the *original failing occurrence* (GitHub run `33059581018`) that
motivated the fix — there is no subsequent real CI/Docker run in evidence
where the new code actually encountered that race again and was observed
retrying and succeeding. The real run performed during this review did
not encounter the race either (consistent with the code's own
documentation that it is a GitHub-hosted-runner-specific phenomenon not
reproducible against local Docker Desktop). Declaring this "CLOSED"
therefore rests partly on synthetic (albeit log-derived, high-fidelity)
fixture evidence standing in for a [D]-tier real-occurrence proof this
project's own evidence-tier philosophy would normally require before
calling a runtime-behavior claim fully closed. The correct status is
**PARTIALLY CLOSED**: the fix is correct and well-tested; full closure is
pending the first real re-occurrence (of either accepted signature) being
observed and successfully retried in a live CI/Docker run.

DAY 7 RELIABILITY ADVERSARIAL REVIEW COMPLETE
