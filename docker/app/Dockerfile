# syntax=docker/dockerfile:1
#
# Day 4 (v0.4.0): two-stage build. The final runtime is Distroless
# (shellless, no package manager) - see docs/build-security.md for the
# full rationale (the originally planned python:3.13-slim runtime was
# rejected: 4 unfixed CRITICAL perl-base CVEs). The builder stage exists
# ONLY to prepare a filesystem tree (application source + the /data mount
# point, correctly owned) that the shellless final stage cannot prepare
# itself, since it has no RUN-capable shell/coreutils at all. No package
# is installed in the builder beyond what python:3.13-slim already ships,
# and the builder's own Python runtime never enters the final image.
#
# Day 6: a third `security-patch` stage was added between `builder` and
# the final Distroless stage - a build-time-only Debian-security package
# overlay (see the stage's own comment below and
# security/runtime-patches.lock) closing a real, currently-blocking
# vulnerability-policy finding without migrating the runtime base or
# weakening the policy. The final stage remains shellless and
# package-manager-free; the patch stage's own shell/dpkg tooling never
# enters it.
#
# --- builder: filesystem preparation only ------------------------------
#
# Resolved from the live Docker Hub registry on 2026-08-18 (re-confirmed
# unchanged 2026-08-20 - see docs/build-security.md):
#   docker pull python:3.13-slim
#   -> python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a
#   (Python 3.13.15, Debian 13 "trixie")
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

WORKDIR /app

COPY app/ ./app/
COPY gateway/ ./gateway/
COPY state/ ./state/
COPY VERSION ./VERSION

# /data is the state role's fixed persisted-volume mount point (see
# compose.yaml's `state_data` named volume). Docker populates a *new*,
# empty named volume from whatever already exists at its target path in
# the image at container-create time - so the directory must already
# exist, owned by the same non-root 10001:10001 user the process runs as.
# Prepared here (the builder still has coreutils) and copied verbatim
# into the final shellless stage below, which cannot run `mkdir`/`chown`
# itself.
RUN mkdir -p /data && chown 10001:10001 /data

# --- security-patch: Debian-security libssl3t64 overlay (Day 6, emergency)
#
# gcr.io/distroless/python3-debian13:nonroot at the digest pinned below
# ships libssl3t64 3.5.6-1~deb13u2, vulnerable to CVE-2026-14456 (a HIGH,
# fixable finding this project's vulnerability policy
# (scripts/security/check_trivy_report.py) treats as release-blocking).
# Debian Security already published the fix (3.5.7-1~deb13u2) but the
# upstream Distroless rebuild has not yet incorporated it, and this
# project's policy forbids both waiting indefinitely and weakening the
# policy (no .trivyignore, no CVE allowlist - see docs/supply-chain.md).
# This stage overlays ONLY that one official, checksum-pinned Debian
# Security package onto the still-pinned Distroless base below; it does
# not change the base digest and does not touch any other package.
#
# Reuses the same digest-pinned python:3.13-slim builder image above
# (real dpkg-deb tooling, no new base image introduced) purely as a
# build-time package-extraction stage - none of its content or its own
# Python runtime enters the final image. All pinned values (URL, exact
# SHA256, version) live in security/runtime-patches.lock, the single
# source of truth scripts/lint/check_dockerfile.py cross-checks this
# stage against and scripts/build/image_audit.py cross-checks the built
# image against - see docs/build-security.md and docs/supply-chain.md.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS security-patch

