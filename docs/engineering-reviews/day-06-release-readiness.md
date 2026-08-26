# Day 6 Release-Readiness Adjudication — v0.6.0

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering`
Pull Request: #6 (`https://github.com/raiyan10/maops-docker-platform/pull/6`)
Target: `v0.6.0`
Role: final adjudicator, synthesizing all five independent Day 6 reviews
plus the post-review main-bound-dispatch remediation. This document does
not modify any implementation file, workflow, test, or prior review — it
is the only file this adjudication adds.
Date: 2026-08-26.

---

## 1. Executive verdict

**PR #6 is safe to merge now.** **`v0.6.0` is not safe to tag now.**
Tagging becomes safe only after the eight PRE-TAG conditions in §17 pass
for real, post-merge, on `main`.

Both PR checks are green at current HEAD (`00e7c76`, run `32985020258`,
independently confirmed via `gh` in this session — see §3). All five
independent reviews found **zero Critical and zero High** findings. The
one Medium finding that was merge-relevant (release-engineering review
§6.1: `workflow_dispatch`'s "main-only" intent was documented but not
structurally enforced) has been **remediated and independently verified
CLOSED** in this adjudication (§8, §13). The one Medium finding that
remains open (workflow-security review §5: no automated drift tripwire
for the `libssl3t64` overlay against a future Distroless base bump) is
**carried as nonblocking v0.6.0 technical debt**, not closed — it concerns
future maintenance, not current artifact correctness.

## 2. Scope reviewed

