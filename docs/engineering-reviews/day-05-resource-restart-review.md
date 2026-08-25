# Day 5 Resource + Restart Review — v0.5.0

Repository: `maops-docker-platform`
Branch: `feature/day-5-health-reliability-resources`
Target: `v0.5.0`
Reviewer: independent RESOURCE + RESTART reviewer (review only — no
implementation file was modified; no commit/push/tag/release was
performed).
Date: 2026-08-25.

## Scope

Independent verification of the Day 5 resource-control and restart/stop
lifecycle claims for all three services (`state`, `app`, `gateway`): real
CPU/memory/PID limits on real containers ([C] `HostConfig` and, where
available, [D] cgroup v2 files); the bounded `on-failure:3` restart
policy's real behavior, including the specific claim that `RestartCount`
is an absolute, cumulative, non-resetting lifetime cap; `stop_grace_period`
and clean-SIGTERM stop semantics; whether `docker compose`'s own
structural validator genuinely rejects bad resource/restart configs
rather than merely checking key presence; and the safety of the OOM
test's temporary `docker update --memory` mutation, specifically whether
a silent restoration failure could let `reliability-check` report PASS
while a container stays incorrectly constrained. Nothing below was
accepted on the implementation's word — every claim was independently
re-derived by reading the source and by real, adversarial `docker`
commands run directly against live containers, separate from (and in
addition to) running the project's own scripts.

## Files reviewed

`compose.yaml`, `scripts/compose/check_compose.py`,
`scripts/reliability/reliability_check.py` (all 1008 lines),
`tests/test_reliability_check.py`, `Makefile`, `docs/reliability.md`,
`docs/compose-platform.md`.

## Environment note (not a Day 5 finding)

This sandbox's `docker` on `PATH` (`~/.local/bin/docker`) is a one-line
wrapper (`exec docker.exe "$@"`) that hands off to the Windows-native
Docker Desktop CLI across the WSL2 boundary. Two consequences, both
confirmed to be artifacts of this specific sandbox, not of the code under
review:

- Environment variables set via `VAR=value docker ...` or Python
  `subprocess.run(..., env=env)` are **not** visible to `docker.exe`
  unless also listed in `WSLENV` — confirmed with a minimal
  `${MY_TEST_PORT:-9999}` Compose fixture (rendered `9999` regardless of
  an exported `MY_TEST_PORT=12345`, until `WSLENV=MY_TEST_PORT` was set,
  after which it correctly rendered `12345`). Without this,
  `GATEWAY_HOST_PORT` overrides (including `reliability_check.py`'s own
  `env["GATEWAY_HOST_PORT"] = "0"`) silently fall back to `8080`, which
  collided with this machine's own separately-running dev stack on first
  attempt.
- With that fixed (`export WSLENV=GATEWAY_HOST_PORT:VERSION`),
  `docker compose config`'s `configs.platform.file` renders as a Windows
  UNC path (`\\wsl.localhost\Ubuntu\...`) instead of the POSIX path
  `scripts/compose/check_compose.py`'s `check_config_object()` compares
  against, producing one spurious finding. Confirmed unrelated to Day 5:
  `check_config_object()` is unmodified Day-3 code, and re-running with
  the native WSL2 binary (`/usr/bin/docker`, a symlink into
  `/mnt/wsl/docker-desktop/...`) first on `PATH` instead of the wrapper
  makes it disappear — `check_compose.py: OK (17 structural checks
  passed)`.

All resource/restart/stop verification below was cross-checked with both
CLI paths where it mattered (the actual container `HostConfig`/cgroup
values, which are an engine property, not a CLI-rendering property, and
were identical either way) and is unaffected by this artifact.

## Tests/probes actually run

1. `python3 -m unittest tests.test_reliability_check -v` — **28/28
   passed**, including the injected-failure/injected-exception restore
   proofs and the `restore_failure_is_a_warning_not_a_raise` test (read
   closely below — this project's own test already documents the design
   choice this review scrutinizes).
2. `python3 scripts/compose/check_compose.py` (native `/usr/bin/docker`
   first on `PATH`) — **17/17 passed**.
