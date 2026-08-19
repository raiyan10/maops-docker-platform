# Day 2 Independent Architecture & Container Security Review

Repository: `maops-docker-platform`
Branch: `feature/day-2-compose-platform`
Target: v0.2.0
Reviewer: independent review agent (review-only; no implementation trust assumed)
Review date: 2026-08-19
Scope: Day 2 (Compose multi-service topology) per `.claude/CLAUDE.md` and `docs/roadmap.md`

This review re-derived every claim from source, from a freshly built image,
and from real running containers — Compose-managed and ad hoc — rather than
trusting the implementation session's own report or prior-day documents.
Where the automated tooling's own coverage had a gap, that gap was closed
manually and reported as a finding rather than silently patched (this
review made no implementation changes).

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 1 |
| Low      | 1 |

No Critical or High findings. Both findings below are coverage/documentation
gaps, not runtime security defects — in both cases this review independently
proved the underlying property (read-only enforcement, Compose+security
integration) actually holds; the gap is that the *automated* suite doesn't
prove it, or a doc oversells what the automated suite proves.

---

## Findings

### M-1 (Medium): Compose-managed containers' read-only rootfs is proven only at [C] (Docker was asked), never at [D] (kernel actually enforces it), by the automated suite — and one doc claims otherwise

**Where**: `scripts/compose/compose_integration.py` (per-container check list,
lines ~280-301); `docs/compose-platform.md` lines 156-164.

`scripts/verify/security_check.py` has a real [D]-tier check,
`check_kernel_readonly_write_fails()`, that attempts an actual write inside
the running container and asserts it is rejected by the kernel *and* the
service keeps serving afterward. This check exists and passes — but it is
only ever run against the single ad hoc `docker run` container
(`app` role, default `CMD`) that `security_check.py` itself starts.

`compose_integration.py` reuses `security_check.py`'s [C]/[D] check
functions for both Compose-managed containers, but its reuse list
(`check_runtime_readonly_rootfs`, `check_runtime_cap_drop_all`,
`check_runtime_no_new_privileges`, `check_runtime_not_privileged`,
`check_runtime_no_host_pid`, `check_runtime_no_host_network`,
`check_runtime_no_docker_socket`, `check_kernel_effective_uid_gid`,
`check_kernel_capabilities_effective`, `check_kernel_no_new_privs`, plus a
PID-1-identity check) omits `check_kernel_readonly_write_fails`. So for
`gateway` and for the Compose-managed `app` container specifically, the
read-only property is only ever proven at [C] (`docker inspect
.HostConfig.ReadonlyRootfs == true`, i.e. "Docker was asked to configure
read-only") by the automated suite — never at [D] (a real write actually
rejected by the kernel on *those* containers).

`docs/compose-platform.md` (the "Runtime hardening on both services"
section) states: *"`read_only: true` ... appl[ies] identically to both
`app` and `gateway` ... and extends the same [A]/[B]/[C]/[D]
evidence-tiered verification ... to the gateway role and to
Compose-managed containers specifically"* — this overstates the automated
suite's actual coverage for the read-only property on Compose-managed
containers (it is real for capabilities/UID-GID/NoNewPrivs, which do have
[D] checks in `compose_integration.py`, but not for read-only). By
contrast, `docs/security.md`'s own Day 2 section describing the same
integration test lists exactly what is reused and correctly omits any
claim of a Compose-managed real-write proof — the two docs disagree with
each other on this point.

