# Day 6 Bootstrap Readiness Review — pre-push safety gate for v0.6.0

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering` (uncommitted; never run on
GitHub Actions)
Role: independent bootstrap-readiness reviewer. Review only — no
implementation file was modified; nothing was committed, pushed, tagged,
merged, or released. The only file created is this one.
Date: 2026-08-26.

**This is NOT the final Day 6 release review.** It answers one narrower
question: is it safe to make the first commit, push the feature branch,
and open a Draft PR so `ci.yml`/`release.yml` can be exercised for real on
GitHub Actions?

---

## 1. Executive verdict

**SAFE TO BOOTSTRAP, with one correctness gap that must be fixed before
`release.yml`'s `workflow_dispatch` dry run is ever manually invoked and
trusted.**

The two-file publish-safety architecture is sound and independently
verified: `workflow_dispatch` is structurally incapable of reaching the
`publish` job (the `if:` condition on that job is the *only* place
publication is gated, and it requires `github.event_name == 'push'`,
which `workflow_dispatch` never sets), no PR run or ordinary push run ever
carries a secret or a write-scoped token, `pull_request_target` is never
used, every `uses:` reference is pinned to a full 40-character commit SHA
that I independently re-resolved against the GitHub API and it matches
exactly, and real-tag publication genuinely requires the tagged commit to
be an ancestor of `origin/main` via `git merge-base --is-ancestor`.

One real, non-publication-risk gap was found and independently confirmed
(§3, §7): `check_release_context.py --mode dry-run` never checks which
branch/ref the `workflow_dispatch` run was invoked from — a dry run
launched from an arbitrary feature branch (not `main`) is accepted and
reported `OK` exactly as if it had run from `main`. This cannot lead to
publication (the `publish` job's guard is independent and unaffected), but
it does mean the workflow's own documented contract ("dry run == main
only") is not actually enforced in code. This does not block pushing the
branch or opening a Draft PR — `release.yml` is not triggered by `push`
to a feature branch or by `pull_request` at all — but it must be closed
before anyone manually dispatches `release.yml` and treats a green dry
run as authoritative main-branch evidence.

Several smaller, non-blocking adversarial gaps were found in
`scripts/ci/check_workflows.py`'s pattern coverage (§9) — none exploitable
against the two files as currently written, all worth tightening later.

Local validation ran to the extent practical in this sandbox: 529/529
unit tests pass, `lint`/`dockerfile-check`/`workflow-check` all pass
clean, `docker compose config` renders successfully, action pins verified
against the live GitHub API, and `git diff --check` is clean. `make
quality`/`make release-check` currently fail in this specific sandbox
only on `compose-check`'s pre-existing WSL/Docker-Desktop path-rendering
artifact (§11) — independently confirmed to reproduce identically on
unmodified `main`, unrelated to any Day 6 change, and not expected to
reproduce on GitHub's native Linux Ubuntu runners.

---

## 2. Files reviewed

- `.github/workflows/ci.yml`, `.github/workflows/release.yml`
- `scripts/ci/check_workflows.py`, `scripts/release/check_release_context.py`
- `Makefile`
- `tests/test_check_workflows.py`, `tests/test_check_release_context.py`,
  `tests/test_check_compose.py`, `tests/test_reliability_check.py`,
  `tests/test_server.py`
- `docs/ci-cd.md`, `docs/releases/v0.6.0.md`, `docs/reliability.md`,
  `docs/security.md`, `docs/supply-chain.md`, `docs/roadmap.md`,
  `README.md`
- `docs/engineering-reviews/day-05-release-readiness.md` (Day 5 final
  adjudication, used as the closure baseline for §14)
- `.claude/CLAUDE.md`, all five `.claude/agents/*.md`, all four
  `.claude/skills/*/SKILL.md` (diffed against `main`)
- `git diff main` for every changed file listed in the session's `git
  status`, to distinguish genuine Day 6 changes from pre-existing content

---

## 3. Commands/tests run

```
python3 -m unittest discover -s tests -t .        # 529/529 OK
python3 scripts/lint/check_source.py               # OK
python3 scripts/lint/check_dockerfile.py           # OK (10/10)
python3 scripts/compose/check_compose.py           # 1 finding — WSL path artifact, see §11
python3 scripts/ci/check_workflows.py              # OK (11/11 policy checks)
make quality                                       # FAILS at compose-check (same WSL artifact)
docker compose config                              # exit 0, renders cleanly
git diff --check                                   # clean, no whitespace errors
git stash && python3 scripts/compose/check_compose.py && git stash pop
                                                    # identical WSL-path finding reproduces on
                                                    # unmodified main — confirms pre-existing,
                                                    # not a Day 6 regression
gh api repos/actions/checkout/git/refs/tags/v7.0.1
gh api repos/actions/setup-python/git/refs/tags/v7.0.0
gh api repos/actions/upload-artifact/git/refs/tags/v7.0.1
gh api repos/actions/download-artifact/git/refs/tags/v8.0.1
                                                    # all 4 SHAs independently re-resolved,
                                                    # exact match to the committed pins — §5, §9
python3 -c "... adversarial probes against check_no_registry_publication
             and check_no_manufactured_pass with synthetic bypass text ..."
                                                    # confirms real, non-exploited pattern gaps
                                                    # — see §13
