# Persistence (Day 3)

## State API

`state` (`state/`, stdlib-only, no third-party dependency, no Flask, no
Redis, no PostgreSQL, no SQLite) exposes:

| Method | Path              | Purpose |
|--------|-------------------|---------|
| GET    | `/`               | service identity |
| GET    | `/healthz`        | process liveness only - never touches the store |
| GET    | `/readyz`         | storage readiness - a real, non-mutating read of the persisted record |
| GET    | `/state`          | `{"value": <int>}` |
| POST   | `/state/increment`| atomically persists and returns the incremented value |

`app` (`app/`) is the only service allowed to talk to `state` in the
normal topology (see `docs/networking.md`), forwarding its own `GET
/state`/`POST /state/increment` to state's identical paths. `gateway`
forwards the same two paths to `app`'s identical paths. The full chain is
`host -> gateway -> app -> state`, and every hop is a real, bounded HTTP
call - never a stub.

The persisted domain is deliberately tiny: a monotonically increasing
integer counter plus a `schema_version` field (see "Storage format"
below). This is a Docker persistence demonstration, not a database
product.

## Named volume

`state_data` (a Compose named volume) is mounted at `/data` inside
`state` only - `app` and `gateway` mount nothing. `docker/app/Dockerfile`
pre-creates `/data`, owned by `10001:10001` (the same non-root user every
service runs as), before `USER 10001:10001` takes effect. This matters
because Docker populates a *new*, empty named volume from whatever
already exists at its mount target in the image at container-create
time - without this, a freshly created volume would be owned by `root`
and the non-root `state` process could not write to it, forcing an
unwanted choice between running as root, `chmod 777`, or a privileged
init container. None of those was needed.

## Read-only rootfs + writable `/data`

`state` keeps `read_only: true` like every other service - the named
volume is the *only* writable path inside its container, not an exception
to the read-only policy. Proven at both evidence tiers (see
`docs/security.md` for the general [C]/[D] framework):

- **[C]** `docker inspect state --format '{{.HostConfig.ReadonlyRootfs}}'`
  is `true`.
- **[D]** a real write to a protected rootfs path (e.g. `/etc/...`) is
  rejected, *and* a real write to `/data` succeeds - both proven by
  `scripts/compose/compose_integration.py` (`check_kernel_readonly_write_fails`
  reused from `security_check.py`, and the new
  `check_state_data_write_succeeds`).

## Storage format and write safety

`state/storage.py` persists `{"schema_version": 1, "value": <int>}` to
`/data/<state_filename>` (the filename comes from the mounted platform
config - see `docs/configuration.md` - never from a request). Every write:

1. Serializes the new record to a temporary file in the *same directory*
   as the target (`.{name}.tmp`).
2. Flushes and `fsync`'s the temporary file before renaming.
3. Atomically renames it onto the real filename via `os.replace` - a
   reader never observes a partially written file, because the rename is
   atomic on the same filesystem.
4. `fsync`'s the containing directory afterward, so the rename itself is
   durable (best-effort - skipped without failing the write if the
   underlying filesystem rejects directory `fsync`, which some overlay/
   test filesystems do).
5. On any failure mid-write, the temporary file is removed rather than
   left behind.

Reading validates the file's shape strictly: valid JSON, a JSON object,
`schema_version == 1`, and `value` a non-negative integer (`bool` is
explicitly rejected - it's an `int` subclass in Python). Any violation
raises `CorruptedStateError` rather than silently returning a default or
guessed value - `GET /state`/`POST /state/increment` then report a
controlled `500` (never a Python traceback), and `GET /readyz` reports
`503 not-ready`, so a caller can tell "no state yet" (`value: 0`, no file
at all) apart from "the store is broken" (a `500`/`503`).

## Concurrency scope (stated honestly)

`StateStore` serializes read-modify-write increments with a
`threading.Lock`, which is sufficient for concurrent requests *within one
running `state` process* (the platform's `ThreadingHTTPServer` model).
It does **not** coordinate writers across multiple processes or
containers sharing the same volume - this platform runs exactly one
`state` replica at a time by design. This is not a distributed database
and does not pretend to be one (see `.claude/CLAUDE.md`'s explicit scope
boundary).

## Lifecycle proofs (all real, not asserted)

`scripts/compose/compose_integration.py` (`make compose-test`) exercises,
against a real stack under a uniquely named Compose project:

1. Read the initial value through the full `gateway -> app -> state`
   path, increment it, record the new value.
2. Recreate the `state` container alone (`docker compose up -d
   --force-recreate --no-deps state`) - the *container* is destroyed and
   rebuilt, but the named volume is untouched - and confirm the value
   survived.
3. Increment again, then `docker compose down` (without `-v`) and `up`
   the whole stack again - confirm the value still survived, proving the
   volume outlives a full stack teardown as long as `-v` isn't passed.
4. Only the test's own final teardown uses `down -v`, and only for its
   own uniquely named project/volume - normal development stack data
   (from a plain `docker compose up -d`) is never touched by any script
   in this repository. `make clean` cleans up only `maops-compose-*`
   test-owned projects/volumes, never a real development volume, and
   never via a global `docker volume prune`.

## Dependency failure / recovery (proven, not asserted)

`compose_integration.py` also proves the `state`-unavailable path:

1. `docker compose stop state`.
2. `app`'s own process stays alive and its `/healthz` (local liveness)
   stays `200` - state's absence never crashes or hangs `app`.
3. `app`'s `/readyz` (which now genuinely calls `state`'s own `/readyz` -
   the same dependency-readiness pattern Day 2 established for
   `gateway -> app`) degrades to a controlled `503`, and this propagates
   through `gateway`'s own `/readyz` (which calls `app`'s `/readyz`) -
   the whole chain's readiness is honest, not independently faked at each
   layer.
4. `GET /state` through the gateway returns a controlled `503`, never a
   hang or a traceback.
5. `docker compose start state` recovers all three layers' readiness
   without any manual intervention.

## Scope limitations (stated explicitly)

- DNS resolution for an unreachable dependency host is not itself bounded
  by `state_timeout_seconds`/`dependency_timeout_seconds` - Python's
  `http.client` timeout covers the socket connect/read phase, not
  `getaddrinfo()`. This only matters when the target hostname cannot
  resolve at all (e.g. running `app` outside Compose, where `state`
  genuinely doesn't exist) - see `scripts/smoke/container_smoke.py`'s own
  scope note. Within Compose, `state`/`app`/`gateway` always resolve (or
  fail fast with connection-refused), so this does not affect the
  documented failure/recovery behavior above.
- No resource limits, no CPU/memory constraints, no restart-policy
  reliability engineering - Day 5 scope.
- No multi-replica state, no distributed consensus - explicitly out of
  scope, see "Concurrency scope" above.
