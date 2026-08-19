# Compose Platform (Day 2)

## Topology

```
host (127.0.0.1 only)
    |
    v
gateway  (published, hardened, sole host-facing service)
    |
    | Compose service-name discovery ("app", never a hardcoded IP)
    v
app      (Day 1 backend, not host-published, reachable only inside
          the Compose project network)
```

Exactly two Compose-managed services exist: `app` (the Day 1 backend,
unchanged in behavior) and `gateway` (new, stdlib-only Python). No
database, cache, reverse proxy, message broker, or third-party runtime
package was added to reach this topology.

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

`app` is unchanged from Day 1 (`GET /`, `/healthz`, `/readyz`, `/info`,
still the exact same JSON schemas) except that it is no longer reachable
from the host directly. It has no awareness of the gateway's existence -
the gateway is simply another HTTP client to it, over the Compose
network.

## One image, two roles

`docker/app/Dockerfile` builds a single image
(`maops-docker-platform:<VERSION>`) containing both `app/` and `gateway/`.
`ENTRYPOINT ["python3"]` (exec form, still PID 1 directly - no shell
wrapper) with `CMD ["-m", "app"]` as the image-level default;
`compose.yaml` overrides `command: ["-m", "gateway"]` for the gateway
service. Both roles run `python3` directly as PID 1 - proven, not just
declared, by `scripts/compose/compose_integration.py`'s `/proc/1/cmdline`
inspection of both Compose-created containers.

## Default Compose network and service-name discovery

Day 2 declares no custom network - the Compose-implicit default project
network is used intentionally. The gateway reaches the backend purely by
Compose service name (`UPSTREAM_HOST=app`), which Compose's embedded DNS
resolves to the current `app` container's address automatically -
`compose.yaml` never hardcodes a container IP anywhere. A real custom
network topology (isolating `gateway`'s and `app`'s reachability more
explicitly, multiple networks, etc.) is explicitly **Day 3** scope, not
implemented here - see `docs/roadmap.md`. Nothing in this document or in
`compose.yaml` claims network isolation beyond the Compose default exists
yet.

## Dependency readiness: `depends_on: condition: service_healthy`

`gateway` declares `depends_on: app: condition: service_healthy`, so
Compose does not start the gateway container until `app`'s own
`HEALTHCHECK` first reports `healthy` - not merely "container created" or
"process started". This is a *startup-ordering* guarantee only; it does
not mean `app` stays healthy for the gateway's whole lifetime (see below).

## Health vs. readiness - two different questions, on both services

- **Health** (`HEALTHCHECK`, Docker/Compose-level): "is this container's
  own process alive and responding?" `app`'s healthcheck is
  `python3 -m app.healthcheck` (probes `app`'s own `/healthz`);
  `gateway`'s is `python3 -m gateway.healthcheck` (probes `gateway`'s own
  `/healthz` - liveness only, never the upstream). A single image can
  only declare one `HEALTHCHECK`, so `compose.yaml` overrides it
  per-service; both invocation forms are regression-tested (see below).
- **Readiness** (`gateway`'s own `/readyz` endpoint, application-level):
  "can this service actually do its job right now?" For the gateway,
  that specifically means "can I reach `app`?" - a healthy gateway
  *process* can still be *not ready* if `app` is unreachable. This
  distinction is what the failure/recovery scenario below exercises.

## Integration testing: real stack, not just rendered config

`scripts/compose/check_compose.py` is a **static** check - it validates
`docker compose config`'s *rendered* output (exactly two services,
correct image/version, correct hardening flags, correct healthcheck
commands, `gateway` the sole loopback publisher, no custom network, no
named volume) but never starts a real container. This closes part of Day
1 finding M-3 but not all of it - a valid-looking config could still
describe containers that behave differently once actually running.

`scripts/compose/compose_integration.py` is the **runtime** counterpart,
and is what actually closes M-3: it brings up the real two-service stack
under a uniquely named Compose project, on a dynamic loopback host port,
and inspects the real Compose-*created* containers (not just Compose's
rendered configuration) - reusing `scripts/verify/security_check.py`'s
existing `[C]`/`[D]` container-inspection functions rather than
duplicating that logic a second time - for read-only rootfs, `cap_drop:
[ALL]`, `no-new-privileges`, non-root UID/GID, absence of host PID/
network mode, absence of a Docker-socket mount, and each role's PID 1
identity.

## Failure and recovery behavior (proven, not asserted)

`compose_integration.py` exercises the full scenario end-to-end with
bounded deadlines throughout:

1. Bring the stack up; both services reach Docker `healthy`.
2. Confirm `gateway`'s `/readyz` succeeds and `/upstream/info` proves real
   HTTP communication with `app`.
3. `docker compose stop app` - and confirm the **gateway process stays
   alive** (it is a separate container; stopping `app` does not touch
   it).
4. Poll `gateway`'s `/readyz` until it reports **not-ready** (a controlled
   `503`, not a hang or a crash) - proving the readiness check is a real,
   live upstream probe, not a cached/stale value.
5. `docker compose start app`; wait for `app` to become healthy again.
6. Poll `gateway`'s `/readyz` until it **recovers** to `200 ready`.
7. Tear the entire stack down and confirm no leftover container or
   network remains for that project.

## Runtime hardening on both services

`read_only: true`, `cap_drop: [ALL]`, `security_opt:
[no-new-privileges:true]`, and non-root `10001:10001` execution apply
identically to **both** `app` and `gateway` - Day 2 does not weaken any
Day 1 hardening property, and extends the same [A]/[B]/[C]/[D]
evidence-tiered verification (see `docs/security.md`) to the gateway role
and to Compose-managed containers specifically, not only direct `docker
run` containers.

## What is explicitly not implemented yet (Day 3+)

- No custom Compose network - only the implicit default project network.
- No named/persistent volumes - both services are stateless.
- No database, cache, message broker, or reverse-proxy technology.
- No Compose secrets or Compose configs.
- No resource limits (CPU/memory) - Day 5.
- No CI-enforced verification - Day 6; `make release-check` is the only
  gate today.

Do not read any bullet in this section as already implemented - see
`docs/roadmap.md` for the authoritative day-by-day scope.
