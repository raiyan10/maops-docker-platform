# CI/CD and Release Engineering (Day 6)

## Runtime plane vs. delivery plane

Every day through Day 5 built the **runtime plane**: the three services
(`gateway -> app -> state`), their hardening, their networking, their
persistence, their resource/restart/timeout controls. Day 6 adds a
**delivery plane** on top of it — how a change gets validated and how a
release gets published — without touching the runtime plane at all.
`compose.yaml`'s topology, `docker/app/Dockerfile`'s base images, and every
runtime hardening property are byte-for-byte unchanged by this day's scope.
The delivery plane's job is to *automate* the validation this project
already had (`make quality`, `make release-check`), not to redesign what is
being validated.

## The Makefile remains the authoritative local contract

`make release-check` is the single source of truth for "is this commit
release-policy clean?" — both for a developer working locally and for
GitHub Actions. Neither `.github/workflows/ci.yml` nor
`.github/workflows/release.yml` reimplements any check's logic in workflow
YAML; both invoke `make quality`/`make release-check` (or the individual
scripts those targets wrap) exactly as documented in `README.md`. This is
deliberate: a gate list hand-duplicated in the Makefile *and* in workflow
YAML *and* in a doc page is exactly the kind of drift risk
`.claude/CLAUDE.md` warns against ("avoid duplicating the same gate list
independently in three places") — here there is exactly one place (the
Makefile) that defines what "release-policy clean" means, and CI is a thin
orchestration layer over it.

```
make quality        # test, lint, dockerfile-check, compose-check, workflow-check
make release-check   # quality, build, inspect, image-audit, smoke, security-check,
                      #   compose-test, reliability-check, reproducibility-check,
                      #   supply-chain-check (sbom, sbom-check, vuln-scan)
```

`workflow-check` (`scripts/ci/check_workflows.py`, new this day) is folded
into `quality` — a malformed or insecure workflow-YAML change now fails the
same fast, Docker-free gate a lint/Dockerfile/Compose regression would,
rather than only being caught after a real GitHub Actions run.

## Two workflows, not a large collection

`.github/workflows/ci.yml` and `.github/workflows/release.yml` are the
entire delivery plane. A third reusable workflow was considered and
rejected: the two files share almost nothing structurally (`ci.yml` is a
two-job quality/release-policy pipeline with no publish step; `release.yml`
is a two-job validate/publish pipeline with a hard safety gate between
them) — extracting a shared "run make release-check" step into a reusable
workflow would save perhaps five lines while adding a third file's worth of
indirection to reason about. Two workflows, each readable start to finish,
was judged clearer.

## `ci.yml`: PR and main validation

**Triggers**: `pull_request` targeting `main`, `push` to `main`, and
`workflow_dispatch` (for an on-demand manual re-run). Deliberately **not**
`pull_request_target` — that trigger runs with the base repository's own
context (including any repository secrets) against a PR branch's own
workflow file, which is exactly the "a malicious PR could exfiltrate a
write-scoped token or secret" hazard this project refuses to introduce.
Ordinary `pull_request` runs the PR branch's *own* workflow file, with the
base repository's default (here: read-only) token and zero secrets — a
fork PR cannot get more access than that, structurally, no matter what its
own workflow file says.

**Job graph**: `quality` (fast, Docker-free — `make quality`) runs first.
`release-policy` (`needs: quality`) runs only if `quality` passed, and runs
the full `make release-check` (which internally re-runs `quality` as its
own first prerequisite — see "why quality runs twice" below), builds the
release image, and runs every Docker-based gate (image-audit, smoke,
security-check, compose-test, reliability-check, reproducibility-check,
supply-chain-check).

