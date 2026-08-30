---
name: release-readiness
description: Reusable MAOps Docker release discipline for maops-docker-platform — quality, build, inspect, smoke, security, Compose, independent reviews, blocker remediation, release-check, PR, merged-main validation, and tag/release. Use before treating any version as release-ready.
---

# Release Readiness

Reusable release procedure. As of Day 6, GitHub Actions CI/CD exists
(`.github/workflows/ci.yml`, `.github/workflows/release.yml` — see
`docs/ci-cd.md`), but no container registry publication exists and none is
planned for the full seven-day arc — do not claim otherwise, and do not
scaffold registry publishing. This repository's own current-day rule (see
`.claude/CLAUDE.md`) is unchanged by Day 6: **never commit, push, tag, or
publish without explicit instruction from the user in that conversation** —
CI/CD automates *validation*, not the decision to actually cut a release.

## Procedure

1. **Quality** — `make quality` (`test` + `lint` + `dockerfile-check` +
   `compose-check` + `workflow-check`, Day 6). All five must pass; a
   failure anywhere stops the chain.

2. **Build** — `make build`. Builds `maops-docker-platform:<VERSION>`
   from `docker/app/Dockerfile` (Day 4: a two-stage build — a
   `python:3.13-slim` builder feeding a Distroless
   `gcr.io/distroless/python3-debian13:nonroot` final runtime; Day 6: a
   third, build-time-only `security-patch` stage overlays a
   checksum-pinned Debian-security package fix, see
   `security/runtime-patches.lock`), `VERSION`-derived, never `latest`.
   See `docker-build-validation` for the full build/inspection procedure.

3. **Inspect** — `make inspect`. Captures `docker image inspect`/`ls`/
   `history` output; record the canonical size metric used, without
   inventing an explanation for any discrepancy between commands.

4. **Smoke** — `make smoke`. Runs `scripts/smoke/container_smoke.py`
   against the real built image in a throwaway, uniquely-named container
   on a dynamic loopback port; verifies `/healthz`, `/readyz`, `/info`
   (including the expected version), and non-root runtime, then cleans
   up on success or failure.

5. **Security** — `make security-check`. Runs
   `scripts/verify/security_check.py`; see `container-security-
   validation` for the full [A]/[B]/[C]/[D] evidence procedure. All
   checks must pass, including the kernel/process-level ones — a
   config-only pass is not sufficient.

6. **Compose** — `make compose-test`
   (`scripts/compose/compose_integration.py`) is now part of the
   automated `make release-check` chain: it runs the real three-service
   stack under a uniquely named project, proves the health-gated
   `state -> app -> gateway` startup ordering, real network isolation
   (`gateway`<->`state` unreachable both directions, plus - Day 4 - a
   real, live `docker network inspect` proof of `backend`/`edge`'s
   `Internal` flag), the full `gateway -> app -> state` persistence path
   (including survival across container recreation and a full
   `compose down`/`up` cycle with the volume retained), the
   state-stop/degrade, state-start/recover scenario, and inspects the
   real Compose-created containers' hardening (including a real,
   role-aware [D] rootfs-write-rejection proof for every container). A
   real `SIGTERM` sent mid-run is now caught and still runs the script's
   own teardown (Day 4, closes Day 3 finding A-5) — this closes the Day 1
   gap where Compose verification was manual-only (see
   `compose-validation`). Still worth a manual walkthrough at least once
   per release for a human-observed sanity check, but it is no longer the
   only evidence.

7. **Reliability (Day 5)** — `make reliability-check`
   (`scripts/reliability/reliability_check.py`), now part of the automated
   `make release-check` chain: proves CPU/memory/PID resource limits and
   the bounded `on-failure:3` restart policy are genuinely applied to real
   Compose-created containers (not merely declared), a real
   `docker pause`/`unpause` adversarial proof that the Day 5 timeout-
   hierarchy invariant (`config/platform.json`'s
   `gateway_upstream_timeout_seconds` >
   `state_dependency_timeout_seconds` + `timeout_safety_margin_seconds`,
   closing Day 3 finding A-6) genuinely bounds a `state` outage's
   failure-detection latency, a real kernel-initiated OOM-kill (a genuine
   SIGKILL, deliberately not `docker kill`/`docker stop` — those are
   exempted from the restart-policy engine, see `docs/reliability.md`)
   crash on `state` with automatic (never manual) bounded restart and
   persisted-value survival, a real intentional-stop-does-not-auto-restart
   proof, and
   `app`-down/`gateway`-down failure isolation. Does not duplicate
   anything `compose-test` already proves (topology, DNS, network
   segmentation, persistence, config mounting, runtime hardening, the H-1
   matrix, startup ordering, the existing `state`-stop/degrade/recover
   scenario) — see `docs/reliability.md`.

8. **Image audit and reproducibility (Day 4)** — `make image-audit`
   (`scripts/build/image_audit.py`) validates release-image-specific
   invariants (exact tag/version, non-root user, truthful OCI metadata,
   all three service packages present, `/data` ownership, image-level
   application-source immutability, absence of repository-only/secret-
   shaped/setuid-setgid/world-writable content, and Distroless-specific
   proof of shell absence, package-manager absence, pip/setuptools
   absence, and the expected `/usr/bin/python3.13` interpreter). `make
   reproducibility-check` (`scripts/build/reproducibility_check.py`)
   independently proves two clean builds from the identical source tree
   produce the same image ID — treat a claimed "reproducible build" as
   unproven until this actually runs and passes.

