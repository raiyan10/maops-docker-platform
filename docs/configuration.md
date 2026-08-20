# Configuration (Day 3)

## Four distinct mechanisms - do not conflate them

This platform now uses four genuinely different configuration
mechanisms. Confusing one for another is a common source of
mis-scoped security review, so they are listed explicitly:

1. **Compose interpolation variables** - `${VERSION:-0.3.0}` in
   `compose.yaml` itself. Resolved by `docker compose` *before* any
   container exists, from the shell environment `make`/the invoking
   script exports. Controls what gets built/rendered, not runtime
   behavior inside a container.
2. **Container environment variables** - `environment:` blocks in
   `compose.yaml` (`APP_HOST`, `STATE_HOST`, `UPSTREAM_HOST`, etc.).
   Baked into each container at `docker compose up` time; changing one
   requires a container recreation (not a rebuild) to take effect. Each
   service reads only its own small, explicit, named set - never
   arbitrary environment variables (`app/config.py`, `gateway/config.py`,
   `state/config.py` each document their own exact list).
3. **Compose-mounted configuration** (new, Day 3) - the top-level
   `configs:` object (`config/platform.json`, mounted read-only into all
   three services at `/etc/maops/platform.json`). A real file on disk
   inside the container, parsed by each service's own small
   `platform_config.py` module. Unlike `environment:`, this can change
   *without* touching `compose.yaml` or the image at all - only the
   mounted file and a container recreation.
4. **Secrets** - not present. This platform has no secret material yet
   (no credentials, tokens, or keys anywhere in its request/response
   surface), so no Compose `secrets:` block exists. Do not add one merely
   to demonstrate the mechanism - `.claude/CLAUDE.md` is explicit that
   secrets are out of scope until something actually needs one.

## `config/platform.json`

```json
{
  "schema_version": 1,
  "platform_name": "maops-docker-platform",
  "dependency_timeout_seconds": 3.0,
  "state_filename": "state.json"
}
```

Entirely non-secret - safe to commit publicly, and it is. Each service
validates and uses only the subset of fields it actually needs:

| Field | Used by | Effect |
|---|---|---|
| `schema_version` | all three | must be exactly `1`; anything else fails loading |
| `platform_name` | app, gateway (metadata only) | validated present/non-empty, not currently surfaced in any response |
| `dependency_timeout_seconds` | app, gateway | bounds the outbound call to their fixed dependency (`state`, `app` respectively) - replaces the Day 2 hardcoded `UPSTREAM_TIMEOUT_SECONDS` constant |
| `state_filename` | state | the bare filename (never a path) of the persisted counter file under the fixed `/data` mount |

## Parsing rules (stdlib only, fail clearly)

Each service's `platform_config.py` (`app/`, `gateway/`, `state/` -
independent small modules, not a shared library, matching this project's
existing per-package convention):

- If the file is absent, sensible defaults are used silently - this
  supports a bare `docker run` outside Compose and every unit test, none
  of which mount anything.
- If the file is present but fails to parse as JSON, is not a JSON
  object, has the wrong `schema_version`, or has a field of the wrong
  type/out of range, loading raises `ValueError` and the process fails to
  start - a mounted, intentionally-provided config that doesn't validate
  is a real operator error, not something to paper over silently.
- `dependency_timeout_seconds` must be a real number (not `bool`, which
  is a `int` subclass in Python and is explicitly rejected) in
  `(0, 30]` seconds.
- `state_filename` must be a bare filename matching `^[A-Za-z0-9._-]+$`,
  and never exactly `.` or `..` - this is joined onto the fixed `/data`
  directory with no further sanitization, so directory traversal is
  rejected at load time, not left to accidental path-joining behavior.
- No field here can ever select an upstream *host*. `STATE_HOST`/
  `UPSTREAM_HOST` remain environment-variable-only (mechanism #2 above),
  fixed at container-start time, never read from this file - keeping the
  Day 1/2 SSRF-prevention design intact (see `docs/security.md`).

## Runtime-config-changes-without-rebuild, proven

Because `config/platform.json` is a bind-mounted file, not baked into the
image, changing `dependency_timeout_seconds` and recreating a container
(no image rebuild) genuinely changes that container's observed timeout
behavior - `scripts/compose/compose_integration.py`'s `UpstreamTimeoutTests`-
equivalent unit coverage (`tests/test_gateway_server.py::
UpstreamTimeoutTests`, `tests/test_config.py`/`test_gateway_config.py`'s
platform-config tests) exercises this at the config-loading layer
directly; the Compose-mounted read-only proof itself is covered by
`compose_integration.py`'s `check_config_mount_readonly`.

## Mount is read-only, proven at both tiers

- **[C] Docker runtime inspection**: `docker inspect <container>
  --format '{{json .Mounts}}'` shows the `/etc/maops/platform.json` bind
  mount with `RW: false`.
- **[D] kernel/process verification**: a real attempted write to
  `/etc/maops/platform.json` inside a running container is rejected
  (`Read-only file system`), and the service keeps functioning
  afterward - see `scripts/compose/compose_integration.py`'s
  `check_config_mount_readonly`.

This mirrors the same [C]/[D] evidence-tiered proof this project already
applies to the container rootfs (see `docs/security.md`).
