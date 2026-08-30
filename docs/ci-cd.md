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
                      #   supply-chain-check (sbom, sbom-check, vuln-scan),
                      #   patch-lifecycle-check, release-bundle
```

`patch-lifecycle-check` and `release-bundle` (Day 7) are documented in
full in `docs/production-readiness.md` §1.1/§1.2 and
`docs/build-security.md`'s "Day 7: runtime security-patch lifecycle
tripwire" section — the same "Makefile is authoritative, CI orchestrates
it" principle applies to both; neither is reimplemented in workflow YAML.

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

## GitHub-hosted runner Buildx portability (real CI finding)

This project's first real GitHub Actions run of this branch's `ci.yml`
(run ID `32938805880`) is not reported as successful here — it genuinely
failed, and the failure is real Day 6 engineering evidence, not a mistake
to be quietly fixed and forgotten.
`quality` passed (529 unit tests, lint, `dockerfile-check` 10/10,
`compose-check` 17/17, `workflow-check` 11/11). `release-policy` failed
inside `make build`, before any image-level gate (image-audit, smoke,
security-check, ...) ever ran:

```
docker buildx build --no-cache \
  --build-arg VERSION=0.6.0 \
  --build-arg SOURCE_DATE_EPOCH=... \
  --output type=docker,rewrite-timestamp=true,...,dest=...tar \
  -f docker/app/Dockerfile .

ERROR: Docker exporter is not supported for the docker driver.
Switch to a different driver, or turn on the containerd image store.
```

**Root cause**: `make build` (see `Makefile`) uses BuildKit's
`--output type=docker,rewrite-timestamp=true,...,dest=<tar>` exporter — the
Day 4 deterministic-build archive-export flow (`docs/build-security.md`).
That exporter requires either a `docker-container` driver Buildx builder,
or a `docker` driver builder backed by the containerd image store. GitHub's
`ubuntu-latest` runner (Docker Engine 28.0.4, confirmed via this run's own
`docker buildx ls`) ships Buildx's default `docker` driver builder without
the containerd image store — so that exporter is structurally unavailable
there, independent of anything this repository controls.

**Why local Docker never exposed this**: a local Docker Desktop
installation's default `docker` driver builder already runs the containerd
image store (`docker info`'s `driver-type io.containerd.snapshotter.v1`),
so the identical `make build` command succeeds locally today. This is a
genuine environment difference between a developer's Docker Desktop and a
clean GitHub-hosted Ubuntu runner — not a bug either environment can be
said to have "gotten wrong" — and it could only ever be caught by a real
run on the real target environment, which is exactly what Day 6's
GitHub Actions integration is for.

**Remediation — CI environment preparation, not an application-image
design change**: both `ci.yml`'s `release-policy` job and `release.yml`'s
`validate` job — the only two jobs that reach `make build`/`make
release-check` — now create and select (`--use`) a job-scoped Buildx
builder using the `docker-container` driver immediately before `make
release-check`, using the Docker CLI already present on the runner (no new
GitHub Action introduced):

```yaml
- name: Create job-scoped Buildx builder (docker-container driver)
  run: |
    BUILDER_NAME="maops-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
    echo "BUILDER_NAME=${BUILDER_NAME}" >> "$GITHUB_ENV"
    docker buildx create --driver docker-container --name "${BUILDER_NAME}" --use
    docker buildx inspect "${BUILDER_NAME}" --bootstrap
```

followed, after `make release-check`, by an `if: always()` cleanup step
that removes only that run's own builder (never a prune, never another
job's builder), checking existence first rather than swallowing the error
with `|| true`:

```yaml
- name: Remove job-scoped Buildx builder
  if: always()
  run: |
    if docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
      docker buildx rm "${BUILDER_NAME}"
    else
      echo "builder ${BUILDER_NAME} not found - nothing to remove"
    fi