**Why quality runs twice**: `make release-check`'s own dependency chain
already starts with `quality` (see `Makefile`). Job 2 could instead invoke
only `release-check`'s remaining prerequisites individually
(`build inspect image-audit ... supply-chain-check`) to avoid re-running
`quality`'s ~1 minute of unit tests/lint a second time — but that would mean
CI hand-lists a *subset* of `release-check`'s own dependency chain, which
re-introduces exactly the drift risk this design avoids: if `Makefile`'s
`release-check` prerequisite list ever changes, a hand-maintained CI subset
could silently fall out of sync. Job 2 instead runs the literal,
unmodified `make release-check` — the same command a developer runs
locally — accepting the small, bounded redundancy (job 1's `quality`
already gave fast PR feedback; job 2's repeated `quality` run inside
`release-check` costs roughly a minute of CI time and carries zero drift
risk) as the right trade-off. This is the "single well-structured full
validation job" option from a job-design perspective (job 2 *is* one
`make release-check` invocation), layered under a cheap `quality`
pre-check for fast failure — not a hand-fragmented pipeline.

**Fast vs. expensive**: `quality` never touches Docker — it's `test`,
`lint`, `dockerfile-check`, `compose-check` (a static render/parse of
`compose.yaml`, no container started), and `workflow-check`. A lint or unit
test regression fails within roughly a minute, before any image is ever
built. `release-policy` needs the runner's pre-installed Docker Engine and
Compose v2 plugin — no Docker Engine installation step is added (GitHub's
Ubuntu runners ship both already), matching the instruction to prefer the
tools the runner already provides.

**Concurrency**: `group: ci-${{ github.workflow }}-${{
github.event.pull_request.number || github.ref }}`, `cancel-in-progress:
true` — pushing a new commit to an open PR cancels that PR's own prior,
now-obsolete CI run rather than letting it keep consuming runner time
alongside the new one.

**Permissions**: `permissions: contents: read` at the workflow level,
inherited by both jobs — neither job widens it. No PR run, and no ordinary
push run, ever receives a write-scoped token or a secret.

**Artifacts**: `release-policy` uploads the release image's SPDX SBOM and
Trivy JSON report (`artifacts/sbom/`, `artifacts/security/`) as CI
evidence, retained 7 days, via `actions/upload-artifact` with `if:
always()`. Uploading on `always()` does not hide a failed gate: a failed
`make release-check` step already marks the job (and the whole workflow
run) as failed regardless of what a later `always()` step does — the
artifact-upload step's own success or failure never changes the job's
overall conclusion.

## `release.yml`: the controlled release workflow

**Triggers**: `push: tags: v*.*.*` — the real release event — and
`workflow_dispatch` on `main` — the safe, non-publishing dry run.

**Concurrency**: `group: release-${{ github.ref }}`, deliberately
`cancel-in-progress: false`. Interrupting a release mid-publish is exactly
the half-finished, unverifiable state this project's "no manufactured
PASS, no silent partial result" philosophy rules out — a release run is
allowed to queue behind another one for the same ref, never to be killed
mid-flight by a newer trigger.