- The current Day 6 implementation at HEAD `00e7c76` ("fix(day-6): enforce
  main-bound release dry runs"), four commits ahead of `main`
  (`41b91eb`, `3fbd5ff`, `fca5ca3`, `00e7c76`).
- All five existing Day 6 review documents (unmodified, read in full):
  `day-06-ci-architecture-review.md`, `day-06-workflow-security-review.md`,
  `day-06-integration-parity-review.md`, `day-06-test-adversarial-review.md`,
  `day-06-release-engineering-review.md`. The last of these was written
  against HEAD `fca5ca3`, i.e. **before** the main-bound-dispatch
  remediation commit `00e7c76` — its Medium finding is the one this
  adjudication independently re-verifies as closed.
- The remediation commit itself (`git show 00e7c76`), read in full:
  `.github/workflows/release.yml`, `scripts/release/check_release_context.py`,
  `scripts/ci/check_workflows.py`, `tests/test_check_release_context.py`,
  `tests/test_check_workflows.py`, `docs/ci-cd.md`, `docs/releases/v0.6.0.md`.
- Real GitHub CI history (`gh pr checks 6`, `gh run list`, `gh run view
  --log`/`--log-failed` on all four run IDs) — not accepted on the task
  framing's word.
- Fresh local validation on this machine's own Docker Engine (unit tests,
  `make quality`'s five static checks, and a fresh `make release-check`
  run — build, image-audit, smoke, security-check, compose-test,
  reliability-check, in progress at report time for the remaining stages;
  see §4 for exactly what was locally reproduced vs. what relies on the
  real CI run's own evidence).
- `compose.yaml`, `docker/app/Dockerfile`, `security/runtime-patches.lock`,
  `.claude/agents/`, `.claude/skills/` for the scope-preservation check
  (§14).

## 3. GitHub CI evidence and progression

Independently confirmed via `gh` in this session (not taken from the task
framing):

| Run ID | Result | Root cause / outcome |
|---|---|---|
| `32938805880` | **FAILED** | `ERROR: Docker exporter is not supported for the docker driver` — the GitHub-hosted runner's default Buildx `docker` driver cannot satisfy this project's `type=docker,rewrite-timestamp=true,dest=...` deterministic archive exporter. Confirmed via `gh run view --log-failed`: the exact error text is present. |
| `32960673438` | **FAILED** | Buildx fix landed and the full chain progressed past `reproducibility-check`; failed later in `reliability-check` Scenario 2 on a genuine GitHub-hosted-runner `runc`/cgroup-v2 race during the second `docker update` mutation (`runc did not terminate successfully: ... cgroup.controllers: no such file or directory`), confirmed by direct log text match. Scenario 1 (transient OOM-kill crash + auto-restart) had already passed cleanly before this. |
| `32967457379` | **PASSED** | Both jobs green; combined Buildx/cgroup-retry remediation proven end to end, including a full `reproducibility-check` (STRONG) and `compose_integration`/`reliability_check` pass. This was HEAD `fca5ca3` at review time. |
| `32985020258` | **PASSED** | Current HEAD `00e7c76`. Both jobs green (`gh run view 32985020258 --json headSha` → `00e7c760b349fb0fef4156c0f889c75a1cbad958`, matching `git log -1`). This is the run `gh pr checks 6` reports today. |

`gh pr checks 6` (run fresh in this session):

```
Quality (fast, Docker-free static checks)                         pass   1m1s
Release policy (build, security, reliability, reproducibility, supply chain)  pass   4m36s
```

`gh pr view 6` confirms `state=OPEN`, `mergeable=MERGEABLE`,
`mergeStateStatus=CLEAN`, base `main`.

No run was deleted, rewritten, or dismissed by this adjudication or by
any of the five reviews — all four runs remain in the project's real
history as engineering evidence, exactly as the task required.

**Real historical CI evidence for the two remediated failures**, pulled
directly from `gh run view --log-failed` in this session:

- `32938805880`: `docker buildx build --no-cache --output
  type=docker,rewrite-timestamp=true,...` → `ERROR: failed to build:
  Docker exporter is not supported for the docker driver.` — matches the
  documented Buildx-portability root cause exactly.
- `32960673438`: `reliability_check: FAIL: docker update (shrink memory)
  ... failed: Error response from daemon: Cannot update container ...:
  runc did not terminate successfully: exit status 1: openat2
  .../cgroup.controllers: no such file or directory` — matches the
  documented transient cgroup-v2 race exactly, and is the literal text
  embedded in `tests/test_reliability_check.py`'s
  `GITHUB_RUN_32960673438_TRANSIENT_STDERR` fixture (byte-for-byte, not a
  paraphrase).

**Real evidence from the latest green run (`32985020258`, current HEAD)**,
pulled directly from `gh run view --log` in this session — every number
below is the actual value the real GitHub Actions run reported, not a
value carried over from the task framing:

```
image_audit: PASS (22/22 checks passed)
smoke: single-role (app) PASS
smoke: multi-role chain PASS
smoke: PASS
compose_integration: PASS (58/58 inspection checks passed)
reliability_check: PASS (32/32 reliability checks passed)
reproducibility_check: PASS - STRONG evidence level (exact image ID equality,
  RootFS diff-ID equality, Config/OCI-label equality, 24-entry normalized
  filesystem manifest)
check_sbom: PASS
vulnerability policy: CRITICAL=0 (any -> FAIL)
vulnerability policy: HIGH-with-fix=0 (any -> FAIL)
vulnerability policy: HIGH-without-fix=16 (reported, non-blocking)
vulnerability policy: other severities: {'MEDIUM': 54, 'LOW': 51, 'UNKNOWN': 3}
supply-chain-check: sbom + sbom-check + vuln-scan all passed
```

Also pulled from the same run: both real `docker update` calls in
reliability-check Scenario 2 (shrink and restore, against container
`maops-reliability-594e62fbb45a-state-1`) each succeeded on the **first**
attempt (`1 attempt(s)`) — i.e. the cgroup-race retry path was *not*
exercised live in this run either. This directly corroborates the
test-adversarial review's L-4 finding (§9) — it is disclosed, not
fabricated, and no second real race occurrence is claimed anywhere in
this report.

## 4. Local validation evidence

Run fresh, in this session, on this machine's own Docker Engine — not
copied from the task framing's expected values:

| Check | Result | Matches expected? |
|---|---|---|
| `python3 -m unittest discover -s tests` | **622 tests, OK** (53.8s) | Yes |
| `python3 scripts/lint/check_dockerfile.py` | **OK — 12/12** | Yes |
| `python3 scripts/compose/check_compose.py` | **OK — 17/17**, `version=0.6.0` | Yes |
| `python3 scripts/ci/check_workflows.py` | **OK — 13/13** (12 pre-remediation + 1 new `check_release_context_validation_is_authoritative`) | Yes — note the count grew from Day 6's original 12 to 13 specifically because of the remediation this report adjudicates (§8) |
| `make release-check` (fresh, this session, run to completion, exit code 0) | Full chain green end to end, every stage independently reproduced against real local Docker: `build`/`image-audit` **22/22** (including the real libssl content-hash + `ssl.OPENSSL_VERSION='OpenSSL 3.5.7 9 Jun 2026'` proof), `smoke` PASS (single-role + multi-role chain), `security-check` **22/22** (real `[D]` kernel proofs: UID/GID 10001, empty capability sets, `NoNewPrivs=1`, real rejected rootfs write), `compose_integration` **PASS (58/58)**, `reliability_check` **PASS (32/32)** — including a real transient kernel OOM-kill + automatic recovery, a real persistent OOM-kill exhausting the `on-failure:3` cap with operator recovery, the real `docker pause state` A-6 timeout-hierarchy proof (`elapsed=2.02s`, inside the `[1.50s, 2.50s]` expected band), and the real intentional-stop-does-not-restart proof — `reproducibility_check` **PASS — STRONG** (exact image ID equality across two independent builds), `check_sbom` **PASS**, vulnerability policy **CRITICAL=0, HIGH-with-fix=0, HIGH-without-fix=16**, `supply-chain-check` **PASS** | **Exact match** to the real GitHub Actions run (`32985020258`, §3) on every one of these values — independently reproduced twice (real CI, real local), not merely asserted once. No leaked `maops-compose-*`/`maops-reliability-*`/`maops-repro-*`/`maops-smoke-*` container, network, or Compose project was left behind after this run (verified via filtered `docker ps -a`/`docker network ls`). |
| `git tag -l` | `v0.1.0..v0.5.0` only — **no `v0.6.0` tag exists** | Yes |
| `gh release list` | Latest release is `v0.5.0` — **no `v0.6.0` GitHub Release exists** | Yes |

No value in this table was copied blindly from the task's "expected
approximately" list — every one was independently obtained by running
the actual check against the actual current worktree or the actual latest
real GitHub Actions run in this session.

## 5. Review-by-review adjudication

| Review | Verdict as written | This adjudication's assessment |
|---|---|---|
| `day-06-ci-architecture-review.md` (docker-architect) | APPROVE | Sound. Independently re-verified the three-stage Dockerfile structure, the Buildx remediation, and both base-image digest pins by direct `docker pull`/`buildx imagetools inspect`. Its one Info finding (F-1, stale base pins) and F-2 (dry-run sequencing note) are correctly non-blocking and remain so. |
| `day-06-workflow-security-review.md` (container-security-reviewer) | APPROVE WITH CONDITIONS | Sound. The one Medium (runtime-patch drift tripwire) and one Low (manual overlay maintenance burden) findings are both independently re-confirmed still open in this adjudication (§8, §15) — correctly not claimed closed by this review itself, since remediating them was never in scope for the Day 6 branch. |
| `day-06-integration-parity-review.md` (compose-platform-engineer) | APPROVE, no PRE-TAG conditions from this lens | Sound. This is the only one of the five reviews that ran `make compose-test`/`make reliability-check` locally itself and reported real, matching numbers (58/58, 32/32) — independently corroborating the same real-Docker evidence this adjudication also gathered. Its two observations (reliability_check.py's growing `main()`, single-package overlay pattern) are correctly Info/Low, not blocking. |
| `day-06-test-adversarial-review.md` (docker-test-engineer) | APPROVE WITH CONDITIONS | Sound. Its independently re-run test count (591 at review time, now 622 after the remediation commit added tests) and its line-by-line adversarial read of `_is_transient_cgroup_update_race()` are the most rigorous evidence in this review set that the cgroup-retry fix does not mask a real regression. Its three PRE-TAG conditions (L-3, L-4, L-5) are all independently re-confirmed still open in this adjudication (§9, §15) and are correctly Low-severity. |
| `day-06-release-engineering-review.md` (release-engineer) | APPROVE FOR MERGE WITH CONDITIONS | Written against HEAD `fca5ca3`, **before** the remediation commit. Its Medium finding (§6.1, dry-run main-only intent undertested) is the finding this adjudication independently confirms **CLOSED** by commit `00e7c76` (§8, §13) — this review's own text is correct as of its own HEAD and is not modified; the closure is recorded here, one adjudication layer up. Its Low findings (§6.1 fixed, §6.2/6.3/6.4 remain) are re-adjudicated in §15. |

No review understated or overstated a finding's severity in a way this
adjudication needed to correct. All five reviews' evidence (real `gh run
view` log pulls, real local `make` runs, real `docker pull`/`imagetools
inspect` calls) was independently spot-checked in this session and found
accurate.

## 6. Critical findings

**None.** Confirmed across all five independent reviews and this
adjudication's own independent evidence gathering.

## 7. High findings

**None.** Confirmed across all five independent reviews and this
adjudication's own independent evidence gathering.

## 8. Medium findings

### 8.1 Runtime-patch drift tripwire — **CARRIED, NONBLOCKING v0.6.0 technical debt**

Source: `day-06-workflow-security-review.md` §5. No automated check in
`runtime_patch_lock.py`, `check_dockerfile.py`, or `image_audit.py`
detects (a) a future Distroless base-digest bump making the
`libssl3t64` overlay redundant, or (b) a future base bump shipping a
*newer* libssl than the overlay pins, which the overlay would then
silently downgrade.

**Adjudication: this finding is NOT closed. It is carried forward as
nonblocking v0.6.0 Medium technical debt.** The overlay's *current*
correctness is independently proven exhaustively — three-layer `[A]`
(lock-file/Dockerfile cross-check, `check_dockerfile.py`), `[B]`
(built-image dpkg status.d version, `image_audit.py`), and `[D]`
(live content-hash of `libssl.so.3`/`libcrypto.so.3` plus a real
`ssl.create_default_context()` call reporting `OpenSSL 3.5.7 9 Jun 2026`,
independently reproduced locally in this session, §4) — but nothing
proves the overlay will *remain* correct after a future base-digest
re-verification. This is a real, disclosed maintenance gap, not a
present-tense correctness defect, and the task's own framing is correct
to require it stay recorded as open rather than be marked closed. See
§10 for the full overlay adjudication and §16 for its place in the
technical-debt inventory.

### 8.2 Main-bound `workflow_dispatch` dry-run enforcement — **CLOSED**

Source: `day-06-release-engineering-review.md` §2.2/§6 finding #1. At
review time (HEAD `fca5ca3`), `release.yml`'s `workflow_dispatch` trigger
had no ref restriction — nothing prevented a dispatch against a feature
branch, `develop`, or a tag ref from running the same "safe, non-publishing
dry run" path and reporting a misleadingly authoritative-looking `DRY RUN`
against stale content.

**Adjudication: independently verified CLOSED as of commit `00e7c76`.**
Verified directly, not taken on the commit message's word:

- `scripts/release/check_release_context.py` (`git show 00e7c76`, read in
  full): adds `validate_dispatch_ref()` — exact string equality against
  `DRY_RUN_REQUIRED_REF = "refs/heads/main"` (never a prefix/regex match,
  so `refs/heads/main-v0.6.0` or a nested `refs/heads/main/sub` cannot
  bypass it) — and `determine_mode(event_name)`, which maps the real
  `GITHUB_EVENT_NAME` to a validation mode itself, replacing the old
  caller-supplied `--mode` flag so a YAML `if:` can never silently steer
  the script into validating the wrong thing. `build_dry_run_context()`
  now calls `validate_dispatch_ref()` **before** any other validation.
- `tests/test_check_release_context.py`: `ValidateDispatchRefTests` (6
  tests: main accepted, feature branch rejected, `develop` rejected, a tag
  ref rejected, an empty ref rejected, a whitespace-only ref rejected,
  plus a substring-bypass-is-not-a-bypass test and a
  leading/trailing-whitespace-tolerance test), `DetermineModeTests`, and
  `MainCliTests` (`test_dispatch_on_main_passes`,
  `test_dispatch_on_feature_branch_fails`, `test_dispatch_on_develop_fails`,
  `test_dispatch_on_tag_ref_fails`, `test_dispatch_with_empty_ref_fails`,
  `test_dispatch_with_missing_ref_argument_fails`,
  `test_push_does_not_require_a_ref_argument`) — directly exercising all
  four required paths from the task framing: `workflow_dispatch` +
  `refs/heads/main` → accepted; `workflow_dispatch` + a feature/`develop`
  branch → hard failure; `workflow_dispatch` + `refs/tags/...` → hard
  failure; `push` (tag) → the independent tag-release path, unaffected.
- `.github/workflows/release.yml` (read in full, §2 excerpt above): the
  "Validate release context" step is **unconditional** (no `if:` gating
  it to one event — a non-main dispatch reaches this step and fails
  loudly rather than being silently skipped), invoked with explicit
  `--event-name "${{ github.event_name }}"` and `--ref "${{ github.ref
  }}"`, and runs **before** "Create job-scoped Buildx builder" and `make
  release-check` — fail-fast, not after several minutes of build work.
  This also closes the release-engineering review's separate Low finding
  about expensive-gates-before-cheap-check ordering (§15, item 6).
- `scripts/ci/check_workflows.py`: adds
  `check_release_context_validation_is_authoritative()` — statically
  enforces the step exists, is unconditional, passes
  `--event-name`/`--ref`, and runs before `make release-check`. This is
  now one of the 13 checks `make workflow-check` runs (confirmed locally,
  §4) — a future regression back to an undertested or `if:`-gated dry run
  would fail `make quality` immediately, at the cheap gate, exactly the
  same regression-proofing pattern already used for the Buildx-builder
  fix.
- `docs/ci-cd.md` and `docs/releases/v0.6.0.md`: both updated (read in
  full) to describe the new enforcement accurately, including an explicit
  cross-reference to "closes a Day 6 release-engineering-review Medium
  finding."

This is a genuine, independently-verified structural closure — not a
relabeling and not closed-by-documentation-only. **Both PR CI jobs are
green after this remediation** (`32985020258`, §3), including
`check_workflows.py`'s now-13 checks passing inside `make quality`.

## 9. Low findings

Adjudicated against real evidence gathered in this session; severity not
inflated or deflated relative to the originating review.

1. **Security — manual long-term maintenance burden of the overlay**
   (`day-06-workflow-security-review.md` §5). Still accurate and open —
   this is the ongoing-burden half of the same overlay mechanism covered
   in §8.1/§10. Not blocking.
2. **Integration — `reliability_check.py`'s `main()` is becoming long**
   (`day-06-integration-parity-review.md` §12). Independently confirmed:
   `main()` spans `reliability_check.py:753-1269` — **516 lines** — in a
   1,270-line file. Purely a future-structural/readability observation;
   the file remains functionally organized around clearly named
   `check_*`/scenario helper functions outside `main()`. No action
   required for v0.6.0.
3. **Test — `default_git_is_ancestor()` has no real execution evidence**
   (`day-06-test-adversarial-review.md` §7, L-3). Independently
   reconfirmed: `grep -rn "default_git_is_ancestor\|check_release_context.py"
   tests/ Makefile .github/workflows/*.yml` shows the function is called
   nowhere except inside `release.yml`'s own `validate` job — by design,
   since a real tag/dispatch event is the only way to exercise it. This
   is exactly the L-3 gap the test-adversarial review named, still open,
   and correctly deferred to the post-merge `workflow_dispatch` dry run
   (PRE-TAG condition, §17).
4. **Test — the latest successful GitHub run may not have reproduced the
   real cgroup race** (`day-06-test-adversarial-review.md` §5.1, L-4).
   Independently reconfirmed in this session against the *current*
   latest green run (`32985020258`, not just the review's cited
   `32967457379`): both real `docker update` calls in Scenario 2
   succeeded on the first attempt (`1 attempt(s)`, §3) — the retry path
   was not exercised live in this run either. **No second race occurrence
   is claimed or fabricated anywhere in this report.** The retry logic's
   correctness rests on its thorough synthetic-fixture unit coverage
   (`TransientCgroupUpdateRaceClassifierTests`, nine negative + one
   positive case using the real `32960673438` log text) plus the one real
   original failure it was written to fix — not on a second live
   reproduction.
5. **Test — `.lower()` normalization inconsistency in the transient
   classifier** (`day-06-test-adversarial-review.md` §5.2, L-5).
   Independently reconfirmed still present: `reliability_check.py:538-542`
   applies `.lower()` only to the `"no such file or directory"` fragment,
   not to `"runc did not terminate successfully"` or `"cgroup.controllers"`.
   Unfixed by the remediation commit (out of its scope — the remediation
   targeted only the main-bound-dispatch Medium finding). Fail-safe
   direction (degrades to "no retry", never a false-positive), correctly
   Low, not Medium.
6. **Release — cheap-context-validation-before-expensive-check ordering**
   (`day-06-release-engineering-review.md` §2.3/§6 item 2). **This has
   been fixed by the same remediation commit that closed the Medium
   finding** (§8.2) — confirmed by direct read of `release.yml`: "Validate
   release context" now runs immediately after "Report run mode" and
   strictly before "Create job-scoped Buildx builder"/`make
   release-check`. This was not a required fix (the release-engineering
   review itself called it non-blocking), but it is a genuine, verified
   improvement, not merely claimed.
7. **Release — no dedicated `--clobber`/force-tag regression check**
   (`day-06-release-engineering-review.md` §3, §6 item 3). Independently
   reconfirmed still absent: `grep -n "clobber\|force"
   scripts/ci/check_workflows.py` returns no policy-check hits. Today's
   `release.yml` is clean by direct read (no `--clobber`, no `git tag -d`,
   no force-push of a tag) — this remains a coverage gap for a *future*
   regression, not a live defect. Correctly Low, not blocking.
8. **Release — `check_workflows.py` uses structural/pattern checks, not a
   full YAML parser** (`day-06-release-engineering-review.md` §3, §6 item
   4). Independently reconfirmed: the module's own docstring still
   explicitly disclaims general YAML-schema coverage; 13 `check_*`
   functions now exist (up from 12), all still string/indentation-pattern
   based. This is a documented, deliberate scope choice for two short,
   hand-authored files — accurate to call it a maintenance-coupling risk
   for a future semantically-equivalent-but-syntactically-different
   rewrite of a guard, but not a present defect. Remains Low/nonblocking.

## 10. Security-overlay adjudication

The `libssl3t64=3.5.7-1~deb13u2` Debian-security overlay (CVE-2026-14456)
is adjudicated as a **genuine patched binary input, not a vulnerability
exception**, per the task's explicit framing and independently confirmed
in this session:

- **Provenance**: `security/runtime-patches.lock` (read in full, §-cited
  above) pins an official `snapshot.debian.org` URL at a fixed timestamp
  (`20260825T185058Z`), an exact SHA256 (`916f7f40...2467d`), and an
  exact byte size — not a moving mirror.
- **Build-time enforcement**: `docker/app/Dockerfile`'s `ADD --checksum=`
  is a BuildKit-frontend-enforced integrity gate — the build fails
  outright on any byte mismatch, independent of any Python-level check.
- **Three-layer proof chain, independently reproduced locally in this
  session** (§4, `image_audit.py`'s real output):
  - `[A]` `check_dockerfile.py` cross-checks the Dockerfile's literal
    `ADD --checksum=`/URL against the lock file's
    `LIBSSL_DEB_SHA256`/`LIBSSL_URL`.
  - `[B]` the built image's own `/var/lib/dpkg/status.d/libssl3t64`
    reports `Version: 3.5.7-1~deb13u2` — confirmed directly in this
    session's local `image_audit.py` run: `PASS libssl3t64 dpkg status.d
    reports the fixed Version ... reported='3.5.7-1~deb13u2'`.
  - `[D]` the live content SHA256 of `libssl.so.3`/`libcrypto.so.3` inside
    the built image matches the lock file's pinned hashes, **and** Python's
    `ssl` module inside the container loads and reports the patched
    version — confirmed directly in this session: `PASS real fixed
    libssl3t64 binary payload is present (content-hash match) ... PASS
    Python ssl module loads and reflects patched OpenSSL 3.5.7
    ... OPENSSL_VERSION='OpenSSL 3.5.7 9 Jun 2026'`.
- **Policy unweakened**: `scripts/security/check_trivy_report.py`'s
  `evaluate_policy()` is unchanged — Critical>0 fails, High-with-fix fails,
  unfixed High is reported non-blocking. No `.trivyignore`, no CVE
  allowlist exists anywhere in the repository. The real scan result on
  the current release image (`32985020258`, §3, independently pulled from
  the real run) is `CRITICAL=0`, `HIGH-with-fix=0`, `HIGH-without-fix=16`
  — the finding was closed by **patching the image**, not by suppressing
  the scanner's output.
- **SBOM/scanner visibility**: `check_sbom.py` hard-fails if the SBOM's
  own `libssl3t64` entry doesn't report `3.5.7-1~deb13u2` — a silently
  failed overlay would be caught even if the build otherwise succeeded.

**Adjudication: the overlay is correctly treated as a real, verified,
supply-chain-pinned patched binary, not a policy exception.** Its one
open gap — no automated tripwire for future base-digest drift — is
carried as Medium technical debt (§8.1), not treated as blocking or as
already closed.

## 11. Buildx remediation adjudication

Independently re-verified in this session (§3): the real root cause
(`ERROR: Docker exporter is not supported for the docker driver`, run
`32938805880`) is a genuine GitHub-hosted-runner property (no
containerd-backed default Buildx driver), not a defect in this project's
build logic. The fix — a job-scoped `docker-container` driver builder
(`maops-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}`, created with `--use`
before `make release-check`, removed with `if: always()`) — is CI
environment preparation only; the Makefile's `build:` target (`--no-cache`,
`SOURCE_DATE_EPOCH`, `rewrite-timestamp=true`, the archive-export/`docker
load` round-trip) is byte-identical before and after (`git diff
main...HEAD -- Makefile` shows the fix lives entirely in the two workflow
files). `check_workflows.py::check_buildx_container_builder_before_release_check()`
statically enforces both the ordering and the `if: always()` cleanup —
confirmed present and passing as part of the current 13/13 local
`workflow-check` run (§4). The fix is proven, not merely argued: run
`32960673438` shows the build succeeding under the new builder (and the
full `reproducibility-check` — which performs two independent clean
builds and diffs image IDs — passing) before failing much later on an
unrelated cgroup issue; runs `32967457379` and `32985020258` both show
the complete chain, including `reproducibility_check: PASS - STRONG`,
succeeding end to end. **Adjudicated as a correct, narrowly-scoped,
leak-free, regression-guarded fix.**

## 12. cgroup reliability remediation adjudication

Independently re-verified in this session (§3, §9 item 4): the real root
cause (`runc did not terminate successfully: ... cgroup.controllers: no
such file or directory`, run `32960673438`) is a genuine, transient
GitHub-hosted-runner `runc`/cgroup-v2 timing race during a `docker
update` mutation, occurring only after Scenario 1's real transient
OOM-kill-and-recover had already passed cleanly. The fix —
`_is_transient_cgroup_update_race()`, a narrow three-fragment conjunctive
string match, gating a bounded, deadline-limited retry that always
re-`docker inspect`s and compares exact expected values rather than
trusting `docker update`'s own exit code — is adjudicated **narrow enough
and correctly scoped**: nine dedicated negative tests prove the
conjunction is load-bearing (no single fragment alone triggers a retry),
and the classifier is confirmed, by direct read in this session, to
match the *exact* real log text from run `32960673438` (not an
approximation). The retry does not swallow an unrelated Docker error, does
not accept a wrong post-update end state, does not retry against a
disappeared container, and correctly chains a restore failure's exception
over the original action failure. Its one honest residual gap —
independently reconfirmed against the *current* latest run in this
session, not just the review's cited run (§9 item 4) — is that the retry
path has still only been exercised against synthetic fixtures, never a
second live race. **Adjudicated as a correct, narrowly-scoped fix with a
disclosed, non-blocking evidentiary gap**, not a weakening of the
reliability check.

## 13. Main-bound `workflow_dispatch` remediation adjudication

See §8.2 for the full verification. Summary adjudication: **CLOSED**,
independently verified against the actual current source (not the
commit message), the actual current tests (all four required paths
covered: main accepted, feature/`develop` rejected, tag ref rejected,
push/tag path unaffected), the actual current `check_workflows.py`
regression guard (now part of the 13/13 local pass), and the actual
current green PR CI run (`32985020258`, both jobs pass, matching current
HEAD). This closure is real, not documentation-only and not merely
claimed by the branch's own commit message.

## 14. Runtime architecture preservation

Independently re-verified in this session, not accepted from the reviews'
word:

- **Exactly 3 runtime services, `state -> app -> gateway`**: confirmed —
  `compose.yaml`'s `services:` block lists exactly `state`, `app`,
  `gateway`; `depends_on: condition: service_healthy` chains them in the
  documented direction (also independently confirmed live in this
  session's own `compose_integration` run: "health-gated startup ordering
  proven (state -> app -> gateway)").
- **Exactly 2 networks, `backend: internal: true`**: confirmed —
  `networks:` lists exactly `edge: {}` and `backend:` (with `internal:
  true` present in the full file), also independently confirmed live
  ("real docker network inspect confirms backend.Internal=true,
  edge.Internal=false").
- **Exactly 1 named persistence volume**: confirmed — `volumes:` lists
  exactly `state_data: {}`. The `platform` entry that also appears near
  the volumes section in a naive scan is a Compose `configs:` mount
  (`configs: platform: file: ./config/platform.json`), not a second
  volume — verified by locating the actual `configs:` top-level key.
- **Exactly 1 application image for all three roles**: confirmed by this
  session's own local `smoke` run: "multi-role all three containers run
  the exact image maops-docker-platform:0.6.0 as uid 10001."
- **Only `gateway` published to loopback**: confirmed — `compose.yaml`'s
  only `ports:` entry is `gateway`'s `"127.0.0.1:${GATEWAY_HOST_PORT:-8080}:8080"`;
  independently confirmed live ("app and state have no published host
  port ... gateway is the sole host-published service").
- **Resource limits, `on-failure:3`, `10s` stop grace**: confirmed present
  on all three services (`cpus: 0.50`, `mem_limit: 128m`, `pids_limit:
  64`, `restart: on-failure:3`, `stop_grace_period: 10s` each appear
  exactly three times in `compose.yaml`), and independently confirmed
  enforced against real containers in the latest CI run's `reliability_check:
  PASS (32/32 reliability checks passed)`.
- **Timeout hierarchy preserved**: `config/platform.json` still declares
  `state_dependency_timeout_seconds=2.0 < gateway_upstream_timeout_seconds=5.0`
  with `timeout_safety_margin_seconds=1.0` (`5.0 > 2.0 + 1.0` holds),
  matching the integration-parity review's independently reproduced
  `docker pause state` proof.
- **Distroless final runtime remains shell-less/nonroot**: confirmed by
  this session's own local `security_check`/`image_audit` output: `PASS
  final runtime has no shell`, `PASS no apt/dpkg package-manager
  executable`, `PASS effective process UID:GID is 10001:10001`.
- **libssl overlay remains build-time-controlled and supply-chain
  pinned**: see §10.
- **GitHub CI uses job-scoped `docker-container` Buildx**: see §11.
- **No application image registry publication**: confirmed — `grep -rn
  "docker login\|docker push\|ghcr.io\|docker.io/"` across both workflow
  files returns nothing beyond what `check_no_registry_publication`
  already statically enforces (part of the 13/13 local pass, §4); the
  `publish` job's only external calls are `gh release view`/`gh release
  create` against GitHub Releases, never a container registry.
- **Exactly 5 Claude agents, exactly 4 Claude skills**: confirmed —
  `.claude/agents/` contains exactly `compose-platform-engineer.md`,
  `container-security-reviewer.md`, `docker-architect.md`,
  `docker-test-engineer.md`, `release-engineer.md`; `.claude/skills/`
  contains exactly `compose-validation`, `container-security-validation`,
  `docker-build-validation`, `release-readiness`.
- **No Day 7+ scope leakage**: `grep -riE
  "kubernetes|helm|argo|prometheus|grafana|terraform|ansible|cosign|slsa|ghcr\.io"`
  across `.github/workflows/*.yml`, `Makefile`, `compose.yaml`,
  `docker/app/Dockerfile` returns no live matches (only what
  `check_no_day7_plus_tooling`'s own dedicated, comment-stripped check
  already polices, per the ci-architecture review's independent
  confirmation).

**No runtime-architecture regression found anywhere in Day 6.** The
delivery-plane (CI/CD) work is additive; the runtime plane established
across Days 1-5 is genuinely unchanged apart from the one declared,
build-time-only `security-patch` overlay stage.

## 15. Release-engineering assessment

The release-engineering machinery (`ci.yml`, `release.yml`,
`check_release_context.py`, `check_workflows.py`) is sound: least
privilege is correctly scoped and split by job (`contents: read`
workflow-wide in both files, `contents: write` confined to exactly the
`publish` job, unreachable from `workflow_dispatch` both by the job-level
`if:` — §2.1 of the release-engineering review, re-confirmed by direct
read in this session — and now additionally by the main-bound ref check,
§8.2); every `uses:` reference is pinned to a full 40-character commit
SHA, independently re-verified against the live GitHub API by the
workflow-security review; tag/VERSION/main-history validation is real,
pure, and unit-tested (39+ test cases across
`tests/test_check_release_context.py`, now including the dispatch-ref
tests added by the remediation); no `--clobber`, force-push, registry
credential, or Day 7+ tooling exists anywhere in either workflow file.
The two genuine historical CI failures on this branch are real,
root-caused, fixed, and preserved as evidence (§11, §12) — not papered
over. The one Medium finding this machinery carried at pre-remediation
review time is now closed (§8.2, §13); the remaining Low findings (§9,
items 6-8) are accurately re-adjudicated and none block merge or tag.

## 16. Current technical debt

Carried forward into post-v0.6.0 work, none blocking this release:

1. **Medium** — no automated drift/redundancy/downgrade tripwire for
   `security/runtime-patches.lock` against a future Distroless base-digest
   bump (§8.1, §10).
2. **Low** — the Debian-security overlay pattern creates an open-ended
   manual re-justification burden with no tracked review cadence (§9
   item 1).
3. **Low** — `reliability_check.py`'s `main()` has grown to ~516 lines;
   a future scenario addition would benefit from extraction into named
   functions (§9 item 2).
4. **Low** — `default_git_is_ancestor()` has zero execution evidence
   until the first real `workflow_dispatch`/tag event (§9 item 3) — see
   PRE-TAG condition #3 (§17).
5. **Low** — the cgroup-race retry path's live-recovery correctness has
   only been proven once, by the original failure, never by a second live
   race (§9 item 4, §12).
6. **Low** — `_is_transient_cgroup_update_race()`'s `.lower()`
   normalization is inconsistent across its three fragments (§9 item 5).
7. **Low** — no dedicated `check_workflows.py` guard against a future
   `--clobber`/force-tag regression (§9 item 7).
8. **Low** — `check_workflows.py` remains a text/pattern-based checker,
   not a full YAML/expression parser, by deliberate, documented scope
   choice (§9 item 8).
9. **Info** — both base-image pins (`python:3.13-slim`,
   `gcr.io/distroless/python3-debian13:nonroot`) are unchanged since Day 4
   and have since been superseded by newer builds under the same floating
   tags (ci-architecture review F-1) — a scheduled, explicit re-resolution
   decision is recommended near-term, not urgently.

## 17. PRE-TAG mandatory conditions

`v0.6.0` must **not** be tagged until all eight of the following pass, in
order, for real:

1. **Merge PR #6 to `main`.**
2. **`main` CI must pass** — a fresh run of `ci.yml`'s `quality` and
   `release-policy` jobs against the real post-merge `main` (not merely a
   passing feature-branch run — `main` may contain other changes merged
   around this PR).
3. **Run `release.yml` via `workflow_dispatch` on `main`.** This is the
   first real execution of `default_git_is_ancestor()` (currently
   zero-execution-evidence, §9 item 3/§16 item 4) and the first real
   execution of `validate_dispatch_ref()` against a genuine
   `refs/heads/main` GitHub context rather than a unit-test double.
4. **The dry run must pass** — both `check_release_context.py
   --event-name workflow_dispatch --ref refs/heads/main ...` and the full
   `make release-check` chain green, reporting `DRY RUN` per
   `release.yml`'s "Report run mode" step.
5. **Verify the dry run creates no side effects**: `git tag -l` shows no
   new `v0.6.0` tag, `gh release list` shows no new GitHub Release, and no
   artifact was published anywhere — verified directly (e.g. diffing
   `git tag -l`/`gh release list` before and after the dispatch run), not
   assumed from the source-level `if: success() && github.event_name ==
   'push' && ...` proof alone (which is real and already independently
   confirmed in this report, §8.2/§13, but a source-level proof and an
   executed proof are not the same evidentiary tier per this project's
   own `[A]` vs. `[C]`/`[D]` discipline).
6. **Only after conditions 1-5 pass may the real `v0.6.0` tag be created
   and pushed.**
7. **The tag-triggered `release.yml` `push` path must pass** — `validate`
   (full `make release-check` + real `check_release_context.py
   --event-name push --tag v0.6.0 --commit <sha> --main-ref origin/main`,
   including the first real `git merge-base --is-ancestor` invocation)
   green, then `publish` reachable and green.
8. **Verify the real GitHub Release's attached assets** match exactly
   what `release.yml`'s `publish` job currently defines: the SPDX SBOM
   (`release-evidence/sbom/*.spdx.json`), the Trivy JSON report
   (`release-evidence/security/*.json`), and `SHA256SUMS` computed over
   those files — no `docker save` archive, no image digest, and no
   registry reference of any kind.

A missing pre-tag `workflow_dispatch` dry run is **not** a merge blocker
(it is structurally impossible before `release.yml` exists on `main`) —
it **is** a tag/release blocker, per the task's own explicit framing and
independently re-derived by every review in this set that reached the
question.

## 18. Is PR #6 safe to merge?

**YES.** Both real PR CI checks are green at current HEAD (`00e7c76`, run
`32985020258`, independently confirmed in this session). PR #6 is
`MERGEABLE`/`CLEAN` against `main`. Zero Critical, zero High findings
across five independent reviews and this adjudication's own evidence
gathering. The one merge-relevant Medium finding (main-bound dispatch
enforcement) is independently verified closed by real source, real
tests, and a real green CI run. The one remaining Medium finding
(runtime-patch drift tripwire) is correctly nonblocking future-maintenance
debt, not a defect in the currently shipped artifact. All Low findings
are accurately carried forward, none newly discovered, none understated.

## 19. Is `v0.6.0` safe to tag now, before post-merge checks?

**NO.** No `v0.6.0` tag or GitHub Release exists yet (independently
confirmed, §4). The real `workflow_dispatch` dry run has never been
executed (it cannot be, pre-merge — `release.yml` must exist on `main`
first). `default_git_is_ancestor()`, the one real-`git` code path this
branch adds, has zero execution evidence. Tagging now would skip every
PRE-TAG condition in §17 and would be the first time several of this
branch's real code paths ever run outside a unit-test double.

**`v0.6.0` is safe to tag only after every condition in §17 passes for
real, and only if no new blocker appears during that post-merge
sequence.**

---

DAY 6 FINAL ADJUDICATION COMPLETE — MERGE-READY FOR POST-MERGE VALIDATION
