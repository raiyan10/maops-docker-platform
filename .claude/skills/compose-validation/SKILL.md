---
name: compose-validation
description: Reusable docker compose config/start/inspect/functional/down/cleanup procedure for maops-docker-platform, meant to grow as the service topology grows across Days 1-7. Use when changing compose.yaml or verifying the Compose-managed application lifecycle.
---

# Compose Validation

Reusable Compose lifecycle procedure. As of Day 2 there are exactly two
services (`app`, `gateway`); this same procedure is meant to extend — not
be replaced — as later days add networks or volumes.

`make compose-check` (`scripts/compose/check_compose.py`) and `make
compose-test` (`scripts/compose/compose_integration.py`) now automate
most of this procedure end-to-end — `check_compose.py` covers step 1's
structural invariants, `compose_integration.py` covers steps 2-6
including the failure/recovery scenario. Use the manual steps below when
investigating a specific failure, extending coverage, or cross-verifying
the automated scripts' own claims.

## Procedure

1. **Static validation** — confirm the file parses and resolves the way
   you expect (both services present, image tag/version, env, port
   mapping, hardening flags, healthchecks, `depends_on`):
   ```bash
   docker compose config
   python3 scripts/compose/check_compose.py
   ```

2. **Start the real stack** (use a project name so you don't collide with
   another run):
   ```bash
   docker compose -p maops-compose-manual up -d
   ```

3. **Wait for and confirm health of both services**, bounded (do not poll
   forever):
   ```bash
   docker inspect maops-compose-manual-app-1 --format '{{.State.Health.Status}}'
   docker inspect maops-compose-manual-gateway-1 --format '{{.State.Health.Status}}'
   ```
   Poll each until `healthy` or a deadline is reached.

4. **Verify effective runtime restrictions on *both* containers**, not
   just that Compose *started* them — this is the same [C]/[D]
   distinction `container-security-validation` uses, and
   `compose_integration.py` automates it by directly reusing
   `security_check.py`'s own check functions rather than a separate
   implementation:
   ```bash
   for c in maops-compose-manual-app-1 maops-compose-manual-gateway-1; do
     docker inspect "$c" --format \
       'ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} SecurityOpt={{.HostConfig.SecurityOpt}}'
     docker exec "$c" id
     docker exec "$c" cat /proc/1/cmdline
   done
   ```

5. **Functional check through the gateway** — `app` has no published
   port, so every check goes through `gateway`'s loopback port; find it
   first, then actually call the service, don't just trust health status:
   ```bash
   docker port maops-compose-manual-gateway-1 8080/tcp
   python3 -c "
   import http.client, json
   conn = http.client.HTTPConnection('127.0.0.1', <mapped-port>, timeout=5)
   for path in ('/', '/healthz', '/readyz', '/upstream/info'):
       conn.request('GET', path)
       r = conn.getresponse()
       print(path, r.status, r.read())
   "
   ```
   Confirm `/upstream/info` actually reflects `app`'s real `/info`
   response (proves gateway→app communication, not a canned value).

6. **Failure/recovery scenario** — stop `app`, confirm `gateway`'s
   process stays alive while its `/readyz` degrades to a controlled
   `503`, then restart `app` and confirm `gateway` recovers:
   ```bash
   docker compose -p maops-compose-manual stop app
   docker inspect maops-compose-manual-gateway-1 --format '{{.State.Running}}'   # must be true
   # poll /readyz -> expect non-200 / {"status": "not-ready", ...}
   docker compose -p maops-compose-manual start app
   # wait for app healthy again, then poll /readyz -> expect 200 {"status": "ready"}
   ```

7. **Tear down and confirm cleanliness**:
   ```bash
   docker compose -p maops-compose-manual down
   docker ps -a --filter "name=maops-compose-manual" --format '{{.Names}}'
   docker network ls --filter "name=maops-compose-manual" --format '{{.Name}}'
   ```
   Both filtered listings must be empty afterward — no leftover
   container or network.

## Extending across Days 3-7

When a later day adds a network or volume:

- Add its own health/functional check to steps 3-6 rather than only
  checking `app`/`gateway`.
- Keep the teardown-cleanliness check (step 7) covering every service
  Compose now manages, not just the first two.
- Never let further growth reintroduce `network_mode: host`, `pid: host`,
  a Docker socket mount, or a host filesystem bind mount — the Day 1/2
  hardening baseline in `compose.yaml` must survive every later day's
  growth, not just the first review.
- Keep `app` non-host-published unless a later day's scope explicitly
  requires otherwise — `gateway` (or whatever becomes the edge service)
  should remain the only host-facing surface.
