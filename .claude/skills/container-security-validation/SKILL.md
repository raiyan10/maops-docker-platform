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
   should print `10001:10001`. [D] `docker exec <container> id -u` / `id
   -g` should both print `10001`.

2. **Capabilities**: [C] `docker inspect <container> --format
   '{{.HostConfig.CapDrop}}'` should show `[ALL]`. [D] `docker exec
   <container> cat /proc/1/status` — `CapEff`, `CapPrm`, and `CapBnd`
   should all read `0000000000000000`. The [C] check alone only proves
   what was *requested*; only [D] proves the kernel actually holds no
   capabilities.

3. **no-new-privileges**: [C] `docker inspect <container> --format
   '{{.HostConfig.SecurityOpt}}'` should contain
   `no-new-privileges:true`. [D] `/proc/1/status`'s `NoNewPrivs` field
   should read `1`.

4. **Read-only root filesystem**: [C] `docker inspect <container>
   --format '{{.HostConfig.ReadonlyRootfs}}'` should be `true`. [D]
   attempt a real write inside the running container (e.g. `docker exec
   <container> sh -c 'echo x > /etc/probe'`) and confirm it fails with a
   read-only-filesystem error, *and* that the service still answers
   `/healthz` afterward — a config check alone doesn't prove the write
   actually fails or that the app survives it. Clean up any probe path
   you create (though the read-only filesystem should reject it before
   anything is written).

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

## Extending this skill

When a later day adds resource limits, seccomp profiles, or additional
namespaces, add a new numbered check here in the same [A]/[B]/[C]/[D]
style rather than inventing a different reporting convention.