**Independent verification performed by this review** (see "Runtime
security" section below): a real `echo probe > /etc/...` write was
attempted against both freshly-started Compose-managed containers
(`app` and `gateway`) in a project this review created, unique-named and
fully torn down afterward. Both rejected the write
(`Read-only file system`, exit 2) and both remained functional
immediately afterward. **The underlying security property is real and
holds for both Compose services** — this is a coverage/documentation gap
in the automated regression suite and in `docs/compose-platform.md`'s
wording, not a live vulnerability.

**Impact**: a future regression that silently dropped `read_only: true`
from the `gateway` service in `compose.yaml` (or from `app`'s Compose
entry specifically, as opposed to the ad hoc `docker run` flags
`security_check.py` passes independently) would still pass
`make compose-test` and `make release-check` today, because nothing in
the automated Compose-container path attempts a real write.
`check_compose.py`'s static `read_only: true` config check would still
catch a `compose.yaml` edit, but not a build-time or entrypoint-level
regression that defeated read-only enforcement while `compose.yaml`
still declared it correctly.

**Recommendation** (not applied — review only): add
`check_kernel_readonly_write_fails(container, port)` (or an equivalent
without the unused `port` parameter) to `compose_integration.py`'s
per-container loop for both `app` and `gateway`, and correct
`docs/compose-platform.md`'s wording to match what `docs/security.md`
already says accurately.

---

### L-1 (Low): `scripts/smoke/container_smoke.py` (`make smoke`) exercises only the `app` role; no smoke-level coverage of the `gateway` role's default `docker run` behavior outside Compose

**Where**: `scripts/smoke/container_smoke.py` (172 lines; no reference to
`gateway` anywhere in the file).

This is Day 1's smoke script, unchanged in Day 2. It is not itself
inaccurate — its own docstring/output never claims gateway coverage — and
the gateway role *is* exercised thoroughly by `make compose-test`
(`compose_integration.py`), including its own healthcheck, readiness
logic, and hardening. This finding is only that a reader running `make
smoke` alone (without `make compose-test`) gets zero signal about whether
`docker run ... -m gateway` (i.e. the second role of the one-image, two
role design, run directly rather than via Compose) still starts and
serves correctly. Low severity because Compose is the documented,
supported way to run `gateway` (it needs `UPSTREAM_HOST=app` to be
meaningful at all), and `make release-check` does independently prove the
gateway role via Compose.

**Recommendation** (not applied): note this scope boundary explicitly in
`scripts/smoke/container_smoke.py`'s own docstring (per this project's own
convention of each script "honestly" stating its scope), or extend it to
smoke-test both roles directly. Not release-blocking.

---

## Verified facts (independent re-derivation)

### Test count

`make test` → **78 tests, `OK`**, 21.2s. Independently cross-checked by
counting `def test_` across every file in `tests/`:
`test_config.py`(18) + `test_gateway_config.py`(18) + `test_server.py`(15)
+ `test_gateway_server.py`(20) + `test_version.py`(3) +
`test_healthcheck.py`(2) + `test_gateway_healthcheck.py`(2) = **78**,
matching exactly.

### Dockerfile checks

`make dockerfile-check` → **9/9 checks pass** against
`docker/app/Dockerfile` (`scripts/lint/check_dockerfile.py`): digest-pinned
non-`latest` `python:*-slim` FROM, non-root `10001:10001` USER, exact
`HEALTHCHECK CMD ["python3","-m","app.healthcheck"]`, no `sudo`, no remote
`ADD`, no secret-shaped `ARG`/`ENV` names, explicit `WORKDIR`, exec-form
runtime command, no privileged/setuid/setcap tokens.

### Compose structural checks

`make compose-check` → **10/10 checks pass** against the *rendered*
`docker compose config --format json`
(`scripts/compose/check_compose.py`). This review adversarially mutated
`compose.yaml` (always reverting the exact original file afterward, byte
verified via `diff`) to independently confirm each of the 10 checks
actually fires, not just that it exists in source:

| Mutation | Detected? |
|---|---|
| Add a 3rd service (`cache`) | Yes — service-set + hardening + image-version findings, 5 total |
| Rename `app` → `backend` (dangling `depends_on: app`) | Yes — caught at `docker compose config` (invalid project), non-zero exit |
| Publish a host port on `app` | Yes — 2 findings (not-published rule + sole-publisher rule) |
| `gateway` binds `0.0.0.0` instead of `127.0.0.1` | Yes |
| Remove `gateway`'s `cap_drop: [ALL]` | Yes |
| Mount `/var/run/docker.sock` into `gateway` | Yes |
| Custom network (`driver: bridge`, `driver_opts`) | Yes |
| Named/persistent volume (`appdata:/data`) | Yes — both top-level and per-mount findings |
| Wrong `HEALTHCHECK` test on `app` | Yes |
| `depends_on: app: condition: service_started` (not `service_healthy`) | Yes |
| Hardcoded image tag mismatch vs. `VERSION` | Yes — both services flagged |