```

`make release-check` was not run to completion: its first prerequisite
(`quality`) fails immediately on the pre-existing `compose-check` WSL
artifact (§11) before any Docker-based gate executes, in a chain that
Day 5's own final adjudication already exhaustively proved (359/359 unit
tests, 32/32 reliability checks, full reproducibility, full vulnerability
policy — see `day-05-release-readiness.md` §14) for the identical,
Day-6-unchanged runtime plane. Given that prior exhaustive verification,
re-running the full multi-minute Docker chain in this environment would
not add material evidence about the Day 6 delivery-plane changes under
review here (compose.yaml, Dockerfile, and the reliability/security
scripts are unchanged this day except the closures independently verified
in §14) — the Docker-free surface (`quality` minus the WSL artifact,
`workflow-check`, `check_release_context.py`'s pure logic) is what is
actually new, and that surface was fully exercised.

---

## 4. CI trigger/job graph

```
on: pull_request(branches:[main]) | push(branches:[main]) | workflow_dispatch
permissions: contents: read                       (workflow-wide, no override anywhere)
concurrency: ci-${{workflow}}-${{PR# or ref}}, cancel-in-progress: true

quality (no needs)
  └─ make quality   (test, lint, dockerfile-check, compose-check, workflow-check — no Docker)

release-policy (needs: quality)
  ├─ make release-check   (re-runs quality, then build→inspect→image-audit→smoke→
  │                         security-check→compose-test→reliability-check→
  │                         reproducibility-check→supply-chain-check→
  │                         `docker compose config`)
  └─ upload-artifact (if: always()) — sbom/security evidence, retention 7d
```

Verified from GitHub Actions semantics, not comments:

- `needs: quality` on `release-policy` means the job is skipped entirely
  (not merely marked failed) if `quality` fails — confirmed by reading
  the job graph; no `if: always()`/`if: success()` override exists on
  `release-policy` itself, so the default "all needed jobs succeeded"
  gate applies unmodified.
- `permissions: contents: read` is declared once, at workflow level, with
  no job-level `permissions:` block on either job — GitHub Actions'
  inheritance model means both jobs run with exactly that scope; there is
  no broader implicit default to worry about (a workflow with no
  `permissions:` block at all would inherit the *repository's* default,
  which can be broader — this workflow avoids that ambiguity entirely by
  declaring it explicitly).
- `upload-artifact`'s `if: always()` step cannot mask a failed
  `release-policy` job: a step's own success/failure is independent of an
  earlier failed step's effect on the job's overall conclusion — GitHub
  Actions marks the job (and the run) failed the moment `make
  release-check` exits non-zero, regardless of what a later `always()`
  step does. Independently confirmed by reading `check_no_continue_on_error`
  and `check_no_manufactured_pass`'s absence of any override on this step.
- `quality` running before `release-policy` is a genuine fail-fast
  ordering, not merely declared: a lint/test regression fails in
  `quality` within roughly a minute, and `release-policy` (which needs a
  full Docker Engine, a built image, and several minutes of gates) never
  starts.
- No `pull_request_target` anywhere in the file — confirmed by direct
  text search, not just by the absence claimed in the comment header.

**Concurrency**: `group: ci-${{ github.workflow }}-${{
github.event.pull_request.number || github.ref }}`, `cancel-in-progress:
true`. For a PR, `github.event.pull_request.number` is set, so pushing a
new commit to the same PR cancels the PR's own prior run — it cannot
cancel a *different* PR's run (different group key) or a `main` push's
run (falls back to `github.ref`, a different key). This is safe
obsolete-run cancellation, not a mechanism that could cancel an unrelated
or in-flight `release-policy` run for another ref.

---

## 5. Release trigger/job graph

```
on: push(tags:["v*.*.*"]) | workflow_dispatch
permissions: contents: read                       (workflow-wide)
concurrency: release-${{github.ref}}, cancel-in-progress: false

validate (permissions: contents: read)
  ├─ checkout (fetch-depth: 0)
  ├─ report run mode (echo only, informational)
  ├─ make release-check                             (identical in both modes)
  ├─ check_release_context.py --mode dry-run          (only if workflow_dispatch)
  ├─ check_release_context.py --mode tag ...           (only if push)
  └─ upload-artifact "release-evidence" (if: always())

publish (needs: validate; permissions: contents: write)
  if: success() && github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')
  ├─ checkout
  ├─ download-artifact "release-evidence"
  ├─ gh release view "$TAG" → exit 1 if it already exists
  ├─ compute SHA256SUMS over the downloaded *.json files
  └─ gh release create "$TAG" --notes-file docs/releases/${TAG}.md <sbom> <trivy> SHA256SUMS
```

---

## 6. Manual dry-run safety — independently traced, not merely observed

I did not stop at "the `if:` has a condition" — I traced every layer the
brief asked for:

1. **Trigger**: `workflow_dispatch: {}` sets `github.event_name =
   'workflow_dispatch'`. GitHub Actions never sets `event_name` to
   `'push'` for a manually dispatched run — this is a platform guarantee,
   not something either workflow file could get wrong even if it tried.
2. **Job condition**: `publish`'s `if:` is `success() &&
   github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')`.
   Adding *any* `if:` to a job replaces GitHub Actions' default "all
   `needs` succeeded" check — the workflow authors correctly re-added
   `success()` explicitly (confirmed: removing it would not, by itself,
   let a failed `validate` reach `publish`, but it is good, defensive
   practice given the docstring's own stated reasoning, and
   `check_manual_dispatch_cannot_publish()` enforces its presence
   statically).
3. **Job dependency**: `needs: validate` — `publish` cannot start before
   `validate` completes, and per (2) needs `validate` to have succeeded.
4. **Permissions**: `publish`'s own `permissions: contents: write` block
   is the only write-scoped token in either workflow, and it is granted
   at the job level — GitHub Actions job-level `permissions:` overrides
   the workflow-level default for that job only, so `validate` never sees
   it. A `workflow_dispatch` run flows through `validate` only if
   `publish`'s `if:` fails (which it structurally must for
   `workflow_dispatch`), and `validate` itself never has write access.
5. **Environment variables / gh commands**: `GH_TOKEN:
   ${{ secrets.GITHUB_TOKEN }}` and `TAG: ${{ github.ref_name }}` are
   declared only inside the `publish` job's `env:` block — they do not
   exist in `validate`'s environment at all, and `workflow_dispatch` never
   reaches `publish` per (2)–(4), so the token is never exposed to a
   manual-dispatch run's own log/steps in the first place.
6. **Artifact flow**: `publish` downloads `release-evidence`, the exact
   artifact `validate` uploaded in the same run — same-run
   `download-artifact` cannot pull an artifact from a different run by
   default, so there is no cross-run substitution surface here.

**Independently reproduced the negative**: I do not have a live GitHub
Actions runner in this review to literally dispatch the workflow, but I
verified the *language-level* guarantee both by reading the compiled
condition against GitHub Actions' documented job-`if:`/`needs` semantics
and by confirming `scripts/ci/check_workflows.py`'s
`check_manual_dispatch_cannot_publish()` — which statically enforces this
exact shape — passes against the real committed file (`make
workflow-check`: 11/11 OK) and rejects five deliberately broken mutants of
it in `tests/test_check_workflows.py::ManualDispatchCannotPublishTests`
(re-run directly: all pass). A manual `workflow_dispatch` run is
therefore **dry run only**: it cannot create a tag (no tag-creation
command exists in `validate`, and `validate` is the only job it reaches),
cannot move a tag, cannot create a GitHub Release (`gh release create`
exists only in `publish`), cannot upload assets to an existing release
(same reason), and cannot publish an application image (no
`docker push`/registry command exists in either workflow at all — see
§13's `check_no_registry_publication` discussion for the one caveat about
that check's own pattern coverage, which does not change the fact that no
such command is present in these two files today).

---

## 7. MAIN-ONLY dry-run determination — **gap found, independently confirmed**

The brief specifically asked whether the "authoritative" dry run is
provably bound to `main`, not merely usually launched from it. It is
**not**.

- `on.workflow_dispatch: {}` carries no `branches:` restriction — GitHub
  Actions' `workflow_dispatch` trigger has no such restriction mechanism
  at the trigger level at all (a user can select any branch/tag that has
  the workflow file present, from the Actions UI's "Use workflow from"
  dropdown). This is a platform fact, not a bug in this file — the
  responsibility to enforce "main only" has to live in the workflow's own
  steps or in the script it calls.
- The `validate` job's dry-run step is: `python3
  scripts/release/check_release_context.py --mode dry-run` — no `--ref`,
  no `--branch`, no `if: github.ref == 'refs/heads/main'` guard on the
  step or the job.
- I read `build_dry_run_context()` in full
  (`scripts/release/check_release_context.py:141-152`): it calls
  `validate_version_format`, derives `proposed_tag`, calls
  `validate_tag_format`, and calls `validate_release_notes_exist`. **None
  of these three functions, nor any other code path in `--mode dry-run`,
  ever inspects `GITHUB_REF`, `GITHUB_REF_NAME`, or any other
  branch-identifying value.** The only place `main` appears at all in this
  script is the `--main-ref` default (`"origin/main"`), which is a
  parameter of `--mode tag`'s `validate_main_history()` — a function
  `build_dry_run_context()` never calls.
- Confirmed by direct execution: running `check_release_context.py
  --mode dry-run` from this feature branch's own checkout (not `main`)
  in this sandbox printed `check_release_context: OK mode=dry-run
  version=0.6.0 tag=v0.6.0 ...` — i.e., it succeeds identically regardless
  of which branch it is run from, because nothing about the check depends
  on the branch at all. `tests/test_check_release_context.py`'s
  `BuildDryRunContextTests` confirms this by construction: no test passes
  a ref/branch parameter to `build_dry_run_context()`, because the
  function signature has no such parameter to pass.

**Consequence**: a `workflow_dispatch` run of `release.yml` launched from
any branch that happens to have a syntactically valid `VERSION`, a
derivable `vX.Y.Z` tag, and a matching `docs/releases/vX.Y.Z.md` file
present will print `OK` and the `::notice::` "DRY RUN" message exactly as
if it had been launched from `main` — with no code-level signal
distinguishing "this ran on the authoritative branch" from "this ran on
an arbitrary feature branch that happens to satisfy the same
pre-tag-format checks."

**Severity and scope**: this does **not** create a publication path — §6
independently established that `publish` is gated purely by
`event_name`/`ref` on the *triggering push event*, a fact entirely
unrelated to what `validate`'s dry-run step checks. The blast radius is
therefore reporting/trust, not integrity: a developer (or this
Draft-PR-testing exercise itself) could be misled into treating a
feature-branch dry run as "main is release-candidate-ready" evidence when
it is not. Given the review brief's own explicit instruction that this
exact scenario is a bootstrap-blocking example, I am classifying it
**High, bootstrap-relevant** (§16–17) rather than folding it into ordinary
carried-forward debt — but it does **not** block the narrower act of
pushing this branch and opening a Draft PR, since neither `push` to a
non-`main` branch nor `pull_request` triggers `release.yml` at all (only
`ci.yml` runs in a Draft PR). It must be closed before anyone manually
dispatches `release.yml` for real and treats a green run as meaningful
main-branch release-candidate evidence.

**Recommended fix** (not implemented — review only): either (a) add a
guard step/job condition in `release.yml`'s `validate` job that fails
fast when `github.event_name == 'workflow_dispatch' && github.ref !=
'refs/heads/main'`, or (b) extend `check_release_context.py --mode
dry-run` to accept and validate an explicit `--ref`/`--branch` argument
sourced from `GITHUB_REF`, matching the same "pure logic, thin adapter"
pattern the tag-mode ancestry check already uses.

---

## 8. Tag/VERSION/main-history validation

Read `check_release_context.py` in full and its two call sites in
`release.yml`. For `--mode tag`:

- `validate_version_format` — `^(\d+)\.(\d+)\.(\d+)$`, anchored, no
  prerelease suffix accepted — matches this project's own `VERSION` file
  convention exactly (confirmed: `VERSION` currently reads `0.6.0`, no
  trailing newline issues since `.strip()` is applied).
- `validate_tag_format` — `^v(\d+)\.(\d+)\.(\d+)$`, anchored, rejects
  `V0.6.0`, `v0.6.0-beta`, `v0.6` — confirmed by
  `tests/test_check_release_context.py::ValidateTagFormatTests`.
- `tag_matches_version` — exact string equality after stripping
  whitespace on `f"v{version}"` vs. the real tag — confirmed rejects
  `VERSION=0.6.0` + `tag=v0.5.0` per the Day 6 spec's own stated example.
- `validate_main_history` → `default_git_is_ancestor` — invoked as `git
  merge-base --is-ancestor <commit> <main_ref>` via `subprocess.run([...])`
  (an argv list, **never** `shell=True`, **never** an f-string/`%`-formatted
  command line) — confirmed by direct source read, lines 107-121. The
  tag ref (`GITHUB_REF_NAME`) and commit SHA (`GITHUB_SHA`) are both
  attacker-influenceable only in the sense that anyone who can push a tag
  to this repository already has write access; neither value is ever
  interpolated into a shell string, so there is no injection surface
  even in principle.
- **Race/checkout semantics on a tag-triggered run**: `validate`'s
  checkout step uses `fetch-depth: 0` specifically so `origin/main`'s
  full history is present locally for the ancestor check — confirmed this
  is necessary (a shallow clone would make `git merge-base
  --is-ancestor` either fail outright for commits outside the shallow
  window or, worse, be silently meaningless). On a `push: tags:` event,
  `github.sha` is the tag's target commit and `actions/checkout`'s default
  behavior for a tag-push event checks out that exact commit — so
  `--commit "${{ github.sha }}"` genuinely names the commit being
  released, not some other ref. The `--main-ref origin/main` default
  correctly points at the *remote-tracking* branch (not a possibly-stale
  local `main`), which `fetch-depth: 0` populates via the default
  checkout's fetch of all refs.
- **A residual, non-bootstrap-blocking observation**: `git merge-base
  --is-ancestor` proves the tagged commit is an ancestor of (or equal to)
  `origin/main` *at the moment the workflow runs* — it does not, and
  cannot, prove `main` hasn't been force-pushed/rewritten between the tag
  being created and the workflow executing. This is standard git-tag
  semantics, not a defect in this script, and is out of scope for a
  bootstrap review (it would require a branch-protection policy on
  `main`, which is a repository-settings question, not a workflow-code
  question).

---

## 9. Action-pin verification table

Every `uses:` in both files, independently re-resolved via `gh api
repos/<org>/<repo>/git/refs/tags/<tag>` against the live GitHub API in
this session (not taken from the comment or the implementation report):

| Action | Pinned SHA (committed) | Claimed release (comment) | Independently verified release |
|---|---|---|---|
| `actions/checkout` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | `v7.0.1` | **MATCH** — `gh api repos/actions/checkout/git/refs/tags/v7.0.1` resolves to this exact commit SHA |
| `actions/setup-python` | `5fda3b95a4ea91299a34e894583c3862153e4b97` | `v7.0.0` | **MATCH** — `gh api repos/actions/setup-python/git/refs/tags/v7.0.0` resolves to this exact commit SHA |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | `v7.0.1` | **MATCH** — `gh api repos/actions/upload-artifact/git/refs/tags/v7.0.1` resolves to this exact commit SHA |
| `actions/download-artifact` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | `v8.0.1` | **MATCH** — `gh api repos/actions/download-artifact/git/refs/tags/v8.0.1` resolves to this exact commit SHA |

All four pins are genuine, exact, and correspond to the release named in
their trailing comment. No fabricated or mismatched pin found. All four
are official `actions/*`-namespace GitHub-maintained actions; no
third-party action exists in either file.

`check_uses_pinned_to_full_sha()`'s regex-based enforcement
(`^[0-9a-f]{40}$`) was independently exercised against both the real
files (0 findings — every `uses:` is a full lowercase-hex 40-char SHA)
and against deliberately bad synthetic input (`@v4`, `@main`, a
36-char short SHA) via `tests/test_check_workflows.py`, all correctly
rejected.

---

## 10. Permissions analysis

| Scope | ci.yml (workflow) | ci.yml jobs | release.yml (workflow) | release.yml `validate` | release.yml `publish` |
|---|---|---|---|---|---|
| Declared | `contents: read` | none (inherits) | `contents: read` | `contents: read` (explicit) | `contents: write` |
| Effective | read-only | read-only (both jobs) | read-only | read-only | write |

- GitHub Actions inheritance: a job with no `permissions:` block inherits
  the workflow-level block; a job with its own `permissions:` block
  overrides it entirely (not additively) for that job. Confirmed both
  `ci.yml` jobs have no job-level override (inherit `contents: read`);
  `release.yml`'s `validate` job explicitly repeats `contents: read`
  (redundant but harmless — matches the workflow default exactly, and
  documents intent) while `publish` is the sole job anywhere in either
  file with a `write` scope.
- `scripts/ci/check_workflows.py`'s `check_release_permissions_scoped()`
  independently enforces via regex (`WRITE_OR_ADMIN_PERMISSION_PATTERN`)
  that **exactly one** `write`/`admin` permission line exists anywhere in
  `release.yml`'s full text — re-ran this against the real file: exactly
  1 match, inside the `publish` job block. `check_ci_permissions_read_only()`
  enforces the same "no write/admin anywhere" invariant for `ci.yml` —
  0 matches, confirmed.
- **No broader implicit/default permission remains**: both workflow files
  declare `permissions:` explicitly at the top level, which overrides
  whatever the repository/organization's own default token permissions
  setting might otherwise be — this is a deliberate, correct choice
  (relying on an org-level default would make this workflow's actual
  permission behavior depend on settings outside this repository).
- **No PR-controlled code can reach a write-scoped job**: `ci.yml` has no
  write-scoped job at all. `release.yml`'s only write-scoped job
  (`publish`) is reachable only via a `push: tags: v*.*.*` event, which a
  pull request (by definition, no tag push) can never trigger. A
  malicious PR branch's own `.github/workflows/*.yml` content is
  irrelevant here too — `pull_request` (not `pull_request_target`) always
  runs the PR branch's workflow file under the *base* repository's
  default read-only token, never a secret, confirmed in §4.

---

## 11. Artifact/release immutability analysis

- **Existing-release detection causes failure**: `gh release view "$TAG"
  ... && exit 1` — confirmed this is a hard `exit 1` with an
  `::error::`-annotated message, not a warning; the step's own failure
  fails the job before `gh release create` is ever reached.
- **No `--clobber`**: confirmed by direct text search of `release.yml` —
  the string does not appear anywhere.
- **No force-tag operation**: no `git tag -f`, no `git push --force
  <tag>`, no tag-deletion command anywhere in either workflow.
- **No delete/recreate behavior**: no `gh release delete` anywhere.
- **Release notes are version-specific**: `--notes-file
  "docs/releases/${TAG}.md"` — a real, version-specific, hand-authored
  file (`docs/releases/v0.6.0.md`, read in full for this review, §2),
  never an inline string in the workflow.
- **Publication happens only after validation**: `needs: validate` plus
  the `success()` guard (§6) — `publish` cannot start until `validate`
  has completed successfully.
- **Artifacts correspond to validation output, no TOCTOU/substitution
  found**: `publish` downloads the `release-evidence` artifact by name
  with no run-id qualifier — `actions/download-artifact` defaults to
  pulling artifacts uploaded *by the same workflow run* unless a
  cross-run `run-id`/`github-token` is explicitly supplied, which neither
  file does. This means the SBOM/Trivy JSON attached to the release are
  provably the exact files `validate`'s own `make release-check` produced
  in this run — there is no window in which a different run's artifact
  (potentially built from different source) could be substituted.
- **SHA256SUMS generated for the exact attached assets**: the `find
  release-evidence -type f -name '*.json' | xargs sha256sum >
  SHA256SUMS` step hashes precisely the SBOM/Trivy JSON files, and `gh
  release create` attaches `release-evidence/sbom/*.spdx.json`,
  `release-evidence/security/*.json`, and `SHA256SUMS` itself — the same
  set of files, no mismatch between what is hashed and what is uploaded.
- **`cancel-in-progress: false`** on `release.yml`'s concurrency group is
  a deliberate, correctly-reasoned choice (documented in both the
  workflow's own header comment and `docs/ci-cd.md`) — it prevents a
  second trigger from killing an in-flight publish mid-way, which would
  otherwise be exactly the kind of half-finished, unverifiable state this
  project's release philosophy rules out. Confirmed this cannot itself
  create a double-publish risk: a second push of the *same* tag is not
  possible under normal git tag semantics (tags are immutable refs unless
  force-pushed, which this project's own workflow never does and would
  require repository-level tag-protection settings to fully prevent —
  out of scope for a workflow-file review).

No TOCTOU or artifact-substitution issue found between `validate` and
`publish`.

---

## 12. Makefile/release-policy alignment

`make release-check`'s dependency chain (`Makefile:131`): `quality build
inspect image-audit smoke security-check compose-test reliability-check
reproducibility-check supply-chain-check`, followed by an unconditional
`docker compose config` in the recipe body. Cross-checked against the
brief's required list:

| Required element | Present in `release-check`? |
|---|---|
| quality | Yes (`quality` — itself `test lint dockerfile-check compose-check workflow-check`) |
| workflow policy | Yes (`workflow-check`, folded into `quality`, new this day) |
| build | Yes (`build`) |
| inspect | Yes (`inspect`) |
| image audit | Yes (`image-audit`) |
| smoke | Yes (`smoke`) |
| security | Yes (`security-check`) |
| Compose integration | Yes (`compose-test`) |
| reliability | Yes (`reliability-check`) |
| reproducibility | Yes (`reproducibility-check`) |
| SBOM | Yes (`supply-chain-check` → `sbom`) |
| vulnerability policy | Yes (`supply-chain-check` → `vuln-scan`) |
| supply-chain validation | Yes (`supply-chain-check` as a whole) |
| `docker compose config` | Yes — final line of the `release-check` recipe |

`make`'s own prerequisite semantics (`SHELL := bash`, `.SHELLFLAGS := -eu
-o pipefail -c`, and GNU Make's default behavior of stopping at the first
failed prerequisite/recipe line) mean any failure anywhere in this chain
halts the whole target with a non-zero exit — confirmed directly: the
sandbox's own `compose-check` failure (§11 of the local-validation
section, §16 findings) caused `make quality` (and therefore what would be
`make release-check`) to stop immediately with `make: ***
[Makefile:69: compose-check] Error 1`, propagating cleanly.

**CI quality → release-policy duplication**: `ci.yml`'s `release-policy`
job re-runs `make release-check`, whose own first prerequisite is
`quality` — meaning `quality`'s ~1-minute Docker-free suite runs twice
per CI run (once standalone, once as part of `release-policy`). This is
the intentional trade-off `docs/ci-cd.md` documents and justifies: the
alternative (CI hand-lists a *subset* of `release-check`'s prerequisites
to skip the redundant `quality` re-run) would require CI's job definition
to independently track `Makefile`'s own dependency list, which is exactly
the drift risk both `.claude/CLAUDE.md` and this project's own stated
philosophy warn against. I assess this as **harmless fast-check
duplication, not unacceptable duplicate expensive work** — the
duplicated portion is the cheap Docker-free suite, never the expensive
Docker-based gates, which only ever run once per CI run (inside
`release-policy`).

---

## 13. workflow-check adversarial assessment

Ran `scripts/ci/check_workflows.py`'s individual `check_*()` functions
directly against ten adversarial synthetic mutations — five drawn from
`tests/test_check_workflows.py`'s own existing coverage (all still
correctly rejected on re-run) and five additional probes not present in
that test file, run live in this review to independently verify claims
the module's docstring makes:

| Adversarial mutation | Caught? |
|---|---|
| `pull_request_target` in any YAML shape (substring search survives comment-stripping and structural reshaping) | **Yes** |
| Action ref not a full 40-char lowercase-hex SHA (`@v4`, `@main`, short SHA) | **Yes** |
| Write/admin permission moved to workflow scope in `release.yml` | **Yes** (`check_release_permissions_scoped`) |
| `workflow_dispatch` referenced anywhere inside the `publish` job block (even via a negation) | **Yes** (`check_manual_dispatch_cannot_publish`) |
| `continue-on-error: true` on any step | **Yes** |
| `\|\| true` used to disguise a gate's exit code | **Yes** (this exact idiom) |
| `docker push`/`docker login`/`ghcr.io` literal substrings | **Yes** |
| Tag trigger removed entirely, or `workflow_dispatch` trigger removed | **Yes** (`check_required_triggers`) |
| **`\|\| exit 0`, `; true`, or `\|\| :` used instead of the literal `\|\| true`** | **No — confirmed bypass, independently reproduced** |
| **`docker image push`, `docker buildx build --push`, or a registry host outside the fixed 6-item list (e.g. `quay.io`, `gcr.io`) reached via `crane`/`skopeo` rather than the literal string `docker push`** | **No — confirmed bypass, independently reproduced** |

I independently reproduced both bypasses live (not merely inferred from
reading the regex): feeding
`"steps:\n  - run: make release-check || exit 0\n"` through
`check_no_manufactured_pass()` returns zero findings, and feeding
`"steps:\n  - run: docker image push x:latest\n"` /
`"docker tag x quay.io/org/x"` through `check_no_registry_publication()`
also returns zero findings.

**Assessment**: neither gap is exploited by the two real committed
files today (confirmed: `release.yml`/`ci.yml` contain no `|| exit 0`,
`; true`, `|| :`, `docker image push`, `--push`, or any registry hostname
beyond what the six fixed patterns cover) — so this is **not**
bootstrap-blocking. But per the brief's own framing ("judge whether the
repository-owned policy is adequate for the specific two controlled
workflow files," not "require a general-purpose parser"), I assess this
as a **genuine, worth-closing gap**: the manufactured-pass check's
pattern list is a single literal idiom rather than the broader class of
"disguise a nonzero exit code" constructs, and the registry-publication
check is a fixed six-hostname/two-command allowlist rather than a
structural "no network push of an image" invariant. Recommend widening
`check_no_manufactured_pass` to also catch `|| exit 0`/`|| :`/`; true`
following a gate command, and widening `check_no_registry_publication` to
catch `docker.*push`/`--push`/`crane push`/`skopeo copy .*docker://` and
a non-exhaustive-allowlist-based registry-hostname heuristic (e.g. any
`docker (image )?push`/`--push` flag at all, rather than only the literal
two-word phrase). Classified **Medium** (§16) — real adversarial gap in
a security-relevant static check, zero current exploitation.

One other adversarial case flagged as a smaller, **Low**-severity gap:
`check_required_triggers()` only confirms the required `v*.*.*` pattern
*is present* in `release.yml`'s `on:` block — it does not confirm no
*additional*, broader tag pattern (e.g. `v*` alongside `v*.*.*`) has also
been added. Widening the trigger this way would not itself create a
publish-permission bypass (§6/§10's guards are independent of which tag
pattern triggered the run), so this is workflow-hygiene rather than a
security gap, but it is a real, confirmed gap in the check's
discriminating power that the brief's adversarial framing calls for
flagging.

**Self-reference determinism** (`MainDeterminismTests`): re-confirmed
directly — `check_workflows.py` contains no `import os`, `os.environ`, or
`os.getenv` anywhere (source-scanned directly, not merely trusting the
docstring's claim), so a local `make workflow-check` and CI's own run of
the identical script against the identical committed files are
guaranteed to agree.

---

## 14. Day 5 finding-closure verification

Independently re-read the actual diffs (not merely the Day 6 release
notes' claims) for all three Medium closures the brief called out by
name, plus spot-checked two of the six Low closures:

- **M-A** (`with_memory_shrink_restored` memory-restore verification) —
  **CONFIRMED genuinely closed**. Read the full diff
  (`git diff main -- scripts/reliability/reliability_check.py`): the
  restore path now re-`docker inspect`s `HostConfig.Memory`/`MemorySwap`
  after the restore `docker update` call and compares against the
  captured original values; a failed restore command *or* a
  restore-that-reports-success-but-doesn't-verify both raise
  `ReliabilityError` (never a `stderr`-only warning), with correct
  exception-chaining (`raise restore_error from action_exc`) when both
  the wrapped action and the restore fail. `WithMemoryShrinkRestoredTests`
  in `tests/test_reliability_check.py` was independently read and
  confirmed to exercise both the failed-restore-command path and the
  restore-succeeds-but-values-don't-match path via a spy `sc`, entirely
  Docker-free.
- **M-B** (app inner-hop timeout regression test) — **CONFIRMED genuinely
  closed**. `git diff main -- tests/test_server.py` shows a new
  `StateTimeoutTests` class (`state_delay_seconds = 0.5`,
  `state_timeout_seconds = 0.1`) asserting a `503` with no `"Traceback"`
  in the body — a real, fast, Docker-free exercise of `app`'s own
  `state_dependency_timeout_seconds` wiring, mirroring
  `test_gateway_server.py::UpstreamTimeoutTests` exactly as claimed. This
  test was independently re-run as part of the 529-test suite (§3) and
  passes.
- **M-C** (`check_compose.py` unit-test gap) — **CONFIRMED genuinely
  closed**. `tests/test_check_compose.py` is new this day (not present on
  `main`), and directly covers `_parse_cpus`/`_parse_bytes`/
  `_parse_duration_seconds` and `check_resource_limits`/
  `check_restart_policy`/`check_stop_grace_period` with the adversarial
  cases named in the Day 5 review (bool-as-int, malformed retry counts,
  Go-duration parsing, the 3600-second nanosecond/second disambiguation
  boundary, below-target values). Independently read the full file (39
  test methods) — coverage matches the closure claim.
- Spot-checked two of the six Low closures by direct diff read: the
  `inner_governed` band tightening (loose `>= 0.5×` lower bound → tight
  `[0.75×, 1.25×]` band, `reliability_check.py`) and the
  `check_resource_limits()` lower-bound-floor closure (both above-target
  and below-target values now rejected, confirmed by
  `test_cpus_below_target_fails`/`test_mem_limit_below_target_fails`/
  `test_pids_limit_below_target_fails` in the new test file) — both
  genuinely present and correctly test-covered, not merely claimed.

No reopening of Day 5-adjudicated history was performed or is warranted;
all closures examined are real Day 6 improvements on top of an already
release-ready v0.5.0.

---

## 15. Scope check

- **Exactly 2 workflow files**: confirmed (`find .github -type f` → only
  `ci.yml`, `release.yml`).
- **Exactly 5 Claude agents, 4 skills**: confirmed (`ls .claude/agents` →
  `compose-platform-engineer`, `container-security-reviewer`,
  `docker-architect`, `docker-test-engineer`, `release-engineer`; `ls
  .claude/skills` → `compose-validation`, `container-security-validation`,
  `docker-build-validation`, `release-readiness`). Diffed every changed
  agent/skill file against `main`: all changes are additive Day 6
  cross-references (CI/CD trust-boundary review duties, action-pin
  verification duties, workflow-test-quality duties) with no new agent or
  skill file created and no scope expansion beyond documenting the new
  delivery plane inside each agent's existing domain.
- **Runtime topology unchanged**: `compose.yaml`'s diff against `main`
  was read directly — the only changes are the `image:` tag bump
  (`0.5.0` → `0.6.0`, expected, VERSION-derived) and no structural change
  to services/networks/volumes/resource limits. Still exactly `state ->
  app -> gateway`, two networks, one named volume, one image.
- **No forbidden Day 7+/registry tooling anywhere in either workflow
  file**: independently confirmed by direct text search for `cosign`,
  `slsa`, `sigstore`, `kubectl`, `helm`, `argocd`, `terraform`, `ansible`,
  `prometheus`, `grafana`, `opentelemetry`, `kubernetes`, `docker login`,
  `docker push`, `ghcr.io`, `docker.io/`, `public.ecr.aws`, `azurecr.io`,
  `registry.hub.docker.com` — zero matches in either file, matching both
  `check_no_registry_publication()`'s and `check_no_day7_plus_tooling()`'s
  own clean results (§3).

---

## 16. Findings table

| # | Severity | Category | Finding | Bootstrap-blocking? |
|---|---|---|---|---|
| F-1 | **High** | release-workflow correctness | `check_release_context.py --mode dry-run` never validates the branch/ref a `workflow_dispatch` run was launched from — a feature-branch dry run is accepted identically to a `main` dry run. Cannot lead to publication (independently verified, §6), but violates the documented "dry run == main only" contract and could mislead a reader of a manually-dispatched run's result. | **For treating a manual dry run as authoritative: yes.** For the narrower act of pushing this branch + opening a Draft PR: no — `release.yml` is not triggered by `push` to a non-`main` branch or by `pull_request` at all. |
| F-2 | Medium | workflow-check adversarial coverage | `check_no_manufactured_pass()` only matches the literal `\|\| true` idiom — `\|\| exit 0`, `; true`, `\|\| :` all bypass it (independently reproduced, §13). Not exploited by either real file today. | No |
| F-3 | Medium | workflow-check adversarial coverage | `check_no_registry_publication()` uses a fixed 6-pattern allowlist (`docker login`, `docker push`, 4 registry hostnames) — `docker image push`, `docker buildx build --push`, `crane push`, `skopeo copy ... docker://`, and any registry hostname outside the fixed list (e.g. `quay.io`, `gcr.io`) all bypass it (independently reproduced, §13). Not exploited by either real file today. | No |
| F-4 | Low | workflow-check adversarial coverage | `check_required_triggers()` confirms the required `v*.*.*` tag pattern is present but does not reject an *additional*, broader pattern (e.g. `v*`) added alongside it. Widening the trigger this way would not bypass the independent publish-permission guard (§6/§10), so this is hygiene, not a security gap. | No |
| F-5 | Info | pre-existing environment artifact | `compose-check` fails in this specific WSL/Docker-Desktop sandbox on a config-file host-path rendering quirk (`\\wsl.localhost\...` vs. the expected POSIX path). Independently confirmed via `git stash` to reproduce byte-for-byte identically on unmodified `main` — not a Day 6 regression, not caused by any file in this review's scope. Not expected to reproduce on GitHub's native Linux Ubuntu runners; the Draft-PR bootstrap itself is the correct way to confirm this for real. | No |
| F-6 | Info | redundant-but-harmless | `validate` job in `release.yml` explicitly repeats `permissions: contents: read`, identical to the workflow-level default — harmless, arguably good for readability/intent-documentation, not a finding requiring action. | No |
| F-7 | Info | minor inefficiency | Both `ci.yml` jobs use `fetch-depth: 0` (full history); `ci.yml` has no code path that needs full history (only `release.yml`'s tag-mode ancestry check does) — slightly slower checkout with no functional benefit in `ci.yml`. Not a correctness or security issue. | No |

No Critical findings. No finding indicates a possible publication bypass,
a secret/token leak to an untrusted context, an unpinned/floating action
reference, or an invalid workflow YAML file.

---

## 17. Bootstrap blockers

**None that block the act of pushing this branch and opening a Draft
PR.** `ci.yml` (the only workflow a Draft PR's `pull_request` event
triggers) has zero findings above Info severity. `release.yml` is not
triggered by opening a Draft PR at all (neither `push: tags` nor
`workflow_dispatch` fires from that action), so F-1 — the one High
finding — does not block this specific bootstrap step, though it **must**
be fixed (§7's recommended fix) before anyone subsequently uses this
Draft-PR-testing window to manually dispatch `release.yml` and treat a
green dry run as meaningful main-branch evidence, rather than as "this
branch's own `VERSION`/tag-format/release-notes happen to parse."

---

## 18. Final recommendation

Push the branch and open the Draft PR to exercise `ci.yml` for real — the
review found no defect in that workflow's trigger scoping, job graph,
permission model, action pinning, or failure propagation that would make
doing so unsafe. Before manually dispatching `release.yml` on GitHub for
the first time and treating its dry-run output as authoritative
main-branch evidence, close F-1 (add an explicit `main`-only guard to the
`workflow_dispatch` dry-run path). F-2/F-3/F-4 are worth tightening in
`scripts/ci/check_workflows.py` as follow-up engineering debt but do not
block bootstrapping.

DAY 6 SAFE FOR GITHUB BOOTSTRAP
