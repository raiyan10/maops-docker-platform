# MAOps Docker Platform

## Mission

Project 3 of the MAOps DevOps portfolio. This repository is intentionally
Docker/platform-focused: the small Python stdlib HTTP application in `app/`
exists only as a deterministic workload through which Docker/container
engineering practices (build architecture, image hardening, Compose
platform design, runtime security verification, release discipline) are
demonstrated. Do not grow the application into a "real" service — new
features belong in the container/platform layer, not in `app/`, unless a
specific day's scope explicitly calls for an application change.

## Seven-day roadmap

- **Day 1 (v0.1.0)** — secure container foundation: the app, a hardened
  single-stage Dockerfile, a one-service Compose baseline, tests,
  source/Dockerfile validation, smoke testing, runtime security
  verification, docs, agents, skills.
- **Day 2 (v0.2.0)** — Compose multi-service topology: a new
  stdlib-only `gateway/` service fronting the Day 1 `app` service, a
  two-service `compose.yaml` (`app` not host-published, `gateway` the
  sole loopback-published service), one image capable of running either
  role, Compose structural + real-stack integration validation, and
  closure of the Day 1 M-2 (PID 1/SIGTERM regression)/M-3 (Compose
  runtime verification) test-review findings. See
  `docs/compose-platform.md`.
- **Day 3 (v0.3.0, this scope)** — networking, configuration, volumes,
  persistence: a new stdlib-only `state/` service (a durably persisted
  monotonic counter under a named Compose volume), extending the chain to
  `state -> app -> gateway`; two explicit Compose networks (`edge`:
  `gateway`+`app`; `backend`: `app`+`state`, `internal: true`) replacing
  Day 2's implicit default, with `gateway`/`state` sharing no network at
  all; a non-secret, Compose-mounted `config/platform.json`; and closure
  of three Day 2 review findings (the `depends_on` startup-ordering proof,
  the `UPSTREAM_HOST`-vs-real-service cross-check, and the Compose-managed
  [D] read-only-write proof). See `docs/networking.md`,
  `docs/configuration.md`, and `docs/persistence.md`.
- **Day 4** — build/image security and reproducibility.
- **Day 5** — health, reliability, resource limits, observability.
- **Day 6** — CI/CD, integration, release engineering.
- **Day 7** — hardening, reviews, portfolio showcase -> v1.0.0.

See `docs/roadmap.md` for the authoritative day-by-day breakdown. Do not
implement a later day's scope early, even if it looks convenient.

## Docker safety constraints (apply in every session, not just Day 1)

- **Never use `sudo` for Docker commands.** Docker must work rootless/
  without elevation in this environment; a command that needs `sudo` to
  run Docker means something is wrong with the assumption, not a reason to
  add `sudo`.
- **Never run a global prune** (`docker system prune`, `docker container
  prune`, `docker image prune`, `docker volume prune`) or otherwise delete
  Docker resources by broad prefix/heuristic matching. Every script in
  this repository that creates a container uses a unique,
  project-prefixed name (e.g. `maops-smoke-<uuid>`, `maops-security-
  <uuid>`, or — for Compose-managed resources —
  `maops-compose-<uuid>` as the Compose *project* name) generated at run
  time, and removes only that exact container/project in a
  `finally`/equivalent block — never anything it didn't create.