# `ADD --checksum=` (not `apt-get install`/`apt-get upgrade` against a
# moving repository) pins the exact package bytes cryptographically - the
# BuildKit frontend itself refuses the build if the downloaded content's
# SHA256 does not match. The URL is an immutable snapshot.debian.org
# archive URL (a fixed timestamp, not a mutable "current" mirror path),
# so the same bytes are fetched on every future build - see
# security/runtime-patches.lock for how this exact SHA256 was
# independently verified against Debian Security's own signed archive
# metadata before being pinned here.
ADD --checksum=sha256:916f7f40b34a06e6ebfaefcdab331bff458328411da672598f126a760472467d \
    https://snapshot.debian.org/archive/debian-security/20260825T185058Z/pool/updates/main/o/openssl/libssl3t64_3.5.7-1~deb13u2_amd64.deb \
    /tmp/libssl3t64.deb

# dpkg-deb -x extracts the real binary payload (the actual fixed
# libssl.so.3/libcrypto.so.3 and friends); dpkg-deb -e extracts the
# package's own real control metadata (`control`, describing the package
# - and `md5sums`, Distroless's own per-file content-hash record), copied
# below to Distroless's own status.d layout (`status.d/<pkg>` and
# `status.d/<pkg>.md5sums`) unmodified except for the rename - never a
# hand-written/minimal status.d entry. This stage never runs
# `apt-get`/`dpkg -i`: no package database is mutated, only a plain
# filesystem tree (/patch-root) is prepared for the final stage to COPY
# from, the same pattern the builder stage above already uses for /data.
RUN mkdir -p /patch-root/var/lib/dpkg/status.d && \
    dpkg-deb -x /tmp/libssl3t64.deb /patch-root && \
    dpkg-deb -e /tmp/libssl3t64.deb /tmp/libssl3t64-control && \
    cp /tmp/libssl3t64-control/control /patch-root/var/lib/dpkg/status.d/libssl3t64 && \
    cp /tmp/libssl3t64-control/md5sums /patch-root/var/lib/dpkg/status.d/libssl3t64.md5sums

# --- final: Distroless Python 3.13 runtime ------------------------------
#
# Resolved from the live registry on 2026-08-20 and independently
# re-confirmed immediately before this Dockerfile was written (see
# docs/build-security.md): the index digest below resolves to a
# linux/amd64 manifest digest of
# sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c
# (Python 3.13.5, Debian 13 "trixie", real interpreter at
# /usr/bin/python3.13, no shell, no package manager). The `nonroot`
# variant is used only for its minimal, no-shell content - never as an
# identity source: this Dockerfile still sets its own explicit
# USER 10001:10001 below rather than inheriting Distroless's own
# 65532:65532 "nonroot" identity, preserving this project's runtime UID/
# GID contract. As of this writing this digest still ships the
# vulnerable libssl3t64 3.5.6-1~deb13u2 - see the `security-patch` stage
# above and security/runtime-patches.lock for the overlay this Dockerfile
# applies on top of it. This base digest is NOT changed by the overlay:
# the final runtime is accurately described as "pinned Distroless Python
# Debian 13 + pinned Debian-security libssl3t64 overlay", never as
# byte-identical to upstream Distroless.
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea

# VERSION is passed at build time (`make build` reads the repository-root
# VERSION file and passes it via --build-arg), so the OCI version LABEL
# below is derived from the single authoritative source rather than a
# second hand-maintained literal. scripts/verify/security_check.py
# independently asserts the built image's label equals VERSION, so a
# forgotten --build-arg (falling back to this ARG's default) is caught by
# `make release-check`, not just trusted.
ARG VERSION=0.0.0-unset

LABEL org.opencontainers.image.title="maops-docker-platform" \
      org.opencontainers.image.description="Secure Python stdlib HTTP platform demonstrating Docker/Compose container engineering practices (app + gateway + state services)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/raiyan10/maops-docker-platform"

WORKDIR /app