```

The builder's name is derived only from `GITHUB_RUN_ID`/`GITHUB_RUN_ATTEMPT`
(GitHub-controlled run identifiers), never from PR/tag/title text, matching
this repository's existing project-prefixed-unique-name discipline for
every other disposable Docker resource it creates
(`.claude/CLAUDE.md`'s Docker safety constraints). A `docker buildx
version`/`docker buildx ls` diagnostic step runs before builder creation
so a CI log makes the environment difference (`driver=docker` locally vs.
the newly created `driver=docker-container` builder in CI) visible.

**What this explicitly is not**: no Dockerfile change, no weakening of
`--no-cache`, `SOURCE_DATE_EPOCH`, `rewrite-timestamp=true`, or the
`type=docker` archive-export/`docker load` flow, no CI-only "weaker build
path," and no `continue-on-error`/`|| true` around the gate. The Makefile
itself is unchanged — `docker buildx build` always targets whichever
Buildx builder is currently selected, so creating and selecting a
`docker-container` builder before invoking the existing, unmodified `make
build`/`make release-check` is purely CI environment preparation, not a
change to what is being built or how its reproducibility is proven. The
Day 4 deterministic-build contract (`docs/build-security.md`) and its
two-independent-builds byte-identity proof (`reproducibility-check`) are
unchanged by this fix.

`scripts/ci/check_workflows.py`'s
`check_buildx_container_builder_before_release_check()` statically enforces
that both `ci.yml`'s `release-policy` job and `release.yml`'s `validate`
job create a `docker-container` driver builder (with `--use`) before `make
release-check`, and remove it with `if: always()` afterward — see
"`workflow-check`: self-validating, deterministically" below.

## GitHub-hosted runner post-restart cgroup/runc resource-update race (real CI finding)

The Buildx portability fix above was independently proven on GitHub Actions
run `32960673438`: `release-policy` created and used the job-scoped
`docker-container` builder, `make release-check` ran, and `make
reliability-check` progressed correctly all the way through 564 unit
tests, `dockerfile-check` 12/12, `compose-check` 17/17, `workflow-check`
12/12, image build/inspect/audit/smoke/security-check/compose-test, and
then genuinely exercised Scenario 1's real transient PID 1 kernel OOM
crash: `state`'s `RestartCount` advanced `0 -> 1`, `state`/`app`/`gateway`
readiness all recovered automatically, the persisted counter survived, and
the full `gateway -> app -> state` chain worked again post-recovery - the
*application/reliability* behavior this platform claims had already been
proven correct, for real, on a clean GitHub-hosted Linux runner.

The run then failed **~0.17s later**, inside Scenario 2, on the very first
`docker update --memory 6m --memory-swap 6m` issued against that
just-restarted `state` container:

```
reliability_check: FAIL: docker update (shrink memory)
maops-reliability-...-state-1 failed:
Error response from daemon: Cannot update container <id>:
runc did not terminate successfully: exit status 1:
openat2 /sys/fs/cgroup/system.slice/docker-<id>.scope/cgroup.controllers:
no such file or directory
```

**This is a Docker control-plane finding, not a reliability-design
finding.** Every property the harness had already checked - resource
limits, restart policy, stop-grace-period, the timeout hierarchy, the A-6
pause proof, and the entire Scenario 1 automatic-recovery chain - had
already passed. The failure is `dockerd`/`runc` itself returning a
non-zero exit from a `docker update` resource mutation issued immediately
after an automatic restart, not a defect in the resource limits, the
restart policy, or the application's own recovery behavior. **Not
reproducible against this project's own local Docker Desktop install** -
treated as a GitHub-hosted-runner/`runc`/cgroup v2 post-restart
synchronization race until proven otherwise: a container the restart-policy
engine just brought back up can, on some Linux runner cgroup v2
hierarchies, have a brief window where `runc`'s own `cgroup.controllers`
bookkeeping for the freshly (re)created cgroup instance is not yet fully
settled when a `docker update` lands, and `runc` fails the whole operation
rather than retrying internally.

**Remediation - bounded, monotonic, independently VERIFIED retry, scoped
to exactly this error, never to Docker errors in general**:
`scripts/reliability/reliability_check.py` adds
`update_container_resources_verified()`, used for BOTH the Scenario 2
memory shrink and its restoration (restoration was already a first-class
verified invariant per the Day 5 M-A fix below - this preserves that, it
does not relax it):

- `docker update` exits `0`: `HostConfig` is independently re-inspected;
  only an EXACT `Memory`/`MemorySwap` match returns success - a
  "successful" update whose inspected values don't match is a real
  verification failure and is never retried.
- `docker update` exits non-zero: the stderr is checked against a narrow
  classifier, `_is_transient_cgroup_update_race()`. As of Day 7
  (`DAY6-POST-M2`, see `docs/production-readiness.md` §1.3, and
  `docs/reliability.md`'s own dedicated section for the full history of
  both real occurrences), this requires ALL of:

  - the literal `"runc did not terminate successfully"` wrapper phrase;
  - a genuine `openat2 <path>: no such file or directory` regex match
    (real ENOENT-on-`openat2` semantics - a bare "no such file or
    directory" appearing anywhere else in the message is common to many
    genuinely non-retryable Docker/runc errors and must never by itself
    be treated as retryable);
  - the missing path's directory containing a real `/cgroup/` hierarchy
    segment (real cgroup-path context, never a same-named file living
    somewhere else); and
  - the missing path's basename being one of a small, explicitly
    enumerated, deliberately restricted set -
    `{"cgroup.controllers", "memory.max"}` - never a broad "any
    cgroup-shaped filename" wildcard. `cgroup.controllers` is the
    original GitHub run `32960673438` signature; `memory.max` is a
    closely related but distinct variant a later post-release
    evidence-commit run (`33059581018`) hit, immediately after a genuine
    Scenario 1 OOM crash and automatic restart - both are real,
    independently evidenced GitHub-hosted-runner occurrences, never
    speculative. Extending this set again requires a new, independently
    observed real GitHub Actions failure.

  Arbitrary `runc` errors are **not** retried; an unrelated missing file
  is **not** retried; `permission denied` (a real `openat2` failure that
  is not ENOENT) is **not** retried; `invalid memory limit`, `invalid
  argument`, `container not found`, daemon-unavailable, an unknown-flag/
  CLI-syntax error, or an actual policy/verification mismatch all fail
  immediately, with no retry; `pids.max`/`cpu.max`/other unapproved cgroup
  filenames are **not** automatically retried even with an otherwise
  byte-identical error.
- On a recognized transient race, `HostConfig` is inspected BEFORE
  retrying (`dockerd`/`runc` can genuinely return non-zero after a partial
  operation): an exact match returns success without reissuing `docker
  update`; a genuine mismatch checks a real `time.monotonic()`-measured
  bounded deadline (~10s, a small ~0.5s retry interval - matching this
  script's own `POLL_INTERVAL_SECONDS` convention) and either retries or
  fails; a container that disappears mid-retry (the verification `docker
  inspect` itself fails) fails immediately rather than retrying against a
  container that may no longer exist.

No `docker` resource mutation in this script ever infers success from exit
code alone, and the deadline is a real monotonic bound, never a blind
`time.sleep()` used as the correctness mechanism. `now`/`sleep` are
injectable (default to the real `time.monotonic`/`time.sleep`), so
`tests/test_reliability_check.py` proves every branch of this - first-try
success, transient-then-success, several-transients-then-success,
deadline exhaustion, an unrelated error's immediate no-retry failure, a
reported-success/verification mismatch, an already-applied value found
mid-retry, a container disappearing mid-retry, and both shrink and
restore going through the identical mechanism (restore failing preserves
the original action failure as `__cause__`, matching the existing Day 5
M-A precedence) - entirely Docker-free, with a fake monotonic clock, no
real fixed-time delay anywhere in the suite.

**What this explicitly is not**: not a weakened persistent-failure
scenario, not a `continue-on-error`/`|| true` around the gate, not a
synthetic exit-code loop replacing the real kernel OOM mechanism, not a
container recreation to dodge the race (the same `state` container
instance keeps its restart-policy budget across Scenario 1 and Scenario
2, exactly as `docs/reliability.md`'s `RestartCount` semantics document),
and not a change to the resource limits, restart policy, or timeout
hierarchy this platform actually enforces. Local `make reliability-check`
behavior is unchanged: a healthy local Docker Desktop install succeeds on
the first `docker update` attempt, so the retry mechanism adds no
observable behavior change there, and the check count stays `32/32`.

GitHub Actions run `32960673438` itself is **not** reported as passing
here - it is real Day 6 engineering evidence of exactly the same kind as
the Buildx portability finding above, fixed and documented rather than
quietly re-run away. The next run against the commit containing this fix
will independently confirm whether the narrow retry closes it.

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
`workflow_dispatch` — the safe, non-publishing dry run. `workflow_dispatch`
can be invoked by GitHub against *any* ref (a feature branch, `develop`, or
even a tag ref selected from the "Run workflow" ref picker) — the trigger
declaration alone cannot restrict that. This project's release contract
requires the dry run to be authoritative *only* on `main`; see "Manual
dispatch is main-only" below for how that is actually enforced.

**Concurrency**: `group: release-${{ github.ref }}`, deliberately
`cancel-in-progress: false`. Interrupting a release mid-publish is exactly
the half-finished, unverifiable state this project's "no manufactured
PASS, no silent partial result" philosophy rules out — a release run is
allowed to queue behind another one for the same ref, never to be killed
mid-flight by a newer trigger.

**Job graph**: `validate` (always runs, both modes) → `publish` (`needs:
validate`, real-tag-only). `validate` first runs
`scripts/release/check_release_context.py` (an unconditional step — see
"Manual dispatch is main-only" below — running before the expensive gate so
an invalid ref/tag/event fails fast), then `make release-check` (identical
in both modes — a dry run genuinely exercises "the same release-policy
gates" a real tag event would).

**`check_release_context.py` is the authoritative event distinguisher**:
the script — not `release.yml`'s own `if:` expressions — decides which
validation to run, from the real `--event-name` (`GITHUB_EVENT_NAME`) it is
invoked with: `workflow_dispatch` maps to the dry-run path,
`push` maps to the real tag path, and any other event fails
(`determine_mode()`). This is deliberate: trusting a YAML `if:` to select
the right script mode duplicates the event-routing logic in two places
(the workflow and the script) that could drift out of sync; here there is
exactly one place that decides.

**Manual dispatch is main-only — structurally enforced**: `workflow_dispatch`
can be invoked against any ref, but this project's release contract requires
the dry run to be authoritative *only* on `refs/heads/main`.
`check_release_context.py`'s `validate_dispatch_ref()` enforces this by
exact string equality (never a prefix/regex match, so a ref that merely
*contains* "main" — a branch named `main-v0.6.0`, or a nested
`refs/heads/main/sub` — still fails) against the real `--ref`
(`GITHUB_REF`) the workflow passes it. A `workflow_dispatch` run against
`refs/heads/feature/...`, `refs/heads/develop`, a tag ref, an empty ref, or
a malformed ref all fail with a clear, non-zero
`check_release_context: FAIL: ...` message — the step is unconditional (no
per-event `if:` gates it), so a non-main dispatch cannot be mistaken for a
silently-skipped, passing run. `scripts/ci/check_workflows.py`'s
`check_release_context_validation_is_authoritative()` statically enforces
that this step exists, is unconditional, passes `--event-name`/`--ref`, and
runs before `make release-check`. This closes a Day 6
release-engineering-review Medium finding: the main-only intent was
previously documented but not structurally enforced (the dry-run script had
no ref check at all).

**Release-candidate dry run** (`workflow_dispatch`, `refs/heads/main`
only): once the ref check above passes, `build_dry_run_context()` derives
the proposed tag from `VERSION` (e.g. `VERSION=0.6.0` → proposed tag
`v0.6.0`), validates its format, and validates the release notes file
(`docs/releases/v<VERSION>.md`) already exists — giving real, actionable
feedback about release readiness *before* the tag is ever created. It never
requires a version tag to already exist for a `workflow_dispatch` event.
The `publish` job's own `if:` condition (below) makes a dry run structurally
incapable of reaching publication, regardless of what `validate` reports —
manual dispatch never publishes, main-only or not.

**Real tag validation** (a `push` event, a separate path from the dispatch
ref check above): `build_tag_context()` validates the tag's own format,
that the tag exactly matches `VERSION` (`VERSION=0.6.0` requires tag
`v0.6.0` — `v0.5.0` fails), and — via a real `git merge-base
--is-ancestor` check against `origin/main` (the checkout step uses
`fetch-depth: 0` specifically so this has full history to check against)
— that the tagged commit genuinely belongs to `main`'s history. This
refuses to publish a release from an arbitrary feature-branch-only commit
that happens to carry a valid-looking tag. Tag publication is a distinct
event path from the manual dry run described above; a tag push is never
required to also satisfy the dispatch ref check, and a `workflow_dispatch`
run is never required to carry a version tag.

**Why the tag/version/history logic lives in a script, not workflow YAML**:
`scripts/release/check_release_context.py`'s core validation
(`validate_version_format`, `validate_tag_format`, `tag_matches_version`,
`validate_release_notes_exist`, `validate_main_history`,
`validate_dispatch_ref`, `determine_mode`) is pure, Docker-free, git-free
logic with a single, separately swappable adapter (`is_ancestor`) at the
one place real `git` is genuinely needed — see
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
validated by `make release-check`, not regenerated a second time), then
runs `scripts/release/prepare_release_bundle.py` (Day 7, closes
DAY6-POST-M1 — see `docs/production-readiness.md` §1.2) to stage a flat,
basename-only `release-bundle/` directory and independently prove the
real, unmodified `sha256sum -c SHA256SUMS` succeeds against it — never
computing checksums inline in this workflow's own YAML. `gh release
create` (GitHub CLI, already present on the runner — no third-party
release action) then runs with `docs/releases/${TAG}.md` as
`--notes-file` and `release-bundle/*` (the SBOM, Trivy report, and
`SHA256SUMS`, verbatim) as attached assets — the exact same three files a
consumer downloads, so what was verified locally is what gets published,
with no separate re-derivation step in between. `GH_TOKEN` is the
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
pattern; the manual-dispatch-cannot-publish shape; every job reaching
`make release-check` creates and selects a `docker-container` driver
Buildx builder beforehand and removes it with `if: always()` afterward
(see "GitHub-hosted runner Buildx portability" above); the
`check_release_context.py` step is unconditional, passes explicit
`--event-name`/`--ref` context, and runs before `make release-check` (the
main-only `workflow_dispatch` dry-run contract, statically checked — see
"Manual dispatch is main-only" above); no registry-publication command; no
Day 7+ tooling reference).

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

### Real example: a blocking finding fixed, not the gate weakened

This is not a hypothetical property. On 2026-08-26, `make release-check`'s
`supply-chain-check` stage genuinely failed locally against an unchanged
policy (`CRITICAL=0`, `HIGH-with-fix=1`: CVE-2026-14456 in `libssl3t64`,
already fixed upstream by Debian Security but not yet by the day's pinned
Distroless digest). The fix was an emergency, checksum-pinned
Debian-security package overlay in `docker/app/Dockerfile` (see
`docs/build-security.md` and `docs/supply-chain.md`) — `scripts/security/
check_trivy_report.py`'s policy itself was never touched. The same
gate — local or in CI — would have failed the release either way; this is
exactly the "fail closed" property this document describes, exercised for
real rather than only by synthetic fixtures.

## How to run the equivalent checks locally

```bash
make quality          # test, lint, dockerfile-check, compose-check, workflow-check
make release-check    # the full authoritative gate ci.yml's release-policy job runs
python3 scripts/release/check_release_context.py --event-name workflow_dispatch --ref refs/heads/main
docker compose config
```

A developer who runs `make release-check` locally and sees it pass has
exercised substantially the same policy `ci.yml`'s `release-policy` job
enforces — GitHub Actions adds independent, clean-runner corroboration
(see `docs/build-security.md`'s point that CI execution on a clean runner
is valuable evidence distinct from a developer's own, possibly
cache-warmed, machine), not a different or stricter policy.