- `make clean` only removes known project-owned generated resources
  (local `__pycache__`/cache directories, any leftover
  `maops-smoke-*`/`maops-security-*` containers, and any leftover
  `maops-compose-*` Compose projects together with their own named
  volume, all matching this project's own deterministic naming scheme) —
  never a broad prune, never another project's resources, and never the
  named volume of a normal `docker compose up -d` development stack
  (which uses no `-p maops-compose-*` project name).
- The built release image (`maops-docker-platform:<VERSION>`) is left in
  place intentionally after validation; nothing in this repository's
  tooling removes it automatically.
- No Docker socket mounts, no `--privileged`, no `network_mode: host`,
  no `pid: host`, no host filesystem bind mount of arbitrary host data
  into any service container. The one narrow exception is Compose's own
  `configs:` mechanism (`config/platform.json`, mounted read-only into
  `app`/`gateway`/`state` at `/etc/maops/platform.json`) — a small,
  tracked, non-secret, version-controlled file, not a general host
  filesystem bind mount, and it is read-only both by Compose default and
  by explicit runtime proof (see `docs/configuration.md`). Do not widen
  this exception to any other host path.

## Security proof philosophy

Runtime security claims in this repository are always backed by evidence,
and that evidence is always labeled by what kind of proof it actually is
(see `docs/security.md` and `scripts/verify/security_check.py`):

- **[A] source/config** — what the Dockerfile/compose.yaml *declare*.
- **[B] image inspection** — facts read from `docker image inspect` on the
  built image.
- **[C] docker runtime inspection** — facts read from `docker inspect` on
  a running container (what Docker was *asked* to configure).
- **[D] kernel/process verification** — facts read from inside the
  running container's own process/kernel state (e.g. `/proc/1/status`),
  or a real attempted action (e.g. an actual rejected write) — what the
  kernel is *actually enforcing*.

A [C]-only claim ("we passed `--cap-drop=ALL`") is never presented as
proof of enforcement without a matching [D] check. Do not weaken this
distinction when extending security verification in later days.

## Implementation and testing expectations

- Python standard library only at runtime — no third-party dependency in
  `app/`, `gateway/`, or `state/`. `unittest` for tests, never `pytest`
  merely for convenience.
- The gateway's upstream destination (`UPSTREAM_HOST`/`UPSTREAM_PORT`) and
  the app's state destination (`STATE_HOST`/`STATE_PORT`) are each fixed
  at process startup and never derived from an incoming request — no
  arbitrary-URL proxying, no SSRF-style behavior. Keep this narrow if
  either service grows further. A mounted, non-secret Compose config
  (`config/platform.json`) may override the *timeout* bound on these
  calls, but never the destination host.
- Tests use loopback/in-process facilities and dynamic ports only; no
  fixed external ports, no public network, no shared mutable global test
  state, environment modifications restored on cleanup.
- `scripts/lint/`, `scripts/smoke/`, `scripts/verify/`, `scripts/compose/`
  are project-specific tools, not general-purpose scanners — each
  documents its own real scope honestly in its own docstring/output
  rather than implying broader coverage than it has.
- Every temporary validation container uses a unique, deterministic,
  project-prefixed name and is cleaned up via `try`/`finally` (or
  equivalent) on both success and failure paths.
- `VERSION` (repository root) is the single authoritative version source.
  Image tags, OCI version labels, and smoke-test expectations derive from
  it rather than duplicating the literal.

## Agents

Five project-local agents live in `.claude/agents/`:
`docker-architect`, `container-security-reviewer`,
`compose-platform-engineer`, `docker-test-engineer`, `release-engineer`.
Review-oriented agents are read-only/plan-mode by design — they are not
granted mutation authority beyond what their review role needs.

## Skills

Four project-local skills live in `.claude/skills/`:
`docker-build-validation`, `container-security-validation`,
`compose-validation`, `release-readiness`. These are reusable procedures
meant to grow across Days 1-7 (e.g. `compose-validation` starts as a
one-service check and grows into the Day 2+ multi-service procedure) —
extend them in place rather than creating parallel day-specific versions.
None of them may claim CI or a container registry exists until a later
day's scope actually adds one.

## Git workflow

- Feature branches are intentionally retained after merge unless the user
  explicitly requests deletion — do not delete a branch as a "cleanup"
  step on your own initiative.
- **Do not commit, push, tag, or publish without explicit instruction
  from the user in that conversation.** A prior approval does not carry
  over to future turns or sessions.
