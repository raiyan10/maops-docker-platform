# Day 6 Release-Engineering Review — CI/CD Workflows (PRE-MERGE)

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering`
PR: #6 (HEAD `fca5ca3`)
Target: `v0.6.0`
Role: release-engineer, review 5 of 5. Scope is the release **engineering**
itself — `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
`scripts/release/check_release_context.py`, `scripts/ci/check_workflows.py`,
and the Makefile/VERSION contract those workflows orchestrate. This is
**not** a full release-readiness adjudication (that is a later,
post-merge/post-dry-run exercise, cf. `docs/engineering-reviews/
day-05-release-readiness.md`'s scope one day prior) — it is a pre-merge
audit of whether the delivery-plane *machinery* is safe, correct, and
honestly scoped.
Review only. No implementation file, workflow, test, or other doc was
modified. Nothing was committed, pushed, merged, tagged, or dispatched.
Date: 2026-08-26.

---

## 0. Evidence base

- Full reads of `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
  `scripts/release/check_release_context.py`,
  `tests/test_check_release_context.py`, `scripts/ci/check_workflows.py`,
  `Makefile`, `VERSION`, `docs/ci-cd.md`, `docs/releases/v0.6.0.md`,
  `docs/roadmap.md`, `docs/engineering-reviews/day-06-bootstrap-readiness.md`.
- `gh pr checks 6` — confirms both PR checks ("Quality", "Release policy")
  currently pass against HEAD `fca5ca3` / run `32967457379`.
- `gh run list --branch feature/day-6-cicd-release-engineering --workflow CI
  --limit 10` — confirms exactly three real CI runs exist, two failed, one
  passed, matching the coordinator's account exactly:
  - `32938805880` (2026-08-26T06:35:21Z) — **failed** at `make build` inside
    `make release-check`: `ERROR: failed to build: Docker exporter is not
    supported for the docker driver.` Independently confirmed via
    `gh run view 32938805880 --log-failed`.
  - `32960673438` (2026-08-26T10:55:12Z) — **failed** at
    `reliability-check`, specifically at the second `docker update --memory
    6m` mutation: `runc did not terminate successfully: ... cgroup.controllers:
    no such file or directory`, after Scenario 1 (transient OOM-kill
    crash/auto-restart) had already passed cleanly. Independently confirmed
    via `gh run view 32960673438 --log-failed`.
  - `32967457379` (2026-08-26T12:13:57Z) — **passed**, both jobs green.
    Current HEAD.
- `scripts/reliability/reliability_check.py`'s current
  `update_container_resources_verified`/retry logic read directly (lines
  ~481-660): the retry is narrowly scoped to a specific transient-error
  string match, bounded by a deadline, and re-verifies success via a fresh
  `docker inspect` rather than trusting `docker update`'s own exit code —
  it does not blindly retry, and any non-matching error still raises
  immediately with no retry. This matches the coordinator's stated
  remediation and does not weaken the check.
- Locally: `python3 scripts/ci/check_workflows.py` → `OK (12 policy checks
  passed)`; `python3 scripts/lint/check_source.py` → `OK`; `git tag -l` →
  `v0.1.0 .. v0.5.0` only, no `v0.6.0` tag exists; `VERSION` = `0.6.0`
  (single trailing newline, no stray whitespace); `git log main..HEAD` →
  three commits (`41b91eb`, `3fbd5ff`, `fca5ca3`), consistent with the
  stated history.
- `security/scanners.lock` and `security/runtime-patches.lock` read in
  full — both are exact-digest/exact-checksum pinned, consistent with the
  stated CVE-2026-14456 remediation (a build-stage Debian-security overlay
  of `libssl3t64=3.5.7-1~deb13u2`, not a base-image swap, not a policy
  loosening).

---

## 1. CI (`ci.yml`) findings

**[Info] Real local/CI parity, not a duplicated gate list.**
`ci.yml`'s `quality` job runs `make quality`; `release-policy` (needs
`quality`) runs `make release-check` verbatim (`ci.yml:120`), then `docker
compose config` at the very end (inside `release-check` itself, per
`Makefile:131-133`). This is the same target a developer runs locally —
CI does not hand-roll an equivalent-but-different check list. Confirmed by
reading the Makefile's `release-check: quality build inspect image-audit
smoke security-check compose-test reliability-check reproducibility-check
supply-chain-check` dependency line and cross-checking that no step here
is skipped or replaced in the workflow.

**[Info] Triggers correctly scoped.** `ci.yml:17-22` — `pull_request:
branches: [main]`, `push: branches: [main]`, plus a `workflow_dispatch`
convenience trigger (harmless: it inherits the same `contents: read`
permission and the same two read-only jobs — no publish path exists in
this file at all). Both PR and post-merge-to-main behavior run the
identical job graph; the only practical difference is that a `push` run
has no `github.event.pull_request` context, which the workflow does not
depend on.

**[Info] Concurrency control present and correctly scoped.**
`ci.yml:24-26` — group keys on PR number or ref, `cancel-in-progress:
true`, so rapid pushes to the same PR/branch cancel the stale run rather
than racing it. Appropriate for a non-publishing validation workflow.

**[Info] Least privilege.** `ci.yml:31-33` — `permissions: contents: read`
workflow-wide, no per-job override anywhere in the file (verified both by
direct read and by `check_workflows.py`'s `check_ci_permissions_read_only`,
which independently re-parses the file rather than trusting a comment).

**[Info] Artifacts correctly scoped.** `ci.yml:132-141` uploads
`artifacts/sbom/` and `artifacts/security/` (the SBOM + Trivy JSON `make
release-check` already produced) under a per-run-SHA name
(`ci-release-evidence-${{ github.sha }}`), `retention-days: 7`,
`if-no-files-found: warn`. This is transient CI evidence, not a permanent
publication surface — GitHub Actions artifacts are private to the
repository/run and expire, which is the correct scope for a non-release
CI run. `if-no-files-found: warn` (not `error`) means a run where
`release-check` failed *before* SBOM generation would still "succeed" at
the upload step and only warn — but this does not mask the real failure,
because the preceding `make release-check` step itself is what determines
job success/failure (confirmed: no `continue-on-error`/`|| true` anywhere
in this job, cross-checked by `check_workflows.py`'s
`check_no_continue_on_error`/`check_no_manufactured_pass`).

**[Medium] `docker-container` Buildx builder cleanup runs even when the
builder was never actually usable, but this is a documented, deliberate
CI-environment adaptation, not a scope violation.** `ci.yml:102-107`
creates a job-scoped builder named `maops-ci-${GITHUB_RUN_ID}-
${GITHUB_RUN_ATTEMPT}` before `make release-check`, and removes it with
`if: always()` (`ci.yml:123-130`). This is exactly what the first failed
run (`32938805880`) proves was *missing* originally, and exactly what run
`32960673438`/`32967457379` prove now works. Not a defect — flagged here
only because it is the single largest structural change CI made in
response to real evidence, and is worth naming explicitly as **proven**
(not merely claimed) by the run history. No action needed.

---

## 2. Release workflow (`release.yml`) findings — primary artifact

Read `release.yml` end to end multiple times; findings below are ordered
by severity.

### 2.1 `workflow_dispatch` is genuinely non-publishing — **proven from source, not yet from a real run**

`release.yml:137-147` — the `publish` job's `if:` is:

```yaml
if: >-
  success() &&
  github.event_name == 'push' &&
  startsWith(github.ref, 'refs/tags/')
```

`workflow_dispatch` never sets `github.event_name` to `'push'`, so this
condition is structurally, not conventionally, false on every dry run.
Adding *any* `if:` to a job replaces GitHub Actions' default "all `needs`
succeeded" check, so `success()` is correctly repeated explicitly — if it
were omitted, a `workflow_dispatch` run combined with some other bypass
would only need the ref/event-name checks, but as written it needs all
three. `scripts/ci/check_workflows.py`'s
`check_manual_dispatch_cannot_publish` (lines 324-356) independently
re-derives this from the actual `publish` job block text (not a hand-typed
copy) and additionally asserts the `publish` job block contains **no**
reference to `workflow_dispatch` at all — the simplest correct policy for
two hand-authored files, and the one currently shipped. **This is sound
by construction and matches PR-time evidence** (`check_workflows.py`
passes locally and in the green CI run). It has never been exercised by
an actual `workflow_dispatch` invocation, because — per the task's own
framing — that invocation is impossible before `release.yml` exists on
`main`. No pre-merge action changes this; see §5.

### 2.2 [Medium] The dry run's "main-only" intent is documented but not structurally enforced

`release.yml`'s own header comment (`release.yml:7`) and `docs/ci-cd.md`
(`docs/ci-cd.md:338`, `354`) both describe the `workflow_dispatch` trigger
as "on `main`" — but nothing in `release.yml` actually restricts which ref
a manual dispatch can target. `workflow_dispatch` always lets the invoker
pick an arbitrary branch/tag in the GitHub UI/API `ref` parameter; GitHub
only requires the *workflow file itself* to exist on the ref chosen at
invocation time for that invocation to be listed at all, and after this
PR merges, `release.yml` will exist on every branch that has it merged in,
not just `main`. There is no `if: github.ref == 'refs/heads/main'` guard
anywhere in `validate`'s steps, and `check_release_context.py --mode
dry-run` (§2.4 below) does not call `validate_main_history` at all — it
only derives the proposed tag from `VERSION` and checks release-notes
existence, neither of which depends on which ref is checked out.
Consequently, a dry run triggered against a stale feature branch (with an
older `VERSION`/`docs/releases/*.md` pair) would validate cleanly against
*that branch's* content and print `DRY RUN` — a technically true but
potentially misleading signal if someone mistook it for a `main` proof.
This does **not** create a publishing risk (§2.1's guard is independent
and unconditional on ref), and does not itself block merge, but the
documentation's phrasing ("on `main`") slightly overstates what the
workflow enforces versus what it merely expects the operator to do
correctly. Recommend either adding an explicit ref guard to the dry-run
path or softening the doc language to "intended to be run from `main`;
not currently ref-enforced."

### 2.3 [Low] Expensive gates run before the cheap tag-format/version-match check on the real-tag path

On the `push` (real tag) path, `validate`'s step order is: create Buildx
builder → `make release-check` (the full multi-minute build/test/
security/reliability/SBOM/vuln-scan chain) → remove builder → `Validate
release context (real tag)` (`release.yml:117-124`, which is where
`tag_matches_version`/tag-format/main-history are actually checked). A
tag that fails simple format validation (e.g. a stray `v0.6.0-rc1` — the
`push.tags: v*.*.*` glob would trigger the workflow for this since glob
`*` matches `-rc1` too, even though `check_release_context.py`'s
`TAG_PATTERN` correctly rejects it) still burns the full expensive gate
chain before failing. This wastes CI time but is not a correctness or
security gap: because no step here uses `continue-on-error`/`|| true`
(confirmed above) and `publish` requires the whole `validate` job to
succeed, a late-discovered tag-format/version-mismatch/non-ancestor
failure still correctly blocks `publish` regardless of step order.
Recommend reordering `check_release_context.py`'s cheap checks before
`make release-check` for fail-fast behavior; not release-blocking.

### 2.4 Tag syntax, VERSION-match, and main-history enforcement — **sound in source; proven only in dry-run/unit-test form pre-merge**

`scripts/release/check_release_context.py` is well-factored: all decision
logic (`validate_version_format`, `validate_tag_format`,
`tag_matches_version`, `validate_release_notes_exist`,
`validate_main_history`) is pure and injectable, with the one real-`git`
call (`default_git_is_ancestor`, lines 107-121) isolated behind a
`GitAncestorChecker` callable and never used with `shell=True` or string
interpolation — a hostile `GITHUB_REF_NAME`/`GITHUB_SHA` value cannot
become shell syntax. `tests/test_check_release_context.py` (39 cases)
exercises every pure function directly, including the exact
"`VERSION=0.6.0`, tag=`v0.5.0` → reject" example the spec calls out
(`test_version_mismatch_is_rejected`), a non-ancestor-commit rejection
(`test_commit_not_in_main_history_is_rejected`), and a real cross-check
against this repository's own actual `VERSION`/`docs/releases/v0.6.0.md`
(`test_real_repository_dry_run_context_succeeds`,
`test_real_shipped_v0_6_0_notes_exist`). This is genuinely strong,
Docker-free, fast unit coverage of the *logic*. What it does **not** and
cannot prove pre-merge is that the real `git merge-base --is-ancestor`
call, invoked with real `GITHUB_REF_NAME`/`GITHUB_SHA` values inside an
actual GitHub Actions runner against a real `origin/main`, behaves as
expected end to end — that is explicitly deferred to a real
`workflow_dispatch`/tag-push run, and the test suite's own docstring says
so honestly (`tests/test_check_release_context.py:10-12`).

### 2.5 [Info] Full-history checkout is present exactly where needed, and *only* there

`release.yml:42-48` — `validate`'s checkout uses `fetch-depth: 0`, with a
comment correctly explaining this is for the `git merge-base
--is-ancestor` check. `publish`'s checkout (`release.yml:155-156`) omits
`fetch-depth: 0` since it does not need history — it only needs the
already-tagged commit's own tree (for `docs/releases/${TAG}.md`) and the
GitHub CLI. This is correctly scoped, not over-broad.

### 2.6 Publication gated behind successful validation — **confirmed by `needs:` and by `if:`**

`publish` declares `needs: validate` (`release.yml:139`) *and* repeats
`success()` in its `if:` (§2.1) — belt-and-suspenders, correctly reasoned
in `docs/ci-cd.md:399-402` (adding any `if:` to a job replaces the
implicit `needs`-success gate, so `success()` must be explicit). If
`validate` fails at any step — including the late tag-format check noted
in §2.3 — `publish` cannot run.

### 2.7 Existing-release-fails-safely, no clobber — **confirmed**

`release.yml:164-170` — `gh release view "$TAG"` is checked first; if it
exists, the step prints an `::error::` and exits 1, which fails the whole
`publish` job before any release-mutating command runs. `grep -rn
"clobber\|--force"` across both workflow files returns nothing; `gh
release create` (`release.yml:178-186`) is called with no `--clobber` and
no tag-deletion/recreation step anywhere in either file. This matches the
"never silently overwritten" requirement exactly.

### 2.8 Release assets and SHA256SUMS — **confirmed as exactly SBOM + vuln report + checksums; no image tarball, no registry reference**

`release.yml:158-186`: `publish` downloads the `validate` job's
`release-evidence` artifact, computes `SHA256SUMS` over its `*.json`
files (`release-evidence/sbom/*.spdx.json` matches this glob; confirmed
against `scripts/security/generate_sbom.py:114`'s real output filename
pattern `maops-docker-platform-<version>.spdx.json`, and
`scripts/security/vuln_scan.py:116`'s `trivy-<version>.json`), then
attaches `release-evidence/sbom/*.spdx.json`,
`release-evidence/security/*.json`, and `SHA256SUMS` to the GitHub
Release via `gh release create`. No `docker save` archive, no image
digest, no registry reference of any kind appears anywhere in either
workflow file (independently confirmed via `check_workflows.py`'s
`check_no_registry_publication`, which greps for `docker login`, `docker
push`, `ghcr.io`, `docker.io/`, and three other registry hostnames — zero
hits — plus a direct manual read of both files).

### 2.9 [Info] No registry publication anywhere — **confirmed, consistent with project scope**

Beyond §2.8's asset list, there is no `docker login`/`docker push`/`docker
buildx build --push` anywhere in either workflow. This is explicitly
correct for Day 6's scope (`docs/roadmap.md`, `.claude/skills/
release-readiness/SKILL.md`'s "What this skill does not cover") and is
independently policed by `check_workflows.py` as a hard gate, not merely
a convention — a future PR that added a registry push would fail `make
quality`/`make workflow-check` immediately.

---

## 3. `scripts/ci/check_workflows.py` findings

**[Info] Self-referential and deterministic by design.** The script reads
only the two committed workflow files from disk and depends on no
`GITHUB_*` runtime variable (documented explicitly in its own docstring,
lines 15-22), so a local `make workflow-check` run is byte-for-byte the
same check CI runs against the identical committed files — genuinely
closing the "policy documented informally" gap the task description warns
about.

**[Low] Text/indentation-based parsing, not a real YAML parser — an
accepted, documented tradeoff.** The module explicitly disclaims general
YAML-schema coverage (docstring lines 4-13) in favor of two hand-authored
files. This is a reasonable scope choice for two short files but means
the checks are only as strong as the specific string/indentation patterns
coded (e.g. `check_manual_dispatch_cannot_publish` looks for the literal
strings `event_name == 'push'` / `"push"` and a specific `startsWith(...)`
regex — a semantically-equivalent but syntactically different rewrite of
the same guard, e.g. using `github.ref_type == 'tag'` instead of
`startsWith(github.ref, 'refs/tags/')`, would need this checker updated in
lockstep or it would silently stop enforcing the real intent). Not a
defect today (the current `release.yml` matches exactly what the checker
expects), but worth naming as a maintenance coupling for future
`release.yml` edits.

**[Info] `--clobber`/tag-force-push is not independently checked by
`check_workflows.py`.** The task's own review checklist calls out
"`--clobber` must never be used" and "no delete/recreate of tags or
releases anywhere." `check_workflows.py`'s `CHECKS` list (lines 481-493)
has no dedicated check for `--clobber` or `git tag -d`/`git push --force
... refs/tags`. Today's `release.yml` genuinely contains neither pattern
(confirmed by direct read and by grep, §2.7), so there is no live defect,
but this is an automated-coverage gap: a future edit that reintroduced
`--clobber` would not be caught by `make workflow-check`/CI, only by a
human re-reading the diff. Recommend adding an explicit
`check_no_release_clobber`/`check_no_tag_force_operations` function
alongside the existing eleven.

---

## 4. VERSION / image-tag / Makefile consistency (release-process angle only)

This is primarily another reviewer's deep-dive scope, but from the
release-engineering angle: `Makefile:5-6` derives `VERSION`/`IMAGE` from
the root `VERSION` file exactly once and `export`s `VERSION`
(`Makefile:21`) so every `docker compose` invocation resolves
`compose.yaml`'s `${VERSION:-...}` to the real value rather than its
fallback literal; `check_release_context.py` reads the same root
`VERSION` file (`REPO_ROOT / "VERSION"`, line 49) rather than duplicating
the literal; `docs/releases/v0.6.0.md`'s filename and the tag format
(`v<VERSION>`) are cross-checked by `tag_matches_version` at actual
release-validation time, not merely by convention. No release-workflow
code path introduces a second, independently-drifting version literal.
`VERSION` itself was confirmed byte-exact (`0.6.0\n`, single trailing
newline, no `v` prefix, no extra whitespace) via `xxd`.

---

## 5. What is PROVEN NOW vs. what CANNOT be proven until after merge

**Proven now, from real PR-time evidence (not simulated, not assumed):**

- `ci.yml`'s `quality` and `release-policy` jobs both pass on a real
  GitHub-hosted runner at HEAD `fca5ca3` (run `32967457379`), including
  the full `make release-check` chain (build, image-audit, smoke,
  security-check, compose-test, reliability-check, reproducibility-check,
  SBOM, SBOM validation, vulnerability-policy enforcement).
- The two prior real failures (`32938805880`, `32960673438`) are genuine,
  documented, root-caused, and their fixes are visible in the diff
  (job-scoped `docker-container` Buildx builder; bounded, re-verified
  `docker update` retry logic) — this is exactly the kind of evidence this
  project's culture requires (real failure → real fix → re-run), not a
  fabricated green run.
- `check_workflows.py`'s 12 static policy checks pass locally against the
  exact committed workflow files, and are self-consistent with a direct
  manual read of both YAML files (permissions shape, SHA-pinning, no
  `pull_request_target`, no `continue-on-error`/`|| true`, no registry
  reference, publish `if:` shape).
- `check_release_context.py`'s pure decision logic (version/tag format,
  tag-VERSION equality, release-notes presence, main-history gate
  *function*) is proven correct by 39 passing Docker-free unit tests,
  including tests that cross-check against this repository's own real
  `VERSION` and `docs/releases/v0.6.0.md`.
- No tag `v0.6.0` exists yet (`git tag -l`); no registry credential,
  GHCR/Docker Hub reference, or Day-7+ tooling (Cosign/SLSA/Kubernetes)
  appears anywhere in either workflow (confirmed by direct read and by
  `check_workflows.py`'s dedicated checks).
- The `publish` job is structurally unreachable from `workflow_dispatch`
  by source-code construction (§2.1) — this is a source-level proof, not
  an executed proof.

**Cannot be proven until after merge, and must not be simulated or
short-circuited pre-merge:**

- That a real `workflow_dispatch` invocation of `release.yml` on `main`
  actually runs to completion and reports `DRY RUN` correctly — this
  requires `release.yml` to exist on `main` first, which requires PR #6
  to merge first. No pre-merge action can produce this evidence honestly.
- That the real `default_git_is_ancestor` git subprocess call behaves
  correctly end to end inside an actual Actions runner with real
  `GITHUB_REF_NAME`/`GITHUB_SHA` values and a real `origin/main` remote
  (unit tests only exercise this function with an injected fake).
- That `make release-check` — run fresh on `main` post-merge, not merely
  on this feature branch — still passes; a passing PR branch is not proof
  that post-merge `main` (which will contain whatever else merges around
  it) is releasable, per this project's own `release-readiness` skill,
  step 15.
- That a real tag push (`v0.6.0`) correctly triggers `publish`, correctly
  refuses a pre-existing release (untestable without one existing — none
  does today), and correctly attaches exactly the three expected assets.

**My assessment of dry-run confidence:** based on the source-level review
above, I have no specific doubt that the dry run will pass once it can be
run on `main` — the `validate` job's dry-run path is identical machinery
to what already passed twice on this branch (`32960673438`'s Scenario-1
pass and `32967457379`'s full green run), plus a `check_release_context.py
--mode dry-run` invocation that is independently unit-tested and already
passes locally against this repository's real `VERSION`/release-notes
pair. The one open item that could plausibly cause a *first* dry-run
surprise is environmental (GitHub-hosted-runner cgroup/runc timing, as
seen in run `32960673438`), not a defect in the dry-run logic itself —
and that specific class of flake is exactly what commit `fca5ca3`'s retry
hardening targets.

---

## 6. Findings summary (severity-classified, file:line)

| # | Severity | Finding | Location |
|---|----------|---------|----------|
| 1 | Medium | Dry run's "main-only" intent is documented, not structurally enforced — `workflow_dispatch` can be invoked against any ref once `release.yml` exists there, and `check_release_context.py --mode dry-run` performs no ref/branch check | `.github/workflows/release.yml:7,354`; `scripts/release/check_release_context.py:141-152` (no `validate_main_history` call in dry-run path); `docs/ci-cd.md:338,354` |
| 2 | Low | Real-tag path runs the full expensive `make release-check` before the cheap tag-format/VERSION-match/main-history check, wasting CI time on a malformed tag (no correctness/security impact — `needs:`/`success()` still block `publish`) | `.github/workflows/release.yml:101-124` |
| 3 | Low | `check_workflows.py` has no dedicated automated check for `--clobber`/tag force-push/tag-recreate patterns; today's files are clean by manual inspection, but a future regression here would not be caught by `make workflow-check` | `scripts/ci/check_workflows.py:481-493` |
| 4 | Low | `check_workflows.py`'s manual-dispatch-cannot-publish and permissions checks are string/indentation-pattern based rather than a real YAML/expression parser — a semantically-equivalent but syntactically different rewrite of a guard could silently stop being enforced | `scripts/ci/check_workflows.py:324-356` |
| 5 | Info | `ci.yml`'s SBOM/vuln-report artifact-upload step uses `if-no-files-found: warn` rather than `error`; does not mask gate failure since the preceding `make release-check` step already determines job success/failure | `.github/workflows/ci.yml:132-141` |
| 6 | Info | Both failed CI runs (`32938805880`, `32960673438`) and their fixes are real, documented, and independently confirmed via `gh run view --log-failed`; no evidence of a fabricated or skipped failure | n/a (GH Actions run history) |

No Critical or High findings in the release-engineering scope reviewed
here.

---

## 7. Verdict

**APPROVE FOR MERGE WITH CONDITIONS.**

The release-engineering machinery in this PR — `ci.yml`, `release.yml`,
`check_release_context.py`, `check_workflows.py` — is sound: least
privilege is correctly scoped and split by job, every `uses:` is
SHA-pinned, the publish path is structurally unreachable from
`workflow_dispatch`, tag/VERSION/main-history validation is real and
independently unit-tested, no `--clobber`/force/registry-publish/Day-7+
tooling exists anywhere, and the two real historical CI failures on this
branch are genuine, root-caused, and fixed — not papered over. The one
Medium finding (§6.1) is a documentation/enforcement gap in the dry run's
"main-only" framing, not a publishing-safety defect, and does not block
merge.

Per the task's explicit framing, the following are **PRE-TAG conditions**,
not merge-blocking conditions — they cannot be satisfied before `release.yml`
exists on `main`, and no pre-merge simulation of them is acceptable:

1. `main` CI (`ci.yml`, both jobs) passes on a fresh run after this PR
   merges — a passing feature-branch run is not proof that post-merge
   `main` is releasable.
2. A real `workflow_dispatch` dry run of `release.yml` is executed on
   `main` and passes (`make release-check` + `check_release_context.py
   --mode dry-run` both green, `DRY RUN` correctly reported).
3. That dry run is confirmed to have created no tag, no GitHub Release,
   and no published artifact of any kind (verified directly, e.g. via
   `git tag -l`/`gh release list` before and after — not assumed from the
   source-level proof in §2.1 alone).
4. Only after 1-3 pass is the real `v0.6.0` tag created and pushed,
   triggering the real `publish` path.

