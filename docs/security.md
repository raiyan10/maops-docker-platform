# Security

## How to read this document

Every claim below is labeled with which of three evidence tiers backs it
(`scripts/verify/security_check.py` uses a finer four-way split of the
same idea — see its module docstring — but the three tiers below are the
ones that matter for a reader deciding how much to trust a claim):

- **Desired/source configuration** — what `docker/app/Dockerfile` and
  `compose.yaml` *declare*. This is a statement of intent, not proof of
  anything at runtime.
- **Docker runtime inspection** — what `docker inspect` reports about a
  *running* container. This proves Docker was *asked* to configure
  something; it does not by itself prove the kernel enforced it.
- **Kernel/process verification** — facts read from inside the running
  container's own process/kernel state (e.g. `/proc/1/status`), or a real
  attempted action (e.g. an actual rejected write). This is the only tier
  that proves *enforcement*, not just *configuration*.

A claim never appears here backed by only the first or second tier when a
third-tier check exists and was run — `make security-check` runs all
three tiers for every applicable control and is the source of the
evidence quoted below.

**Day 2 scope note**: `make security-check` verifies the image and a
direct `docker run` container (the `app` role, image default). The
`gateway` role and Compose-*managed* containers (both roles, as Compose
actually creates them) are verified separately by `make compose-test`
(`scripts/compose/compose_integration.py`), which deliberately reuses
this script's own `[C]`/`[D]` check functions rather than duplicating
them — see `docs/compose-platform.md` for that evidence.

## Non-root execution

