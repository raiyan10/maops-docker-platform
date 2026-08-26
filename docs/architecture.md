# Architecture

## Three-package layout

As of Day 3, one release image contains three small, dependency-free
Python stdlib packages: `app/` (the Day 1 backend), `gateway/` (Day 2,
the sole host-facing service), and `state/` (Day 3, the persistence
service). All three exist only to give the container/Compose layer
something real to build, harden, test, and verify — the interesting
engineering in this repository is the Docker/Compose layer, not the
application logic. See `docs/compose-platform.md` for the full topology
rationale, `docs/networking.md` for the network segmentation, and
`docs/persistence.md` for `state`'s design.

```
app/
    __init__.py
    __main__.py     # `python3 -m app` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, signal handling, bounded state calls
    config.py          # APP_HOST/PORT/NAME, STATE_HOST/PORT parsing + validation
    platform_config.py   # loads the mounted config/platform.json (dependency_timeout_seconds)
    version.py              # reads the repository-root VERSION file
    healthcheck.py            # stdlib-only Docker HEALTHCHECK probe

gateway/
    __init__.py
    __main__.py     # `python3 -m gateway` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, bounded upstream HTTP calls
    config.py          # GATEWAY_HOST/PORT, UPSTREAM_HOST/PORT parsing + validation
    platform_config.py   # loads the mounted config/platform.json (dependency_timeout_seconds)
    healthcheck.py          # stdlib-only Docker HEALTHCHECK probe (own /healthz only)

state/
    __init__.py
    __main__.py     # `python3 -m state` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, signal handling
    config.py          # STATE_HOST/PORT/NAME parsing + validation, fixed /data mount point
    platform_config.py   # loads the mounted config/platform.json (state_filename)
    storage.py              # atomic fsync'd persistence for a single counter
    healthcheck.py            # stdlib-only Docker HEALTHCHECK probe
```

`app` and `state` never reach outside their own process boundary except
to answer an HTTP request, `app`'s own bounded outbound call to `state`
(see below). Neither has any subprocess use or third-party dependency.
`state`'s only filesystem write is its own persisted counter file under
the fixed `/data` mount (see `docs/persistence.md`) — never an arbitrary
path, and never outside that one mount point.

`gateway` and `app` both make real, bounded outbound HTTP calls to a
single fixed destination — `gateway` to `UPSTREAM_HOST`/`UPSTREAM_PORT`
(defaulting to `app`:`8080`), `app` to `STATE_HOST`/`STATE_PORT`
(defaulting to `state`:`8080`) — resolved once at process startup, never
influenced by an incoming request's path, query string, headers, or
body. This is what prevents either from being an SSRF-style
arbitrary-URL proxy. See `docs/compose-platform.md` for the endpoint
tables and error models.

### `app` endpoints

| Method | Path                | Purpose                                    |
|--------|---------------------|---------------------------------------------|
| GET    | `/`                 | service identity (`name`, `version`, `status`) |
| GET    | `/healthz`          | liveness (local only, never calls `state`)  |
| GET    | `/readyz`           | readiness — a real, bounded call to `state`'s own `/readyz` |
| GET    | `/info`             | safe, explicitly-selected metadata          |
| GET    | `/state`            | forwards to `state`'s `GET /state`          |
| POST   | `/state/increment`  | forwards to `state`'s `POST /state/increment` |
| HEAD   | (GET routes above)  | same headers, no body                       |

Every response is `application/json`. Unknown paths return a controlled
`404` JSON body; a known path called with an unsupported method returns a
controlled `405` with an `Allow` header listing only the methods that
path actually supports; any other unrecognized method (or malformed
request) is caught by an overridden `send_error()` that always emits a
fixed JSON error body — the client never sees a Python traceback or the
default `http.server` HTML error page.

`/info` exposes exactly five fields (`name`, `version`, `python_version`,
`host`, `port`) built from the application's own typed `AppConfig` and
`platform.python_version()` — never `os.environ` itself, so there is no
mechanism by which an unrelated environment variable could leak through
it.

## Process model — one image, three roles, all PID 1

`app`, `gateway`, and `state` all run **directly as PID 1** inside their
respective containers, from the same image:

- `ENTRYPOINT ["/usr/bin/python3.13"]` in exec form — the absolute
  interpreter path, no shell wrapper, no `sh -c`, no process manager (no
  supervisord/tini/dumb-init) — with `CMD ["-m", "app"]` as the
  image-level default; `compose.yaml` overrides `command: ["-m",
  "gateway"]` / `command: ["-m", "state"]` for those services. Every
  service's `command:` is explicit in `compose.yaml`, matching this
  default. `gateway/server.py` and `state/server.py` both mirror
  `app/server.py`'s process model exactly, so all three roles share the
  same lifecycle guarantees. As of Day 4, the final runtime
  (`gcr.io/distroless/python3-debian13:nonroot`) has no shell at all, so
  the absolute path is a hard requirement, not a style choice — a bare
  `python3` name would depend on PATH resolution the runtime cannot
  perform. See `docs/build-security.md`.
- No daemonization: `serve_forever()` blocks in the foreground for the
  lifetime of the container, for every role.
- All logging goes to stdout (`log_message` is overridden to write to
  stdout instead of the library default of stderr, and each role's own
  startup/shutdown lines use `sys.stdout.write`) — no application log
  file is ever created by any role.
