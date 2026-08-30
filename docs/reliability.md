# Reliability (Day 5)

## Liveness vs. readiness — a platform-wide contract, not per-service ad hoc

Every service exposes both `/healthz` and `/readyz`, and the two answer
genuinely different questions:

- **`/healthz` (liveness)**: "is this process's own event loop alive and
  responding?" It is **local-process-only** — it never makes a network
  call, never touches a dependency, and never touches `state`'s persisted
  store. A dependency being down must never make a service's own
  `/healthz` fail. This was already true as of Day 3/4 (see
  `docs/security.md`'s "Role-aware liveness" section for the Day 4 H-1
  fix); Day 5 formalizes it as an explicit platform contract rather than
  an implicit per-service convention, and proves it under a real failure
  (see "Real failure/recovery proof" below) rather than only by reading
  the source.
- **`/readyz` (readiness)**: "can this service actually do its job right
  now?" This *is* dependency-aware, and the chain is genuinely nested, not
  independently faked at each layer:
  - `state`'s `/readyz` — local readiness only (a real, non-mutating read
    of its own persisted store; see `docs/persistence.md`).
  - `app`'s `/readyz` — depends on `state`'s own `/readyz` (a real,
    bounded HTTP call).
  - `gateway`'s `/readyz` — depends on `app`'s own `/readyz`, which
    therefore *transitively* incorporates `state`'s readiness without
    `gateway` ever contacting `state` directly (it cannot — see
    `docs/networking.md`).

A healthy process can be not-ready; a not-ready service is never reported
as unhealthy. Confusing the two — e.g. making liveness transitively depend
on a downstream dependency — is exactly the anti-pattern this contract
rules out: it would turn a single flaky dependency into a cascading
liveness failure (and, under most container orchestrators, a restart
storm) across the whole chain, instead of the bounded, honest readiness
degradation this platform actually implements.

## Role-specific health (Day 4 H-1, preserved)

Each `/healthz` body carries a fixed `role` field
(`{"status": "ok", "role": "app"}` / `"gateway"` / `"state"`), and each of
`app.healthcheck`/`gateway.healthcheck`/`state.healthcheck` rejects a
well-formed response carrying the *wrong* role — closing Day 4 finding
H-1. This is unchanged by Day 5; see `docs/security.md` for the full
mechanism and proof, and `scripts/compose/compose_integration.py`'s
`check_role_discrimination_matrix()` for the real 3x3 regression proof
(preserved, still part of `make compose-test`, not duplicated here).

## Timeout hierarchy — closing Day 3 finding A-6

### Why A-6 existed

Through Day 4, `gateway`'s call to `app` and `app`'s call to `state` both
read the *same* generic `dependency_timeout_seconds` field from
`config/platform.json` and applied it independently, with no awareness of
how much of that budget the hop below had already spent. During a `state`
outage, the outermost caller's effective worst-case failure-detection
latency could be up to ~2x the advertised single-hop value — an external
caller/monitor with a timeout near that single-hop bound could observe a
raw connection timeout instead of `gateway`'s intended clean `503` (see
`docs/engineering-reviews/day-03-networking-review.md` finding M-1, and
the historical `docs/persistence.md` scope-limitations note this
document's predecessor left explicitly deferred to Day 5).

### How v0.5.0 closes it

`config/platform.json` now declares an explicit, named two-hop budget
instead of one ambiguous shared constant:

```json
{
  "schema_version": 1,
  "platform_name": "maops-docker-platform",
  "state_dependency_timeout_seconds": 2.0,
  "gateway_upstream_timeout_seconds": 5.0,
  "timeout_safety_margin_seconds": 1.0,
  "state_filename": "state.json"
}
```

| Field | Used by | Meaning |
|---|---|---|
| `state_dependency_timeout_seconds` | `app` (the *inner* hop) | bounds `app`'s own call to `state` |
| `gateway_upstream_timeout_seconds` | `gateway` (the *outer* hop) | bounds `gateway`'s own call to `app` |
| `timeout_safety_margin_seconds` | the invariant below | minimum required headroom between the two hops |

The required invariant, enforced at config-*load* time (not merely
documented, and not merely "the values happen to look right today"):

