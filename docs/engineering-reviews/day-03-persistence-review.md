# Day 3 Persistence and Docker Volume Review

**Scope:** `state/storage.py`, `state/config.py`, `state/platform_config.py`,
`state/server.py`, the `state_data` named volume, `compose.yaml`'s
persistence/config wiring, and `scripts/compose/compose_integration.py`'s
persistence assertions, for MAOps Docker Platform v0.3.0
(`feature/day-3-network-config-persistence`).

**Method:** independent, not a re-run of the repository's own scripts.
Storage-format/atomic-write/concurrency claims were verified by direct
`python3` execution against `state/storage.py` in an isolated temp
directory. Volume/ownership/lifecycle/read-only claims were verified by
building the image (`maops-docker-platform:0.3.0`) and driving a real
`docker compose` stack under a disposable, uniquely named project
(`maops-review-<hex>`), plus one bare `docker run` against a fresh,
unmanaged volume for the non-root-ownership proof. All review-created
Docker resources (containers, the stack's networks, its named volume,
one throwaway `docker volume create`) were removed at the end; nothing
outside this review's own naming was touched, and no global prune was
run. This document is the only file created.

## Severity counts

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 3 |

No release blockers. Three low-severity findings, all pre-existing,
narrow, and consistent with scope this project has already stated
explicitly elsewhere (see "Limitations").

---

## Storage-format verdict: **SOUND**

`state/storage.py` persists exactly `{"schema_version": 1, "value": <int>}`.
Read validation (`_read_locked`) was exercised directly (not merely
read from source) against every case the review brief lists:

| Case | Result |
|---|---|
| absent initial file | `read()` returns `value=0`, no file created (verified: `store.path.exists()` stays `False`) |
| valid initial state | parses correctly |
| increment | `0 -> 1`, correct |
| multiple increments | monotonic `1,2,3,4,5` |
| malformed JSON | `CorruptedStateError` (existing test) |
| wrong schema version | `CorruptedStateError` (existing test) |
| missing field | `CorruptedStateError` (existing test) |
| negative value | `CorruptedStateError` (existing test) |
| boolean value | `CorruptedStateError` (existing test; `bool` explicitly excluded since it's an `int` subclass) |
| float value | `CorruptedStateError` (verified independently: `value: 3.0` → `"'value' is not a non-negative integer: 3.0"`; **not in the test suite**) |
| string value | `CorruptedStateError` (existing test) |
| empty file | `CorruptedStateError` via `JSONDecodeError` (verified independently: `""` → `"Expecting value: line 1 column 1"`; **not in the test suite**) |
| truncated file | `CorruptedStateError` via `JSONDecodeError` (verified independently: `'{"schema_version": 1, "val'` → `"Unterminated string..."`; **not in the test suite**) |
| extra fields | accepted on read (ignored), silently **dropped** on the next write since `_write_locked` only ever serializes `schema_version`/`value` (verified independently; not a bug — the schema was never advertised as extensible — but an operator hand-editing the file with an extra field should know it won't survive the next increment) |

**Corruption is genuinely surfaced, never silently destroyed or reset.**
Verified against a real running Compose-managed `state` container (not
just unit-level): corrupting `/data/state.json` in-place produced a
controlled `500` from `GET /state`/`POST /state/increment`, a controlled
`503` from `GET /readyz`, and the corrupt bytes remained on disk
untouched (`cat /data/state.json` still showed the injected garbage,
never auto-rewritten to `{"value": 0}` or any guessed value). Restoring
valid JSON in place caused immediate recovery on the very next request —
no restart, no cached bad state, no cached good state either.

## Corruption-handling verdict: **SOUND, with a diagnosability gap (Low)**

**Finding L-1 (test coverage gap, not a correctness bug):** the four
cases above marked "not in the test suite" (float, empty file, truncated
file) plus "extra fields" and a genuine multi-thread concurrency stress
test are named explicitly in this review's brief and are not present in
`tests/test_state_storage.py`. Independent verification (above, and see
"Concurrency verdict") confirms the underlying code already handles all
of them correctly — this is a coverage gap that could let a future
regression through undetected, not a present defect.

**Finding L-2:** corruption detail does not survive the full
`gateway -> app -> state` proxy chain. `state`'s own `GET /state`
correctly returns `500 {"error": "state store corrupted"}`, and its own
`/readyz` returns `503` with the real `CorruptedStateError` message. But
`app/server.py::_call_state` treats *any* non-`200` response from
`state` as a generic `StateError`, and `gateway/server.py::_call_upstream`
does the same one hop further out — so an external client hitting the
public gateway port during a corruption episode sees only
`{"error": "upstream returned unexpected status 503"}` (a 503, not the
underlying 500), indistinguishable from `state` being merely unreachable.
Verified by directly comparing `state`'s own response (`500`,
specific message) against the same request routed through the real
gateway (`503`, generic message) on the same corrupted file. The specific
diagnostic reason is still available — by curling `state` directly, or
via container logs — just not through the platform's public API. This
matches the project's stated narrow proxy design (no request/response
transformation beyond pass-through of a `200`), so it is not scored
above Low, but it is a real diagnosability gap worth naming.

