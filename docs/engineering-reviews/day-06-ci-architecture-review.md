# Day 6 CI/CD Architecture Review — v0.6.0

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering` (PR #6)
Target: `v0.6.0`
Reviewer: independent DOCKER-ARCHITECT reviewer (review only — no
implementation, workflow, test, or prior review file was modified; no
commit/push/merge/tag/`workflow_dispatch` was performed).
Date: 2026-08-26.

## Scope

This review is scoped to delivery-plane architecture (`.github/workflows/`,
the Makefile as release contract, `scripts/ci/`, `scripts/release/`) and to
confirming the runtime plane (`docker/app/Dockerfile`, `compose.yaml`,
PID 1/process design) is genuinely unchanged in spirit apart from the one
declared exception (the Day 6 `security-patch` overlay stage). It does not
re-adjudicate reliability-scenario correctness, SBOM/Trivy policy content,
or test-suite completeness — those are other reviewers' domains.

## Evidence gathered independently

- Read: `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
  `Makefile`, `VERSION`, `docker/app/Dockerfile`,
  `security/runtime-patches.lock`, `scripts/ci/check_workflows.py`,
  `scripts/release/check_release_context.py`, `scripts/lint/check_dockerfile.py`,
  `scripts/build/image_audit.py`, `README.md`, `docs/ci-cd.md`,
  `docs/build-security.md`, `docs/architecture.md`, `docs/roadmap.md`,
  `docs/releases/v0.6.0.md`, `docs/engineering-reviews/day-05-release-readiness.md`,
  `docs/engineering-reviews/day-06-bootstrap-readiness.md`.
- `git diff main...HEAD --stat` and targeted `git diff main...HEAD --` for
  `Makefile`, `compose.yaml`, `docker/app/Dockerfile`, `.dockerignore`,
  `README.md`, `docs/build-security.md` — used to independently verify
  "runtime plane unchanged" claims rather than accept them on the docs'
  word.
- `gh pr checks 6`, `gh run list --branch feature/day-6-cicd-release-engineering
  --workflow CI --limit 10`, and `gh run view <id> --log-failed` for all
  three cited run IDs (`32938805880`, `32960673438`, `32967457379`) —
  confirmed the failure/remediation narrative against real GitHub Actions
  logs, not merely the documentation's account of it.
- `make workflow-check` and `make dockerfile-check`, run locally against
  the current worktree — both pass (12/12 and 12/12 respectively).
- Independent live digest resolution: `docker buildx imagetools inspect`
  and `docker pull` against both pinned digests
  (`python:3.13-slim@sha256:ffb752e1...` and
  `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c...`), and
  against the current floating tags, to check freshness and confirm the
  Dockerfile's own claimed `linux/amd64` manifest digest for Distroless.

## 1. Base image assessment