```
gateway_upstream_timeout_seconds > state_dependency_timeout_seconds
                                    + timeout_safety_margin_seconds
```

`gateway/platform_config.py` validates all three fields and this
invariant — a `config/platform.json` that violates it fails to load
(`ValueError`), so `gateway` refuses to start with a timeout hierarchy
that doesn't actually protect its own caller. `app/platform_config.py`
independently validates its own field (`state_dependency_timeout_seconds`)
with the same strict-numeric rules. Each module stays narrowly scoped to
what its own service uses operationally (matching this project's existing
per-package convention — see `docs/configuration.md`) except that
`gateway`'s module additionally reads (but never uses operationally)
`state_dependency_timeout_seconds`, specifically so it can check the
invariant against the *whole* shared config file, not just its own field.
The obsolete single `dependency_timeout_seconds` field from Day 3/4 is not
kept for backwards compatibility — this is a v0.5.0 config-shape change.

Strict validation, identical shape in both modules: every numeric field
rejects `bool` (a subclass of `int` in Python), any non-numeric type, `0`,
negative values, non-finite values (`NaN`/`Infinity`/`-Infinity` — Python's
`json` module accepts these as a non-standard extension unless explicitly
disabled, so a config author really could ship one), and anything above a
documented sane maximum (30s for the inner hop, 60s for the outer hop, 30s
for the margin).

### Effect under a real stalled dependency

When `state` becomes unresponsive (see "Real failure/recovery proof"
below for the actual mechanism used):

1. `app`'s call to `state` blocks for up to `state_dependency_timeout_seconds`
   (2.0s), then raises a socket timeout — caught, never leaked — and `app`
   returns a controlled `503`.
2. `gateway`'s call to `app` uses `gateway_upstream_timeout_seconds` (5.0s)
   as its own budget. Since `app` answers in ~2s (well under 5s), `gateway`
   receives `app`'s real `503` and forwards a controlled failure to the
   external caller — `gateway`'s own outer timeout never has to fire.
3. The external caller's total wait is bounded by the single larger
   budget (`gateway_upstream_timeout_seconds`), not by the sum of two
   independently-expiring timeouts stacked serially.

No raw traceback, no hang, no `inner + outer` serial wait. This is proven
against a real Docker container, not simulated — see below.

## Resource controls

All three services declare explicit, reviewable Compose resource limits —
the non-Swarm `cpus`/`mem_limit`/`pids_limit` fields, which a plain
`docker compose up` actually applies as real Docker `HostConfig` values
(unlike a Swarm-only `deploy.resources.limits` block, which ordinary
Compose ignores):

| Service | `cpus` | `mem_limit` | `pids_limit` |
|---|---:|---:|---:|
| `state` | 0.50 | 128m | 64 |
| `app` | 0.50 | 128m | 64 |
| `gateway` | 0.50 | 128m | 64 |