**Finding L-3:** Docker's own reported container health
(`docker inspect --format '{{.State.Health.Status}}'`, what
`docker compose ps` shows) stays `healthy` throughout a corruption
episode, because `HEALTHCHECK`/`state.healthcheck` probes `/healthz`
(liveness only) and never `/readyz`. Verified directly: with
`/data/state.json` corrupted and `/readyz` returning `503`, `docker
inspect` still reported `Health.Status: healthy`. This is intentional
and documented (`docs/persistence.md`: "Liveness only... never touches
the persisted store") — restarting a container whose *data* is corrupt
would not fix anything, so coupling health to readiness would just churn
restarts — but it means an operator watching only Docker's own health
column, not polling `/readyz` specifically, will not notice a corrupted
store.

## Atomic-write verdict: **GENUINE, order confirmed by direct inspection**

`_write_locked`'s actual sequence, confirmed by reading `state/storage.py`
directly (not trusting the docstring): serialize → `os.open` the temp
file (`.{name}.tmp`, same directory, mode `0o600`) → write payload →
`flush()` → `fsync(fileno())` → `os.replace(tmp, target)` → best-effort
directory `fsync`. This is the intended order and is genuinely
implemented, not merely claimed.

- **Same-filesystem atomicity:** the temp file is created via
  `self._path.with_name(...)`, i.e. in the exact same directory as the
  target — `os.replace` is therefore a same-filesystem rename and
  POSIX-atomic. Since `/data` is a single named-volume mount point, this
  holds in the real container topology, not just in unit tests.
- **Temp-file naming/collision safety:** the temp name is deterministic
  (`.{name}.tmp`), not randomized/PID-suffixed. This is safe *only*
  because `threading.Lock` serializes every write within the one process
  that is ever allowed to hold this file open, and the platform is
  explicitly single-replica by design (see "Concurrency verdict"). It
  would not be safe if two independent processes ever wrote the same
  path concurrently — that scenario is out of scope, not silently
  assumed away, per `docs/persistence.md`.
- **Cleanup on exception:** verified structurally — `os.open` (which
  creates the temp file) sits *outside* the `try`; everything from
  `fdopen` through `os.replace` sits *inside* a `try/except BaseException`
  that unlinks the temp file and re-raises. A failure before the temp
  file exists needs no cleanup; a failure after it exists is always
  cleaned up. The existing `test_write_failure_does_not_leave_a_stray_tmp_file`
  (chmod'd read-only directory) confirms this for one failure mode;
  independently re-verified the code path covers `fdopen`/`write`/`fsync`/
  `replace` failures identically since they share the same `except` block.
- **Permissions:** `os.open(..., 0o600)` — confirmed on a real running
  container (`docker exec state ls -la /data`): `-rw------- appuser
  appgroup state.json`. No world- or group-readable state file, no
  `chmod 777` anywhere (`grep -rn "777" Dockerfile compose.yaml` finds
  none; the one `chmod` mentioned in a comment is prose explaining what
  was deliberately *avoided*).
- **Ownership:** files are written by whatever UID the process runs as —
  confirmed `10001:10001` end-to-end (see "Volume-ownership verdict").
- **Stale temp files:** a crash between `os.open` and `os.replace`
  (e.g. `SIGKILL`) would leave a `.state.json.tmp` behind with no cleanup
  on next start — acceptable, since it never gets read (only `self._path`
  is read) and the next successful write's `O_TRUNC` reuses/overwrites it.
  Not tested here (would require an actual kill mid-syscall), but the
  code path (`os.open(..., O_TRUNC)`) makes it self-healing by
  construction; noted as an unverified-but-argued claim, not asserted as
  proven.
- **Crash windows:** the genuinely uncovered window is between
  `os.replace` returning and the directory `fsync` completing — if power
  is lost there, the rename may not be durable on some filesystems/journal
  modes. `docs/persistence.md` already states this honestly
  ("best-effort... not just database-grade durability"); this review found
  no over-claim beyond what's documented.

## Concurrency verdict: **CORRECT for the documented single-process scope, proven under real load**

Ran an independent stress test (50 threads × 20 increments each = 1000
total, against one `StateStore` instance, real `threading.Thread`, not
mocked): **final persisted value was exactly 1000, zero errors, zero lost
updates.** `increment()` acquires `self._lock` once and holds it across
the entire read-modify-write (`_read_locked` then `_write_locked`) —
that single hold is what matters for lost-update safety, and it was
confirmed correct by the load test's exact-match result, not by
code-reading alone.

This is single-process concurrency safety only, as documented. It does
**not** coordinate multiple `state` processes/containers sharing the
volume simultaneously — this platform runs exactly one `state` replica by
design, and `docs/persistence.md` states this limitation explicitly
rather than overclaiming distributed correctness. This review did not
require, and did not find, any multi-container locking — correctly out
of Day 3 scope.

**Coverage gap (part of L-1 above):** despite the correctness result, no
automated test in `tests/test_state_storage.py` exercises concurrent
increments — the only concurrency-adjacent test comment
(`test_partial_write_never_visible_to_a_reader`) is sequential, not
threaded. A regression that reintroduced a lost-update race would not be
caught by CI today.

## Volume-ownership verdict: **SOUND, proven on a genuinely fresh volume**

`docker inspect` on the real three-service stack showed exactly one
named volume, `state_data`, mounted `rw` at `/data` **only** inside
`state`; `app` and `gateway` had no volume mount at all in their `Mounts`
list (only the read-only `configs:` bind for `platform.json`). No host
bind mount is used for persisted state anywhere.

`app`/`gateway` do have an (empty, unmounted) `/data` directory baked
into the shared image itself — an artifact of one image serving three
roles, not a leak of the state volume or a bypass of read-only rootfs.
Verified directly: writing into that directory from `app`/`gateway`
fails with `Read-only file system` (their rootfs is read-only and no
volume is attached there), and it is confirmed structurally empty
(`ls -la /data` shows only `.`/`..`).

Non-root startup on a **brand-new, never-before-used** volume (created
via a bare `docker volume create`, not via `compose up`, and mounted via
a bare `docker run --user 10001:10001` with no root init process
whatsoever) succeeded on the first attempt: container reached `running`,
`/proc/1/status` showed `Uid: 10001 10001 10001 10001` /
`Gid: 10001 10001 10001 10001`, `/data` was already `appuser:appgroup`
(not `root`), a real write/delete succeeded, and `POST
/state/increment` returned `{"value": 1}`. This matches the documented
mechanism (Docker populates a new empty named volume from the image's
existing directory contents at that mount point — `docker/app/Dockerfile`
line 51's `mkdir -p /data && chown 10001:10001 /data`, executed *before*
`USER 10001:10001`) and was independently reproduced, not merely
re-read from the docs.

## Rootfs-vs-data write verdict: **PROVEN, kernel-level, both directions**

On the real Compose-managed `state` container: `HostConfig.ReadonlyRootfs`
is `true` **[C]**; a real write to `/etc/maops-test-probe` was rejected
with `Read-only file system` **[D]**; a real write+read+delete cycle
against `/data/.write-probe` succeeded **[D]**; and the state HTTP API
kept functioning throughout (`python3 -m state.healthcheck` exit `0`
immediately after both probes). All three observed in the same live
container, not separately or with a restart between them — a genuine
simultaneous proof, not three isolated facts stitched together.

## Container-recreation proof: **INDEPENDENTLY REPRODUCED**

Performed personally against a disposable project
(`maops-review-c47441fff210`), reading every value via real HTTP through
the public gateway port (never in-process state):

1. Fresh stack up. `GET /state` → `{"value": 0}`.
2. `POST /state/increment` → `{"value": 1}`.
3. `docker compose up -d --force-recreate --no-deps state` (container
   destroyed and rebuilt; volume untouched). Waited for Docker `healthy`.
4. `GET /state` → `{"value": 1}` — **survived**, read from the newly
   created container/process, not a cached value.

## Down/up persistence proof: **INDEPENDENTLY REPRODUCED**

5. `POST /state/increment` → `{"value": 2}`.
6. Confirmed the named volume existed before teardown.
7. `docker compose down -t 10` (no `-v`) — all containers and both
   networks removed.
8. Confirmed the volume **still existed** after `down` (no `-v`).
9. `docker compose up -d` — full stack recreated from scratch (new
   containers, new networks, same volume).
10. `GET /state` (through the newly assigned gateway host port) →
    `{"value": 2}` — **survived a full stack teardown/recreate**, proven
    against the actually-running new process.

## Stop/start proof: **INDEPENDENTLY REPRODUCED**

11. `docker compose stop state` then `docker compose start state`,
    waited for Docker `healthy` again.
12. `GET /state` → `{"value": 2}` — **survived**.

## Cleanup safety: **CONFIRMED**

13. Final teardown used `docker compose -p maops-review-c47441fff210
    ... down -t 10 -v`. Verified `docker volume ls` before/after: exactly
    one volume existed (`maops-review-c47441fff210_state_data`) and it
    was the only one removed; `docker volume ls` was empty afterward.
    `docker images` confirmed `maops-docker-platform:0.3.0` (and the
    0.1.0/0.2.0 release images) were left in place, untouched, as
    intended. No global prune was run at any point in this review. A
    post-review sweep (`docker ps -a`, `docker volume ls`,
    `docker network ls`, all grepped for `maops-review`) found zero
    leftover resources.

`down` without `-v` was independently confirmed to preserve the named
volume (step 8 above) — the project's core down-vs-down–v safety claim
holds under direct, adversarial-minded testing (i.e., this review did
not simply trust the compose file comment; it tore the stack down and
looked at `docker volume ls` itself).

## Config isolation: **CONFIRMED SEPARATE**

`state`'s `Mounts` list shows two independent entries: `state_data`
(volume, `/data`, `RW: true`) and the `platform.json` bind
(`/etc/maops/platform.json`, `RW: false`). Different mount types,
different paths, different mutability. `state_filename` (the one field
`state`'s `platform_config.py` consumes) only ever selects a filename
*under* `/data`; the config file itself is never written to, and no code
path in `state/storage.py` touches anything outside `data_dir`. The
read-only config mount was independently re-confirmed by attempting a
write from inside a live container context via the same pattern used for
the rootfs probe (not merely re-reading `compose_integration.py`'s
`check_config_mount_readonly`).

## Persistence-test quality (`scripts/compose/compose_integration.py`): **GENUINE, not self-deceiving**

Read the full persistence section of the script adversarially, looking
specifically for the failure mode named in the brief — an assertion that
would pass even if the volume silently failed to persist (e.g. comparing
against a Python variable held in the test process's own memory rather
than a fresh read).

**Result: every persistence assertion re-reads from the live stack over
real HTTP after the state-changing operation, through the public gateway
port:**

- `initial_value` and `incremented_value` come from real `GET`/`POST`
  calls before any container is disturbed.
- After `--force-recreate --no-deps state`, the very next check is a
  fresh `GET /state` compared against `incremented_value` — if the
  volume had not truly persisted, a freshly created `state_data`-mount
  would read back `{"value": 0}` (see "absent initial file" behavior
  above) or, if Docker somehow reused a stale anonymous volume, a
  different value — either way the comparison would fail. This is not
  possible to pass by accident from in-memory state, because the test
  process's Python variable and the actual persisted bytes on disk are
  two entirely different things being compared, and the comparison
  reads the latter via a real network hop into a real new container.
- The same pattern repeats for the `down`(no `-v`)/`up` cycle:
  `post_recreate_value` is captured from a real `POST`, then after a full
  `down`/`up` (all three containers and both networks destroyed and
  recreated), `GET /state` is re-issued against the **new** gateway
  container's **newly assigned** host port
  (`get_actual_gateway_host_port` is called again, not reused) and
  compared.
- The stop/start (graceful) cycle likewise re-reads after `start`.

This review's own independent, separately-scripted reproduction (see
above sections) reached identical qualitative results using its own
disposable project, which further corroborates that
`compose_integration.py`'s persistence checks reflect real, reproducible
volume behavior and are not an artifact of one lucky run or a
self-referential assertion.

One minor observation, not a defect: the script's `EXPECTED_NETWORKS`,
health-ordering, and hardening checks are all genuine `docker
inspect`/`docker exec`-based [C]/[D] checks (already independently spot-
checked for `state` and cross-referenced against this review's own
`docker inspect` output for the review's own stack) — consistent with
the rest of this project's evidence-tiering discipline.

## Limitations (of this review and of the system under review)

- This review exercised the *documented* concurrency scope (single
  process, in-container threads) and did not attempt multi-container or
  multi-replica writers against the same volume — correctly out of scope
  per `docs/persistence.md` and this review's own brief.
- The "crash mid-write" and "stale temp file left behind" claims in the
  Atomic-write section are argued from code structure, not reproduced via
  an actual `SIGKILL` mid-syscall — doing so reliably would require
  fault injection beyond this review's scope; the existing "no stray tmp
  file after failure" test only covers a *directory-permission* failure
  mode, not every possible failure point.
- Directory-`fsync` durability was not independently stress-tested
  against an actual power-loss scenario (not practically testable
  outside specialized tooling); this review confirmed the code path
  exists and degrades gracefully (catches `OSError`), matching the
  "best-effort" claim already made in `docs/persistence.md`.
- This review did not test behavior under a genuinely full disk (`ENOSPC`
  mid-write) — plausible from the code path (the same `except BaseException`
  cleanup would fire) but not empirically reproduced here.

## Release blockers

**None.** All three findings above are Low severity: two are test-
coverage gaps for behavior independently confirmed correct (L-1), and
two are diagnosability/observability limitations that are already
consistent with this project's explicitly documented, narrow scope
(L-2, L-3) rather than silent overclaims. Nothing found here contradicts
a claim made in `docs/persistence.md` or `docs/configuration.md`; if
anything, this review found the documentation slightly conservative
relative to what was actually, independently reproduced (e.g. the
container-recreation and down/up proofs held on the first attempt, with
no flakiness observed).

## Final persistence verdict: **PASS**

The Day 3 persistence, atomic-write, concurrency, volume-topology,
non-root-ownership, read-only-rootfs-with-writable-data, and
config-isolation claims all held under independent, adversarial-minded
verification using disposable Docker resources. The persistence
integration test in `scripts/compose/compose_integration.py` is genuine
— it reads real state from real containers after real disruption, not a
self-confirming in-memory value. Recommended follow-ups (non-blocking):
add the explicitly-named-but-missing unit tests (empty file, truncated
file, float value, extra-fields-on-write-drop, and a real multi-thread
concurrency stress test) to `tests/test_state_storage.py` so today's
correct behavior is regression-protected going forward, and consider
whether the public API should ever distinguish "dependency corrupted"
from "dependency unreachable" if that distinction becomes operationally
important in a later day.
