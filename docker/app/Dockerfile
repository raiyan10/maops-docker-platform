# syntax=docker/dockerfile:1
#
# Resolved from the live Docker Hub registry on 2026-08-18:
#   docker pull python:3.13-slim
#   -> python@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a
#   (Python 3.13.15, Debian 13 "trixie")
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a

# VERSION is passed at build time (`make build` reads the repository-root
# VERSION file and passes it via --build-arg), so the OCI version LABEL
# below is derived from the single authoritative source rather than a
# second hand-maintained literal. scripts/verify/security_check.py
# independently asserts the built image's label equals VERSION, so a
# forgotten --build-arg (falling back to this ARG's default) is caught by
# `make release-check`, not just trusted.
ARG VERSION=0.0.0-unset

LABEL org.opencontainers.image.title="maops-docker-platform" \
      org.opencontainers.image.description="Secure Python stdlib HTTP platform demonstrating Docker/Compose container engineering practices (app + gateway services)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/raiyan10/maops-docker-platform"

# Dedicated, fixed-ID non-root user/group. No login shell, no home
# directory needed for a stateless HTTP workload.
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Read-only root filesystem is expected at runtime (see compose.yaml): never
# attempt to write .pyc bytecode caches, and always flush stdout/stderr
# immediately so log lines are never buffered/lost on shutdown.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --chown=10001:10001 app/ ./app/
COPY --chown=10001:10001 gateway/ ./gateway/
COPY --chown=10001:10001 VERSION ./VERSION

USER 10001:10001

EXPOSE 8080

# Stdlib-only probe (no curl/wget installed merely for HEALTHCHECK). This
# is the image-level default for the `app` role; compose.yaml overrides
# `healthcheck:` for the `gateway` role with `python3 -m
# gateway.healthcheck` instead, since a single image can only declare one
# HEALTHCHECK and the two roles probe different endpoints.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python3", "-m", "app.healthcheck"]

# One image, two roles: ENTRYPOINT stays a bare interpreter (exec form,
# still PID 1 directly - no shell wrapper) and CMD selects the default
# module. compose.yaml overrides `command: ["-m", "gateway"]` for the
# gateway service; the app service's `command: ["-m", "app"]` is explicit
# too, matching this default.
ENTRYPOINT ["python3"]
CMD ["-m", "app"]
