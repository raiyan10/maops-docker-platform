---
name: compose-validation
description: Reusable docker compose config/start/inspect/functional/down/cleanup procedure for maops-docker-platform, meant to grow as the service topology grows across Days 1-7. Use when changing compose.yaml or verifying the Compose-managed application lifecycle.
---

# Compose Validation

Reusable Compose lifecycle procedure. Day 1 has exactly one service
(`app`); this same procedure is meant to extend — not be replaced — as
later days add services, networks, or volumes.

## Procedure

1. **Static validation** — confirm the file parses and resolves the way
   you expect (image tag, env, port mapping, hardening flags):
   ```bash
   docker compose config
   ```

2. **Start the real stack**:
   ```bash
   docker compose up -d
   ```

3. **Wait for and confirm health**, bounded (do not poll forever):
   ```bash
   docker inspect maops-docker-platform-app --format '{{.State.Health.Status}}'
   ```
   Poll until `healthy` or a deadline is reached.

4. **Verify effective runtime restrictions**, not just that Compose
   *started* — this is the same [C]/[D] distinction
   `container-security-validation` uses:
   ```bash
   docker inspect maops-docker-platform-app --format \
     'ReadonlyRootfs={{.HostConfig.ReadonlyRootfs}} CapDrop={{.HostConfig.CapDrop}} SecurityOpt={{.HostConfig.SecurityOpt}}'
   docker exec maops-docker-platform-app id
   ```

5. **Functional check** — actually call the service, don't just trust
   the health status:
   ```bash
   python3 -c "
   import http.client, json
   conn = http.client.HTTPConnection('127.0.0.1', 8080, timeout=5)
   for path in ('/', '/healthz', '/readyz', '/info'):
       conn.request('GET', path)
       r = conn.getresponse()
       print(path, r.status, r.read())
   "
   ```

6. **Tear down and confirm cleanliness**:
   ```bash
   docker compose down
   docker ps -a --filter "name=maops-docker-platform" --format '{{.Names}}'
   docker network ls --filter "name=maops-docker-platform" --format '{{.Name}}'
   ```
   Both filtered listings must be empty afterward — no leftover
   container or network.

## Extending across Days 2-7

When a later day adds a service, network, or volume:

- Add its own health/functional check to step 3-5 rather than only
  checking the original `app` service.
- Keep the teardown-cleanliness check (step 6) covering every service
  Compose now manages, not just the first one.
- Never let a multi-service topology reintroduce `network_mode: host`,
  `pid: host`, a Docker socket mount, or a host filesystem bind mount —
  the Day 1 hardening baseline in `compose.yaml` must survive every
  later day's growth, not just the first review.
