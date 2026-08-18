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
`compose.yaml` declares no Compose secrets (out of Day 1 scope) and its
`environment:` block carries only `APP_HOST`/`APP_PORT`/`APP_NAME` —
none of which is sensitive.

## Healthcheck

- *Desired*: `docker/app/Dockerfile` declares `HEALTHCHECK --interval=10s
  --timeout=3s --start-period=5s --retries=3 CMD ["python3", "-m",
  "app.healthcheck"]` — a stdlib-only probe (`http.client`, not
  `urllib.request`, to avoid proxy-environment-variable interference)
  that calls the app's own `/healthz` over loopback. No `curl`/`wget` was
  installed merely to support this.
- *Docker runtime inspection*: the built image's `Config.Healthcheck.Test`
  is present and not `NONE`; a running container's `State.Health.Status`
  was polled to a bounded deadline and observed to reach `healthy`.
  Verified 2026-08-18: healthy within ~1s of container start (well inside
  the 5s `start_period`).
- A negative-path check (the probe returning failure when the app is
  unavailable) is proven implicitly by the probe's own logic — it treats
  any non-`200` status, any non-JSON body, or any `status != "ok"` field
  as failure and exits `1` — rather than by permanently modifying source
  for adversarial testing, which section 20 of the Day 1 scope explicitly
  disallows.

## Day 1 limitations (deliberately not implemented yet)

- No vulnerability scanner, no SBOM generation — planned for Day 4 (build/
  image security and reproducibility); see `docs/roadmap.md`.
- No resource limits (CPU/memory) — planned for Day 5.
- No CI-enforced verification — Day 1's gates are local (`make
  release-check`) only; Day 6 adds CI/CD.
- No multi-stage build — the current image has no build-time toolchain to
  strip out (stdlib-only application), so a second stage would add
  complexity without a corresponding benefit yet; revisit if a future
  day's dependency adds a compiler/toolchain.
- This document reflects a point-in-time verification run
  (2026-08-18, `maops-docker-platform:0.1.0`). Re-run `make
  security-check` after any change to `docker/app/Dockerfile`,
  `compose.yaml`, or `app/` before trusting these figures again.