**Job graph**: `validate` (always runs, both modes) → `publish` (`needs:
validate`, real-tag-only). `validate` runs `make release-check` (identical
in both modes — a dry run genuinely exercises "the same release-policy
gates" a real tag event would) and then
`scripts/release/check_release_context.py` in the mode matching the
triggering event.

**Release-candidate dry run** (`workflow_dispatch` on `main`):
`check_release_context.py --mode dry-run` derives the proposed tag from
`VERSION` (e.g. `VERSION=0.6.0` → proposed tag `v0.6.0`), validates its
format, and validates the release notes file
(`docs/releases/v<VERSION>.md`) already exists — giving real, actionable
feedback about release readiness *before* the tag is ever created. The
`publish` job's own `if:` condition (below) makes a dry run structurally
incapable of reaching publication, regardless of what `validate` reports.

**Real tag validation**: `check_release_context.py --mode tag` additionally
validates the tag's own format, that the tag exactly matches `VERSION`
(`VERSION=0.6.0` requires tag `v0.6.0` — `v0.5.0` fails), and — via a real
`git merge-base --is-ancestor` check against `origin/main` (the checkout
step uses `fetch-depth: 0` specifically so this has full history to check
against) — that the tagged commit genuinely belongs to `main`'s history.
This refuses to publish a release from an arbitrary feature-branch-only
commit that happens to carry a valid-looking tag.

**Why the tag/version/history logic lives in a script, not workflow YAML**:
`scripts/release/check_release_context.py`'s core validation
(`validate_version_format`, `validate_tag_format`, `tag_matches_version`,
`validate_release_notes_exist`, `validate_main_history`) is pure,
Docker-free, git-free logic with a single, separately swappable adapter
(`is_ancestor`) at the one place real `git` is genuinely needed — see
`tests/test_check_release_context.py`. Burying this in shell/YAML
conditionals would make it untestable outside a real GitHub Actions run;
as a script, it has the same fast, local, `unittest`-verified feedback
loop every other check in this repository has. User-controlled strings (a
tag from `GITHUB_REF_NAME`, a commit SHA from `GITHUB_SHA`) are always
passed as separate `argv` elements to `subprocess.run([...])`, never
`shell=True`, never string-formatted into a command line — there is no
shell-injection surface even though the tag ref is nominally
attacker-influenceable (anyone who can push a tag to this repository
already has write access, but the discipline is the same regardless).

**Permissions — split by job, not workflow-wide**: workflow-level
`permissions: contents: read`. `validate` job: `contents: read` (explicit,
matching the workflow default). `publish` job: `contents: write` — the
*only* place in either workflow a write-scoped token exists, and it only
ever exists in a job that can only run on a real tag push (see below).
`scripts/ci/check_workflows.py`'s `check_release_permissions_scoped()`
enforces this exact shape statically — exactly one `write`/`admin`
permission scope may exist anywhere in `release.yml`, and it must be
`contents: write` inside the `publish` job specifically.

**Manual dispatch cannot publish — the actual mechanism**: adding *any*
`if:` condition to a job replaces GitHub Actions' own default "all needed
jobs succeeded" check, so `publish`'s condition explicitly repeats
`success()` alongside the real guard:

```yaml
if: >-
  success() &&
  github.event_name == 'push' &&
  startsWith(github.ref, 'refs/tags/')
```

`workflow_dispatch` never sets `github.event_name` to `'push'`, so this
condition is structurally false on every dry run — not merely
conventionally false. `scripts/ci/check_workflows.py`'s
`check_manual_dispatch_cannot_publish()` statically enforces that the
`publish` job's condition contains this exact shape and *never* mentions
`workflow_dispatch` at all (a job that even references it, e.g. to
"except" it via a negation, is rejected outright — the simplest correct
policy for two hand-authored files is "never mention it," not "mention it
correctly").

**Automated GitHub Release**: `publish` downloads the `validate` job's
`release-evidence` artifact (the SBOM + Trivy JSON already generated and
validated by `make release-check`, not regenerated a second time),
computes `SHA256SUMS` over the downloaded files, and calls `gh release
create` (GitHub CLI, already present on the runner — no third-party release
action) with `docs/releases/${TAG}.md` as `--notes-file` and the SBOM,
Trivy report, and `SHA256SUMS` as attached assets. `GH_TOKEN` is the
built-in `secrets.GITHUB_TOKEN`, scoped to `contents: write` by the job's
own `permissions:` block — not a personal access token, not a
registry credential.

**Release immutability**: before creating the release, `publish` runs `gh
release view "$TAG"` and fails the job (`exit 1`, a clear
`::error::`-annotated message) if a release for that tag already exists —
this workflow never overwrites published release evidence and never passes
`--clobber`. Tags themselves are never moved, force-pushed, or rewritten by
anything in either workflow. This is a deliberate one-way model: a mistake
discovered after publication requires a new patch version and a new tag,
not a mutated `v0.6.0`.

**Release notes**: version-specific, hand-authored files under
`docs/releases/` (`docs/releases/v0.6.0.md` for this release), never
inlined into workflow YAML. `check_release_context.py` fails clearly (a
`ReleaseContextError` naming the missing path) if the file required by the
current mode's tag is absent — both in dry-run mode (using the *proposed*
tag) and real-tag mode (using the *actual* tag), so a forgotten release-
notes file is caught before publication, not discovered by a user reading
an empty GitHub Release.

## Action supply-chain security: SHA pinning

Every `uses:` reference in both workflows is pinned to a full, immutable
40-character commit SHA, independently resolved via the GitHub API
(`gh api repos/<org>/<repo>/releases/latest` → `gh api
.../git/refs/tags/<tag>`) at the time this branch was written, with the
corresponding trusted release tag named in a trailing comment for
human readability:

| Action | Pinned tag | Commit SHA |
|---|---|---|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | `v7.0.0` | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | `v7.0.1` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| `actions/download-artifact` | `v8.0.1` | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` |

All four are official, GitHub-maintained actions — no third-party action
was introduced. `scripts/ci/check_workflows.py`'s
`check_uses_pinned_to_full_sha()` statically enforces that every `uses:`
reference in both files resolves to exactly 40 lowercase hex characters —
`@v4`, `@main`, or a short SHA all fail this check.

## `workflow-check`: self-validating, deterministically

`scripts/ci/check_workflows.py` (`make workflow-check`, part of `make
quality`) is a small, project-specific text/indentation analyzer — not a
YAML parser dependency — against the two committed workflow files
themselves. It is explicitly *not* a general GitHub Actions schema
validator; see its own module docstring for the exact policy list it
enforces (files exist; no `pull_request_target`; `ci.yml`'s permissions;
`release.yml`'s permission scoping; SHA pinning; no `continue-on-error:
true`; no `|| true` around a gate; required triggers; the `v*.*.*` tag
pattern; the manual-dispatch-cannot-publish shape; no registry-publication
command; no Day 7+ tooling reference).

**Self-reference, handled deliberately**: `ci.yml`'s own `quality` job runs
this exact script against its own checked-out copy of `ci.yml` and
`release.yml` — the committed workflow validates itself. This only stays
deterministic because the script reads nothing but the two files
(`REPO_ROOT`-relative paths) and never touches an `os.environ`/`GITHUB_*`
runtime variable — confirmed by a direct source-scan unit test
(`tests/test_check_workflows.py::MainDeterminismTests`). A version of this
check that behaved differently depending on whether it happened to be
running inside GitHub Actions would defeat the entire point of "the
committed workflow validates itself" — a local `make workflow-check` and
CI's own run of the identical script must always agree.

A subtlety worth recording: comment text that *explains* why a forbidden
pattern is absent (e.g. "Deliberately NOT pull_request_target: ...", or a
comment describing why `publish` "keeps workflow_dispatch ... from ever
publishing") would trivially false-positive a naive substring search. Every
check strips YAML comments (`#` to end of line) before scanning, so
explanatory prose can freely *name* what it's avoiding without tripping the
check that looks for the real thing.

## Why registry publication is out of scope

Day 6's entire delivery destination is the GitHub Release (with its
attached SBOM, vulnerability report, and checksums) — not a container
registry. No `docker login`, no `docker push`, no GHCR/Docker Hub/ECR/ACR
configuration, no registry credential of any kind exists anywhere in this
repository. `scripts/ci/check_workflows.py`'s
`check_no_registry_publication()` statically forbids the relevant command/
hostname patterns in both workflow files. This is a scope boundary, not an
oversight — see `docs/roadmap.md` and this project's release notes for the
explicit statement that registry publication remains out of scope for this
day, distinct from earlier drafts of the roadmap that once anticipated it.

## Failure behavior

Every gate in both workflows fails closed: no `continue-on-error: true`
appears anywhere, no `|| true` disguises a required command's exit code,
and `scripts/ci/check_workflows.py` statically enforces both of those
absences as a repository policy, not merely a convention. A required
validation failure produces a non-zero job result, which GitHub Actions
surfaces as a failed check on the PR/commit — nothing in either workflow
can turn a failed gate into a green one.

## How to run the equivalent checks locally

```bash
make quality          # test, lint, dockerfile-check, compose-check, workflow-check
make release-check    # the full authoritative gate ci.yml's release-policy job runs
python3 scripts/release/check_release_context.py --mode dry-run
docker compose config
```

A developer who runs `make release-check` locally and sees it pass has
exercised substantially the same policy `ci.yml`'s `release-policy` job
enforces — GitHub Actions adds independent, clean-runner corroboration
(see `docs/build-security.md`'s point that CI execution on a clean runner
is valuable evidence distinct from a developer's own, possibly
cache-warmed, machine), not a different or stricter policy.