- **Graceful shutdown**: `SIGTERM`/`SIGINT` are handled by a signal
  handler that starts a *separate* thread calling
  `HTTPServer.shutdown()`, identically in `app/server.py`,
  `gateway/server.py`, and `state/server.py`. This is deliberate:
  `shutdown()` blocks until the `serve_forever()` loop actually exits, so
  calling it from the same thread that's running `serve_forever()` would
  deadlock — a signal handler runs on the main thread, which is the
  thread `serve_forever()` occupies. A real `docker stop` against the
  `app` role was measured at **~0.6s** wall time with a clean `exit code
  0` (see `docs/security.md`'s validation log), well inside Docker's
  default 10s SIGTERM-then-SIGKILL grace window — and this is now an
  automated regression check (`scripts/verify/security_check.py`'s
  `check_lifecycle_docker_stop()`), not only a manually measured claim.

`ENV PYTHONDONTWRITEBYTECODE=1` is set so the interpreter never attempts
to write a `.pyc` cache under the (intentionally read-only) container
root filesystem, and `PYTHONUNBUFFERED=1` ensures stdout/stderr lines are
flushed immediately rather than buffered until process exit.

## Docker vs. Compose responsibility

- **`docker/app/Dockerfile`** owns everything about what the image *is*:
  base image(s), non-root user, file layout (including pre-creating
  `/data`, owned by the non-root user, so a fresh `state_data` volume
  works without a privileged init step — see `docs/persistence.md`), the
  `app`-role `HEALTHCHECK` default, OCI labels, the exec-form
  `ENTRYPOINT`/default `CMD`. As of Day 4, it also owns application
  source's *ownership* as a security property: `app/`, `gateway/`,
  `state/`, and `VERSION` are root-owned (no `--chown` on the final
  stage's `COPY --from=builder` instructions), not owned by the non-root
  `10001:10001` runtime user — an image-level immutability property
  independent of `compose.yaml`'s runtime `read_only: true`. See
  `docs/build-security.md`.
  **Day 4 build architecture**: the Dockerfile is now a two-stage build —
  a digest-pinned `python:3.13-slim` builder stage (filesystem
  preparation only: copies application source, creates and owns `/data`;
  never entering the final image itself) feeding a digest-pinned
  `gcr.io/distroless/python3-debian13:nonroot` final runtime stage (no
  shell, no package manager). This is a *build-image* architecture
  change only — the runtime service topology (`state -> app -> gateway`,
  one image capable of all three roles) is unchanged from Day 3. See
  `docs/build-security.md` for the full rationale and the base-image
  rejection this replaced (`python:3.13-slim`, rejected on unfixed
  CRITICAL findings).
  **Day 6 addition**: a third, build-time-only `security-patch` stage was
  inserted between the two — a checksum-pinned Debian-security package
  overlay closing a real, fixable vulnerability-policy finding
  (CVE-2026-14456) that the pinned Distroless digest had not yet picked
  up upstream. It reuses the same builder base image (no new base
  introduced) and never enters the final image itself beyond the specific
  verified files it copies in; the final stage remains shellless and
  package-manager-free. This is again a *build-image* change only — see
  `docs/build-security.md`'s "Day 6: emergency Debian-security overlay"
  section.
- **`compose.yaml`** owns everything about how the image is *run*: which
  role each service plays (`command:`), port mapping (`app`/`state`:
  none; `gateway`: loopback-only), environment variables, the per-role
  `healthcheck:` override (a single image can only declare one
  `HEALTHCHECK`, so `compose.yaml` overrides it for the `gateway` and
  `state` roles), the `depends_on`/health-ordering chain (`state` ->
  `app` -> `gateway`), the network topology (`edge`/`backend`), the
  `state_data` named volume, the mounted `platform` config, and the
  runtime hardening flags (`read_only`, `cap_drop`, `security_opt`) that
  Docker enforces at container-start time rather than at build time —
  applied identically to all three services.

This split is deliberate so that further growth (networks, volumes,
configuration) only ever touches `compose.yaml`, never requires
re-architecting the image itself — exactly what happened going from Day 1
to Day 2 for the `gateway` role, and from Day 2 to Day 3 for `state`,
networking, and configuration.

## Build-context hygiene

`.dockerignore` excludes repository/development-only content
(`.git`, `.github`, `.claude`, tests, docs, images, editor files, build
output) from ever reaching the Docker daemon. Every pattern that must
apply at *any* directory depth uses an explicit `**/` prefix
(`**/__pycache__/`, `**/*.pyc`, `**/*.pyo`) rather than a bare
one-level glob — a real prior review finding was a non-recursive pattern
that let a nested `__pycache__` directory leak into the image. See
`docs/security.md` for the recursive-verification proof, not just the
pattern's presence.

As of Day 4, `.dockerignore` also excludes the new generated-artifact
directories (`artifacts/`, `.cache/`), the `security/` directory (a
repo-governance file, `scanners.lock` — not application runtime content),
and any `.tar`/`.tar.gz` file — so a saved Docker image archive or a
generated SBOM/vulnerability report can never itself leak into a
subsequent build. See `docs/supply-chain.md`.

## Delivery plane vs. runtime plane (Day 6)

Everything above describes the **runtime plane**: what the image *is* and
how it *runs*. Day 6 (`docs/ci-cd.md`) added a separate **delivery
plane** — `.github/workflows/` (GitHub Actions), `scripts/ci/` (workflow
policy validation), and `scripts/release/` (release-context validation) —
that automates *when and how* the runtime plane's own existing `make`
targets get run (on a pull request, on a push to `main`, on a release tag),
without adding any new runtime service or changing `compose.yaml`'s
topology. (`docker/app/Dockerfile` itself did gain a third,
build-time-only `security-patch` stage during Day 6 — see
`docs/build-security.md` — but that is a runtime-plane image-security fix,
not a delivery-plane change: it does not add a service, alter the 3
services/2 networks/1 volume topology, or touch `.github/workflows/`.)
The Docker-vs-Compose split described
above is unaffected by this boundary; the delivery plane sits one layer
above both.