Structurally enforced by `scripts/compose/check_compose.py`
(`check_resource_limits`) against the rendered Compose config; genuinely
applied to real containers, proven by `scripts/reliability/
reliability_check.py`'s `check_resource_limits_applied()` — real `docker
inspect ... HostConfig` values (`NanoCpus`/`CpuQuota`+`CpuPeriod`,
`Memory`, `PidsLimit`) for all three real Compose-created containers, a
`[C]` proof. Where the host/Docker Desktop backend's cgroup v2 files are
actually readable from inside the container (not guaranteed — see
`check_cgroup_v2_resource_limits()`'s own docstring), a second, independent
`[D]` proof reads `/sys/fs/cgroup/memory.max`, `/sys/fs/cgroup/pids.max`,
and `/sys/fs/cgroup/cpu.max` directly via a stdlib-only Python probe (no
shell — the Distroless final runtime has none) and cross-checks them
against the same targets. An environment where those paths genuinely
aren't visible is reported honestly, not silently treated as a pass or a
failure of the underlying resource limit itself.

## Restart policy — bounded, not unbounded

All three services declare `restart: on-failure:3` — a Compose non-Swarm
`restart:` field, real Docker `HostConfig.RestartPolicy`
(`{"Name": "on-failure", "MaximumRetryCount": 3}`), not `always` or
`unless-stopped` (both of which would also restart after an *intentional*
stop, and `always` would additionally restart on every host/daemon
reboot regardless of whether the process ever actually failed). An
unexpected crash retries automatically, up to 3 times; nothing in this
platform can crash-loop forever.

**`RestartCount` semantics (Day 6, closes Day 5 finding L-1,
`day-05-failure-recovery-review.md`):** Docker's own `RestartCount` field
is **cumulative across automatic restart-policy attempts within one
continuous run of the container instance** — it is not reset per crash
episode, and `reliability_check.py`'s Scenario 2 assertion is deliberately
written against the absolute configured maximum (`== 3`), not a delta from
an arbitrary baseline, precisely because of this (confirmed empirically:
Scenario 1's single transient crash already spends 1 of the 3 lifetime
attempts, so Scenario 2's persistent condition only gets 2 *more* retries
before hitting the same cap — `before=1, after=3`, never `before + 3`).
Separately, and independently confirmed by real experiment: an **explicit**
`docker start`/`docker compose start` (as opposed to an automatic
restart-policy retry) **does reset the counter to `0`** — an operator who
restarts a container without having fixed the underlying condition gets a
fresh budget of 3 more automatic retries, not zero. Both properties are
specific to this project's own Docker Desktop install (server 29.7.2,
Compose v5.4.0 at last verification) and are not claimed as a universal
guarantee across every Docker/`dockerd` version — re-verify before relying
on this framing against a materially different Docker version.

## Graceful shutdown

All three services declare `stop_grace_period: 10s` — real Docker
`Config.StopTimeout`. Every role's process already handles `SIGTERM`
gracefully (see `docs/architecture.md`'s process-model section — unchanged
by Day 5): a signal handler starts a separate thread calling
`HTTPServer.shutdown()`, since `shutdown()` blocks until `serve_forever()`
actually exits and would deadlock if called from the same thread that's
running it. `stop_grace_period` gives that handler a real, bounded window
before Docker escalates to `SIGKILL` — proven, not merely declared, by
`reliability_check.py`: a real `docker stop` against a live container
completes with `ExitCode == 0` well inside the 10s window.

## Real failure/recovery proof — `make reliability-check`

Preserves and does not duplicate `scripts/compose/compose_integration.py`
(topology, DNS, network segmentation, persistence, config mounting,
runtime hardening, the H-1 3x3 healthcheck matrix, startup ordering, and
the existing `state`-stop/degrade/recover scenario all stay exactly where
they already were). `scripts/reliability/reliability_check.py`
(`make reliability-check`) owns everything new this day, against its own
uniquely named Compose project (`maops-reliability-<uuid>`), with real
`time.monotonic()`-measured bounded deadlines throughout — no fixed sleep
used as a correctness assertion, no `shell=True`/`os.system`/`os.popen`:

1. **Resource limits, restart policy, stop_grace_period, timeout
   hierarchy** — all proven against real containers and the real shipped
   `config/platform.json` (see sections above).
2. **A-6 real adversarial proof**: `docker pause state` — a real stalled
   dependency, not a mock (pausing freezes the process via the kernel
   cgroup freezer; the kernel network stack can still complete a new TCP
   handshake into the frozen container's listen backlog, so the caller
   genuinely hangs until its own read timeout, exactly the scenario the
   timeout hierarchy exists to bound). While paused:
   - `app`'s and `gateway`'s own `/healthz` stay `200` (proven via
     `docker exec ... app.healthcheck`/`gateway.healthcheck` — real
     kernel/process-level checks, not just an HTTP call to a possibly-
     cached value).
   - `app`'s own `/readyz` (probed from *inside* `app`'s own container,
     since `app` has no published host port) and `gateway`'s `/readyz`
     both become `503`.
   - A state-dependent request (`GET /state` through `gateway`) is
     measured with `time.monotonic()` and asserted to: return a
     controlled `503` with no `"Traceback"` in the body, complete inside
     the configured *outer* budget (never anywhere near
     `inner + outer` stacked serially), and have genuinely waited on the
     *inner* timeout (not failed instantly) — the three-part proof that
     A-6 is closed for real, not just configured to look closed.
   - `docker unpause state` (always run, even on a failure inside the
     paused block, via `try`/`finally`) — `state` becomes Docker-healthy
     again automatically, `gateway`'s `/readyz` recovers to `200`, and the
     previously-persisted value survives the pause/unpause cycle
     unchanged.
3. **Three distinct crash/stop lifecycle semantics, kept deliberately
   separate** — Docker's `on-failure` restart policy behaves differently
   depending on *how* and *why* a container stopped, and this project's
   own experimentation (below) found that conflating these three cases
   produces a materially wrong proof. `reliability_check.py` exercises all
   three against the real `state` container, never against a mock:

   #### TRANSIENT FAILURE → automatic restart *and* recovery

   The authoritative automatic-crash-recovery proof. Two candidate
   mechanisms were tried first and **rejected**, both confirmed by direct
   experiment against this project's own Docker Desktop install:

   - `docker kill`/`docker stop` (**daemon-API-initiated**): Docker's
     restart-policy engine treats *any* daemon-API-initiated kill/stop as
     a manual/intentional termination and does **not** apply `on-failure`
     to it, regardless of exit code. A bare
     `docker run --restart on-failure:3 ...` container, killed with
     `docker kill` (a genuine `SIGKILL`, `ExitCode 137`), never
     restarted — `RestartCount` stayed `0` even after a 40s bounded
     observation.
   - `docker exec <container> python3 -c "os.kill(1, signal.SIGKILL)"`
     (an **internal signal**, sent from a sibling process inside the
     *same* PID namespace as PID 1): confirmed to have **no effect at
     all** — the container was untouched, `RestartCount` stayed `0`. This
     is real, documented Linux kernel behavior
     (`man 7 pid_namespaces`): a PID namespace's init process (PID 1)
     only receives signals for which it has installed a handler, *even
     `SIGKILL`/`SIGSTOP`*, when the sender is a process inside the same
     namespace — only a sender in an **ancestor** namespace (the host,
     i.e. `docker kill`) can force it through, which is exactly the
     already-rejected mechanism above. Writing
     `/sys/fs/cgroup/memory.max` from inside the container was also
     tried, and confirmed blocked outright (`OSError: [Errno 30]
     Read-only file system` — the cgroup controller files are genuinely
     read-only from inside this hardened, non-privileged container).

   What **does** work, confirmed reproducible: a process can write its
   *own* container's `/proc/1/oom_score_adj` from inside (same real UID
   as PID 1, no elevated capability required) to bias which process the
   kernel's OOM killer selects, then generate memory pressure from a
   disposable sibling process — never touching PID 1's own code, and
   never touching the container's actual `mem_limit` (128m stays 128m the
   entire time) — against the *existing, unmodified* cgroup limit. The
   kernel's OOM killer (not dockerd) selects PID 1, since its badness
   score is now maximized, and delivers a real `SIGKILL` directly — a
   delivery path that bypasses the same-namespace signal-immunity rule
   above, because it is the kernel's own memory-accounting subsystem
   acting on the cgroup, not a `kill()` syscall between two processes.
   When PID 1 dies, the kernel's own PID-namespace teardown rule SIGKILLs
   every other process left in the namespace too — which is *why* the
   `docker exec` command itself is expected to return non-zero; this is
   the success signature, not a failure, and `reliability_check.py`
   treats it as such rather than as a script error. Because the real
   `mem_limit` was never touched, the freshly restarted process starts
   under completely normal conditions and stays up — genuinely
   **transient**, confirmed by a real `docker events` "oom" → "die" →
   "start" sequence occurring **exactly once** (no loop): `RestartCount`
   advances by exactly `1`, `state` reaches Docker-`healthy` again with
   **no manual `docker start` anywhere in the script**, `app`'s own
   `/readyz` (probed from inside `app`'s own container) and `gateway`'s
   `/readyz` both recover to `200` automatically, the persisted counter
   value is proven unchanged, and a real `POST /state/increment` proves
   the full chain works again.

   #### PERSISTENT FAILURE → bounded retry exhaustion, *not* automatic recovery

   Deliberately different: here the memory limit itself is lowered
   (`docker update --memory 6m --memory-swap 6m`) and stays lowered
   across every restart attempt, so every restart attempt re-triggers the
   same OOM condition. This does **not** prove the service comes back on
   its own — under a genuinely persistent failure condition it cannot —
   it proves the *bound*: `on-failure:3` retries automatically exactly
   `3` times (never more — a real crash loop would retry forever) and
   then correctly stops, leaving the container `exited`, requiring a real
   **operator** action. `reliability_check.py`'s
   `with_memory_shrink_restored()` helper wraps the shrink/poll/assert
   sequence in `try`/`finally` so the memory limit is **always** restored
   to its original value afterward — proven directly by a Docker-free
   unit test that injects a failure into the wrapped action and asserts
   the restore call still happened (see `tests/test_reliability_check.py`,
   `WithMemoryShrinkRestoredTests`) — even if the bound assertion itself
   fails or a `docker` subprocess call raises.

   **Day 6 (GitHub Actions run `32960673438`, see `docs/ci-cd.md`'s
   "GitHub-hosted runner post-restart cgroup/runc resource-update race"
   section for the full record):** both the shrink and the restore now go
   through `update_container_resources_verified()` — a bounded, monotonic,
   independently re-inspected retry, narrowly scoped to the EXACT transient
   `runc`/cgroup v2 race GitHub's Linux runner hit immediately after
   Scenario 1's automatic restart (never to Docker errors in general — an
   unrelated `docker update` failure still fails immediately, with no
   retry). This closes a real GitHub-only failure without touching what
   Scenario 2 actually proves: the memory limit is still genuinely
   lowered, the kernel still genuinely OOM-kills `state` on every retry,
   `on-failure:3` still retries automatically exactly 3 times and no more,
   and restoration remains a first-class **verified** invariant — a
   restore that cannot be applied AND confirmed via a follow-up `docker
   inspect` within the bounded retry deadline still fails
   `reliability-check`, never merely a warning. Local Docker Desktop
   succeeds on the first `docker update` attempt, so this adds no
   observable local behavior change — `make reliability-check` still
   reports `32/32`.

   **Day 7 (`DAY6-POST-M2`, see `docs/production-readiness.md` §1.3):**
   a second, distinct real occurrence of the same underlying post-restart
   race (GitHub run `33059581018`, a post-release evidence-commit run,
   immediately after a genuine Scenario 1 OOM crash and automatic
   restart) hit `memory.max` instead of `cgroup.controllers`, which the
   Day 6 classifier correctly (per its own narrow design) refused to
   retry, failing that run. `_is_transient_cgroup_update_race()` was
   widened conservatively — never loosened to a general "any cgroup
   error" retry — requiring, in order, ALL of:

   - the literal `"runc did not terminate successfully"` wrapper phrase;
   - a genuine `openat2 <path>: no such file or directory` match (real
     ENOENT-on-`openat2` semantics via a regex, not merely the words "no
     such file or directory" appearing anywhere in the message);
   - the missing path's directory containing a real `/cgroup/` hierarchy
     segment (real cgroup-path context, not merely a same-named file
     living somewhere else); and
   - the missing path's basename being one of a small, explicitly
     enumerated, deliberately restricted set —
     `{cgroup.controllers, memory.max}` — never a broad "any
     cgroup-shaped filename" wildcard.

   Consequences of this being a conjunction, not a loosened match:
   arbitrary `runc` errors are **not** retried; an unrelated missing file
   (even one that happens to say "no such file or directory") is **not**
   retried; `permission denied` (a real `openat2` failure that is not
   ENOENT) is **not** retried; `pids.max`/`cpu.max`/`memory.swap.max` or
   any other cgroup-shaped filename — never observed, not accepted — is
   **not** automatically retried, even with an otherwise byte-identical
   error. Extending the accepted-filename set again requires a new,
   independently observed real GitHub Actions failure, not speculation.
   The retry itself remains exactly as bounded as the Day 6 design: a
   real `time.monotonic()`-measured deadline (never wall-clock/`datetime`
   based), a bounded sleep between attempts (never a busy loop), and —
   on every success path, first-try or retried — `HostConfig.Memory`/
   `HostConfig.MemorySwap` are re-inspected via a real `docker inspect`
   and must **exactly** match the expected values before the helper
   returns; a "successful" `docker update` whose inspected values don't
   match is a real verification failure, never inferred from exit code
   alone. See `docs/production-readiness.md` §1.3 for this finding's
   precise evidence-tier disposition (code-level closed; live-recurrence
   confirmation against a fresh real occurrence remains pending) and
   `tests/test_reliability_check.py`'s
   `TransientCgroupUpdateRaceClassifierTests`/
   `UpdateContainerResourcesVerifiedTests` for the Docker-free positive
   and negative unit coverage of every branch above.

   Only *after* the bound is
   proven (and the memory limit already restored by that `finally` block)
   does the script issue an explicit `docker compose start state` —
   clearly labeled as the deliberate **operator** action it is, not part
   of the automatic-recovery claim (that claim belongs entirely to the
   Transient Failure scenario above, which is already complete by this
   point) — after which the persisted value and the full
   `gateway -> app -> state` chain are both re-verified.

   #### INTENTIONAL STOP → no automatic restart at all

   `docker stop state` — proven to exit cleanly (`ExitCode == 0`) well
   inside the 10s grace period (the graceful-shutdown proof from the
   section above, reused here rather than duplicated as a separate step),
   and — the actual point of a *bounded* `on-failure` policy, as opposed
   to `always`/`unless-stopped` — proven to **not** auto-restart: the
   container is polled over a short bounded window and confirmed to stay
   stopped, and `RestartCount` is confirmed unchanged. `docker compose
   start state` (explicit, this script's own deliberate action, not the
   restart policy) then recovers the chain, and the persisted value is
   confirmed unchanged (nothing incremented while stopped).

   These three are genuinely different Docker lifecycle semantics, not
   three names for the same behavior — conflating "the policy retried
   automatically" (true of both Transient and Persistent Failure) with
   "the service recovered automatically" (true only of Transient Failure)
   was exactly the gap this section closes.
4. **`APP DOWN` / `GATEWAY DOWN`**: `docker compose stop app` — `gateway`'s
   own `/healthz` stays `200` while its `/readyz` degrades; restarting
   `app` recovers it. `docker compose stop gateway` — `app` and `state`
   are proven completely unaffected (both processes alive, both local
   healthchecks still pass); restarting `gateway` recovers the full
   externally-reachable chain.
5. **Cleanup**: `docker compose ... down -t 10 -v` in a `finally` block on
   every exit path (including a real mid-run `SIGTERM`, converted to a
   catchable exception the same way `compose_integration.py` already
   does) — this run's own project/containers/volume only, never another
   Docker resource, never a global prune.

## What Day 5 deliberately does not cover

- **Observability beyond `HEALTHCHECK`/`/healthz`/`/readyz`** — no metrics
  endpoint, no structured/JSON logging pipeline, no tracing, no log
  aggregation. The roadmap's Day 5 theme includes "observability" at a
  high level; this release's actual scope is the health/reliability/
  resource-control triad above, not a metrics stack — adding one (even a
  minimal `/metrics` endpoint) is a new application-surface decision this
  release does not make.
- **CI-enforced verification** — as of Day 5 every gate here was still
  local (`make release-check`) only; Day 6 (`docs/ci-cd.md`) now runs the
  identical gate automatically on every pull request and push to `main`,
  via GitHub Actions, without changing what is being verified.
- **A container registry, or any publishing** — Day 6 added a controlled
  GitHub Release publication workflow (`docs/ci-cd.md`); a container
  registry remains out of scope for the full seven-day arc.
- **Multi-replica `state`, distributed consensus, leader election** — out
  of scope by design (see `docs/persistence.md`'s "Concurrency scope").
  The restart policy in this document restarts a *single* `state`
  replica; it does not make `state` highly available in the sense of
  "another replica keeps serving while one is down."
- **Autoscaling, horizontal scaling of `app`/`gateway`** — not attempted;
  this remains a fixed three-service topology.
- **Seccomp/AppArmor profile changes, a service mesh, TLS between
  services** — unchanged scope boundary from `docs/networking.md`.
- **A precise, universally-portable cgroup v2 proof** — the `[D]`
  cgroup-file check in `reliability_check.py` is explicitly best-effort;
  the `[C]` Docker `HostConfig` proof is the one this project treats as
  authoritative and mandatory, matching CLAUDE.md's instruction not to
  assume cgroup representation without checking the environment.
