# Compose Platform (Day 2 baseline, Day 3 topology)

## Topology

```
host (127.0.0.1 only)
    |
    v
gateway  (published, hardened, sole host-facing service)
    |
    | Compose service-name discovery ("app", never a hardcoded IP)
    | -- edge network --
    v
app      (Day 1 backend, not host-published, reachable only inside
          the Compose project network)
    |
    | Compose service-name discovery ("state", never a hardcoded IP)
    | -- backend network (internal: true) --
    v
state    (Day 3 persistence service, not host-published, reachable
          only from app)
```

Exactly three Compose-managed services exist: `state` (Day 3, stdlib-only
persistence), `app` (the Day 1 backend, unchanged endpoints plus a new
state-dependent readiness check and `/state`/`/state/increment`
forwarding), and `gateway` (Day 2, stdlib-only, extended in Day 3 to
forward the same two paths to `app`). No database, cache, reverse proxy,
message broker, or third-party runtime package was added to reach this
topology. See `docs/networking.md` for the full two-network (`edge`/
`backend`) rationale and `docs/persistence.md` for `state`'s design.

## Why `app` is not host-published

`app`'s `compose.yaml` service declares no `ports:` mapping at all - it is
reachable only from inside the Compose project's default network, and
only by the `gateway` service, over Compose's built-in service-name DNS
(`app` resolves to the `app` container's address). This means the backend
has no host-facing attack surface whatsoever; every external request must
pass through the gateway's own narrow, validated routing first. This is
proven at runtime, not just declared: `scripts/compose/
compose_integration.py` inspects the real Compose-created `app` container
and asserts `HostConfig.PortBindings` is empty.

## Gateway responsibility

The gateway (`gateway/`) is the only service Docker publishes to the
host, and only on `127.0.0.1` (never `0.0.0.0`) - see `compose.yaml`'s
`ports: ["127.0.0.1:${GATEWAY_HOST_PORT:-8080}:8080"]`. It exposes four
endpoints:

| Method | Path              | Purpose                                          |
|--------|-------------------|---------------------------------------------------|
| GET    | `/`               | gateway service identity                          |
| GET    | `/healthz`        | gateway *process* liveness only - never contacts `app` |
| GET    | `/readyz`         | gateway readiness - makes a real, bounded HTTP request to `app`'s own `/readyz` |
| GET    | `/upstream/info`  | makes a real, bounded HTTP request to `app:8080/info` and returns it, wrapped |

The gateway's outbound destination is fixed at process startup from
`UPSTREAM_HOST`/`UPSTREAM_PORT` (defaulting to `app`/`8080`) and is never
influenced by anything in an incoming request's path, query string,
headers, or body - there is no arbitrary-URL proxying and no
user-controlled upstream host, which is what prevents this gateway from
being an SSRF-style open proxy. Every outbound call uses a single bounded
socket timeout (`UPSTREAM_TIMEOUT_SECONDS`, 3s) covering both connect and
read, so the gateway can never hang indefinitely waiting on `app`. No
Python traceback or raw network-exception text is ever returned to a
client - connection failures, non-200 upstream statuses, and malformed
upstream JSON are all converted to a controlled `503` (`/readyz`) or
`502` (`/upstream/info`) with a fixed, generic error message.

## App responsibility

`app`'s `GET /`, `/healthz`, `/info` are unchanged from Day 1/2. `/readyz`
changed in Day 3: it now makes a real, bounded HTTP call to `state`'s own
`/readyz` (the same pattern `gateway`'s `/readyz` already used toward
`app`), so a healthy `app` *process* can still be *not ready* if `state`
is unreachable - mirroring the existing health-vs-readiness distinction
one layer deeper. `app` also gained `GET /state`/`POST /state/increment`,
forwarding to `state`'s identical paths using a fixed, environment-
configured destination (`STATE_HOST`/`STATE_PORT`) - never derived from
an incoming request, the same SSRF-prevention design `gateway` already
used toward `app`. `app` has no awareness of `gateway`'s existence - it
is simply another HTTP client, over the Compose network. See
`docs/persistence.md` for the full state API and lifecycle.

## One image, three roles

`docker/app/Dockerfile` builds a single image
(`maops-docker-platform:<VERSION>`) containing `app/`, `gateway/`, and
`state/`. `ENTRYPOINT ["python3"]` (exec form, still PID 1 directly - no
shell wrapper) with `CMD ["-m", "app"]` as the image-level default;
`compose.yaml` overrides `command: ["-m", "gateway"]` / `command: ["-m",
"state"]` for those services. All three roles run `python3` directly as
PID 1 - proven, not just declared, by `scripts/compose/
compose_integration.py`'s `/proc/1/cmdline` inspection of every
Compose-created container.

## Explicit networks and service-name discovery

Day 3 replaces the Day 2 implicit default network with two explicit,
purpose-built networks (`edge`, `backend`) - see `docs/networking.md` for
the full topology, isolation rationale, and runtime proof. Every
cross-service call still uses Compose service-name discovery
(`UPSTREAM_HOST=app`, `STATE_HOST=state`), resolved by Compose's embedded
DNS - `compose.yaml` never hardcodes a container IP anywhere, on either
network.

## Dependency readiness: `depends_on: condition: service_healthy`

The Day 3 chain is `state -> app -> gateway`: `app` declares `depends_on:
state: condition: service_healthy`, and `gateway` declares `depends_on:
app: condition: service_healthy` (unchanged from Day 2). Compose does not
start a dependent container until its dependency's own `HEALTHCHECK`
first reports `healthy` - not merely "container created" or "process
started". This is a *startup-ordering* guarantee only; it does not mean a
dependency stays healthy for the dependent's whole lifetime (see below).
`scripts/compose/compose_integration.py` now proves this ordering
directly with real `docker inspect` timestamps (each dependency's first
`healthy` transition time vs. each dependent's own `StartedAt`), closing
the Day 2 review finding that eventual-healthy polling alone couldn't
distinguish a real ordering guarantee from timing luck.

## Health vs. readiness - two different questions, on all three services

- **Health** (`HEALTHCHECK`, Docker/Compose-level): "is this container's
  own process alive and responding?" `app`'s healthcheck is
  `python3 -m app.healthcheck`, `gateway`'s is `python3 -m
  gateway.healthcheck`, `state`'s is `python3 -m state.healthcheck` -
  each probes only its own `/healthz`, liveness only, never a dependency.
  A single image can only declare one `HEALTHCHECK`, so `compose.yaml`
  overrides it per-service; all three invocation forms are
  regression-tested (see below).
- **Readiness** (each service's own `/readyz` endpoint,
  application-level): "can this service actually do its job right now?"
  `gateway`'s `/readyz` means "can I reach `app`?"; `app`'s `/readyz`
  (new in Day 3) means "can I reach `state`?"; `state`'s `/readyz` means
  "is my persisted store readable, not corrupted?" A healthy process can
  still be *not ready* if its own dependency is unreachable - and because
  each layer's readiness genuinely calls the next, a `state` outage
  propagates honestly through `app`'s readiness and then `gateway`'s,
  never faked independently at each layer. This is what the failure/
  recovery scenario below exercises.

## Integration testing: real stack, not just rendered config

`scripts/compose/check_compose.py` is a **static** check - it validates
`docker compose config`'s *rendered* output (exactly three services,
correct image/version, correct hardening flags, correct healthcheck
commands, `gateway` the sole loopback publisher, the `edge`/`backend`
network topology and isolation, the `state_data` volume, the mounted
`platform` config, and a real cross-check that `UPSTREAM_HOST`/
`STATE_HOST` both name a real service *and* share a network with the
consumer) but never starts a real container. This closes part of Day 1
finding M-3 but not all of it - a valid-looking config could still
describe containers that behave differently once actually running.

`scripts/compose/compose_integration.py` is the **runtime** counterpart,
and is what actually closes M-3: it brings up the real three-service
stack under a uniquely named Compose project, on a dynamic loopback host
port, and inspects the real Compose-*created* containers (not just
Compose's rendered configuration) - reusing
`scripts/verify/security_check.py`'s existing `[C]`/`[D]`
container-inspection functions rather than duplicating that logic a
second time - for read-only rootfs, `cap_drop: [ALL]`,
`no-new-privileges`, non-root UID/GID, absence of host PID/network mode,
absence of a Docker-socket mount, each role's PID 1 identity, **and**
(new in Day 3, closing the Day 2 review's M-1/L-2 finding) a real [D]
rejected-write proof against the rootfs for every Compose-managed
container, not merely the [C] "Docker was asked" check Day 2 shipped.

## Failure and recovery behavior (proven, not asserted)

`compose_integration.py` exercises the full scenario end-to-end with
bounded deadlines throughout, now one layer deeper than Day 2:

1. Bring the stack up; all three services reach Docker `healthy`, in the
   proven order `state` -> `app` -> `gateway`.
2. Confirm the full `gateway -> app -> state` path works (`GET /state`,
   `POST /state/increment` through the public gateway port).
3. `docker compose stop state` - and confirm **both `app` and `gateway`
   processes stay alive**, and `app`'s own local `/healthz` liveness
   stays healthy throughout.
4. Poll `gateway`'s `/readyz` until it reports **not-ready** (a controlled
   `503`, not a hang or a crash) - proving the readiness chain is real,
   live probes at every layer, not a cached/stale value anywhere.
5. Confirm `GET /state` through the gateway returns a controlled `503`
   while `state` is down.
6. `docker compose start state`; wait for `state` to become healthy
   again.
7. Poll `gateway`'s `/readyz` until it **recovers** to `200 ready`, and
   confirm the previously-persisted value is unchanged.
8. Recreate the `state` container alone (volume retained) and confirm the
   value survived; increment again; `compose down` (without `-v`) and
   `up` the whole stack, and confirm the value still survived.
9. Tear the entire stack down (`down -v`, this test project's own
   uniquely-named volume only) and confirm no leftover container,
   network, or volume remains for that project.

See `docs/persistence.md` for the full persistence-specific detail.

## Runtime hardening on all three services

`read_only: true`, `cap_drop: [ALL]`, `security_opt:
[no-new-privileges:true]`, and non-root `10001:10001` execution apply
identically to **all three** of `app`, `gateway`, and `state` - Day 3
does not weaken any Day 1/2 hardening property, and now genuinely
automates the full [A]/[B]/[C]/[D] evidence-tiered verification (see
`docs/security.md`) for Compose-managed containers specifically,
including the [D] rootfs-write-rejection proof that Day 2 only performed
against an ad hoc `docker run` container. `state` additionally proves its
one exception - `/data`, via the named volume - is genuinely writable
despite the same read-only rootfs (see `docs/persistence.md`).

## What is explicitly not implemented yet (Day 4+)

- No CPU/memory resource limits or restart-policy reliability
  engineering - Day 5.
- No CI-enforced verification - Day 6; `make release-check` is the only
  gate today.
- No vulnerability scanning, SBOM, or build-reproducibility framework
  beyond `VERSION` consistency - Day 4.
- No container registry or published image - Day 6.

Do not read any bullet in this section as already implemented - see
`docs/roadmap.md` for the authoritative day-by-day scope.