3. Brought up a fresh, uniquely-named Compose stack
   (`maops-review-<uuid>`, this review's own project, `GATEWAY_HOST_PORT`
   pinned to an unused loopback port to avoid the pre-existing dev
   stack) via a plain `docker compose up -d` — **not** through
   `reliability_check.py` — and independently inspected every one of
   `state`/`app`/`gateway`'s real `HostConfig` (`NanoCpus`, `Memory`,
   `MemorySwap`, `PidsLimit`, `RestartPolicy`) and `Config.StopTimeout`,
   plus each container's own `/sys/fs/cgroup/{memory.max,pids.max,
   cpu.max}` via `docker exec`.
4. Against that same stack's `state` container, directly and manually
   (no script): `docker kill` (confirmed `RestartCount` stays `0`,
   confirmed still `Running=false` after a 13s bounded wait); the
   project's own real-kernel-OOM transient-crash technique via
   `docker exec ... oom_score_adj + memory pressure` (confirmed one
   automatic restart, `RestartCount` `0→1`, no manual `docker start`);
   `docker update --memory 6m --memory-swap 6m` against the
   then-running container (confirmed immediate, repeated OOM-kill and
   bounded exhaustion at `RestartCount=3`); `docker stop` (confirmed
   `ExitCode=0`, ~0.6s, well inside the 10s grace period, and confirmed
   no auto-restart).
5. A **4-round repeated-crash adversarial probe**, purpose-built for this
   review, specifically to test the "absolute, non-per-episode cap"
   claim: reset to `RestartCount=0`, then four separate real kernel
   OOM-kills against the *unmodified* 128 MiB limit, each followed by a
   full recovery to Docker-`healthy` **and 15 seconds of stable healthy
   uptime** (comfortably past Docker's internal restart-manager reset
   window) before the next crash — see Finding I-1.
6. A **direct adversarial simulation of a failed memory restore**: forced
   `state` to `6m`/`6m` (as `with_memory_shrink_restored`'s shrink step
   would, but without ever running its `finally` restore — i.e.
   simulating the exact failure mode the review brief asked about), then
   issued the same `docker start` the script's own post-Scenario-2
   "operator recovery" step issues, and polled for 30s (the same
   deadline `check_runtime_healthy` uses) — see Finding M-1.
7. Ran the actual production mechanism end to end via
   `make reliability-check` (`WSLENV=GATEWAY_HOST_PORT:VERSION` exported
   so the real script's own dynamic-port env override actually reached
   `docker.exe` — see "Environment note" above) — **32/32 checks
   passed**, matching every value this review independently derived in
   probes 3-6.
8. Adversarially exercised `check_compose.py`'s
   `check_resource_limits`/`check_restart_policy`/`check_stop_grace_period`
   functions directly (no file mutation — hand-built config dicts passed
   straight into the functions) against 19 deliberately bad cases:
   missing/`None`/zero/negative/bool-`True`/target-exceeding `cpus`;
   missing/zero/negative/target-exceeding `mem_limit` (both as a numeric
   string, matching this Compose version's real rendering, and as `-1`);
   missing/zero/negative (`-1`, the real "unlimited" `pids_limit`
   sentinel) `pids_limit`; missing/`always`/`unless-stopped`/no-count/
   zero-count/wrong-count `restart`; and missing/zero/wrong-value
   `stop_grace_period` in both integer-nanosecond and Go-duration-string
   shapes, plus a `bool` value for both `cpus` and `stop_grace_period`
   (Python's `bool` is an `int` subclass — a classic silent-pass trap).
   **All 19 were correctly rejected; the one known-good baseline was
   correctly accepted.**
9. Every review-created resource was confirmed fully removed after use
   (`docker ps -a`/`volume ls`/`network ls`, filtered by this review's own
   `maops-review-*` prefix and by `maops-reliability-*` — both empty
   after every run) and the user's own separately-running
   `maops-docker-platform` (default project) dev stack on `127.0.0.1:8080`
   was confirmed untouched throughout (`docker ps` before/after: same
   container, same 32-minute-plus uptime, never restarted).

## Findings

### I-1 (Info — verified correct, not a defect): the absolute, cumulative `RestartCount` cap holds under adversarial spacing, and the documentation states this honestly

`docs/reliability.md` and `reliability_check.py`'s own inline comments
claim `RestartCount` is "a CUMULATIVE, lifetime counter for this one
container instance, not reset per crash episode" and that Scenario 2's
correct assertion is therefore against the absolute configured maximum
(`restart_count_after == EXPECTED_RESTART_MAX_ATTEMPTS`, i.e. exactly
`3`), not a delta from wherever the count happened to already be. This
claim is exactly the kind Docker's own restart-manager has a
well-known alternate behavior for in some versions/configurations: an
internal failure counter that **resets** once a container has run
successfully past a short window (historically ~10s in some `dockerd`
releases) — which, if true here, would make "nothing can crash-loop
forever" **false** in general (a service failing every 15s would restart
indefinitely, each episode getting a fresh budget).

This review specifically adversarially tested for that: four separate
real kernel OOM-kills against `state`'s **unshrunk** 128 MiB limit, each
followed by a full recovery to Docker-`healthy` *and* 15 seconds of
stable uptime (comfortably past any such reset window) before the next
crash. Result: `RestartCount` advanced `0→1→2→3`, and the **fourth**
crash did **not** restart the container at all — it stayed `exited`,
`Running=false`, `RestartCount` pinned at `3`, confirmed for a further
observation window. This is strictly more adversarial than the project's
own Scenario 1→Scenario 2 sequence (which only spaces two episodes,
~20s apart, and only tests the boundary once) and independently confirms
the absolute-cap claim is correct **in this Docker Desktop install**
(server 29.7.2, Compose v5.4.0) — not merely asserted. `reliability_check.py`'s
exact-equality assertion (`== 3`, not `>= before + 3`) is therefore the
right assertion, not a coincidentally-passing one.

Caveat worth keeping in the back of mind (not a finding against this
code): this behavior is a property of the Docker Desktop / `dockerd`
version in use, not of this project's compose.yaml. The docs already
scope this claim to "this project's own Docker Desktop install" and do
not overclaim universality — that framing is accurate and should be
preserved if this project's target Docker version ever changes.

### M-1 (Medium): `with_memory_shrink_restored`'s warning-only restore failure is an accidental, not designed, safety net

The review brief specifically asked whether a restoration failure being
warning-only could let `reliability-check` PASS while a container stays
incorrectly constrained. `with_memory_shrink_restored()`
(`scripts/reliability/reliability_check.py:474-513`) shrinks `state`'s
memory to `6m`/`6m`, runs the bounded-exhaustion assertion, and in its
`finally` block attempts to restore the original values — but if that
restore `docker update` call itself fails, the code only prints a
`WARNING` to `stderr` (line 505-510); it never raises, never appends a
`CheckResult`, and never re-verifies via a follow-up `docker inspect`
that the restore actually took effect. `tests/test_reliability_check.py`'s
`test_restore_failure_is_a_warning_not_a_raise` already documents this as
an intentional design choice, not an oversight.

This review adversarially reproduced the exact failure mode: forced
`state` to `6m`/`6m` and left it there (simulating a failed restore),
then issued the identical `docker start` the script's own
post-Scenario-2 "operator recovery" step issues, and polled for the same
30s `check_runtime_healthy` uses. Result: the container repeatedly
OOM-killed on every attempted boot and never reached Docker-`healthy`
within the deadline — meaning `check_runtime_healthy` would correctly
return `passed=False`, and `main()`'s
`if not state_healthy_after_operator_recovery.passed: raise
ReliabilityError(...)` would correctly fail the whole run. **In this
project, today, with these exact numbers (`6m` shrink target vs. `state`'s
real memory footprint), a silently-failed restore cannot currently
produce a false PASS** — the downstream health-check assertion happens
to catch it.

That protection is incidental, however, not a designed guarantee, and is
tied to the specific numeric choice (`6m`) being small enough that
`state` genuinely cannot boot under it. If a future change ever moved
the shrink target higher (e.g. while tuning for a slower `state` startup
path), or `state`'s own memory footprint dropped, a stuck-at-the-shrunk-
value container could become health-check-clean while still permanently
violating `compose.yaml`'s declared `128m` limit — and `reliability-check`
would report PASS with no record anywhere in its `results` list that a
restore had ever failed. Recommend either: (a) after the restore call,
re-`docker inspect` `HostConfig.Memory`/`MemorySwap` and raise
`ReliabilityError` if they don't match the captured original values
(turns an incidental catch into an explicit one, independent of whether
`state` happens to be sensitive enough to the shrink target to fail
health on its own), or, at minimum, (b) append a `CheckResult` for the
restore outcome so a failed restore shows up in the final tally
(`results`/`failures`) rather than only in `stderr`, which a CI log
consumer watching exit codes and the printed summary — not raw stderr —
could otherwise miss entirely.

### L-1 (Low): `check_compose.py`'s structural resource-limit check has no lower-bound sanity check

`check_resource_limits()` (`scripts/compose/check_compose.py:480-519`)
correctly rejects missing/`None`/zero/negative/`bool`/unlimited-sentinel
values and correctly rejects anything **exceeding** the approved target
(`cpus > 0.5`, `mem_limit > 128 MiB`, `pids_limit > 64`) — independently
confirmed against 19 adversarial cases (probe 8, all rejected correctly).
It does **not**, however, reject a valid-but-absurdly-restrictive value
below the target (e.g. `pids_limit: 1`, `mem_limit: 1` byte) — anything
`> 0` and `<= EXPECTED_*` is accepted. Such a value would almost
certainly prevent the affected service from ever starting.

This is a real gap in the cheap, fast, structural gate specifically, but
it is not a full miss: `reliability_check.py`'s
`check_resource_limits_applied()` asserts **exact equality** against the
single approved target for all three fields on real containers, so a
too-low value would still be caught — just one stage later, at real
Docker runtime (`make reliability-check`, itself part of
`make release-check`), rather than at the cheaper `make compose-check`
stage. Recommend tightening `check_resource_limits()` to also reject a
value below some documented reasonable floor (or simply requiring exact
equality with the approved target, matching what
`check_resource_limits_applied()` and `check_restart_policy()`/
`check_stop_grace_period()` already do for their own fields) so a broken
config is caught at the cheapest gate, not the most expensive one.

## What was independently confirmed correct (no finding)

- **CPU/memory/PID limits, all three services, both evidence tiers**:
  `HostConfig` (`NanoCpus=500000000`, `Memory=134217728`,
  `PidsLimit=64`) and cgroup v2 (`memory.max=134217728`, `pids.max=64`,
  `cpu.max=50000 100000`) matched the declared targets exactly, on every
  one of `state`/`app`/`gateway`, both on this review's own independently
  brought-up stack and on the official `make reliability-check` run.
  Applied by a **plain** `docker compose up -d` — confirmed without
  going through `reliability_check.py` at all.
- **Restart policy matches Compose**: `HostConfig.RestartPolicy ==
  {"Name": "on-failure", "MaximumRetryCount": 3}` on all three real
  containers, matching `compose.yaml`'s `restart: on-failure:3` exactly.
- **`docker kill`/`docker stop` are correctly never used as the
  "unexpected failure" proof mechanism**: confirmed by reading the code
  (only a real kernel OOM-kill is used) and by independently reproducing
  the rejected alternative — `docker kill` against a real
  `on-failure:3` container left `RestartCount` at `0` and the container
  `exited`, confirmed for 13s, matching the documented (and correctly
  acted-upon) claim that Docker's restart-policy engine never applies to
  a daemon-initiated kill/stop regardless of exit code.
- **Bounded retry exhaustion under a genuinely persistent condition**:
  shrinking memory to `6m` on a running container produced immediate,
  repeated real OOM-kills, correctly stopping at exactly `RestartCount=3`
  with `OOMKilled=true`, `Running=false` — never more.
- **Intentional stop does not fight the restart policy**: `docker stop`
  against a real container completed with `ExitCode=0` in ~0.6s (well
  under the 10s `stop_grace_period`/`Config.StopTimeout`), and the
  container stayed stopped with `RestartCount` unchanged — independently
  confirmed both manually and via the official run.
- **Structural validation genuinely validates values, not just key
  presence**: 19/19 adversarial bad configs correctly rejected by
  `check_resource_limits`/`check_restart_policy`/`check_stop_grace_period`
  (see L-1 for the one gap found: no lower-bound floor).
- **Self-cleanup and resource-naming discipline**: every container/
  network/volume/project this review or the project's own scripts
  created was uniquely, deterministically prefixed and fully removed;
  the pre-existing, unrelated dev stack on `127.0.0.1:8080` was never
  touched.

## Verdict

One Medium (M-1, an incidental rather than designed safety net around a
restore-failure edge case — adversarially tested and confirmed *not*
currently exploitable given this project's actual numbers, but a real
robustness gap worth closing) and one Low (L-1, a missing lower-bound
structural check, already caught one stage later at runtime) finding.
Neither invalidates a core claim under real Docker behavior — every
resource/restart/stop-grace claim this review could independently test
against live containers held exactly as documented, including the two
claims (the absolute non-resetting restart cap, and the deliberate
exclusion of `docker kill`/`stop` as valid on-failure proof) most likely
to be wrong in a subtly release-blocking way.

RESOURCE-RESTART REVIEW PASS
