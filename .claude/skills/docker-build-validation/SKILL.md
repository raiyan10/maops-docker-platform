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
   leaked in**, at any nesting depth. The Day 4 Distroless final runtime
   has no `find`/shell at all, so this is now a `docker exec` + stdlib
   Python `rglob()` walk (or `docker cp` + a host-side walk — both
   `scripts/verify/security_check.py`'s `check_image_content_recursive()`
   and `scripts/build/image_audit.py` use the `docker cp` form):
   ```bash
   docker run -d --name probe "$IMAGE"
   docker exec probe /usr/bin/python3.13 -c "
   from pathlib import Path
   hits = [p for p in Path('/app').rglob('*') if p.name == '__pycache__' or p.suffix in ('.pyc', '.pyo')]
   print(hits)
   "
   docker rm -f probe
   ```
   This must print `[]`. A one-level directory listing is not sufficient
   proof — a genuinely recursive walk is required because
   `.dockerignore`'s recursive-glob correctness (`**/__pycache__/`, not
   `app/__pycache__/*.pyc`) is exactly what a shallow check would miss.
   For a real regression proof, temporarily create a nested probe (e.g.
   `app/nested/deep/__pycache__/probe.pyc`) before an `--no-cache` build,
   confirm it does *not* appear in the built image, then remove the probe
   file(s) again.

5. **Remember the `ENTRYPOINT` override gotcha**: this image sets
   `ENTRYPOINT ["/usr/bin/python3.13"]` (the absolute interpreter path —
   the Distroless final runtime has no shell for PATH resolution) with
   `CMD ["-m", "app"]` as the default, so `docker run <image> <cmd>`
   *appends* `<cmd>` as arguments to the entrypoint rather than replacing
   it — a bare `docker run <image> /usr/bin/python3.13 -m app` actually
   runs `/usr/bin/python3.13 /usr/bin/python3.13 -m app` and fails. Use
   `docker run --rm --entrypoint <cmd> "$IMAGE" ...` whenever you need to
   run something other than the default server inside the image — but
   note the Distroless final runtime has **no shell at all** (not even
   `sh`), so `--entrypoint sh -c "..."` (a common override pattern against
   the old slim runtime) itself fails with "no such file or directory";
   override with the absolute Python interpreter instead
   (`--entrypoint /usr/bin/python3.13 "$IMAGE" -c "..."`), or omit any
   command entirely and rely on the image's own default `CMD` (`-m app`).
   To run the other two roles directly (rare - Compose is the documented
   way, since `gateway` needs `UPSTREAM_HOST` and `state` needs a `/data`
   mount to be meaningful), override `CMD` explicitly: `docker run --rm
   "$IMAGE" -m gateway` / `-m state`.

6. **Build deterministically (Day 4)** — `make build` (not a plain
   `docker build`) uses BuildKit's reproducible-builds export mode:
   ```bash
   docker buildx build --no-cache \
       --build-arg VERSION="$VERSION" \
       --build-arg SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)" \
       --output type=docker,rewrite-timestamp=true,name="$IMAGE",dest=/tmp/image.tar \
       -f docker/app/Dockerfile .
   docker load -i /tmp/image.tar
   ```
   `SOURCE_DATE_EPOCH` is the current commit's own timestamp, never the
   wall clock. See `docs/build-security.md` for why the plain
   `docker buildx build --output type=docker,rewrite-timestamp=true`
   form (no `dest=`) fails on this project's `docker`-driver builder
   (`exporter option "rewrite-timestamp" conflicts with "unpack"`), and
   why the tar+`docker load` round-trip above is what actually works.

7. **Prove reproducibility, not just determinism-in-principle** —
   `make reproducibility-check` (`scripts/build/reproducibility_check.py`)
   independently performs two clean, `--no-cache` builds with two
   disposable, uniquely tagged images (never the real release tag) and
   compares: exact image ID, RootFS diff IDs, `Config`/OCI labels, and a
   normalized content-addressed filesystem manifest of `/app`
   (path/type/mode/uid/gid/symlink-target/SHA-256 content hash -
   deliberately excluding mtime). All four must agree; a mismatch prints
   the first manifest divergence found rather than a bare failure.

8. **Prove image-level application-source immutability (Day 4)** —
   `app/`/`gateway/`/`state/`/`VERSION` are root-owned in the image (no
   `--chown` on the final stage's `COPY --from=builder` instructions).
   Prove this with a real write attempt against a container started with
   *no* hardening flags at all — via `docker exec` (no shell to
   `--entrypoint sh` into):
   ```bash
   docker run -d --name probe "$IMAGE"
   docker exec probe /usr/bin/python3.13 -c "open('/app/app/server.py', 'a').write('x')"  # must fail
   docker exec probe /usr/bin/python3.13 -c "open('/app/newfile', 'w').write('x')"         # must fail
   docker rm -f probe
   ```
   `/data` remains the one deliberate `10001:10001`-owned, writable
   exception (`state`'s persisted-volume mount point).

9. **Prove shell/package-manager absence (Day 4)** — the final runtime
   must genuinely lack `/bin/sh`/`/bin/bash`, `apt`/`dpkg`, and
   importable `pip`/`setuptools`:
   ```bash
   docker exec probe /bin/sh -c "echo probe"   # must fail: no such file or directory
   docker exec probe /usr/bin/python3.13 -c "import pip"  # must raise ImportError
   ```

10. **Run the project-specific image policy audit** —
   `make image-audit` (`scripts/build/image_audit.py`) checks the above
   plus exact tag/version, non-root `Config.User`, a truthful
   `org.opencontainers.image.source` (cross-checked against the real git
   remote), entrypoint/default command, all three service packages
   present, `/data` ownership, and absence of repository-only/secret-
   shaped/setuid-setgid/world-writable content. It reuses
   `security_check.py`'s own image-inspection functions rather than
   duplicating them.

## What this does not cover

Runtime hardening (capabilities, read-only rootfs, no-new-privileges) is
`container-security-validation`'s job, not this skill's. Compose-level
lifecycle is `compose-validation`'s job. SBOM generation and vulnerability
scanning are a separate concern - see `docs/supply-chain.md` and
`scripts/security/` (no dedicated skill file for these; this skill and
`release-readiness` both reference them where relevant, per
`.claude/CLAUDE.md`'s "do not add a separate SBOM agent/skill" guidance).
This skill is only about building the image correctly, deterministically,
and proving its *contents* are what they should be.
