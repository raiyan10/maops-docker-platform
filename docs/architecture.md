# Architecture

## Two-package layout

As of Day 2, one release image contains two small, dependency-free
Python stdlib packages: `app/` (the Day 1 backend) and `gateway/` (new,
the sole host-facing service). Both exist only to give the container/
Compose layer something real to build, harden, test, and verify — the
interesting engineering in this repository is the Docker/Compose layer,
not the application logic. See `docs/compose-platform.md` for the full
Day 2 topology rationale (request flow, why `app` is not host-published,
health vs. readiness, failure/recovery behavior).

```
app/
    __init__.py
    __main__.py     # `python3 -m app` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, signal handling
    config.py          # APP_HOST / APP_PORT / APP_NAME parsing + validation
    version.py           # reads the repository-root VERSION file
    healthcheck.py         # stdlib-only Docker HEALTHCHECK probe

gateway/
    __init__.py
    __main__.py     # `python3 -m gateway` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, bounded upstream HTTP calls
    config.py          # GATEWAY_HOST/PORT, UPSTREAM_HOST/PORT parsing + validation
    healthcheck.py        # stdlib-only Docker HEALTHCHECK probe (own /healthz only)
```

`app` never reaches outside its own process boundary except to answer an
HTTP request on the port it was configured to bind. It has no filesystem
writes, no subprocess use, no third-party dependency, and no network
client behavior of its own (its own HEALTHCHECK probe connects to `app`'s
own loopback listener, nothing external).

`gateway` is the one exception to "no network client behavior": its whole
job is making real, bounded outbound HTTP calls to a single fixed
destination (`UPSTREAM_HOST`/`UPSTREAM_PORT`, defaulting to `app`:`8080`)
resolved once at process startup — never influenced by an incoming
request's path, query string, headers, or body. This is what prevents it
from being an SSRF-style arbitrary-URL proxy. See
`docs/compose-platform.md` for the gateway's endpoint table and error
model.

### `app` endpoints

| Method | Path       | Purpose                                    |
|--------|------------|---------------------------------------------|
| GET    | `/`        | service identity (`name`, `version`, `status`) |
| GET    | `/healthz` | liveness                                    |
| GET    | `/readyz`  | readiness                                   |
| GET    | `/info`    | safe, explicitly-selected metadata          |
| HEAD   | (all above)| same headers, no body                       |

Every response is `application/json`. Unknown paths return a controlled
`404` JSON body; a known path called with an unsupported method returns a
controlled `405` with an `Allow` header; any other unrecognized method
(or malformed request) is caught by an overridden `send_error()` that
always emits a fixed JSON error body — the client never sees a Python
traceback or the default `http.server` HTML error page.

`/info` exposes exactly five fields (`name`, `version`, `python_version`,
`host`, `port`) built from the application's own typed `AppConfig` and
`platform.python_version()` — never `os.environ` itself, so there is no
mechanism by which an unrelated environment variable could leak through
it.

## Process model — one image, two roles, both PID 1

Both `app` and `gateway` run **directly as PID 1** inside their
respective containers, from the same image:

- `ENTRYPOINT ["python3"]` in exec form — no shell wrapper, no `sh -c`,
  no process manager (no supervisord/tini/dumb-init) — with
  `CMD ["-m", "app"]` as the image-level default; `compose.yaml`
  overrides `command: ["-m", "gateway"]` for the gateway service. Both
  services' `command:` is explicit in `compose.yaml`, matching this
  default. `gateway/server.py` mirrors `app/server.py`'s process model
  exactly, so both roles share the same lifecycle guarantees.
- No daemonization: `serve_forever()` blocks in the foreground for the
  lifetime of the container, for both roles.
- All logging goes to stdout (`log_message` is overridden to write to
  stdout instead of the library default of stderr, and each role's own
  startup/shutdown lines use `sys.stdout.write`) — no application log
  file is ever created by either role.
- **Graceful shutdown**: `SIGTERM`/`SIGINT` are handled by a signal
  handler that starts a *separate* thread calling
  `HTTPServer.shutdown()`, identically in both `app/server.py` and
  `gateway/server.py`. This is deliberate: `shutdown()` blocks until the
  `serve_forever()` loop actually exits, so calling it from the same
  thread that's running `serve_forever()` would deadlock — a signal
  handler runs on the main thread, which is the thread `serve_forever()`
  occupies. A real `docker stop` against the `app` role was measured at
  **~0.6s** wall time with a clean `exit code 0` (see
  `docs/security.md`'s validation log), well inside Docker's default 10s
  SIGTERM-then-SIGKILL grace window — and this is now an automated
  regression check (`scripts/verify/security_check.py`'s
  `check_lifecycle_docker_stop()`), not only a manually measured claim.

`ENV PYTHONDONTWRITEBYTECODE=1` is set so the interpreter never attempts
to write a `.pyc` cache under the (intentionally read-only) container
root filesystem, and `PYTHONUNBUFFERED=1` ensures stdout/stderr lines are
flushed immediately rather than buffered until process exit.

## Docker vs. Compose responsibility

- **`docker/app/Dockerfile`** owns everything about what the image *is*:
  base image, non-root user, file layout, the `app`-role `HEALTHCHECK`
  default, OCI labels, the exec-form `ENTRYPOINT`/default `CMD`.
- **`compose.yaml`** owns everything about how the image is *run*: which
  role each service plays (`command:`), port mapping (`app`: none;
  `gateway`: loopback-only), environment variables, the per-role
  `healthcheck:` override (a single image can only declare one
  `HEALTHCHECK`, so `compose.yaml` overrides it for the `gateway` role),
  the `depends_on`/health-ordering relationship between the two services,
  and the runtime hardening flags (`read_only`, `cap_drop`,
  `security_opt`) that Docker enforces at container-start time rather
  than at build time — applied identically to both services.

This split is deliberate so that further growth (a third service, a
custom network, volumes) only ever touches `compose.yaml`, never requires
re-architecting the image itself — exactly what happened going from Day 1
to Day 2 for the `gateway` role.

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
