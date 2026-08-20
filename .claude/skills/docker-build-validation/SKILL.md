---
name: docker-build-validation
description: Reusable exact-version Docker build and image-inspection procedure for maops-docker-platform. Use when building, rebuilding, or verifying the application's Docker image, or when checking that no repository/development content or nested Python cache leaked into it.
---

# Docker Build Validation

Reusable procedure for building and inspecting the `maops-docker-platform`
image at an exact, `VERSION`-derived tag — never `latest`.

## Procedure

1. **Resolve the version and tag.**
   ```bash
   VERSION=$(cat VERSION)
   IMAGE="maops-docker-platform:${VERSION}"
   ```

2. **Build from repository root with `--no-cache`** when the goal is
   proving build-context/`.dockerignore` correctness (a cached layer can
   hide a leak that a fresh build would catch):
   ```bash
   docker build --no-cache -f docker/app/Dockerfile -t "$IMAGE" .
   ```
   `make build` does this. For everyday iteration where leak-detection
   isn't the point, a cached build is fine.

3. **Inspect the built image** — never assume, always look:
   ```bash
   docker image inspect "$IMAGE"
   docker image ls "$IMAGE"
   docker history "$IMAGE"
   ```
   `make inspect` runs all three. Record which single metric (e.g.
   `docker image ls`'s `CONTENT SIZE` column) is being treated as the
   canonical size figure in any report — `docker image ls` and `docker
   history` can present different totals for the same image (compressed
   vs. layer-sum accounting); never invent an explanation for the
   difference, just name the command/field used.

4. **Prove no repository/development content or nested Python bytecode
   leaked in**, at any nesting depth:
   ```bash
   docker run --rm --entrypoint find "$IMAGE" /app -iname '*.pyc' -o -iname '__pycache__'
   ```
   This must produce no output. A one-level directory listing is not
   sufficient proof — `find` (or an equivalent recursive walk) is
   required because `.dockerignore`'s recursive-glob correctness
   (`**/__pycache__/`, not `app/__pycache__/*.pyc`) is exactly what a
   shallow check would miss. For a real regression proof, temporarily
   create a nested probe (e.g. `app/nested/deep/__pycache__/probe.pyc`)
   before an `--no-cache` build, confirm it does *not* appear in the
   built image, then remove the probe file(s) again.
   ```bash
   docker run --rm --entrypoint find "$IMAGE" /app -type f
   ```
   should list only the application's own runtime files.

5. **Remember the `ENTRYPOINT` override gotcha**: this image sets
   `ENTRYPOINT ["python3"]` with `CMD ["-m", "app"]` as the default, so
   `docker run <image> <cmd>` *appends* `<cmd>` as arguments to the
   entrypoint rather than replacing it — a bare `docker run <image>
   python3 -m app` actually runs `python3 python3 -m app` and fails. Use
   `docker run --rm --entrypoint <cmd> "$IMAGE" ...` (as above) whenever
   you need to run something other than the default server inside the
   image, or omit any command entirely and rely on the image's own
   default `CMD` (`-m app`). To run the other two roles directly (rare -
   Compose is the documented way, since `gateway` needs `UPSTREAM_HOST`
   and `state` needs a `/data` mount to be meaningful), override `CMD`
   explicitly: `docker run --rm "$IMAGE" -m gateway` / `-m state`.

## What this does not cover

Runtime hardening (capabilities, read-only rootfs, no-new-privileges) is
`container-security-validation`'s job, not this skill's. Compose-level
lifecycle is `compose-validation`'s job. This skill is only about
building the image correctly and proving its *contents* are what they
should be.
