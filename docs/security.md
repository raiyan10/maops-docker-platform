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

**Scope note**: `make security-check` verifies the image and a direct
`docker run` container (the `app` role, image default). The `gateway` and
`state` roles, and Compose-*managed* containers (all three roles, as
Compose actually creates them) are verified separately by `make
compose-test` (`scripts/compose/compose_integration.py`), which
deliberately reuses this script's own `[C]`/`[D]` check functions rather
than duplicating them — see `docs/compose-platform.md` for that evidence.
As of Day 3, this now includes a real [D] rejected-write proof against
the rootfs for every Compose-managed container (see "Read-only root
filesystem" below) — Day 2 only performed that specific proof against the
ad hoc `docker run` container this script itself starts.

## Non-root execution

- *Desired*: `docker/app/Dockerfile`'s final instruction is `USER
  10001:10001`, after `groupadd --gid 10001 appgroup && useradd --uid
  10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin
  appuser`.
- *Docker runtime inspection*: the built image's `Config.User` is
  `10001:10001`.
- *Kernel/process verification*: `docker exec <container>
  /usr/bin/python3.13 -c "import os; print(os.getuid()); print(os.getgid())"`
  returns `10001`/`10001` for the actual running process (Day 4: the
  final runtime is Distroless and has no `id` binary - this stdlib-only
  probe is the shell-free equivalent, same [D] evidence tier). Verified
  2026-08-20 (`maops-docker-platform:0.4.0`): `uid=10001 gid=10001`.

## Read-only root filesystem

- *Desired*: `compose.yaml` sets `read_only: true` for every service,
  including `state` (which needs *some* writable path — see below, that
  path is deliberately not the rootfs). No writable `tmpfs` is mounted —
  no service needs one.
- *Docker runtime inspection*: `docker inspect <container> --format
  '{{.HostConfig.ReadonlyRootfs}}'` reports `true`, for `app`, `gateway`,
  and `state`, both as direct `docker run` containers and as
  Compose-managed containers.
- *Kernel/process verification*: an actual write attempt inside the
  running hardened container was executed and rejected, with the service
  continuing to serve requests afterward. Verified 2026-08-20 (direct
  `docker run`, `app` role, `maops-docker-platform:0.4.0`): a stdlib-only
  Python probe (`open('/etc/maops-readonly-probe', 'w')` - Day 4: the
  final runtime is Distroless and has no shell, so the probe execs
  `/usr/bin/python3.13 -c '...'` directly rather than `sh -c 'echo ...'`)
  raised `PermissionError: [Errno 30] Read-only file system:
  '/etc/maops-readonly-probe''`, and a subsequent `/healthz` probe still
  returned `200`. **As of Day 3**, this exact [D] proof is also performed
  automatically against every Compose-managed container (`app`,
  `gateway`, `state`) by `scripts/compose/compose_integration.py` —
  closing the Day 2 review finding (M-1/L-2,
  `docs/engineering-reviews/day-02-security-review.md`/
  `day-02-compose-review.md`) that this specific proof previously existed
  only for the ad hoc `docker run` container, never for Compose-managed
  ones, despite `docs/compose-platform.md` at the time implying broader
  coverage than the automated suite actually had.

### `state`'s one writable path: `/data`, via a named volume

`state` keeps `read_only: true` like every other service — `/data` (the
`state_data` named volume's mount point) is its *only* writable path, not
an exception carved into the rootfs policy. Proven both ways on the same
container: a write to a protected rootfs path (e.g. `/etc/...`) is
rejected, and a write to `/data` succeeds
(`scripts/compose/compose_integration.py`'s
`check_state_data_write_succeeds`). See `docs/persistence.md` for the
volume-ownership design that makes this work without running `state` as
root.

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
`compose.yaml` declares no Compose secrets (this platform has no secret
material yet — see `docs/configuration.md`) and its `environment:` blocks
carry only `APP_HOST`/`APP_PORT`/`APP_NAME`/`STATE_HOST`/`STATE_PORT`
(app), `GATEWAY_HOST`/`GATEWAY_PORT`/`UPSTREAM_HOST`/`UPSTREAM_PORT`
(gateway), and `STATE_HOST`/`STATE_PORT` (state, its own bind address) —
none of which is sensitive. `config/platform.json` (the new Day 3
Compose-mounted config) is equally non-secret by design and safe to
commit publicly — see `docs/configuration.md`.

## Gateway/app SSRF prevention (Day 2, extended Day 3)

The gateway's outbound HTTP destination (`gateway.config.
GatewayConfig.upstream_host`/`upstream_port`) is resolved exactly once,
at process startup, from `UPSTREAM_HOST`/`UPSTREAM_PORT` — never from an
incoming request's path, query string, header, or body. `gateway/
server.py`'s `_call_upstream()` is the only place an outbound connection
is ever made, and it always connects to that same fixed
`config.upstream_host`/`config.upstream_port` pair, regardless of which
gateway route triggered it (including the new `/state`/`/state/increment`
forwarding routes). `app` mirrors this exactly for its own dependency:
`app/server.py`'s `_call_state()` always connects to the fixed
`config.state_host`/`config.state_port` pair, resolved once at startup
from `STATE_HOST`/`STATE_PORT`. Neither service has any route, parameter,
or header that accepts a client-supplied host/URL, and the Compose-mounted
`config/platform.json` (Day 3) can only ever override the *timeout* bound
on these calls (`dependency_timeout_seconds`) — never the destination
host, which stays environment-variable-only by design (see
`docs/configuration.md`). `scripts/lint/check_source.py` scans `app/`,
`gateway/`, and `state/` with the same AST-based checks — legitimate
stdlib HTTP networking (`http.client`, `socket`, `urllib.parse`) is
permitted (it's `gateway`'s and `app`'s whole job), while shell/process
execution remains forbidden.

## Healthcheck

- *Desired*: `docker/app/Dockerfile` declares `HEALTHCHECK --interval=10s
  --timeout=3s --start-period=5s --retries=3 CMD ["/usr/bin/python3.13",
  "-m", "app.healthcheck"]` as the image-level (app-role) default — a
  stdlib-only probe (`http.client`, not `urllib.request`, to avoid
  proxy-environment-variable interference) that calls the app's own
  `/healthz` over loopback. `compose.yaml` overrides this per-service:
  `gateway`'s healthcheck is `/usr/bin/python3.13 -m gateway.healthcheck`
  and `state`'s is `/usr/bin/python3.13 -m state.healthcheck`, each
  probing only its own `/healthz` (liveness only) — never a dependency,
  so any healthcheck failure always means that service's own process is
  unresponsive, not that something it depends on is down. No `curl`/
  `wget` was installed merely to support any of the three probes. Day 4:
  the absolute interpreter path is required, not stylistic — the final
  runtime is Distroless and has no shell to perform PATH resolution
  against a bare `python3` name.
- *Docker runtime inspection*: every service's `Healthcheck.Test` is
  present and not `NONE`; each running container's `State.Health.Status`
  was polled to a bounded deadline and observed to reach `healthy`.
  Verified 2026-08-20 (`maops-docker-platform:0.4.0`, both direct-`docker
  run` and Compose-managed containers): healthy well inside each
  `start_period`, in the proven order `state` -> `app` -> `gateway`.
- A negative-path check (the probe returning failure when the target is
  unavailable) is proven implicitly by each probe's own logic — any
  non-`200` status, any non-JSON body, any `status != "ok"` field, *or*
  any connection-level `OSError`/`http.client.HTTPException` is treated
  as failure, so an unreachable target returns a controlled `False`/exit
  `1` rather than an uncaught traceback, in all three healthcheck modules.
- *Regression protection*: `scripts/lint/check_dockerfile.py`'s
  `check_healthcheck()` asserts the Dockerfile's `HEALTHCHECK CMD` is
  *exactly* `["/usr/bin/python3.13", "-m", "app.healthcheck"]` (not
  merely "a `HEALTHCHECK` exists"), and `scripts/compose/check_compose.py`
  asserts the exact same for all three services' `healthcheck.test` in
  the rendered Compose config — a regression to a bare `python3`
  invocation (which would depend on shell PATH resolution the Distroless
  final runtime cannot perform) now fails automated validation instead of
  being caught only by `make security-check`'s ~30s runtime health-status
  polling.

### Role-aware liveness (Day 4 — closes finding H-1)

Each service's `/healthz` remains a *local process liveness* check only —
it never calls a dependency, and `/readyz`'s dependency-aware semantics are
unchanged. Prior to this fix, all three services' `/healthz` bodies were
behaviorally identical (`{"status": "ok"}`), so `healthcheck_module_for_role()`
correctly selected a different probe *module name* per role, but the
probe itself had no way to detect that it was talking to the wrong
service — `python -m state.healthcheck` exited `0` against an `app` or
`gateway` container just as readily as against a real `state` container,
the exact scenario the final Day 4 independent review reproduced against
a live container and classified High/fix-before-release.

Each `/healthz` now additionally carries a fixed, non-secret `role` field
identifying which MAOps workload is answering:

```json
{"status": "ok", "role": "app"}
{"status": "ok", "role": "gateway"}
{"status": "ok", "role": "state"}
```

Each of `app.healthcheck`/`gateway.healthcheck`/`state.healthcheck`
now defines its own `EXPECTED_ROLE` constant and accepts a response only
when *both* `status == "ok"` **and** `role == EXPECTED_ROLE` — a
well-formed `{"status": "ok"}` from the wrong service (missing `role`,
or a mismatched one) is rejected, not merely a malformed/unreachable one.
No environment, hostname, container ID, IP address, PID, secret, or
internal configuration is exposed — only the fixed role name.

*Proof*: `scripts/compose/compose_integration.py`'s
`check_role_discrimination_matrix()` runs all three healthcheck modules
against each of the three real, Compose-managed role containers and
asserts the full 3x3 matrix — each container's own role's module exits
`0`, and both other roles' modules exit non-zero, on every container.
Direct-container-level unit tests in `tests/test_healthcheck.py`,
`tests/test_gateway_healthcheck.py`, and `tests/test_state_healthcheck.py`
exercise the real `check()` parsing/validation path (not merely the
`EXPECTED_ROLE` constant) against a stub server returning a correct role,
a wrong role, and a missing role.

## PID 1 / SIGTERM lifecycle (Day 2 — closes Day 1 finding M-2)

Prior to Day 2, every script in this repository only ever force-removed
its containers (`docker rm -f`, SIGKILL-equivalent), so a broken
`signal.signal(SIGTERM, ...)` registration would have produced zero
automated test failures anywhere. `scripts/verify/security_check.py`'s
`check_lifecycle_docker_stop()` now issues a real `docker stop` (real
SIGTERM, bounded 10s grace period) against the running hardened container
and asserts a clean, fast exit (`ExitCode == 0`, `Status == "exited"`,
elapsed well under the grace period) — verified 2026-08-20
(`maops-docker-platform:0.4.0`): exit code 0 in ~0.46s.
`check_kernel_pid1_identity()` additionally asserts PID 1 is genuinely
`/usr/bin/python3.13 -m app` (Day 4: the absolute interpreter path — the
Distroless final runtime has no shell for PATH resolution), read from
`/proc/1/cmdline` inside the container via a stdlib-only Python probe (no
`cat`, which the Distroless runtime also lacks), not merely inferred from
the Dockerfile. This check runs last among container-requiring checks,
since it stops the container. `scripts/compose/compose_integration.py`
proves the equivalent PID 1 identity (`/usr/bin/python3.13 -m app` /
`-m gateway` / `-m state`) for every Compose-managed container.

## Compose-level and Compose-managed-container verification (Day 2 —
closes Day 1 finding M-3; extended Day 3)

`scripts/compose/check_compose.py` is a static check against `docker
compose config`'s *rendered* output only — it never starts a container.
`scripts/compose/compose_integration.py` is the runtime counterpart: it
brings up the real three-service stack and inspects the real
Compose-*created* containers (`app`, `gateway`, `state`), reusing
`security_check.py`'s own `[C]`/`[D]` check functions rather than
duplicating them, for read-only rootfs, `cap_drop: [ALL]`,
`no-new-privileges`, non-root `10001:10001`, absence of host PID/network
mode, absence of a Docker-socket mount, each role's PID 1 identity, and
(new in Day 3) a real [D] rootfs-write-rejection proof and a real
Compose-mounted-config-write-rejection proof for every container — see
`docs/compose-platform.md` for the full failure/recovery scenario this
same script exercises, and `docs/networking.md` for the network
segmentation proofs it also performs.

## Day 4 additions (build/image security and reproducibility)

- **Shellless Distroless final runtime**: the release image's final
  runtime stage is `gcr.io/distroless/python3-debian13:nonroot`, which
  genuinely has no `/bin/sh`/`/bin/bash` (verified: `docker exec
  <container> /bin/sh -c 'echo probe'` fails with "no such file or
  directory" — this is now an *expected*, asserted security property, not
  an accident) and no `apt`/`dpkg` package-manager executable. `pip` and
  `setuptools` are neither importable nor present as executables. See
  `docs/build-security.md` for the full rationale (the originally planned
  `python:3.13-slim` runtime was rejected on 4 unfixed CRITICAL
  `perl-base` findings) and `scripts/build/image_audit.py` for the
  automated proof of all of the above.
- **Two-stage build**: a digest-pinned `python:3.13-slim` builder stage
  (filesystem preparation only — never entering the final image) and the
  digest-pinned Distroless final stage. `scripts/lint/check_dockerfile.py`
  validates both `FROM` pins and asserts no `RUN` instruction exists in
  the shellless final stage.
- **Image-level immutability**: application source is root-owned in the
  built image (no `--chown` on the final stage's `COPY --from=builder`
  instructions), independent of and in addition to the `read_only: true`
  rootfs hardening above. Proven with a real attempted write (a
  stdlib-only Python probe — the Distroless runtime has no shell)
  against a container started with *no* hardening flags at all. See
  `docs/build-security.md` for the full proof and rationale.
- **Numeric runtime UID/GID, not Distroless's own identity**: this
  Dockerfile sets its own explicit `USER 10001:10001` rather than
  inheriting the Distroless `nonroot` tag's baked-in `65532:65532`
  identity — the `nonroot` tag is used only for its minimal, shell-free
  *content*.
- **Deterministic, reproducible builds**: `make build` still uses
  BuildKit's reproducible-builds export mode (unchanged mechanism across
  the migration); `make reproducibility-check` proves exact image-ID
  equality across two independent builds of the two-stage Dockerfile. See
  `docs/build-security.md`.
- **SBOM and vulnerability scanning**: `make sbom`/`sbom-check` (Syft,
  SPDX JSON) and `make vuln-scan` (Trivy, JSON, with an explicit
  Critical/fixable-High-blocks-release policy, unweakened by the runtime
  migration) now exist and are wired into `make release-check`. Neither
  scanner is ever given the Docker socket. See `docs/supply-chain.md` —
  the Distroless-based release image's vulnerability policy genuinely
  passes (Critical=0, fixable High=0), reported honestly alongside the
  15 unfixed-High findings that remain, non-blocking under policy.
- **Project-specific image policy audit**: `scripts/build/image_audit.py`
  (`make image-audit`) validates release-image invariants (tag/version,
  non-root user, OCI metadata truthfulness, package presence, `/data`
  ownership, image-level immutability, absence of repository-only/
  secret-shaped/setuid-setgid/world-writable content, shell absence,
  package-manager absence, pip/setuptools absence, expected Python
  executable).

## Day 5 addition: resource limits (not a security-hardening claim)

`compose.yaml` now declares explicit CPU/memory/PID limits and a bounded
restart policy on all three services, genuinely applied to real
containers. This is a **reliability** property, not a runtime-hardening
one in the sense the rest of this document uses — it bounds resource
*consumption* and *failure recovery*, not attack surface — so the full
design, the config-load-time A-6 timeout-hierarchy fix, and the real
crash/restart/pause proofs live in `docs/reliability.md`, not here. The
[A]/[B]/[C]/[D] evidence-tier discipline this document established is
reused there for the resource/restart proofs (`[C]` real `docker inspect
HostConfig` values, best-effort `[D]` cgroup v2 corroboration).

## Day 6 addition: emergency Debian-security overlay evidence chain

`docs/build-security.md` and `docs/supply-chain.md` cover the full Day 6
`libssl3t64`/CVE-2026-14456 remediation; this section places it in the
`[A]`/`[B]`/`[C]`/`[D]` evidence discipline this document established.
The overlay's real-payload claim is backed by a full evidence chain, not
a single layer of trust:

- **`[A]` source/config** — `scripts/lint/check_dockerfile.py` verifies
  the `security-patch` stage's `ADD --checksum=` matches
  `security/runtime-patches.lock`'s pinned URL/SHA256 exactly, and that
  the final stage actually `COPY --from=security-patch`s the patched
  payload — a Dockerfile that fetched the right package but forgot to
  copy it anywhere is still caught.
- **`[B]` image inspection** — `scripts/build/image_audit.py` reads the
  **built image's** own `/var/lib/dpkg/status.d/libssl3t64` and confirms
  it reports the fixed `Version:`.
- **`[D]` kernel/process verification** — the same script computes the
  live content hash of `libssl.so.3`/`libcrypto.so.3` **inside the built
  image** and compares it against the hashes pinned in
  `runtime-patches.lock` (independently verified against the official
  Debian Security `.deb` beforehand — see `docs/supply-chain.md`), and
  execs Python's own `ssl` module inside the image to confirm it loads,
  reports the patched OpenSSL version, and successfully constructs an
  `SSLContext`. A `[B]`-only claim ("status.d says 3.5.7") is never
  presented as proof the real binaries were replaced — the `[D]`
  content-hash and runtime checks are what actually prove that.

## Day 4 limitations (deliberately not implemented yet)

- As of Day 5, gates were local (`make release-check`) only. Day 6
  (`docs/ci-cd.md`) now runs the same gates automatically via GitHub
  Actions on every pull request and push to `main`, with least-privilege
  permissions, SHA-pinned actions, and no `pull_request_target` — CI/CD
  security posture is documented there, not repeated in this file.
- No cryptographic build provenance/attestation/signing — deferred past
  Day 4, see `docs/build-security.md`.
- DNS-resolution-phase bound for `app`'s/`gateway`'s outbound dependency
  calls — see `docs/persistence.md`'s scope-limitations note; only
  observable when the target hostname cannot resolve at all, which never
  happens inside a correctly configured Compose stack.
- This document reflects a point-in-time verification run (2026-08-20,
  `maops-docker-platform:0.4.0`, Distroless-based release image). Re-run
  `make security-check`, `make image-audit`, and `make compose-test`
  after any change to `docker/app/Dockerfile`, `compose.yaml`, `app/`,
  `gateway/`, or `state/` before trusting these figures again.