All 10 checks independently confirmed to detect real drift, not merely to
exist. `compose.yaml` was restored byte-for-byte identical after every
mutation (verified via `diff`, and again via the final full-repo checksum
below).

**Version-fallback cross-check challenge**: this review edited the raw
`${VERSION:-0.2.0}` literals in `compose.yaml` to `${VERSION:-0.1.9}`
while `VERSION` remained exported as `0.2.0` in the environment (exactly
`make`'s own behavior) — confirming that `docker compose config`'s
*rendered* output would look correct (Compose resolves the env var, not
the fallback) while the raw source had silently drifted.
`check_compose.py: 1 finding(s): ... fallback literal(s) that do not
match VERSION ('0.2.0'): ['0.1.9']`, exit 1. The raw-text cross-check
genuinely closes the gap it claims to close. Reverted and reconfirmed
clean.

### Security checks

`make security-check` → **22/22 checks pass**
(`scripts/verify/security_check.py`), independently re-run against a
fresh `--no-cache` build of `maops-docker-platform:0.2.0`:

- `[A]` 2 checks (Dockerfile declares non-root USER; declares HEALTHCHECK)
- `[B]` 6 checks (image `Config.User`; image `Healthcheck`; OCI labels incl.
  exact version match; regression self-test for recursive bytecode
  detection; recursive bytecode-absence in extracted `/app`;
  repository-only-files absence)
- `[C]` 8 checks (read-only rootfs; `cap_drop: [ALL]`; no-new-privileges;
  not privileged; no host PID; no host network; no Docker socket;
  `HEALTHCHECK` reaches `healthy`) + 1 more `[C]` (`docker stop` clean-exit
  lifecycle check) = 9 `[C]` total
- `[D]` 5 checks (effective UID:GID; capability sets all-zero; NoNewPrivs;
  real read-only-write-failure + continued service; PID 1 identity)

2 + 6 + 9 + 5 = 22, matching exactly. The **exact OCI version-label
check** (`check_image_labels`) was independently confirmed to be a genuine
version-match assertion, not a presence-only check — it explicitly
compares `Labels["org.opencontainers.image.version"]` against the
`VERSION` file's contents and would fail on any mismatch (read directly in
source; not separately re-mutated in this review since `check_compose.py`'s
equivalent version-drift path was already adversarially proven above and
the label-comparison logic is straightforward `!=`).

The **automated PID1/SIGTERM regression** (`check_lifecycle_docker_stop`,
closing Day 1 finding M-2) was independently observed to pass: real
`docker stop` against the hardened `app` container returned exit 0, status
`exited`, in ~0.5s (well inside the 10s grace period) — proving the
`SIGTERM` handler in `app/server.py` (and by identical construction,
`gateway/server.py`) is real and fast, not a silent fallback to Docker's
full SIGKILL grace period.

### Compose runtime integration

`make compose-test` → **25/25 inspection checks pass**
(`scripts/compose/compose_integration.py`), independently re-run. Full
dependency-behavior lifecycle proven end-to-end by the script and then
**independently reproduced by this review in a separate, unique-named
Compose project** (`maops-compose-review25593`, fully torn down
afterward — confirmed via `docker ps -a`/`docker network ls`/
`docker compose ls` showing no leftover resources):

- both services reached Docker `healthy`
- `app` has zero published host ports (`docker inspect` `PortBindings`
  empty)
- `gateway` is the sole published service, bound `127.0.0.1` only (both
  the rendered-config `host_ip` and the real OS-assigned port via
  `docker port`)
- `gateway /readyz` → `200 {"status":"ready"}`, proving real service-name
  (`app`, not a hardcoded container IP — independently confirmed via
  `docker inspect .NetworkSettings.Networks` showing DNS aliases
  `["...-app-1","app"]` on the one and only project network) discovery
- `gateway /upstream/info` → real `app` JSON payload (`name`, `version`
  matching `VERSION`), proving a genuine outbound HTTP call, not a stub
- `docker compose stop app` → gateway container/process stayed running
  (`State.Running == true`)