- *Desired*: `docker/app/Dockerfile`'s final instruction is `USER
  10001:10001`, after `groupadd --gid 10001 appgroup && useradd --uid
  10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin
  appuser`.
- *Docker runtime inspection*: the built image's `Config.User` is
  `10001:10001`.
- *Kernel/process verification*: `docker exec <container> id -u` / `id
  -g` both return `10001` for the actual running process. Verified
  2026-08-18: `uid=10001 gid=10001`.

## Read-only root filesystem

- *Desired*: `compose.yaml` sets `read_only: true`. No writable `tmpfs`
  is mounted — the application never writes anything, so none is needed.
- *Docker runtime inspection*: `docker inspect <container> --format
  '{{.HostConfig.ReadonlyRootfs}}'` reports `true`.
- *Kernel/process verification*: an actual write attempt inside the
  running hardened container was executed and rejected, with the service
  continuing to serve requests afterward. Verified 2026-08-18:
  `sh -c 'echo probe > /etc/maops-readonly-probe'` exited non-zero with
  `sh: cannot create /etc/maops-readonly-probe: Read-only file system`,
  and a subsequent `/healthz` probe still returned `200`.

## Capabilities

- *Desired*: `compose.yaml` sets `cap_drop: [ALL]`.
- *Docker runtime inspection*: `docker inspect <container> --format
  '{{.HostConfig.CapDrop}}'` reports `[ALL]`. **This alone is not proof
  of enforcement** — it only proves what was requested.
- *Kernel/process verification*: `/proc/1/status` inside the running
  container was read directly. Verified 2026-08-18: `CapEff=
  0000000000000000 CapPrm=0000000000000000 CapBnd=0000000000000000` —
  the effective, permitted, and bounding capability sets are all empty.

## no-new-privileges

- *Desired*: `compose.yaml` sets `security_opt: [no-new-privileges:true]`.
- *Docker runtime inspection*: `docker inspect <container> --format
  '{{.HostConfig.SecurityOpt}}'` reports `[no-new-privileges:true]`.
- *Kernel/process verification*: `/proc/1/status`'s `NoNewPrivs` field.
  Verified 2026-08-18: `NoNewPrivs=1`.

## Namespaces and Docker socket

- *Desired*: `compose.yaml` sets none of `privileged`, `network_mode:
  host`, `pid: host`, and mounts nothing.
- *Docker runtime inspection*, verified 2026-08-18:
  `HostConfig.Privileged=false`, `HostConfig.PidMode=""` (not `host`),
  `HostConfig.NetworkMode="bridge"` (not `host`), and `.Mounts` contains
  no entry whose source or destination mentions `docker.sock`.
- No kernel-level check is meaningful here beyond the Docker-runtime
  inspection above — there is no "effective" state to separately observe
  for "a socket wasn't mounted."

## Image content / build-context exclusions

`.dockerignore` uses genuinely recursive patterns (`**/__pycache__/`,
`**/*.pyc`, `**/*.pyo`) rather than a one-level glob — a real prior
review finding was `app/__pycache__/*.pyc`, which only matches one
specific nesting depth and silently lets a nested `__pycache__`
directory (e.g. `app/nested/deep/__pycache__/x.pyc`) leak into the image.

This is not asserted from reading the pattern alone. It was proven with
an actual nested probe: `app/__pycache__/probe.pyc` and
`app/nested/deep/__pycache__/probe.pyc` were created, the image was built
with `docker build --no-cache`, and the built image's `/app` tree was
walked (`docker run --rm --entrypoint find <image> /app -iname '*.pyc'
-o -iname '__pycache__'`) — zero matches, at any depth. The probe files
were then removed and the image rebuilt clean for release. See
`scripts/lint/check_source.py`'s sibling proof inside
`scripts/verify/security_check.py`: `recursive_find_forbidden_bytecode()`
uses `Path.rglob()` (genuinely recursive, bounded to the extracted image
tree, never `/proc`/`/sys`/`/dev`) and ships its own regression test —
`regression_prove_recursive_detection()` — which builds a synthetic
fixture with a `__pycache__` nested three directories deep and asserts
the scanner actually catches it. This exists specifically because a
*prior* implementation of this same checker used one-level `os.listdir()`
logic and missed nested content; the regression test exists so that
defect can't silently return.

The same extraction is also used to confirm no repository-only file
(`.git`, `.claude`, `.github`, `tests/`, `docs/`, `README.md`,
`scripts/`, `compose.yaml`, `.dockerignore`) exists anywhere under the
image's `/app` tree.

## Secrets

No `ARG`/`ENV` in `docker/app/Dockerfile` carries a secret, credential,
or token — `scripts/lint/check_dockerfile.py` checks every `ARG`/`ENV`
variable name against a pattern covering `PASSWORD`/`SECRET`/`TOKEN`/
`API_KEY`/`PRIVATE_KEY`/`ACCESS_KEY`/`CREDENTIAL` (case-insensitive).
`compose.yaml` declares no Compose secrets (out of Day 1/2 scope) and its
`environment:` blocks carry only `APP_HOST`/`APP_PORT`/`APP_NAME` (app)
and `GATEWAY_HOST`/`GATEWAY_PORT`/`UPSTREAM_HOST`/`UPSTREAM_PORT`
(gateway) — none of which is sensitive.

## Gateway SSRF prevention (Day 2)

The gateway's outbound HTTP destination (`gateway.config.
GatewayConfig.upstream_host`/`upstream_port`) is resolved exactly once,
at process startup, from `UPSTREAM_HOST`/`UPSTREAM_PORT` — never from an
incoming request's path, query string, header, or body. `gateway/
server.py`'s `_call_upstream()` is the only place an outbound connection
is ever made, and it always connects to that same fixed
`config.upstream_host`/`config.upstream_port` pair, regardless of which
gateway route triggered it. There is no route, parameter, or header that
accepts a client-supplied host/URL. `scripts/lint/check_source.py` scans
`gateway/` (not just `app/`) with the same AST-based checks used for the
app role — legitimate stdlib HTTP networking (`http.client`, `socket`,
`urllib.parse`) is permitted (it's the gateway's whole job), while
shell/process execution remains forbidden.

## Healthcheck

- *Desired*: `docker/app/Dockerfile` declares `HEALTHCHECK --interval=10s
  --timeout=3s --start-period=5s --retries=3 CMD ["python3", "-m",
  "app.healthcheck"]` as the image-level (app-role) default — a
  stdlib-only probe (`http.client`, not `urllib.request`, to avoid
  proxy-environment-variable interference) that calls the app's own
  `/healthz` over loopback. `compose.yaml` overrides this per-service:
  `gateway`'s healthcheck is `python3 -m gateway.healthcheck`, which
  probes the gateway's *own* `/healthz` (liveness only) — never the
  upstream `app`, so a gateway healthcheck failure always means the
  gateway process itself is unresponsive, not that `app` is down. No
  `curl`/`wget` was installed merely to support either probe.
- *Docker runtime inspection*: both images'/services' `Healthcheck.Test`
  is present and not `NONE`; each running container's
  `State.Health.Status` was polled to a bounded deadline and observed to
  reach `healthy`. Verified 2026-08-19 (`maops-docker-platform:0.2.0`,
  both direct-`docker run` and Compose-managed containers): healthy well
  inside each `start_period`.
- A negative-path check (the probe returning failure when the target is
  unavailable) is proven implicitly by each probe's own logic — any
  non-`200` status, any non-JSON body, any `status != "ok"` field, *or*
  any connection-level `OSError`/`http.client.HTTPException` (added in
  Day 2 for both `app/healthcheck.py` and `gateway/healthcheck.py`, so an
  unreachable target returns a controlled `False`/exit `1` rather than an
  uncaught traceback) is treated as failure.
- *Regression protection*: `scripts/lint/check_dockerfile.py`'s
  `check_healthcheck()` asserts the Dockerfile's `HEALTHCHECK CMD` is
  *exactly* `["python3", "-m", "app.healthcheck"]` (not merely "a
  `HEALTHCHECK` exists"), and `scripts/compose/check_compose.py` asserts
  the exact same for both services' `healthcheck.test` in the rendered
  Compose config — a regression to the broken bare-script form
  (`python3 app/healthcheck.py`, which breaks because `/app` isn't on
  `sys.path` for a bare script) now fails automated validation instead of
  being caught only by `make security-check`'s ~30s runtime health-status
  polling.

## PID 1 / SIGTERM lifecycle (Day 2 — closes Day 1 finding M-2)

Prior to Day 2, every script in this repository only ever force-removed
its containers (`docker rm -f`, SIGKILL-equivalent), so a broken
`signal.signal(SIGTERM, ...)` registration would have produced zero
automated test failures anywhere. `scripts/verify/security_check.py`'s
`check_lifecycle_docker_stop()` now issues a real `docker stop` (real
SIGTERM, bounded 10s grace period) against the running hardened container
and asserts a clean, fast exit (`ExitCode == 0`, `Status == "exited"`,
elapsed well under the grace period) — verified 2026-08-19: exit code 0
in ~0.55-0.73s. `check_kernel_pid1_identity()` additionally asserts PID 1
is genuinely `python3 -m app`, read from `/proc/1/cmdline` inside the
container, not merely inferred from the Dockerfile. This check runs last
among container-requiring checks, since it stops the container.

## Compose-level and Compose-managed-container verification (Day 2 —
closes Day 1 finding M-3)

`scripts/compose/check_compose.py` is a static check against `docker
compose config`'s *rendered* output only — it never starts a container.
`scripts/compose/compose_integration.py` is the runtime counterpart: it
brings up the real two-service stack and inspects the real
Compose-*created* containers (both `app` and `gateway`), reusing
`security_check.py`'s own `[C]`/`[D]` check functions rather than
duplicating them, for read-only rootfs, `cap_drop: [ALL]`,
`no-new-privileges`, non-root `10001:10001`, absence of host PID/network
mode, absence of a Docker-socket mount, and each role's PID 1 identity —
see `docs/compose-platform.md` for the full failure/recovery scenario
this same script exercises.

## Day 1/2 limitations (deliberately not implemented yet)

- No vulnerability scanner, no SBOM generation — planned for Day 4 (build/
  image security and reproducibility); see `docs/roadmap.md`.
- No resource limits (CPU/memory) — planned for Day 5.
- No CI-enforced verification — gates are local (`make release-check`)
  only; Day 6 adds CI/CD.
- No multi-stage build — the current image has no build-time toolchain to
  strip out (stdlib-only application), so a second stage would add
  complexity without a corresponding benefit yet; revisit if a future
  day's dependency adds a compiler/toolchain.
- No custom Compose network beyond the implicit default — Day 3.
- This document reflects a point-in-time verification run (2026-08-19,
  `maops-docker-platform:0.2.0`). Re-run `make security-check` and `make
  compose-test` after any change to `docker/app/Dockerfile`,
  `compose.yaml`, `app/`, or `gateway/` before trusting these figures
  again.
