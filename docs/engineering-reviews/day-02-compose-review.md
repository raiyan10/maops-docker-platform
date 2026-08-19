# Day 2 Independent Compose & Integration Review

Repository: `maops-docker-platform`
Branch: `feature/day-2-compose-platform`
Target: v0.2.0
Reviewer: independent Compose/integration review agent (review-only)
Review date: 2026-08-19
Scope: `compose.yaml`, `scripts/compose/check_compose.py`,
`scripts/compose/compose_integration.py`, and the real Compose-managed
runtime they claim to prove — per `.claude/CLAUDE.md` and
`docs/roadmap.md`'s Day 2 scope. This review does not re-litigate general
image/Dockerfile/security findings already covered independently in
`docs/engineering-reviews/day-02-security-review.md`; where this review's
own hands-on testing touches the same ground (e.g. the missing [D]
read-only-write proof for Compose-managed containers), that overlap is
called out explicitly as corroboration, not claimed as new.

This review did not trust `compose_integration.py`'s own PASS output, nor
the claim of "10 structural checks" / "25 integration checks" at face
value. Every claim below was independently re-derived: by building the
real image, running the real scripts, standing up separate ad hoc Compose
projects by hand, curling real endpoints, reading `/proc` inside real
containers, and fault-injecting through temporary, byte-for-byte-restored
mutations of `compose.yaml` (verified via `diff`/`md5sum` after every
mutation; no permanent repository change was made; no global prune was
run at any point).

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 1 |
| Low      | 2 |

No Critical or High findings. Nothing below is a live defect in the
running platform's actual behavior — every behavior `docs/compose-platform.md`
claims was independently reproduced and holds. All findings are gaps in
what the *automated verification* independently proves versus what it
implicitly relies on Compose/the config being correct for.

---

## Findings

### M-1 (Medium): `compose_integration.py` never independently proves the `depends_on: condition: service_healthy` startup-ordering *guarantee* — only `check_compose.py`'s static config check does

**Where**: `scripts/compose/compose_integration.py` — `check_runtime_healthy`
(reused from `scripts/verify/security_check.py`) polls up to 30s until a
container's Docker health status becomes `healthy`; nothing in the
runtime script asserts *when* the `gateway` container was created/started
relative to `app`'s health transition.

**Reproduced**: temporarily mutated the tracked `compose.yaml`, changing
`gateway`'s `depends_on: app: condition:` from `service_healthy` to
`service_started` (a real ordering regression — Compose would then start
`gateway` as soon as `app`'s process starts, not once it's healthy).
Restored byte-for-byte afterward (`diff`/`md5sum` before-and-after
confirmed identical; see Appendix).

- `check_compose.py` correctly caught it: `1 finding(s): service 'gateway'
  depends_on 'app' condition is 'service_started', expected
  'service_healthy'`, exit 1.
- `compose_integration.py`, run against the *same* mutated file,
  **passed cleanly**: `compose_integration: PASS (25/25 inspection checks
  passed)`, exit 0. It never noticed the ordering guarantee was gone.
- To confirm this wasn't a coincidence of timing, a separate manual
  `docker compose -p maops-compose-review-order -f compose.yaml up -d`
  run (same mutated condition) showed via `docker inspect
  .State.StartedAt`: `app` started at `08:15:59.453908583Z` while its
  `Health.Status` was still `starting`; `gateway` started at
  `08:15:59.739090951Z` — **0.29s later, while `app` was still
  unhealthy** (its 5s `start_period` alone guarantees `app` cannot be
  `healthy` that early). Compose genuinely did not wait.

**Impact**: `compose_integration.py`'s own health-poll loop is
eventually-consistent (it waits *up to* 30s for `healthy`, regardless of
whether Compose actually gated `gateway`'s start on that health state or
just got lucky on timing). A regression that silently weakened or
dropped the `condition: service_healthy` dependency in `compose.yaml`
would be caught by `check_compose.py` today — but if that structural
check were ever skipped, disabled, or a future dependency were added
that this script's runtime path doesn't independently check, nothing in
the *runtime* proof would catch it. This is exactly the kind of gap the
project's own M-3 (Day 1) closure was meant to eliminate for the
Compose-runtime layer specifically.

