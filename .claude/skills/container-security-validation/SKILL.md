---
name: container-security-validation
description: Reusable hardened-runtime verification procedure for maops-docker-platform, distinguishing configured (source), Docker-inspected, and kernel/process-effective state. Use when verifying non-root execution, capabilities, no-new-privileges, read-only rootfs, or namespace/mount isolation for the application container.
---

# Container Security Validation

Reusable procedure for verifying container hardening claims with the
right *kind* of evidence, every time. Never stop at "we configured it" —
always also prove "the kernel is actually enforcing it" when a proof
category below has a [D] step.

Run `python3 scripts/verify/security_check.py` (or `make security-check`)
to execute all of the following automatically against a throwaway,
uniquely-named hardened container that it starts and always removes. Use
the manual steps below when investigating a specific failure or extending
coverage.

**Day 4: the release image's final runtime is Distroless
(`gcr.io/distroless/python3-debian13:nonroot`) - no shell, no coreutils
(`sh`, `cat`, `id`, `find`, `stat` are all genuinely absent).** Every
manual `docker exec` probe below that used to shell out to a coreutils
binary now execs the absolute `/usr/bin/python3.13` interpreter directly
with a small stdlib-only one-liner - never a shell, never a bare
`python3` name (PATH resolution needs a shell the runtime doesn't have).

## Proof categories

- **[A] source/config** — what `docker/app/Dockerfile`/`compose.yaml`
  declare.
- **[B] image inspection** — `docker image inspect` on the built image.
- **[C] Docker runtime inspection** — `docker inspect` on a running
  container (what Docker was *asked* to configure).
- **[D] kernel/process verification** — facts from inside the running
  container's own process/kernel state, or a real attempted action.

## Checks and how to reproduce them manually

1. **Non-root**: [B] `docker image inspect "$IMAGE" --format '{{.Config.User}}'`
   should print `10001:10001`. [D] `docker exec <container>
   /usr/bin/python3.13 -c "import os; print(os.getuid()); print(os.getgid())"`
   should print `10001` twice (no `id` binary in the Distroless runtime).

2. **Capabilities**: [C] `docker inspect <container> --format
   '{{.HostConfig.CapDrop}}'` should show `[ALL]`. [D] `docker exec
   <container> /usr/bin/python3.13 -c "from pathlib import Path; print(Path('/proc/1/status').read_text())"`
   — `CapEff`, `CapPrm`, and `CapBnd` should all read
   `0000000000000000`. The [C] check alone only proves what was
   *requested*; only [D] proves the kernel actually holds no
   capabilities.

3. **no-new-privileges**: [C] `docker inspect <container> --format
   '{{.HostConfig.SecurityOpt}}'` should contain
   `no-new-privileges:true`. [D] `/proc/1/status`'s `NoNewPrivs` field
   (read the same way as above) should read `1`.

4. **Read-only root filesystem**: [C] `docker inspect <container>
   --format '{{.HostConfig.ReadonlyRootfs}}'` should be `true`. [D]
   attempt a real write inside the running container (e.g. `docker exec
   <container> /usr/bin/python3.13 -c "open('/etc/probe', 'w').write('x')"`
   — no shell available in the Distroless runtime, so this is a Python
   probe, not `sh -c 'echo ...'`) and confirm it raises `OSError`/
   `PermissionError`, *and* that the service still answers `/healthz`
   afterward — a config check alone doesn't prove the write actually
   fails or that the app survives it. `state` (Day 3) is the one service
   with a writable path (`/data`, via the `state_data` named volume) —
   verify the rootfs-write-rejection proof *still* holds for `state` (a
   write to `/etc/...` still fails) in addition to, never instead of, a
   real write to `/data` succeeding (the same Python `open(...,
   'w').write(...)` pattern against `/data/probe`, then remove it). The
   same [C]/[D] pair applies to the Compose-mounted
   `config/platform.json` (`/etc/maops/platform.json` in every service):
   [C] `Mounts[].RW == false`, [D] a real write to it is rejected.

5. **Namespaces/mounts**: [C] `docker inspect <container> --format
   '{{.HostConfig.Privileged}}'` is `false`; `{{.HostConfig.PidMode}}`
   and `{{.HostConfig.NetworkMode}}` are not `host`; `{{json
   .Mounts}}` contains no `docker.sock` source/destination.

6. **Healthcheck**: [B] the image's `Config.Healthcheck` exists and its
   `Test` is not `NONE`. [C] poll `docker inspect <container> --format
   '{{.State.Health.Status}}'` until it reads `healthy` within a bounded
   deadline (do not poll forever).

7. **Image content leakage**: see `docker-build-validation`'s recursive
   leak-detection procedure — this is a [B] image-inspection check, and
   its own detector must be proven recursive via a synthetic nested
   fixture, not just run against the real (already-clean) image.

8. **Image-level application-source immutability (Day 4)** - a property
   independent of, and in addition to, the read-only-rootfs check above:
   application source is genuinely not writable by the runtime UID even
   without `--read-only` at all. [B] `docker image inspect` extracted
   `/app` content is root-owned (no `--chown` on the final stage's
   `COPY --from=builder` instructions for `app/`/`gateway/`/`state/`/
   `VERSION`). [D] a real attempted write against a container started
   with **no** hardening flags whatsoever - via `docker exec` (the
   Distroless runtime has no shell, so `--entrypoint sh` itself fails
   with "no such file or directory"; use the image's own default
   entrypoint and probe with `docker exec`):
   ```bash
   docker run -d --name probe "$IMAGE"
   docker exec probe /usr/bin/python3.13 -c "open('/app/app/server.py', 'a').write('x')"  # must raise PermissionError
   docker exec probe /usr/bin/python3.13 -c "open('/app/newfile', 'w').write('x')"         # must raise PermissionError
   docker exec probe /usr/bin/python3.13 -c "open('/data/probe', 'w').write('x'); print(open('/data/probe').read())"  # must succeed
   docker rm -f probe
   ```
   `make image-audit` (`scripts/build/image_audit.py`) automates this.
   Do not treat this as a substitute for `read_only: true` - it is a
   second, independent layer, proven separately.

9. **Shell/package-manager absence (Day 4)** - a hard requirement, not
   an optional hardening nicety: `docker exec <container> /bin/sh -c
   "echo probe"` must fail with "no such file or directory" (proving the
   shell is genuinely absent, not merely unused), and no `apt`/`dpkg`
   executable or importable `pip`/`setuptools` may exist. `make
   image-audit` automates all of this via real exec/import attempts, not
   a filename search.

10. **Supply-chain scanner isolation (Day 4)** - not a container-runtime
   hardening check in the same sense as 1-9 above, but the same [C]/[D]
   discipline applies: `scripts/security/generate_sbom.py`/`vuln_scan.py`
   must never mount `/var/run/docker.sock` into the Syft/Trivy scanner
   container - verify by reading the actual `docker run` argv each script
   constructs (or the constructed-argv assertions in
   `tests/test_generate_sbom.py`/`tests/test_vuln_scan.py`), not by
   trusting a docstring claim. Both scanner images must be pinned by
   exact digest in `security/scanners.lock`.

## Extending this skill

When a later day adds resource limits, seccomp profiles, or additional
namespaces, add a new numbered check here in the same [A]/[B]/[C]/[D]
style rather than inventing a different reporting convention.