- `gateway /readyz` degraded to `503 {"error":"upstream unavailable",
  "status":"not-ready"}` — controlled, no traceback
- `gateway /upstream/info` while `app` down → `502
  {"error":"upstream unavailable"}` — controlled, no traceback
- `gateway /healthz` stayed `200 {"status":"ok"}` throughout `app`'s
  downtime — liveness genuinely independent of upstream reachability
- `docker compose start app` → `app` healthy again; `gateway /readyz`
  recovered to `200 {"status":"ready"}`
- both containers' `/proc/1/status`, read directly by this review (not
  merely trusted from the script's own `docker exec`), showed: `Pid: 1`,
  `Name: python3`, `Uid`/`Gid` all four fields `10001`, `CapInh`/`CapPrm`/
  `CapEff`/`CapBnd`/`CapAmb` all `0000000000000000`, `NoNewPrivs: 1`
- a real write (`echo probe > /etc/maops-review-probe`) against both
  Compose-managed containers was rejected (`Read-only file system`, exit
  2) and both containers remained responsive afterward — this is the
  manual closure of finding **M-1** above, since the automated suite does
  not perform this specific check against Compose-managed containers

### Adversarial gateway requests (performed by this review)

Against a live Compose-managed `gateway`: normal `/` (200, correct
schema); unknown path (404 JSON); `POST /healthz` (405 JSON + `Allow`
header); path-traversal-shaped path `/../../etc/passwd` (404 — the
gateway has no filesystem-serving code path for any path, traversal or
not); `/upstream/info?upstream_host=evil.example.com` (200, still hit the
real fixed `app` — the query string has no effect on the outbound
destination, confirming no SSRF-style parameter injection); `Host:
evil.example.com` header (200, still hit the real fixed `app` — the Host
header has no effect either); `HEAD /healthz` (200, `Content-Length: 16`,
zero-byte body, matching `do_HEAD`'s `write_body=False`). No response
anywhere contained a Python traceback, exception class name, or file path.

### Image / Dockerfile

- `FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`
  — well-formed 64-hex-char digest, matches the Dockerfile comment's
  documented resolution date/source; independently confirmed present and
  used by `docker image inspect` (image built from this exact digest,
  `Architecture: amd64`, `Os: linux`).
  This review did not re-pull the tag against Docker Hub live (out of
  scope for a source/build review without registry network access), but
  the pinning mechanism itself (`FROM ...@sha256:...`, enforced by
  `check_dockerfile.py`'s `DIGEST_PATTERN`) is real and would reject an
  unpinned or malformed digest.
- `VERSION` = `0.2.0` (repository root file, single source of truth).
- OCI `org.opencontainers.image.version` label = `0.2.0` — independently
  read via `docker image inspect --format '{{json .Config}}'`, matches
  `VERSION` exactly.
- OCI `org.opencontainers.image.source` label =
  `https://github.com/raiyan10/maops-docker-platform` — independently
  confirmed truthful by cross-checking `git remote -v`
  (`origin git@github.com:raiyan10/maops-docker-platform.git`), same
  repository.
- `Config.User` = `"10001:10001"` (image inspection); kernel-effective
  `id -u`/`id -g` = `10001`/`10001` on a running container (both ad hoc
  and both Compose-managed containers, independently).
- No unnecessary packages: Dockerfile installs nothing beyond the base
  `python:3.13-slim` image plus `groupadd`/`useradd` (both from the base
  image's existing `passwd`/`shadow` toolchain, no `apt-get install`
  anywhere in the file) and copies only `app/`, `gateway/`, `VERSION`.
- No third-party runtime dependency: `check_source.py` (AST-based, scans
  `app/` and `gateway/`) confirms no `subprocess`/`pickle`/`ctypes`
  import, no `eval`/`exec`/`compile`/`__import__`, no `os.system`/
  `os.popen`, no `shell=True` — and manual inspection of every import
  statement in `gateway/*.py` and `app/*.py` shows only stdlib modules
  (`http.client`, `http.server`, `json`, `signal`, `sys`, `threading`,
  `urllib.parse`, `pathlib`, `dataclasses`, `os`, `platform`, `typing`,
  `types`).
- No `:latest` tag anywhere (`check_dockerfile.py` `check_from`;
  independently re-read the Dockerfile's single `FROM` line).
- No secrets: `check_dockerfile.py`'s `check_no_secret_vars` scans every
  `ARG`/`ENV` name; the only `ARG` is `VERSION`, the only `ENV`s are
  `PYTHONDONTWRITEBYTECODE`/`PYTHONUNBUFFERED` — none secret-shaped.
- **Recursive `.dockerignore` protection — re-proven adversarially by
  this review, not trusted from Day 1 or from
  `security_check.py`'s own synthetic self-test**: this review created
  real nested `__pycache__` directories at 3-4 levels of depth inside
  the actual build context (`gateway/a/b/c/__pycache__/probe.cpython-313.pyc`,
  `app/x/y/__pycache__/probe.cpython-313.pyc`, plus an extra file inside
  the pre-existing `gateway/__pycache__/`), ran a real
  `docker build --no-cache` against the mutated context, extracted `/app`
  from the resulting image via `docker cp`, and confirmed via `find
  -iname __pycache__ -o -iname *.pyc -o -iname *.pyo` that **zero** such
  paths existed anywhere in the extracted image content — while the
  parent directories that contained *only* the injected `__pycache__`
  subdirectory (`gateway/a/b/c`, `app/x/y`) were copied as empty
  directories, proving the exclusion is happening at the `__pycache__`
  pattern level, not by some coarser exclusion that would have hidden a
  gap. The adversarial probe image and all injected files were then
  removed; the final full-repository checksum (below) confirms no trace
  was left in the tracked/working tree.
- `docker run maops-docker-platform:0.2.0 python3 -m app` and (via
  Compose) `python3 -m gateway` both independently confirmed to run
  `python3` as PID 1 (`/proc/1/cmdline` == `["python3","-m","app"]` /
  `["python3","-m","gateway"]` respectively, read directly by this
  review, not only via the scripts' own assertions).

### Runtime security — both Compose services

See "Compose runtime integration" above for the full independently
reproduced evidence. Summary table (both proven [C] Docker-runtime and
[D] kernel/process, independently, for both `app` and `gateway`):

| Property | app | gateway |
|---|---|---|
| UID:GID 10001:10001 | ✅ (kernel) | ✅ (kernel) |
| `ReadonlyRootfs=true` (config) | ✅ | ✅ |
| Real write rejected (kernel) | ✅ (this review) | ✅ (this review) |
| `CapDrop=[ALL]` | ✅ | ✅ |
| `Privileged=false` | ✅ | ✅ |
| No host PID | ✅ | ✅ |
| No host networking | ✅ | ✅ |
| No Docker socket mount | ✅ (empty `Mounts`) | ✅ (empty `Mounts`) |
| No unexpected bind mounts | ✅ (`Mounts: []`) | ✅ (`Mounts: []`) |
| `NoNewPrivs=1` | ✅ | ✅ |
| `CapEff=CapPrm=CapBnd=0` | ✅ | ✅ |
| `CapInh`/`CapAmb` | both `0000000000000000` | both `0000000000000000` |
| PID 1 identity | `python3 -m app` | `python3 -m gateway` |

All kernel-level facts above were read from `/proc/1/status` /
`/proc/1/cmdline` directly by this review inside a Compose stack this
review started and tore down independently — not inferred from
`compose.yaml` or `HostConfig` alone.

### Compose structural checker — challenge summary

See "Compose structural checks" above. All 10 checks (service-count
drift, wrong service names, app host-port exposure, non-loopback gateway
exposure, missing hardening, Docker socket, custom network, persistence
volume, wrong healthcheck, missing `service_healthy`, version mismatch —
11 scenarios tested against 10 checks since one check function covers two
related assertions) genuinely detect the drift they claim to detect. No
check in this script appeared stronger than its evidence — each is a
direct, specific comparison against the rendered JSON config, with no
hidden assumptions found.

### Security checker — 22-check audit

See "Security checks" above for the full [A]/[B]/[C]/[D] breakdown
(2+6+9+5=22). Every check's category label was independently verified
against what it actually reads (source text vs. `docker image inspect`
vs. `docker inspect` `HostConfig` vs. `/proc` or a real action) — no
check was found mislabeled (e.g., no `[C]`-only check dressed up as
proving kernel enforcement). The one gap identified (missing [D]
read-only-write check reused in the Compose-integration path) is
finding **M-1**, not a mislabeling of an existing check.

### Version consistency

- `VERSION` = `0.2.0`
- Built image = `maops-docker-platform:0.2.0` (`docker image ls`)
- OCI `image.version` label = `0.2.0` (`docker image inspect`)
- Rendered Compose `services.*.image` = `maops-docker-platform:0.2.0` for
  both services (`docker compose config`)
- `scripts/smoke/container_smoke.py`, `scripts/verify/security_check.py`,
  `scripts/compose/compose_integration.py`, `scripts/compose/
  check_compose.py` all independently read `(REPO_ROOT / "VERSION")` at
  runtime rather than hardcoding a literal — confirmed by direct source
  read of each script's `read_version()`/equivalent.
- Raw Compose `${VERSION:-<default>}` fallback-literal cross-check:
  independently proven to fire on drift (see above), including the
  specific case (env var correct, raw fallback literal stale) that a
  naive rendered-config-only check would miss.

### Resource safety / cleanup

- `Makefile`'s `clean` target only targets `__pycache__`/cache
  directories, and containers/projects matching `maops-smoke-*`,
  `maops-security-*`, `maops-compose-*` — no global prune, no broad
  `docker system prune` or equivalent anywhere in the repository (grepped
  `scripts/`, `Makefile`).
- Every temporary container/project name observed during this review used
  a `uuid4().hex`-derived unique suffix (`maops-security-<hex>`,
  `maops-compose-<hex>`).
- Induced-failure cleanup: this review's own manually created Compose
  project (`maops-compose-review25593`) — including a deliberate mid-test
  `app` stop/degrade/restart cycle — was torn down via `docker compose
  down`, and independently confirmed removed via `docker ps -a`,
  `docker network ls`, and `docker compose ls` all showing zero remaining
  resources for that project. The automated scripts' own `finally`-block
  teardown was also observed to leave zero residue after every `make`
  target run in this review (`docker ps -a --filter name=^maops-` empty,
  `docker compose ls` empty, after `make security-check`, `make
  compose-test`, and `make release-check`).
- No other Docker resource (image, volume, network, or container
  unrelated to this project) was touched at any point in this review.

### Documentation verdict

Accurate overall, and notably careful about scope boundaries (e.g.
`docs/compose-platform.md`'s explicit "What is explicitly not implemented
yet (Day 3+)" section, and consistent "do not read this as already
implemented" framing). No false claims of CI or a container registry
existing were found anywhere in `docs/`, `README.md`, or
`.claude/skills`/`.claude/agents` — every CI/registry mention is
correctly framed as Day 6+ future scope. The one documentation defect
found is **M-1**'s overclaim in `docs/compose-platform.md` about
evidence-tier coverage for Compose-managed read-only enforcement, which
disagrees with `docs/security.md`'s own (accurate) description of the
same integration test.

---

## Verdicts

- **Topology verdict**: PASS. Exactly `app` + `gateway`, independently
  confirmed via `docker compose config` and via real Compose-created
  containers. `host → gateway → app` flow proven live. `app` has zero
  published ports. `gateway` is the sole published service, loopback-only
  by both rendered config and real OS-assigned-port inspection. Service
  discovery is genuinely by Compose DNS name (`app`), confirmed via
  `NetworkSettings.Networks` DNS aliases, never a hardcoded IP anywhere in
  source. Exactly one network (the implicit `default`), independently
  confirmed via `docker network ls` scoped to the review's own project.
  No named volumes, no database/Redis/Nginx/broker, no Compose
  `configs`/`secrets` blocks (confirmed absent from rendered
  `docker compose config` output), no Day 3+ scope present.

- **Gateway-security verdict**: PASS. stdlib-only, fixed/configured
  upstream only (independently defeated via query-string and Host-header
  adversarial requests — no effect), bounded 3s timeout, no shell/process
  execution, no arbitrary file serving, no arbitrary environment
  disclosure, no raw exception disclosure (independently probed), controlled
  JSON 404/405/502/503 with correct `Content-Type` and deterministic
  schema. `/healthz` is genuinely local-process-only (stayed `200`
  throughout `app`'s downtime). `/readyz` and `/upstream/info` genuinely
  perform real app HTTP requests (independently proven via the
  stop/degrade/restart/recover cycle).

- **Service-discovery proof**: PASS — DNS alias inspection, not just
  successful connectivity, confirms name-based (not IP-based) resolution.

- **Host-exposure verdict**: PASS — `app` unreachable from host at any
  point; `gateway` reachable only on `127.0.0.1`.

- **App failure/recovery proof**: PASS — full stop/degrade/restart/recover
  cycle independently reproduced by this review in a separate stack, with
  every intermediate state (`503` degraded, `502` upstream-info failure,
  `200` recovery) observed directly.

- **Image-content verdict**: PASS — no repository/dev files, no nested
  bytecode, confirmed via both the automated regression and this review's
  own independent adversarial rebuild.

- **Recursive cache proof**: PASS — independently re-proven with a real
  adversarial build, not trusted from Day 1 or from the script's own
  synthetic self-test.

- **UID/GID both services**: PASS — `10001:10001`, kernel-effective, both.

- **PID1 both services**: PASS — `python3 -m app` / `python3 -m gateway`,
  read from `/proc/1/cmdline` directly by this review, both.

- **Capabilities both services**: PASS — `CapEff=CapPrm=CapBnd=0`,
  `CapInh=CapAmb=0`, both, read from `/proc/1/status` directly.

- **NoNewPrivs both services**: PASS — `1`, both.

- **Read-only proof**: PASS for the underlying property (independently
  verified by this review with a real write against both Compose-managed
  containers), but see **M-1**: the automated suite itself does not
  perform this specific proof for Compose-managed containers.

- **Docker socket/namespace verdict**: PASS — no socket mount, no host
  PID, no host networking, no unexpected bind mounts, any service, any
  context tested.

- **Version-consistency verdict**: PASS — `VERSION`, image tag, OCI label,
  rendered Compose images, and tooling all agree on `0.2.0`; the
  fallback-literal drift path independently proven to fail closed.

- **Cleanup verdict**: PASS — no global prune anywhere in the repository;
  every temporary resource uniquely named and independently confirmed
  removed, including this review's own manually created stack.

- **Documentation verdict**: PASS with one Medium finding (**M-1**) — an
  overclaim in `docs/compose-platform.md` about evidence-tier coverage
  that a sibling doc (`docs/security.md`) states correctly.

- **Release blockers**: **None.** M-1 and L-1 are coverage/documentation
  gaps in an already-thorough verification suite, not runtime security
  defects — this review independently supplied the missing proof and
  confirmed the underlying property holds. Recommended (not blocking):
  add the missing per-Compose-container read-only-write check to
  `compose_integration.py` and reconcile the two docs' wording before or
  shortly after v0.2.0.

- **Final architecture/security verdict**: **APPROVED for v0.2.0.**
  The Day 2 two-service Compose topology is correctly scoped, the gateway
  is a genuinely narrow, non-SSRF-capable proxy with real bounded upstream
  calls and controlled error handling, both services carry identical,
  independently kernel-verified hardening, the dependency/failure/recovery
  lifecycle is real and automated, the Compose structural and security
  checkers were adversarially confirmed to detect real drift rather than
  merely existing, and cleanup discipline holds under induced failure.
  Two low-blast-radius coverage/documentation gaps are noted for
  follow-up; neither is release-blocking.

---

## Implementation integrity

Full-repository SHA-256 checksums (all files except `.git/` and
`docs/engineering-reviews/day-02-*.md`) were captured against the
baseline at `/tmp/maops-docker-day2-implementation.sha256` (82 files) at
the start of this review, and regenerated at the end after all review
activity (including a real adversarial `docker build`, real container
starts/stops, and deliberate `compose.yaml` mutations that were always
reverted). **Both checksum sets are identical, file-for-file and
byte-for-byte** — the Day 2 implementation was not modified by this
review.

```
baseline:  82 files, sha256 set captured before review
final:     82 files, sha256 set captured after review
diff:      (empty) — no differences
```
