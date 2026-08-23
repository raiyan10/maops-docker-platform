---
name: compose-validation
description: Reusable docker compose config/start/inspect/functional/down/cleanup procedure for maops-docker-platform, meant to grow as the service topology grows across Days 1-7. Use when changing compose.yaml or verifying the Compose-managed application lifecycle.
---

# Compose Validation

Reusable Compose lifecycle procedure. As of Day 3 there are exactly three
services (`state`, `app`, `gateway`) across two explicit networks (`edge`,
`backend`), one named volume (`state_data`), and one Compose config
object (`platform`); this same procedure is meant to extend — not be
replaced — as later days add more.

`make compose-check` (`scripts/compose/check_compose.py`) and `make
compose-test` (`scripts/compose/compose_integration.py`) now automate
most of this procedure end-to-end — `check_compose.py` covers step 1's
structural invariants, `compose_integration.py` covers steps 2-7
including the network-isolation, startup-ordering, and persistence
proofs. Use the manual steps below when investigating a specific failure,
extending coverage, or cross-verifying the automated scripts' own claims.

## Procedure

1. **Static validation** — confirm the file parses and resolves the way
   you expect (all three services present, image tag/version, env, port
   mapping, hardening flags, healthchecks, `depends_on` chain, network
   membership, the named volume, the mounted config):
   ```bash
   docker compose config
   python3 scripts/compose/check_compose.py
   ```

2. **Start the real stack** (use a project name so you don't collide with
   another run):
   ```bash
   docker compose -p maops-compose-manual up -d
   ```

3. **Wait for and confirm health of all three services**, bounded (do not
   poll forever), and check the ordering, not just the end state:
   ```bash
   for c in state app gateway; do
     docker inspect maops-compose-manual-$c-1 --format '{{.State.Health.Status}}'
   done
   docker inspect maops-compose-manual-state-1 --format '{{json (index .State.Health.Log 0).End}}'
   docker inspect maops-compose-manual-app-1 --format '{{json .State.StartedAt}}'
   # app's StartedAt should be >= state's first-healthy time; same for gateway vs. app
   ```

4. **Verify effective runtime restrictions on *all three* containers**, not
   just that Compose *started* them — this is the same [C]/[D]
   distinction `container-security-validation` uses, and
   `compose_integration.py` automates it by directly reusing
   `security_check.py`'s own check functions rather than a separate
   implementation. **Day 4: the release image's final runtime is
   Distroless (no shell, no `id`/`cat`) — every probe execs the absolute
   `/usr/bin/python3.13` interpreter directly:**
   ```bash
   for c in maops-compose-manual-state-1 maops-compose-manual-app-1 maops-compose-manual-gateway-1; do
     docker inspect "$c" --format \
       'ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} SecurityOpt={{.HostConfig.SecurityOpt}}'
     docker exec "$c" /usr/bin/python3.13 -c "import os; print(os.getuid(), os.getgid())"
     docker exec "$c" /usr/bin/python3.13 -c "from pathlib import Path; print(Path('/proc/1/cmdline').read_text())"
     docker exec "$c" /usr/bin/python3.13 -c "open('/etc/maops-manual-probe', 'w').write('x')" # must fail: read-only
   done
   docker exec maops-compose-manual-state-1 /usr/bin/python3.13 -c "
   import os
   open('/data/manual-probe', 'w').write('x')
   os.remove('/data/manual-probe')
   "  # must succeed
   ```

5. **Network membership and isolation** — confirm real DNS resolution
   succeeds along the intended path and fails across the isolation
   boundary:
   ```bash
   docker exec maops-compose-manual-gateway-1 /usr/bin/python3.13 -c "import socket; socket.gethostbyname('app')"     # succeeds
   docker exec maops-compose-manual-app-1 /usr/bin/python3.13 -c "import socket; socket.gethostbyname('state')"       # succeeds
   docker exec maops-compose-manual-gateway-1 /usr/bin/python3.13 -c "import socket; socket.gethostbyname('state')"   # must fail
   docker exec maops-compose-manual-state-1 /usr/bin/python3.13 -c "import socket; socket.gethostbyname('gateway')"   # must fail
   ```