9. **Supply chain (Day 4)** — `make sbom`/`sbom-check` (Syft, SPDX JSON)
   and `make vuln-scan` (Trivy, JSON) scan the exact release image via a
   `docker save` archive, using scanner images pinned by exact digest in
   `security/scanners.lock`, neither ever given the Docker socket.
   `vuln-scan` enforces an explicit policy (any CRITICAL, or any HIGH
   with a fix available, fails the gate) — see `docs/supply-chain.md`:
   the Distroless-based release image genuinely **passes** this policy
   (Critical=0, fixable High=0), with 15 unfixed-High findings reported
   non-blocking. Treat the specific counts as scan-time values (Trivy's
   database changes over time), not a timeless guarantee — re-run before
   trusting them again. `make supply-chain-check` composes `sbom` +
   `sbom-check` + `vuln-scan` as one convenience target outside
   `release-check`'s own chain.

10. **Independent reviews** — before treating a version as release-ready,
    route the diff through the relevant project agents
    (`docker-architect`, `container-security-reviewer`,
    `compose-platform-engineer`, `docker-test-engineer`,
    `release-engineer`) for a second opinion beyond the automated checks.

11. **Blocker remediation** — fix anything a review or check flagged,
    then re-run the affected steps (not just the one that failed — a fix
    can have side effects on earlier steps). A genuinely unavoidable
    vulnerability-policy finding (no fix available, base digest already
    current) is reported and the gate is left failing, per
    `.claude/CLAUDE.md` — never silenced via a `.trivyignore` or a
    loosened policy threshold.

12. **`make workflow-check` (Day 6)** — `scripts/ci/check_workflows.py`
    statically audits `.github/workflows/*.yml` for the security/integrity
    invariants `docs/ci-cd.md` documents (permissions, action SHA pinning,
    required triggers, manual-dispatch-cannot-publish, no registry
    publication, no Day 7+ tooling). Part of `make quality`.

13. **`make release-check`** — the single composed gate (`quality
    (test -> lint -> dockerfile-check -> compose-check -> workflow-check)
    -> build -> inspect -> image-audit -> smoke -> security-check ->
    compose-test -> reliability-check -> reproducibility-check ->
    supply-chain-check (sbom -> sbom-check -> vuln-scan) ->
    patch-lifecycle-check -> release-bundle`). Every failure must
    propagate; nothing in this chain may silently swallow a nonzero exit
    code. This is also exactly what `.github/workflows/ci.yml`'s
    `release-policy` job and `.github/workflows/release.yml`'s `validate`
    job both run — see `docs/ci-cd.md`.
    - **`patch-lifecycle-check` (Day 7)** — `scripts/security/
      patch_lifecycle_check.py` independently pulls the exact pinned
      Distroless base and proves whether `security/runtime-patches.lock`'s
      emergency overlay is still required, now redundant, or has drifted
      from its own recorded rationale. See `docs/build-security.md`.
    - **`release-bundle` (Day 7)** — `scripts/release/
      prepare_release_bundle.py` stages the flat, consumer-shaped release
      bundle and independently proves the real `sha256sum -c SHA256SUMS`
      succeeds against it. See `docs/production-readiness.md` §1.2.

14. **PR** — only when the user explicitly asks for one. Do not create a
    GitHub repository, PR, tag, or release on your own initiative. Once a
    PR is opened, `.github/workflows/ci.yml` runs automatically on
    GitHub's own runners — this is independent, clean-runner evidence, not
    a substitute for having run `make release-check` locally first.

15. **Merged-main validation** — once a PR exists and is merged, re-run
    this entire procedure against `main` before tagging (locally, and via
    `.github/workflows/ci.yml`'s own automatic run on the `push` to
    `main`) — a passing PR branch is not itself proof that `main`
    post-merge is releasable.

16. **Release-candidate dry run (Day 6)** — only when the user explicitly
    asks for it: a `workflow_dispatch` run of `.github/workflows/
    release.yml` on `main` is a safe, structurally non-publishing way to
    exercise the exact release-policy gates a real tag would, before the
    tag exists. It can never create a tag, a GitHub Release, or publish an
    image — see `docs/ci-cd.md`. Triggering a GitHub Actions workflow run
    is itself an action requiring the same explicit-instruction discipline
    as a commit/push/tag.

17. **Tag/release** — only when the user explicitly asks for it, and only
    after step 15 passes on `main`. Pushing the real `vMAJOR.MINOR.PATCH`
    tag is what triggers `.github/workflows/release.yml`'s real
    publication path (tag/`VERSION` equality, main-history ancestry,
    `make release-check`, then an automated GitHub Release with the flat,
    consumer-verifiable release bundle attached — SBOM, Trivy report, and
    a basename-only `SHA256SUMS` a consumer can run `sha256sum -c` against
    unmodified, see `docs/production-readiness.md` §1.2) — this is a
    one-way, immutable
    operation (no tag is ever moved/rewritten, no existing GitHub Release
    is ever overwritten), so treat pushing the tag with the same care as
    any other irreversible action.

## What this skill does not cover

Publishing the application container image to a registry (GHCR, Docker
Hub) is explicitly out of scope for the full seven-day arc — this skill
must not be used to justify adding it. GitHub Actions CI/CD workflow
*configuration* itself is `docs/ci-cd.md`'s domain (implemented Day 6);
this skill only describes *running* the resulting procedure, not authoring
or modifying the workflow files. Cryptographic build provenance/
attestation/signing is likewise deferred past Day 4 — see
`docs/build-security.md`.