**Three-stage structure verified.** `docker/app/Dockerfile` has exactly
three `FROM` lines (lines 29, 70, 121): `python:3.13-slim` (builder,
filesystem preparation only — `COPY` of `app/`/`gateway/`/`state/`/
`VERSION` and one `RUN mkdir -p /data && chown 10001:10001 /data`, nothing
else installed), `python:3.13-slim` again (the new `security-patch` stage,
reusing the identical pinned digest — no second base image introduced),
and `gcr.io/distroless/python3-debian13:nonroot` (final runtime). All
three are pinned `tag@sha256:digest`, never `:latest`.
`scripts/lint/check_dockerfile.py::check_from()` statically enforces
exactly this three-`FROM` shape (line 220: "expected exactly 3 FROM
instructions (builder + security-patch + final)"), and
`check_no_run_in_final_stage()` enforces no `RUN` in the last stage — I
confirmed by direct read that the final stage (lines 121–210) contains
`LABEL`/`WORKDIR`/`ENV`/`COPY`/`USER`/`EXPOSE`/`HEALTHCHECK`/
`ENTRYPOINT`/`CMD` only, no `RUN`, consistent with Distroless having no
shell to run one against. `RUN` appears only in the builder (line 46) and
`security-patch` (lines 95–99) stages, which is correct and expected.

**Digest verification — independently confirmed, not trusted.** I ran
`docker buildx imagetools inspect` and `docker pull` against both pinned
digests directly (not `docker manifest inspect`, which — as a known CLI
quirk unrelated to this repo — reported "manifest verification failed"
for both digests even though they resolve and pull correctly via
`buildx imagetools`/`docker pull`; a reviewer trusting `docker manifest
inspect` alone here would have wrongly concluded the pins were broken).
Both digests resolve and pull successfully. The Distroless pin's
documented `linux/amd64` manifest digest
(`sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c`,
Dockerfile lines 106–107) is **exactly** what `buildx imagetools inspect`
reports for that digest today — the claim is accurate, not invented.

**Finding (Info): base-image pins are now stale relative to today's
floating tags, and this PR did not re-resolve them.** Resolving the plain
tags today shows both have moved past the pins carried over unchanged from
Day 4 (dated 2026-08-18/re-confirmed 2026-08-20 in the Dockerfile's own
comments): `python:3.13-slim` now resolves to index digest
`sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4`
(vs. pinned `sha256:ffb752e1...`), and
`gcr.io/distroless/python3-debian13:nonroot` now resolves to index digest
`sha256:6bfc400d0a6d89f50f5bbc0a4b4ff57214ae5c01647c3a74c2a0c8d830b4cc00`
(vs. pinned `sha256:4376456c...`). This is expected and not itself a
defect — digest pinning is specifically designed to be immune to a moving
tag, and the whole reason the `security-patch` overlay stage exists is
that the pinned Distroless build already lags upstream. But it does mean
the emergency overlay is being layered onto an increasingly stale base
rather than onto the freshest available Distroless build, which may
already carry additional (or the same) fixes. This is not a Day 6
blocker — re-resolving a base digest is exactly the kind of decision this
project requires to be "equally explicit, re-verified, and documented"
(the Distroless/`debug` guardrail this review's brief calls out), not done
reflexively under release pressure. I recommend it as a near-term,
explicitly-decided follow-up (see Recommended order, §6), not a same-PR
requirement.

**Never `debug`/`debug-nonroot`.** Confirmed: the final `FROM` uses only
the `nonroot` tag, and `scripts/lint/check_dockerfile.py` rejects any
image reference outside the approved repository/tag pin
(`check_expected_pin`-style logic, lines 169–206). No drift toward the
shell-bearing `debug` variant.

**`security-patch` stage assessment: architecturally clean, not a
hack.** Three properties support this: (1) it reuses the *same*
digest-pinned `python:3.13-slim` image already present in the Dockerfile
— no new base image, no new trust root; (2) it fetches exactly one
official Debian Security `.deb` via `ADD --checksum=sha256:...` from an
immutable `snapshot.debian.org` timestamped URL, which BuildKit itself
refuses to build if the byte content doesn't match — this is stronger
than a plain `curl | dpkg -i` and is not achievable via `apt-get upgrade`
against a moving mirror; (3) it never runs `apt-get`/`dpkg -i` (no package
database mutation) — only `dpkg-deb -x`/`-e` into a scratch
`/patch-root` tree that the final stage `COPY --from`s verbatim, the same
filesystem-preparation pattern the `builder` stage already uses for
`/data`. The base digest itself is not changed by the overlay (verified:
the final stage's own `FROM` line, and its own comment, are byte-identical
to the Day 4 Dockerfile apart from the added explanatory comment — see
`git diff main...HEAD -- docker/app/Dockerfile`). The overlay is
cross-checked in two independent, automated places rather than merely
declared: `scripts/lint/check_dockerfile.py` checks the `ADD --checksum=`
URL/SHA256 against `security/runtime-patches.lock` exactly (verified: I
read the matching `LIBSSL_URL`/`LIBSSL_DEB_SHA256` values in both files —
they match byte-for-byte), and `scripts/build/image_audit.py` checks the
*built image's* dpkg `status.d` version string and the two shared
libraries' live content hashes against the same lock file. This is a
real, three-layer [A]/[B]/[D]-style proof chain, not a metadata-only
claim.

## 2. Build context / layering findings

No changes to `.dockerignore` in this PR (confirmed via `git diff
main...HEAD -- .dockerignore`: empty diff) — it already excludes `.github`
and other repository/dev-only content with genuinely recursive patterns.
Layer ordering is unaffected by the new stage: dependency/setup work
(`mkdir`/`chown` for `/data`, the patch extraction) still precedes the
frequently-changing `COPY app/ ./app/` etc. in the builder stage, and the
final stage's own `COPY --from=` ordering places the rarely-changing
security-patch payload copies (lines 179–185) after the application-source
copies (lines 165–169) — a reasonable, if minor, layer-cache ordering; it
does not affect correctness. No unnecessary layers were introduced: the
`security-patch` stage's `RUN` is a single compound command (one layer),
matching the project's existing style.

## 3. PID 1 / process / signal-handling findings

Unaffected by this PR. `ENTRYPOINT ["/usr/bin/python3.13"]` (absolute
interpreter path, exec form) and `CMD ["-m", "app"]` are byte-identical to
the pre-Day-6 Dockerfile (confirmed via diff — no lines changed in this
region except added comments). No `RUN` was added to the final stage, so
the "no shell to run one against" invariant this section polices remains
satisfied. I did not re-verify the SIGTERM/`HTTPServer.shutdown()`
mechanism itself in `app/gateway/state`'s `server.py` — that logic is
untouched by this PR's diff (`git diff main...HEAD --stat` shows no
`server.py` changes; only `test_server.py`/`test_gateway_platform_config.py`
gained new tests), and Day 5's reviewers already adjudicated it. This
review's job per its brief is only to confirm the process model *this*
PR didn't quietly change it — confirmed it didn't.

## 4. OCI metadata findings

Unaffected: `LABEL org.opencontainers.image.*` (lines 132–136) is
byte-identical to the pre-Day-6 file. `org.opencontainers.image.source`
still points at `https://github.com/raiyan10/maops-docker-platform` —
this review did not independently re-verify whether that repository now
exists (a real GitHub PR #6 and three real run IDs against
`raiyan10/maops-docker-platform` were directly observed via `gh` in this
session, which is at minimum strong evidence a real repository now backs
this URL, unlike the "no GitHub repository exists yet" caution baked into
this agent's general brief for earlier days). No build-date, random build
identifier, hostname, or absolute workstation path was found embedded in
any `LABEL`/`ARG`/`ENV` in the final stage.

## 5. Docker-vs-Compose boundary findings

Clean. `compose.yaml`'s only change in this PR is the `VERSION` fallback
default bump (`0.5.0` → `0.6.0`) in three services' `build.args` and
`image:` interpolation — a version-bump mechanic, not a topology,
hardening, or resource-policy change (confirmed via `git diff
main...HEAD -- compose.yaml`: exactly six one-line changes, all
`${VERSION:-0.5.0}` → `${VERSION:-0.6.0}`). Runtime/deployment concerns
(`read_only`, `cap_drop`, `security_opt`, `stop_grace_period`, resource
limits, restart policy) remain entirely in `compose.yaml`, untouched by
this PR. Image-level concerns (base images, build stages, the security
overlay) remain entirely in the Dockerfile. No boundary crossing found.

## 6. Delivery-plane architecture

**`ci.yml`**: two jobs, `quality` (fast, Docker-free — `make quality`)
then `release-policy` (`needs: quality`, the full `make release-check`).
Triggers: `pull_request` → `main`, `push` → `main`, `workflow_dispatch`.
Deliberately not `pull_request_target` (correct — avoids the
secrets-against-untrusted-code hazard). Concurrency cancels obsolete PR
runs; permissions are `contents: read` workflow-wide with no widening in
either job. This is a sound, minimal two-job pipeline; the "why quality
runs twice" trade-off documented in `docs/ci-cd.md` (accept ~1 minute of
redundant test time rather than hand-duplicate a subset of
`release-check`'s dependency chain in YAML) is the architecturally correct
call — a hand-listed CI-only subset of `release-check`'s prerequisites is
exactly the kind of drift-prone duplication this project's own philosophy
rejects.

**`release.yml`**: `validate` (`needs`: none, runs on both
`workflow_dispatch` and tag-push) → `publish` (`needs: validate`,
gated by an `if:` that repeats `success()` explicitly alongside
`github.event_name == 'push'` and `startsWith(github.ref, 'refs/tags/')`).
This is correct GitHub Actions practice — an `if:` on a job replaces the
default "needs succeeded" check, so omitting `success()` here would be a
real defect (a failed `validate` could otherwise still let `publish` run
under some `if:` formulations); the workflow gets this right, and
`scripts/ci/check_workflows.py::check_manual_dispatch_cannot_publish()`
statically enforces it never regresses. `permissions: contents: write`
exists in exactly one place (the `publish` job), confirmed both by direct
read and by `check_release_permissions_scoped()`'s static single-write-
scope assertion.

**Job ordering / fail-fast**: sound. `quality` fails fast and cheaply
before any Docker image is built; `release-policy`/`validate` only reach
expensive Docker-based gates after the fast gate passes. `publish` cannot
run without `validate` succeeding.

## 7. Makefile as release contract / local-CI parity

Confirmed by direct diff: **zero changes to the `build:` target itself**
(`git diff main...HEAD -- Makefile` shows the `--no-cache`,
`--build-arg SOURCE_DATE_EPOCH=...`, `--output
type=docker,rewrite-timestamp=true,...,dest=...`, and `docker load`
commands are byte-identical to pre-Day-6). Both `ci.yml` and `release.yml`
invoke `make release-check`/`make quality` verbatim — grep of both
workflow files confirms no `docker build`/`docker buildx build` command is
hand-rolled anywhere in either file; the only `docker`/`docker buildx`
invocations outside `make` are the diagnostic `docker version`/`docker
buildx version`/`docker buildx ls` steps and the job-scoped builder
create/remove steps, none of which perform a build. This satisfies the
brief's explicit requirement: CI orchestrates `make` targets, it does not
reimplement build/inspection logic in workflow YAML. Local/CI parity is
therefore real, not aspirational — a developer's `make release-check` and
CI's `release-policy`/`validate` job exercise the textually identical
Makefile targets.

## 8. Buildx portability remediation — technical assessment

**Root cause, independently confirmed real.** I pulled the actual failure
log for run `32938805880` via `gh run view --log-failed`: `ERROR: Docker
exporter is not supported for the docker driver` immediately after `make
build`'s `docker buildx build ... --output type=docker,rewrite-
timestamp=true,...` invocation — an exact match for `docs/ci-cd.md`'s
account. This is a genuine GitHub-hosted-runner environment property (no
containerd image store under Buildx's default `docker` driver), not a bug
in this repository's build logic.

**The fix**: both `ci.yml`'s `release-policy` job and `release.yml`'s
`validate` job now run, immediately before `make release-check`:

```yaml
BUILDER_NAME="maops-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
docker buildx create --driver docker-container --name "${BUILDER_NAME}" --use
docker buildx inspect "${BUILDER_NAME}" --bootstrap
```

followed by an `if: always()` cleanup step that checks existence before
`docker buildx rm`. This is the correct fix, for the following reasons:

1. **It is CI environment preparation, not a build-logic change.** The
   Makefile's `build:` target is untouched; `docker buildx build` in that
   target always targets whichever builder is currently `--use`d, so
   selecting a capable builder *before* invoking the existing, unmodified
   command changes nothing about *what* is built or *how* its
   determinism is achieved — only *where* the build executes.
2. **Naming discipline matches project convention.** `maops-ci-<run
   id>-<attempt>` is derived only from GitHub-controlled identifiers,
   never PR/tag/title text (which could otherwise make the builder name
   attacker-influenceable), and carries the project's `maops-` prefix
   consistent with every other disposable resource this repo creates.
3. **Cleanup is job-scoped and leak-free.** Because the builder name is
   unique per `(run id, attempt)` and removed with `if: always()` (so
   cleanup runs even after a failed `make release-check`), no builder
   state persists across jobs or runs. `scripts/ci/check_workflows.py
   ::check_buildx_container_builder_before_release_check()` statically
   enforces both the ordering (builder created before `make
   release-check`) and the `if: always()` cleanup shape — this is not
   merely a convention, it is a machine-checked invariant that runs on
   every future workflow-file change. I ran `make workflow-check` locally
   and confirmed it passes (12/12) against the current committed files.
4. **No socket-mount, `--privileged`, or scope-widening was introduced.**
   `docker buildx create --driver docker-container` uses the Docker CLI
   and daemon already available to the job; it does not require any new
   permission, secret, or host mount beyond what the runner already
   grants a job with `contents: read`.

**Deterministic-build contract preservation — explicitly verified, not
assumed.** `--no-cache`, `SOURCE_DATE_EPOCH` (derived from `git log -1
--format=%ct`, never wall-clock), `rewrite-timestamp=true`, the
`type=docker,...,dest=<tar>` archive-export/`docker load` round-trip are
all unchanged in the Makefile. The `docker-container` driver is a
*more* capable builder with respect to this exact exporter (it is, in
fact, the driver documented elsewhere in this project's own
`docs/build-security.md` as the one needed for the reproducible-build
export mode in the first place) — switching to it does not relax any
determinism property; if anything it makes the previously-implicit
"this exporter needs `docker-container` or a containerd-backed `docker`
driver" requirement explicit and enforced in CI rather than accidentally
satisfied by a developer's local Docker Desktop configuration. This is
corroborated by real evidence, not just architectural reasoning: run
`32960673438`'s log (independently pulled via `gh run view --log-failed`)
shows the build succeeded under the new builder and the full release-check
chain — including `reproducibility-check`, which independently performs
two clean `--no-cache` builds and diffs image IDs/RootFS/manifests —
progressed correctly before failing much later, inside Scenario 2 of
`reliability-check`, on an unrelated `docker update` cgroup race. Run
`32967457379` (current HEAD, passing) confirms the full chain, including
`reproducibility-check`, completes successfully under the
`docker-container` builder.

**Answering the brief's direct question — was adapting the CI builder
the right choice, rather than weakening the deterministic build
contract?** Yes, unambiguously. The alternative — relaxing the
`--output type=docker,rewrite-timestamp=true` exporter, dropping
`--no-cache`, or accepting a CI-only "looser" build path — would have
directly contradicted Day 4's central claim (two independent builds
produce a byte-identical image ID) for the one environment (a clean,
attacker-uncontrolled GitHub-hosted runner) where that claim's
independent corroboration matters most. The chosen fix instead treats the
failure as exactly what it was: a Buildx *builder capability* mismatch
between "what runs on a developer's Docker Desktop" and "what runs on
GitHub's hosted runner," solved by asking for a capable builder rather
than asking less of the build. The fix is narrowly scoped (job-local,
torn down after use), statically enforced against regression
(`check_workflows.py`), and independently corroborated by a real passing
`reproducibility-check` run on the actual target CI environment. This is
the textbook "fix the environment gap, not the policy" resolution the
project's own stated philosophy (`docs/ci-cd.md`, `.claude/CLAUDE.md`)
calls for, and it was executed correctly.

## 9. Cleanup discipline

No leaks found. The only new Docker-adjacent resource this PR's CI
tooling creates is the job-scoped Buildx builder, which is (a) uniquely
named per run/attempt, (b) removed with `if: always()`, and (c)
statically checked for both properties by `check_workflows.py`. No new
container, network, image, or Compose project name pattern was introduced
by this PR beyond what `reliability_check.py`'s existing
`maops-reliability-*` convention already covered (its only Day 6 change
is the retry-classifier logic, not new resource creation). `make clean`
was not modified in this PR and needed no update, since no new
project-owned resource category was introduced.

## 10. No unnecessary runtime changes

Confirmed by direct diff review: the only `docker/app/Dockerfile` change
is the additive `security-patch` stage plus the corresponding `COPY
--from=security-patch` lines and comments in the final stage — no base
digest change, no `ENTRYPOINT`/`CMD`/`USER`/`HEALTHCHECK` change, no
`ENV` change. `compose.yaml`'s only change is the `VERSION` fallback
literal. No `app/`, `gateway/`, or `state/` source file appears in `git
diff main...HEAD --stat` except test files (`tests/test_server.py`,
`tests/test_gateway_platform_config.py`, both additive test-coverage
changes, not behavior changes to the services themselves — confirmed by
reading the diff context, which adds new test classes/cases rather than
modifying existing assertions against production code). The application
runtime behavior is genuinely untouched.

## 11. Day 7+ scope leakage

None found. `scripts/ci/check_workflows.py::check_no_day7_plus_tooling()`
statically forbids `cosign`, `slsa`, `sigstore`, `kubectl`, `helm`,
`argocd`/`argo-cd`, `terraform`, `ansible`, `prometheus`, `grafana`,
`opentelemetry`, and `kubernetes` from appearing in either workflow file,
and `check_no_registry_publication()` forbids `docker login`/`docker
push`/`ghcr.io`/`docker.io/`/`public.ecr.aws`/`azurecr.io`/
`registry.hub.docker.com`. I independently grepped both workflow files
and confirmed none of these patterns appear outside of comment text
explicitly naming what is being avoided (and confirmed the checker itself
strips comments before scanning, so it isn't fooled by that same
explanatory prose — verified by reading `_strip_comments()`). No SBOM
signing/attestation, no multi-arch/multi-platform publication, no
Kubernetes/Helm/GitOps tooling, and no registry credential of any kind
exists in either workflow. This is a correctly-scoped Day 6 delivery
plane, not a Day 7 hardening/portfolio-showcase scope creeping in early.

## Findings summary

| # | Severity | Finding |
|---|---|---|
| F-1 | Info | Both base-image pins (`python:3.13-slim` builder, `gcr.io/distroless/python3-debian13:nonroot` final) are unchanged since Day 4 (2026-08-18/20) and have since been superseded by newer builds under the same floating tags, independently confirmed via live registry resolution during this review. Not a Day 6 defect — digest pinning is working as designed, and the emergency overlay stage exists precisely because Distroless already lags upstream — but a documented, explicit decision on whether/when to advance either base digest (potentially superseding the manual libssl overlay) is worth scheduling rather than letting the overlay linger indefinitely on an aging base. |
| F-2 | Info | `docs/releases/v0.6.0.md`'s "Validation" section does not explicitly flag that `release.yml`'s `workflow_dispatch` dry run has not yet been executed against `main` (structurally impossible pre-merge, per this task's own context, and correctly not treated as a defect). Recording this explicitly in the release notes (or in a pre-tag checklist) would make the "reviews → merge → main CI → dry run → tag" sequence self-documenting for a future reader of the release notes alone. |
| — | — | No Critical, High, Medium, or Low findings from a docker-architect lens. All checked invariants (three-stage structure, digest pinning, no-`RUN`-in-final-stage, PID 1/process model, OCI metadata, Docker/Compose boundary, Makefile-as-contract, local/CI parity, Buildx remediation, deterministic-build preservation, cleanup discipline, no runtime drift, no Day 7+ leakage) were independently verified against real source, real `gh` evidence, and real local command execution — not accepted on the documentation's word — and all held.

## Recommended implementation order for any fixes

Given no Critical/High/Medium findings exist from this lens, the
following are advisory, not blocking, ordered by decreasing priority:

1. **(Low-priority, near-term)** Schedule an explicit, documented decision
   on re-resolving the `python:3.13-slim`/Distroless base digests
   (F-1) — this is naturally the kind of review that belongs alongside
   the next scheduled vulnerability-policy check-in (`docs/supply-chain.md`),
   not this PR.
2. **(Cosmetic)** Optionally add one sentence to `docs/releases/v0.6.0.md`
   or a pre-tag checklist noting the `workflow_dispatch` dry run is a
   mandatory post-merge, pre-tag step (F-2) — purely documentation, no
   code change.

Neither item blocks merge from this reviewer's lens.

## Verdict

**APPROVE** for merge, from the docker-architect lens specifically.

### PRE-TAG conditions (post-merge, before `v0.6.0` is tagged)

1. Execute the real `workflow_dispatch` release-candidate dry run against
   `main` after this PR merges (structurally required — `workflow_dispatch`
   cannot fire until the workflow file exists on the default branch — and
   already correctly identified as non-blocking for this PR by the task's
   own context). Confirm it reports `DRY RUN` and cannot reach the
   `publish` job, then only tag `v0.6.0` after it passes.
2. No Dockerfile/Compose/PID-1/OCI-metadata change is required before
   tagging from this review's findings — F-1 and F-2 above are
   post-tag-eligible follow-ups, not gates.