# Read-only root filesystem is expected at runtime (see compose.yaml): never
# attempt to write .pyc bytecode caches, and always flush stdout/stderr
# immediately so log lines are never buffered/lost on shutdown.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Day 4 hardening: application source and VERSION are owned by root
# (COPY --from's default when no --chown is given preserves the builder
# stage's own ownership, and the builder never chowned this content, so
# it stays root-owned), not by the non-root 10001:10001 runtime user.
# This is deliberate defense-in-depth, independent of and in addition to
# compose.yaml's `read_only: true` rootfs: even if this image were ever
# run without --read-only (a bare `docker run` with no Compose flags,
# say), the non-root process still cannot modify its own shipped source
# code, because it does not own it and standard file mode bits
# (world-readable, not world-writable) never grant it write access
# either. /data (below) is the one deliberate exception - state
# legitimately persists data there, so it stays 10001:10001-owned and
# writable. `COPY --from` does NOT preserve the source stage's ownership
# by default (empirically confirmed: it resets to root:root the same as a
# build-context COPY would) - so /data's 10001:10001 ownership needs its
# own explicit --chown here even though the builder already chowned it;
# omitting --chown on the application-source COPYs below is deliberate
# (their default-root-ownership is exactly the property this section
# wants), not an oversight. See docs/build-security.md.
COPY --from=builder /app/app ./app/
COPY --from=builder /app/gateway ./gateway/
COPY --from=builder /app/state ./state/
COPY --from=builder /app/VERSION ./VERSION
COPY --from=builder --chown=10001:10001 /data /data

# Real fixed libssl3t64 payload overlay (see the `security-patch` stage
# above and security/runtime-patches.lock): every file below is copied
# verbatim from the official, checksum-verified Debian Security .deb -
# root-owned by COPY --from's default, same as the application source
# above, matching the ownership these files already have in the
# unpatched Distroless base. scripts/build/image_audit.py independently
# verifies the two shared-library files' content hashes against
# security/runtime-patches.lock after the image is built.
COPY --from=security-patch /patch-root/usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/lib/x86_64-linux-gnu/libssl.so.3
COPY --from=security-patch /patch-root/usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/lib/x86_64-linux-gnu/libcrypto.so.3
COPY --from=security-patch /patch-root/usr/lib/x86_64-linux-gnu/engines-3/ /usr/lib/x86_64-linux-gnu/engines-3/
COPY --from=security-patch /patch-root/usr/share/doc/libssl3t64/ /usr/share/doc/libssl3t64/
COPY --from=security-patch /patch-root/usr/share/lintian/overrides/libssl3t64 /usr/share/lintian/overrides/libssl3t64
COPY --from=security-patch /patch-root/var/lib/dpkg/status.d/libssl3t64 /var/lib/dpkg/status.d/libssl3t64
COPY --from=security-patch /patch-root/var/lib/dpkg/status.d/libssl3t64.md5sums /var/lib/dpkg/status.d/libssl3t64.md5sums

USER 10001:10001

EXPOSE 8080

# Stdlib-only probe (no curl/wget installed merely for HEALTHCHECK - and
# the Distroless final stage has neither anyway). This is the image-level
# default for the `app` role; compose.yaml overrides `healthcheck:` for
# the `gateway` role (`/usr/bin/python3.13 -m gateway.healthcheck`) and
# the `state` role (`/usr/bin/python3.13 -m state.healthcheck`), since a
# single image can only declare one HEALTHCHECK and each role probes its
# own endpoint. The absolute interpreter path is required: the Distroless
# final stage has no shell to perform PATH resolution, and this Dockerfile
# never relies on it even where a `python3` symlink happens to also exist.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/usr/bin/python3.13", "-m", "app.healthcheck"]

# One image, three roles: ENTRYPOINT stays a bare interpreter (exec form,
# still PID 1 directly - no shell wrapper, and the Distroless final stage
# has no shell to wrap with even if one were attempted) and CMD selects
# the default module. compose.yaml overrides `command: ["-m", "gateway"]`
# / `command: ["-m", "state"]` for those services; the app service's
# `command: ["-m", "app"]` is explicit too, matching this default.
ENTRYPOINT ["/usr/bin/python3.13"]
CMD ["-m", "app"]