6. **Functional check through the gateway** — `app`/`state` have no
   published port, so every check goes through `gateway`'s loopback port;
   find it first, then actually call the service, don't just trust health
   status (this uses the host's own `python3`, not the container's —
   `http.client` runs on the host against the mapped loopback port):
   ```bash
   docker port maops-compose-manual-gateway-1 8080/tcp
   python3 -c "
   import http.client, json
   conn = http.client.HTTPConnection('127.0.0.1', <mapped-port>, timeout=5)
   for method, path in (('GET','/'), ('GET','/healthz'), ('GET','/readyz'), ('GET','/state'), ('POST','/state/increment')):
       conn.request(method, path)
       r = conn.getresponse()
       print(method, path, r.status, r.read())
   "
   ```
   Confirm `/state`/`/state/increment` genuinely reflect a real,
   persisted value through the full `gateway -> app -> state` chain, not
   a canned response.

7. **Failure/recovery scenario** — stop `state`, confirm `app` and
   `gateway` processes stay alive while `gateway`'s `/readyz` degrades to
   a controlled `503`, then restart `state` and confirm recovery:
   ```bash
   docker compose -p maops-compose-manual stop state
   docker inspect maops-compose-manual-app-1 --format '{{.State.Running}}'      # must be true
   docker inspect maops-compose-manual-gateway-1 --format '{{.State.Running}}'  # must be true
   # poll gateway /readyz -> expect non-200 / {"status": "not-ready", ...}
   docker compose -p maops-compose-manual start state
   # wait for state healthy again, then poll gateway /readyz -> expect 200 {"status": "ready"}
   ```

8. **Persistence across recreation** — recreate `state` alone (volume
   retained) and confirm the value survived:
   ```bash
   docker compose -p maops-compose-manual up -d --force-recreate --no-deps state
   # re-poll GET /state through gateway -> same value as before recreation
   ```

9. **Tear down and confirm cleanliness** — include `-v` only for a test's
   own uniquely named project, never for a normal development stack:
   ```bash
   docker compose -p maops-compose-manual down -v
   docker ps -a --filter "name=maops-compose-manual" --format '{{.Names}}'
   docker network ls --filter "name=maops-compose-manual" --format '{{.Name}}'
   docker volume ls --filter "name=maops-compose-manual" --format '{{.Name}}'
   ```
   All three filtered listings must be empty afterward — no leftover
   container, network, or volume.

## Day 4 additions to steps 4 and 5

- Step 4's rootfs-write-rejection reuse of `security_check.py` is now
  **role-aware**: `compose_integration.py` calls
  `check_kernel_readonly_write_fails(container, port, role=name)`, so the
  "service kept serving" half genuinely probes that container's own
  `state.healthcheck`/`app.healthcheck`/`gateway.healthcheck` module, not
  a hardcoded `app.healthcheck` regardless of role (closes Day 3 finding
  A-2).
- A real, live `docker network inspect` proof for step 5's network-
  isolation claim: `backend`'s `Internal` field is `true` and `edge`'s is
  `false`, checked against the actual running network object
  (`docker network inspect <project>_backend --format '{{json .Internal}}'`),
  not merely the rendered `compose.yaml` config `check_compose.py`
  already checks (closes Day 3 finding A-3).
  ```bash
  docker network inspect maops-compose-manual_backend --format '{{json .Internal}}'  # true
  docker network inspect maops-compose-manual_edge --format '{{json .Internal}}'     # false
  ```

## Extending across Days 4-7

When a later day adds a resource limit, restart policy, or CI-driven
verification:

- Add its own health/functional check to the relevant step above rather
  than only checking `state`/`app`/`gateway`.
- Keep the teardown-cleanliness check (step 9) covering every resource
  Compose now manages, not just the current three services/two networks/
  one volume/one config.
- Never let further growth reintroduce `network_mode: host`, `pid: host`,
  a Docker socket mount, or an arbitrary host filesystem bind mount — the
  Day 1-3 hardening baseline in `compose.yaml` must survive every later
  day's growth, not just the first review.
- Keep `app`/`state` non-host-published unless a later day's scope
  explicitly requires otherwise — `gateway` (or whatever becomes the edge
  service) should remain the only host-facing surface.
- Never remove a persisted named volume except via that specific test
  project's own `down -v` — no global `docker volume prune`, ever.
