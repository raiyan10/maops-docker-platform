# Architecture

## Application / container boundary

`app/` is a small, dependency-free Python stdlib HTTP workload. It exists
only to give the container layer something real to build, harden, test,
and verify — the interesting engineering in this repository is the
Docker/Compose layer, not the application logic.

```
app/
    __init__.py
    __main__.py     # `python3 -m app` entrypoint -> server.serve_forever()
    server.py        # ThreadingHTTPServer, routing, signal handling
    config.py          # APP_HOST / APP_PORT / APP_NAME parsing + validation
    version.py           # reads the repository-root VERSION file
    healthcheck.py         # stdlib-only Docker HEALTHCHECK probe
```

The application never reaches outside its own process boundary except to
answer an HTTP request on the port it was configured to bind. It has no
filesystem writes, no subprocess use, no third-party dependency, and no
network client behavior of its own (the HEALTHCHECK probe connects to the
app's *own* loopback listener, nothing external).

### Endpoints

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

## Day 1 process model

The application process runs **directly as PID 1** inside the container:

- `ENTRYPOINT ["python3", "-m", "app"]` in exec form — no shell wrapper,
  no `sh -c`, no process manager (no supervisord/tini/dumb-init).
- No daemonization: `serve_forever()` blocks in the foreground for the
  lifetime of the container.
- All logging goes to stdout (`app/server.py` overrides
  `BaseHTTPRequestHandler.log_message` to write to stdout instead of the
  library default of stderr, and the app's own startup/shutdown lines use
  `sys.stdout.write`) — no application log file is ever created.
- **Graceful shutdown**: `SIGTERM`/`SIGINT` are handled by a signal
  handler that starts a *separate* thread calling
  `HTTPServer.shutdown()`. This is deliberate: `shutdown()` blocks until
  the `serve_forever()` loop actually exits, so calling it from the same
  thread that's running `serve_forever()` would deadlock — a signal
  handler runs on the main thread, which is the thread `serve_forever()`
  occupies. A real `docker stop` was measured at **~0.4s** wall time with
  a clean `exit code 0` (see `docs/security.md`'s validation log), well
  inside Docker's default 10s SIGTERM-then-SIGKILL grace window.

`ENV PYTHONDONTWRITEBYTECODE=1` is set so the interpreter never attempts
to write a `.pyc` cache under the (intentionally read-only) container
root filesystem, and `PYTHONUNBUFFERED=1` ensures stdout/stderr lines are
flushed immediately rather than buffered until process exit.

## Docker vs. Compose responsibility

- **`docker/app/Dockerfile`** owns everything about what the image *is*:
  base image, non-root user, file layout, `HEALTHCHECK` definition, OCI
  labels, the exec-form runtime command.
- **`compose.yaml`** owns everything about how the image is *run* in this
  local baseline: port mapping, environment variables, and the runtime
  hardening flags (`read_only`, `cap_drop`, `security_opt`) that Docker
  enforces at container-start time rather than at build time.

This split is deliberate so that Day 2+ growth (additional services,
networks, volumes) only ever touches `compose.yaml`, never requires
re-architecting the image itself.

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