**Recommendation** (not applied — review only): add a genuine ordering
assertion to `compose_integration.py`, e.g. record `app`'s health
transition time and `gateway`'s container `StartedAt`/first successful
health-poll time via `docker inspect`, and assert `gateway` did not start
before `app` was healthy — or, more simply, assert `gateway`'s container
does not exist/is not running immediately after `up -d` returns unless
`app` is already healthy at that instant.

---

### L-1 (Low): `check_compose.py` has no cross-check that `gateway`'s `UPSTREAM_HOST` actually names a real service in the same file

**Where**: `scripts/compose/check_compose.py` — no check reads
`services.gateway.environment.UPSTREAM_HOST`/`UPSTREAM_PORT` at all.

**Reproduced**: temporarily mutated the tracked `compose.yaml`, changing
`gateway`'s `UPSTREAM_HOST` from `"app"` to `"nonexistent-service-typo"`
(a realistic refactor-typo: e.g. renaming the `app` service without
updating the gateway's target). Restored byte-for-byte afterward
(verified, see Appendix).

- `check_compose.py: OK (10 structural checks passed ...)`, **exit 0** —
  completely silent. None of the 10 checks inspects this value.
- Only the real runtime test caught it: bringing the mutated stack up for
  real, `gateway`'s `/readyz` correctly returned a controlled `503
  {"error": "upstream unavailable", "status": "not-ready"}` (no
  traceback, no hang) — proving the gateway's own error handling is
  solid — but this required a full `docker compose up` cycle (tens of
  seconds) to discover what a one-line static check could catch
  instantly.

**Impact**: `make quality` (which includes `compose-check` but not
`compose-test`) gives a false-clean signal for this entire class of
regression. Only `make compose-test`/`make release-check` — the slower,
real-container path — would surface it, and only because the gateway
happens to fail closed safely (by design) rather than because anything
detected the misconfiguration directly.

**Recommendation** (not applied): add a `check_gateway_upstream_target`
structural check asserting `services.gateway.environment.UPSTREAM_HOST`
equals `"app"` (or, more generally, is a key of `EXPECTED_SERVICES`) and
`UPSTREAM_PORT` equals `app`'s exposed port. Cheap, instant, and closes
the gap `check_compose.py` otherwise leaves entirely to the slow path.

---

### L-2 (Low, corroborated): no automated check proves [D]-tier (kernel-enforced) read-only-rootfs for either Compose-managed container

**Where**: `scripts/compose/compose_integration.py`, per-container reused
check list (`check_runtime_readonly_rootfs` through the PID-1-identity
check) — `security_check.py`'s real [D] write-rejection check
(`check_kernel_readonly_write_fails`) is present in that module but is
never included in `compose_integration.py`'s list for either `app` or
`gateway`.

This is the same underlying gap already reported as **M-1** in
`docs/engineering-reviews/day-02-security-review.md`, independently
re-confirmed here by reading `compose_integration.py`'s per-container
check block directly (only `[C]` `check_runtime_readonly_rootfs` — "Docker
was asked" — is present; no `[D]` real-write attempt exists in this
script for either container). Recorded here at Low (rather than Medium,
as the security review scored it) because this review's scope is the
Compose script specifically and the underlying property is already
independently confirmed to actually hold (per the security review's own
real-write test against Compose-managed containers) — it is a coverage
gap in this script's own reused-check list, not an open question about
runtime behavior. Not re-litigated further here; see the security review
for full detail and recommendation.

---

## Strongest Compose areas (independently reproduced, not just trusted)

- **Exactly two services, correctly separated.** `docker compose config
  --format json` renders exactly `{app, gateway}`; `app` has no `ports:`
  key at all in the rendered config and, on a real running container,
  `docker inspect .HostConfig.PortBindings` is empty (`{}`).
- **`gateway` is the sole, loopback-only host-published service.**
  Confirmed both in rendered config (`host_ip: 127.0.0.1`) and against a
  real running container via `docker port <container> 8080/tcp` →
  `127.0.0.1:<dynamic-port>` — never `0.0.0.0`.
- **Real service-name DNS, not a hardcoded IP.** `docker exec` into a
  live `gateway` container and running `socket.gethostbyname("app")`
  resolved to the exact IP `docker inspect` reports for the `app`
  container on the same project's default network.
- **A genuine gateway→app HTTP call, not a stub.** Curled
  `/upstream/info` on a manually-started stack directly (not just via the
  script) and got back the real `app` container's live `name`/`version`
  payload.
- **`depends_on: condition: service_healthy` genuinely gates startup**
  (when correctly configured — see M-1 above for the gap in *proving*
  this at runtime): `docker inspect .State.StartedAt` timestamps on a
  clean stack showed `gateway` only starting after `app`'s health
  status had already progressed past `starting`, consistent with the
  documented ordering guarantee.
- **`app`-stop / `gateway`-stays-alive / readiness degrade-and-recover is
  real, and survives a harder fault than the script tests.** Beyond the
  script's own graceful `docker compose stop app` / `start app` cycle
  (independently re-run and confirmed), this review additionally
  hard-killed `app` (`docker kill --signal=KILL`, exit code 137 — a real
  crash, not a graceful shutdown) against a manually-created stack:
  `gateway`'s container stayed `Running`, `/healthz` stayed `200
  {"status":"ok"}` throughout, and `/readyz` correctly degraded to `503`
  with a controlled error body — no traceback, no hang, no crash of the
  gateway process itself, in either failure mode.
- **Dynamic host-port automation is real, not a fixed default in
  disguise.** `GATEWAY_HOST_PORT=0` produced a genuinely different
  OS-assigned host port on repeated independent runs (`32785`, `32789`,
  observed directly), confirmed via `docker port` (not merely the
  requested `HostConfig.PortBindings`, which always shows the requested
  `"0"`).
- **Unique Compose project naming and repeated-run safety are real.** Ran
  `compose_integration.py` twice back-to-back: both runs produced
  distinct `maops-compose-<uuid>` project names, both passed 25/25, and
  `docker ps -a`/`docker network ls` showed zero residue between runs or
  after either run.
- **Cleanup-on-induced-failure genuinely works, not just cleanup-on-success.**
  Deliberately broke the stack (mutated `compose.yaml` to publish `app`'s
  port — see Appendix) and re-ran `compose_integration.py` against the
  real, broken config: it correctly failed (`FAIL: app must not publish a
  host port ...`, exit 1) **and** its `finally`-block `docker compose ...
  down` still ran, leaving zero leftover containers or networks —
  independently confirmed via `docker ps -a`/`docker network ls`
  immediately afterward.
- **Effective runtime hardening genuinely holds on both containers, not
  just `app`.** Independently re-derived (not trusted from the script's
  own PASS lines) via `docker inspect` and `/proc/1/status` inside a
  separately-created project: UID/GID `10001:10001`, all four capability
  sets (`CapInh/CapPrm/CapEff/CapBnd`) zero, `NoNewPrivs: 1`,
  `HostConfig.Privileged: false`, no host PID/network namespace, no
  Docker-socket mount — identically for both `app` and `gateway`.
- **Sensible container-recreation semantics.** A no-op `docker compose up
  -d` re-run left `app`'s container identity (`.Id`) unchanged (no
  unnecessary recreation); changing `GATEWAY_HOST_PORT` recreated only
  `gateway`, leaving `app` untouched, and the recreated `gateway` still
  correctly waited on `app`'s existing health state.
- **The advertised check counts are real, not inflated.** Every one of
  `check_compose.py`'s 10 top-level checks and every one of
  `compose_integration.py`'s 25 counted `CheckResult`s maps to a
  specific, concrete field-level comparison against real data (rendered
  JSON config or live `docker inspect`/`/proc` output) — no vacuous
  "service exists" check with no property assertion was found in either
  script. If anything, the round numbers **undercount** actual
  verification work: several of `check_compose.py`'s 10 functions bundle
  multiple independent field assertions per service into a single list
  entry (e.g. `check_hardening_flags` checks six distinct properties
  across both services — twelve assertions folded into "1 of 10"), and
  `compose_integration.py`'s 25 counted results exclude roughly ten
  additional real, independent, `raise`-based assertions performed
  earlier in the same run (exact image match ×2, app-not-published,
  gateway-loopback-binding, live `/readyz`, live `/upstream/info`,
  gateway-stays-alive, degrade poll, restart-healthy, recovery poll) that
  aren't counted in the printed "25/25" because they fail fast via
  exception rather than accumulating in the `results` list.

---

## Fault injection summary

| Injection | Method | Caught by | Result |
|---|---|---|---|
| Unavailable app (graceful) | `docker compose stop app` | both scripts + manual | Gateway alive, `/readyz` → controlled 503, recovers on `start app` |
| Unavailable app (hard crash) | `docker kill --signal=KILL app` (manual, not scripted) | manual only | Gateway alive throughout, `/readyz` → controlled 503; `app` does **not** self-recover (no `restart:` policy — confirmed Day 5 scope, see L-below) |
| Broken dependency: `condition: service_started` | temp `compose.yaml` mutation, restored | `check_compose.py` only | `compose_integration.py` passed 25/25 despite the ordering guarantee being gone (**M-1**) |
| Broken dependency: bad `UPSTREAM_HOST` | temp `compose.yaml` mutation, restored | runtime only (`/readyz` 503) | `check_compose.py` passed silently (**L-1**) |
| Unexpected host publication (`app` gets a `ports:` mapping) | temp `compose.yaml` mutation, restored | both scripts | Both correctly failed (exit 1); `compose_integration.py`'s teardown still ran cleanly |
| Malformed/non-JSON upstream response | not practically injectable via Compose config alone (fixed proxy target, no configurable path/body) | unit tests (`test_upstream_info_malformed_response_returns_controlled_502`, real loopback fake-server fixture) | Confirmed by source read; correctly out of scope for Compose-level fault injection, and already covered at the right layer |

All mutations were made directly to the tracked `compose.yaml`, verified
restored byte-for-byte via `diff` and `md5sum` against a pre-mutation
backup after every single change (see Appendix), and no mutation was ever
left in place between experiments.

---

## Highest-value missing regression tests

1. **A true startup-ordering assertion in `compose_integration.py`**
   (ties to M-1) — prove `gateway` was not started/created before `app`
   reported healthy, not just that both eventually become healthy.
2. **A structural `UPSTREAM_HOST`/`UPSTREAM_PORT`-vs-real-service-name
   cross-check in `check_compose.py`** (ties to L-1) — cheap, instant,
   and currently the only thing standing between a routine rename typo
   and a silent `compose-check` pass.
3. **A hard-crash (`SIGKILL`, not `stop`/`start`) recovery scenario.**
   Not a defect — this project has no `restart:` policy today, and
   `docs/roadmap.md` explicitly assigns "restart policy review" to Day 5
   — but right now no test or doc distinguishes "recovers after an
   orchestrated `stop`/`start`" (proven) from "recovers after a crash"
   (not proven, and today, *not true* — `app` stayed `exited` with no
   self-healing 15s after a `SIGKILL` in this review's manual test).
   Lowest-cost fix today: nothing needs to change in Day 2 itself, but
   this scope boundary is worth stating explicitly somewhere (it
   currently isn't) so a reader doesn't over-extrapolate from the
   stop/start test.
4. **`[D]`-tier read-only-write proof for Compose-managed containers**
   (ties to L-2/the security review's M-1) — lowest priority of the four
   since the underlying property is already independently confirmed to
   hold; purely a coverage/completeness item.

---

## Flakiness risk assessment

- `check_runtime_healthy`'s 30s deadline (`HEALTHY_DEADLINE_SECONDS`,
  reused from `security_check.py`) is called up to three times serially
  in one `compose_integration.py` run (initial `app`, initial `gateway`,
  `app` again after restart). Observed actual healthy-transition times in
  this review were 1-2s each — comfortable margin today on this machine,
  but this is real wall-clock budget (up to ~90s worst case across the
  three calls) that would tighten on slower/loaded CI hardware; no CI
  exists yet (Day 6 scope), so this is a forward-looking note, not a
  current problem.
- `get_actual_gateway_host_port`'s parsing of `docker port`'s output via
  `line.rpartition(":")` is correct for the IPv4-loopback-only binding
  `compose.yaml` currently declares, but would misparse an
  IPv6-bracketed host address (`[::1]:PORT`) if the binding were ever
  changed to IPv6 loopback. Purely latent — not exercised by anything in
  the current config — flagged only because it's a silent
  string-parsing assumption rather than an explicit IPv4 check.
- No other timing-fragile assumptions were found: the degrade/recover
  polls (`DEGRADE_DEADLINE_SECONDS=30`, `RECOVER_DEADLINE_SECONDS=60`)
  have generous margins relative to the 10s healthcheck interval driving
  the underlying state transitions, and connection-level failures are
  explicitly handled as an immediate "not ready" signal rather than
  waiting out the full poll loop.

---

## Cleanup verdict

**Clean, on every path tested.** Confirmed via `docker ps -a`, `docker
network ls`, and `docker compose ls -a` immediately after each of: a
normal successful run, two consecutive runs back-to-back (repeated-run
safety), and a deliberately-induced failure run (broken `compose.yaml`).
In every case, zero leftover `maops-compose-*` containers or networks
remained. `docker compose down`'s `finally`-block placement in
`compose_integration.py` is unconditional and correctly scoped (only its
own unique `-p <project>` project, never a global or heuristic match).
`make release-check`, run end-to-end in this review (~64s total), also
left no residue afterward.

---

## Verdict: does Day 2 genuinely demonstrate a multi-service Compose platform?

**Yes.** This is a real, independently-reproduced two-service topology
with a real internal-only backend, a real loopback-only gateway, real
Compose-native service discovery (not a hardcoded IP or `links:`), a real
health-gated startup dependency (when correctly configured), real
degrade-and-recover behavior under both a graceful stop and a harder
crash than the automated suite itself tests, real per-container runtime
hardening carried through Compose for *both* roles from a single image,
and real automation for safe, parallel, repeated, and failure-tolerant
test runs (dynamic ports, unique project names, unconditional cleanup).
Every claim in `docs/compose-platform.md` that this review attempted to
independently reproduce did reproduce, exactly as documented.

The two Medium/Low findings above are gaps in what the *verification
tooling* independently proves — not gaps in the platform's actual
behavior. Both are cheap to close (a timestamp comparison; a one-line
string-equality check) and neither blocks calling Day 2's Compose
platform genuine.

---

## Appendix: mutation/restore integrity log

Every temporary `compose.yaml` mutation performed by this review was
restored and verified as follows (all four mutations, each performed and
reverted independently, never overlapping):

```
$ md5sum compose.yaml                     # before any mutation
a8968ffb220cf6f6f76e2f985f7b7846  compose.yaml
...
$ diff compose.yaml <backup>.orig && echo "IDENTICAL - restore confirmed"
IDENTICAL - restore confirmed
$ md5sum compose.yaml                     # after every mutation cycle
a8968ffb220cf6f6f76e2f985f7b7846  compose.yaml
```

Final `git status --short compose.yaml` at the end of this review shows
only the same pre-existing Day 2 working-tree diff against `HEAD` that
was present before this review began — no residual change from any
mutation performed during this review.

No global Docker prune was run at any point in this review. Every
container/project this review created directly (outside the two scripts)
used a unique, project-prefixed Compose project name
(`maops-compose-review-*`) and was torn down via `docker compose ...
down` before the next experiment began; final state was independently
confirmed clean via `docker ps -a` and `docker network ls`.
