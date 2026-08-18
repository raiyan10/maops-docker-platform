---
name: release-readiness
description: Reusable MAOps Docker release discipline for maops-docker-platform — quality, build, inspect, smoke, security, Compose, independent reviews, blocker remediation, release-check, PR, merged-main validation, and tag/release. Use before treating any version as release-ready.
---

# Release Readiness

Reusable release procedure. As of Day 1, no CI and no container registry
exist — do not claim otherwise, and do not scaffold either early. Steps
that reference a PR/tag/release describe the eventual full-portfolio
process; this repository's own current-day rule (see `.claude/CLAUDE.md`)
is: **never commit, push, tag, or publish without explicit instruction
from the user in that conversation.**

## Procedure

1. **Quality** — `make quality` (`test` + `lint` + `dockerfile-check`).
   All three must pass; a failure anywhere stops the chain.

2. **Build** — `make build`. Builds `maops-docker-platform:<VERSION>`
   from `docker/app/Dockerfile`, `VERSION`-derived, never `latest`. See
   `docker-build-validation` for the full build/inspection procedure.

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

6. **Compose** — `docker compose config` at minimum (part of `make
   release-check`); a full `up`/health/functional/`down` cycle (see
   `compose-validation`) should also be run at least once per release,
   even though it isn't wired into the automated `make release-check`
   target (starting a long-lived service isn't appropriate for every
   invocation of that target).

7. **Independent reviews** — before treating a version as release-ready,
   route the diff through the relevant project agents
   (`docker-architect`, `container-security-reviewer`,
   `compose-platform-engineer`, `docker-test-engineer`,
   `release-engineer`) for a second opinion beyond the automated checks.

8. **Blocker remediation** — fix anything a review or check flagged, then
   re-run the affected steps (not just the one that failed — a fix can
   have side effects on earlier steps).

9. **`make release-check`** — the single composed gate
   (`quality -> build -> inspect -> smoke -> security-check -> compose
   config`). Every failure must propagate; nothing in this chain may
   silently swallow a nonzero exit code.

10. **PR** — only when the user explicitly asks for one. Do not create a
    GitHub repository, PR, tag, or release on your own initiative.

11. **Merged-main validation** — once a PR exists and is merged (future
    day, explicit instruction only), re-run this entire procedure against
    `main` before tagging — a passing PR branch is not itself proof that
    `main` post-merge is releasable.

12. **Tag/release** — only when the user explicitly asks for it, and only
    after step 11 passes on `main`.

## What this skill does not cover

Publishing to a registry (GHCR, Docker Hub) and CI workflow configuration
are explicitly out of scope until a later day's scope adds them — this
skill must not be used to justify adding either early.
