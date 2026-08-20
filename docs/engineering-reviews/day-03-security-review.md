# Day 3 Independent Architecture and Container Security Review

**Reviewer scope:** independent review only. Implementation was not modified.
**Repository:** `maops-docker-platform`
**Branch:** `feature/day-3-network-config-persistence`
**Target:** v0.3.0
**Date:** 2026-08-19
**Method:** direct source reading of every new/changed file, real Docker/Compose runtime execution (build, `make release-check`, adversarial probes, induced-failure resource-cleanup test), and independent regeneration of the implementation checksum baseline. No implementation-session claim was accepted without independent reproduction.

---

## Severity counts

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 2 |
| Low | 4 |
| Informational | 0 |

No Critical or High findings were identified. This is a well-engineered, honestly-scoped day of work; the findings below are rigor/precision gaps, not exploitable vulnerabilities.

---

## 1. Reconciliation of implementation report

Independently computed, not trusted from the implementation report:

| Metric | Implementation report claim | Independently verified |
|---|---|---|
| New files | 16 | **20** |
| Modified (tracked) files | not separately audited here | **33** |
| Total test count (Day 3) | not verified here | **195** (`python3 -m unittest discover`, actual run: `Ran 195 tests ... OK`) |
| Day 2 baseline test count | — | **78** (counted at merge commit `8dbec96`, `def test_` methods across `tests/test_config.py`, `tests/test_gateway_config.py`, `tests/test_gateway_healthcheck.py`, `tests/test_gateway_server.py`, `tests/test_healthcheck.py`, `tests/test_server.py`, `tests/test_version.py`) |
| Day 2 → Day 3 net-new tests | — | **117** (195 − 78) |

**New-file count is wrong in the implementation report.** `git ls-files --others --exclude-standard` (respects `.gitignore`, so no `__pycache__` noise) returns exactly 20 new paths:

```
app/platform_config.py
config/platform.json
docs/configuration.md
docs/networking.md
docs/persistence.md
gateway/platform_config.py
state/__init__.py
state/__main__.py
state/config.py
state/healthcheck.py
state/platform_config.py
state/server.py
state/storage.py
tests/test_app_platform_config.py
tests/test_gateway_platform_config.py
tests/test_state_config.py
tests/test_state_healthcheck.py
tests/test_state_platform_config.py
tests/test_state_server.py
tests/test_state_storage.py
```

This matches the task brief's observation that the report's own explicit file list contains 20 entries while its headline number says 16 — the headline is stale/wrong, not the list. See Finding L-1 below.

Modified tracked files: 33, via `git diff --name-only` / `git status --porcelain` (both agree), spanning `.claude/` agent+skill docs, `Makefile`, `README.md`, `VERSION`, `app/config.py`, `app/server.py`, `compose.yaml`, `docker/app/Dockerfile`, six `docs/*.md` files, `gateway/config.py`, `gateway/server.py`, three `scripts/**` validators, and six `tests/test_*.py` files.

---

## 2. Architecture verdict: **PASS**

- Exactly three Compose services declared in `compose.yaml`: `gateway`, `app`, `state`. No fourth service, no separate Dockerfile, no separate image found anywhere in the repository (`find docker -type f` → only `docker/app/Dockerfile`).
- Request path `host -> gateway -> app -> state` confirmed both structurally (compose.yaml `depends_on`/`environment`: `UPSTREAM_HOST=app` on gateway, `STATE_HOST=state` on app) and at runtime (`compose_integration.py`: real `GET /state`/`POST /state/increment` through the gateway's public port, forwarded end-to-end; value observed to change and persist).
- One release image, three roles, independently confirmed:
  - `ENTRYPOINT ["python3"]` / `CMD ["-m", "app"]` in `docker/app/Dockerfile`; `compose.yaml` overrides `command: ["-m", "gateway"]` and `command: ["-m", "state"]` for those services, all three from `image: maops-docker-platform:${VERSION:-0.3.0}`.
  - `compose_integration.py` explicitly cross-checks each of the three Compose-created containers' `Config.Image` equals the single expected tag before proceeding (`compose_integration: all three services created from exact image maops-docker-platform:0.3.0` — observed in this review's own `make release-check` run).
  - Each role's PID 1 identity independently confirmed via `/proc/1/cmdline` inside the real Compose-managed containers: `['python3', '-m', 'state']`, `['python3', '-m', 'app']`, `['python3', '-m', 'gateway']` — all PASS.
- No unnecessary separate runtime images/Dockerfiles were added.

---

## 3. State service security verdict: **PASS**

Read `state/__init__.py`, `state/__main__.py`, `state/config.py`, `state/healthcheck.py`, `state/platform_config.py`, `state/server.py`, `state/storage.py` in full.

| Requirement | Verdict | Evidence |
|---|---|---|
| Python stdlib only | PASS | `grep -rn "^import\|^from" state/*.py` — only `json`, `os`, `re`, `threading`, `http.client`, `dataclasses`, `pathlib`, `typing`, and intra-package imports. |
| Fixed `/data` storage policy | PASS | `state/config.py:37`: `DATA_DIR = Path("/data")`, not env-configurable, not request-controlled. |
| No arbitrary filename / no path traversal | PASS | `state/platform_config.py:38,56-64`: `state_filename` validated against `^[A-Za-z0-9._-]{1,255}$` (no `/` permitted at all) and explicitly forbids `.`/`..`; sourced only from the Compose-mounted config, never a request. |
| No file serving / no directory listing | PASS | `state/server.py` `ROUTES` dict is a fixed, closed set of 5 paths; no filesystem-path-to-response mapping exists. |
| No shell/subprocess execution | PASS | No `subprocess`, `os.system`, `os.popen`, `eval`/`exec` anywhere in `state/`. |
| No arbitrary environment dump | PASS | No response handler reads or echoes `os.environ`. |
| No raw exception/traceback disclosure | PASS | `state/server.py:144-149`: all route-handler exceptions caught generically, client gets `{"error": "internal server error"}` (500); real exception only logged server-side via `self.log_error`. |
| Deterministic JSON responses / correct Content-Type | PASS | `_send_json` always sets `Content-Type: application/json`, `Content-Length`, sorted keys. |
| Controlled 404/405 | PASS | `state/server.py:130-142`: unknown path → 404 JSON; known path, wrong method → 405 JSON with `Allow` header. |
| Safe handling of corrupted persisted state | PASS (adversarially tested, see below) | `state/storage.py:69-92`. |
| Boolean rejected where int required | PASS (adversarially tested) | `state/storage.py:90`: `isinstance(value, bool)` explicitly excluded before the `int` check (Python `bool` is an `int` subclass). |
| Negative values rejected | PASS (adversarially tested) | `state/storage.py:90`: `value < 0` rejected. |
| Schema version checked | PASS (adversarially tested) | `state/storage.py:84-88`. |
| No unsafe silent data reset on corruption | PASS | `CorruptedStateError` is raised, never silently coerced to a default; `GET`/`POST` on `/state*` return a controlled `500`, `/readyz` returns `503`. |

**Adversarial testing performed** (disposable directories under the scratchpad, never touching the repo): wrote `not_json`, `not_object` (JSON array), `missing_schema`, `wrong_schema` (99), `bool_value` (`True`), `negative_value` (`-5`), `string_value`, `float_value`, `missing_value_key`, `extra_keys`, and `valid` payloads directly against `StateStore.read()`. Result: every malformed case raised `CorruptedStateError` with an accurate message; `extra_keys` (schema-tolerant) and `valid` returned correctly. All match the source exactly — no discrepancy between claimed and actual behavior.

---

## 4. Atomic persistence verdict: **PASS** (single-process scope, honestly stated)

`state/storage.py` audited in full; write sequence independently confirmed to be exactly as claimed:

1. `_write_locked` (lines 94-111) builds the JSON payload, opens `tmp_path = self._path.with_name(f".{self._path.name}.tmp")` — `with_name` guarantees the temp file is always in the **same directory** as the target, so it cannot escape `/data`.
2. Writes the payload, `flush()`, `os.fsync(tmp_file.fileno())` — all inside the `try` block, before `os.replace`.
3. `os.replace(tmp_path, self._path)` — atomic same-filesystem rename.
4. `except BaseException: tmp_path.unlink(missing_ok=True); raise` — any failure during write/flush/fsync/replace cleans up the temp file before propagating.
5. `self._fsync_dir()` (lines 113-128) runs only after a successful replace, fsyncing the containing directory; failures here are swallowed (best-effort, matches the documented "practical, not database-grade" scope).

**Empirically forced-failure tests performed** (mocking `os.replace` and `os.fsync` to raise mid-write, against disposable scratch directories):
- Forced `os.replace` failure: `increment()` raised the injected `OSError` as expected; **zero files** remained in the data directory afterward (no stale `.tmp` file) — cleanup confirmed.
- Forced `os.fsync` failure (before replace is ever attempted): same result — `OSError` raised, **zero files** remained.
- A subsequent normal `increment()` after either forced failure produced the correct value (`1`), proving no corrupted/partial state was left behind by the failed attempt.

**Stale temp files:** cannot persist across a failed write in the observed code path (the `except BaseException` cleanup unconditionally unlinks). This does *not* protect against an external process crash (SIGKILL) between file creation and the `except` handler running — that's a real, if narrow, gap inherent to any non-transactional filesystem write, correctly out of scope for this project's stated ambitions (not a database).

**Lock scope:** `increment()` (lines 61-67) acquires `self._lock` once and holds it across *both* `_read_locked()` and `_write_locked()` — the complete read-modify-write sequence is inside a single critical section. Empirically confirmed: 200 concurrent `threading.Thread` increments against one `StateStore` produced exactly `200`, with zero lost updates.

**Scope honestly stated, confirmed accurate:** `docs/persistence.md`'s "Concurrency scope" section correctly states this is single-process, in-container safety only, not a distributed database — matches the actual code (no file locking, no cross-process coordination).

---

## 5. Configuration security verdict: **PASS**

Read `config/platform.json`, `app/platform_config.py`, `gateway/platform_config.py`, `state/platform_config.py` in full, plus every call site (`app/config.py`, `gateway/config.py`, `state/config.py`, `app/server.py`, `gateway/server.py`).

- **Non-secret:** `config/platform.json` contains only `schema_version`, `platform_name`, `dependency_timeout_seconds`, `state_filename`. `grep -iE "password|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|credential"` against the file: no match.
- **Schema/type validation meaningful, adversarially confirmed:** for both `app.platform_config` and `gateway.platform_config`, directly invoked `load_platform_config()` against synthetic temp files: non-JSON, wrong `schema_version` (2), boolean timeout, negative timeout, zero timeout, timeout > 30, string timeout — every case raised `ValueError` with an accurate message; a valid payload loaded correctly. Identical, independently-confirmed behavior in both modules (they are near-identical, per-package by design).
- **Config path is fixed, not HTTP-controlled:** `DEFAULT_CONFIG_PATH = Path("/etc/maops/platform.json")`; the only override is `PLATFORM_CONFIG_PATH`, an environment variable set (or not) at container-start time — never derived from a request.
- **Cannot override upstream host / cannot become an SSRF vector:** confirmed by reading the dataclasses — `app.platform_config.PlatformConfig` and `gateway.platform_config.PlatformConfig` each carry only `schema_version` and `dependency_timeout_seconds`. Neither carries a host/port/URL field. `STATE_HOST`/`UPSTREAM_HOST`/`STATE_PORT`/`UPSTREAM_PORT` are read exclusively from `app/config.py`/`gateway/config.py` via `os.environ`, never from the mounted file. `app/server.py`'s `_call_state()` and `gateway/server.py`'s `_call_upstream()` both connect only to `config.state_host`/`config.upstream_host`, resolved once at process startup.
- **Does not override network policy:** the config schema has no field capable of expressing a network/host/port destination at all.
- **Mounted config genuinely read-only, proven at [C]+[D]:** `compose_integration.py`'s `check_config_mount_readonly()` — real `docker inspect` shows `Mounts[].RW == false` for the config mount on all three containers, **and** a real `docker exec ... sh -c 'echo probe > /etc/maops/platform.json'` was attempted and rejected (`write_exit=2`, "Read-only file system") on all three, confirmed in this review's own `make release-check` run (`[D:kernel/process] PASS platform config mount rejects a real write ([C]+[D])` × 3).

---

## 6. Container security — all three services: **PASS**

Verified against a real, uniquely-named Compose stack (`maops-compose-13c015798da7`, this review's own `make release-check` run; 55/55 `compose_integration.py` checks passed) and cross-checked with a standalone hardened `docker run` (`scripts/verify/security_check.py`, 22/22 passed):

| Property | gateway | app | state |
|---|---|---|---|
| UID:GID | 10001:10001 | 10001:10001 | 10001:10001 |
| PID 1 identity | `python3 -m gateway` | `python3 -m app` | `python3 -m state` |
| ReadonlyRootfs | `true` | `true` | `true` |
| Protected-root write fails | rejected (`Read-only file system`) | rejected | rejected |
| Service remains functional after rejected write | PASS, but see **Finding M-1** below | PASS | PASS, but see **Finding M-1** |
| CapDrop | `[ALL]` | `[ALL]` | `[ALL]` |
| CapEff / CapPrm / CapBnd | all `0000000000000000` | all `0000000000000000` | all `0000000000000000` |
| NoNewPrivs | `1` | `1` | `1` |
| Privileged | `false` | `false` | `false` |
| Host PID namespace | no (`PidMode=""`) | no | no |
| Host network | no (`bridge`/Compose network, never `host`) | no | no |
| Docker socket mounted | no | no | no |
| Unexpected host bind mount | none — only the `configs:` platform-config mount (a small, tracked, non-secret file, the CLAUDE.md-documented exception) and, for `state`, the named `state_data` volume | none | `/data` via named volume only |

`state`-specific additional proofs, all PASS:
- `/data` genuinely writable despite `read_only: true` on the rootfs: `check_state_data_write_succeeds` — real `echo probe > /data/.maops-write-probe && rm -f` succeeded (`exit=0`).
- `state` does not require root: confirmed via the same UID:GID=10001:10001 check above, and via `docker/app/Dockerfile`'s `RUN mkdir -p /data && chown 10001:10001 /data` executing before `USER 10001:10001`.
- Only `/data` is writable: rootfs write-rejection proof above, plus source review confirms no other path is ever opened for writing by `state/storage.py`.

### Finding M-1 (Medium) — see full writeup below: the "service remains functional after rejected write" sub-check is not genuinely per-service.

---

## 7. Image security verdict: **PASS**

Performed a real `docker build --no-cache` (via `make build`, part of `make release-check`) and inspected/exported the resulting image rather than trusting source declarations alone:

- **Base image digest-pinned:** `FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`. Independently confirmed this digest resolves and matches (`docker image inspect python:3.13-slim@sha256:...` → `.Id` equals the same digest). No `:latest` anywhere.
- **VERSION / labels:** `VERSION` file = `0.3.0`; built image tagged `maops-docker-platform:0.3.0`; `org.opencontainers.image.version` label = `0.3.0` (exact match, independently read via `docker image inspect ... .Config.Labels`). `org.opencontainers.image.source` label truthfully points at the actual repository URL.
- **Non-root:** `Config.User` = `10001:10001` (image-level, [B]-tier), confirmed matching runtime [C]/[D] evidence in Section 6.
- **`state/` included:** confirmed via `docker history` (separate `COPY --chown=10001:10001 state/ ./state/` layer) and via the fact that all three roles run correctly from the one image.
- **`/data` ownership correct:** independently verified inside a fresh container (`docker run --rm --entrypoint python3 ... -c "import os; print(os.stat('/data').st_uid, .st_gid)"` → `10001 10001`).
- **No unnecessary apt packages:** `docker/app/Dockerfile` contains zero `apt-get`/`apt` invocations of its own (only `groupadd`/`useradd`, both base-image-provided utilities, no new packages installed).
- **No runtime pip dependencies:** no `pip install` anywhere in the Dockerfile; `state/` imports confirmed stdlib-only (Section 3).
- **No secrets in the image:** `docker image inspect ... .Config.Env` contains only `PATH`, `GPG_KEY` (Python's public release-signing key, not a secret), `PYTHON_VERSION`, `PYTHON_SHA256` (a public checksum), `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`. No credential-shaped value anywhere.
- **No dev/review content in the image:** `security_check.py`'s `check_image_content_recursive` extracted `/app` from a real running container and recursively scanned it — `.git`, `.claude`, `.github`, `tests`, `docs`, `README.md`, `scripts`, `compose.yaml`, `.dockerignore` all confirmed absent ("PASS ... clean").
- **Recursive nested `__pycache__`/`.pyc` probe, repeated under a Day 3 package:** `security_check.py`'s `regression_prove_recursive_detection()` self-test (a synthetic 3-levels-deep `__pycache__` fixture, unrelated to the real image) confirmed the scanner genuinely catches nested content, not just top-level; the real image scan came back clean under the same recursive logic. **In addition, this review independently repeated the probe literally under `state/`** (not a generic synthetic fixture): created `state/nested/deep/__pycache__/probe.cpython-313.pyc` and a second, deeper `state/nested/deep/__pycache__/evendeeper/__pycache__/probe2.pyc`, ran a real `docker build --no-cache` against the actual `docker/app/Dockerfile`, and confirmed via `docker run --rm --entrypoint find <image> /app -iname '*.pyc' -o -iname '__pycache__'` that **zero matches** were found anywhere in the built image — the probe content was excluded at both nesting depths. Probe files and the throwaway image tag were removed immediately afterward; `git status --porcelain state/` confirmed no residue.

---

## 8. Health and readiness verdict: **PASS**

Independently read `gateway/server.py`, `app/server.py`, `state/server.py` route handlers for `/healthz`/`/readyz` on all three services:

- **`/healthz` is local-process liveness only, on all three services**, confirmed by source: each `_route_healthz` unconditionally returns `200 {"status": "ok"}` without touching any dependency or the persisted store (`state/server.py:53-56` explicitly comments "Never touches the persisted store").
- **`/readyz` genuinely propagates dependency failure, does not crash the healthy process:**
  - `state`'s `/readyz` performs a real, non-mutating `StateStore.read()`; a corrupted store yields `503 {"status": "not-ready", ...}`, never a crash.
  - `app`'s `/readyz` makes a real, bounded HTTP call to `state`'s own `/readyz`; on failure or non-"ready" status, returns `503`.
  - `gateway`'s `/readyz` makes a real, bounded call to `app`'s own `/readyz`, same pattern.
  - Runtime-proven end-to-end in `compose_integration.py`: `docker compose stop state` → `app` and `gateway` processes stay alive (`is_running` still `true`) and `app`'s own `/healthz` stays `200`; `gateway`'s `/readyz` degrades to `503`; `GET /state` through the gateway returns a controlled `503` (never a hang or traceback); `docker compose start state` recovers all three layers' readiness without manual intervention. All observed directly in this review's own `make release-check` run.
- **Storage-failure behavior is documented accurately:** `docs/persistence.md`'s description of `500`/`503` behavior on corruption matches the actual `state/server.py` code exactly (Section 3 above).

---

## 9. Day 2 [D]-tier read-only-write finding closure verdict: **CLOSED (automated)**, with a rigor caveat (Finding M-1)

The requirement was: the automated Compose path must attempt an actual prohibited write for gateway, app, and state, and assert continued service afterward — not merely `docker inspect`.

Confirmed genuinely automated, not manual: `scripts/compose/compose_integration.py:543` calls `sc.check_kernel_readonly_write_fails(container, 0)` inside a loop over all three real Compose-managed containers. Each invocation performs a **real** `docker exec <container> sh -c 'echo probe > /etc/maops-readonly-probe'` against the actual running container and checks the exit code — this is genuinely [D]-tier (a real attempted action against the kernel), not [C]-tier inspection. Observed directly in this review's own run: identical `write exit=2 stderr='sh: 1: cannot create /etc/maops-readonly-probe: Read-only file system'` for all three containers. **The Day 2 finding is genuinely closed** — this is real automation, reproduced independently by this review, not asserted from the implementation report.

**However (Finding M-1, Medium):** the same function's "service remains functional after rejected write" half is hardcoded to `python3 -m app.healthcheck` (`scripts/verify/security_check.py:367`) regardless of which container it is checking. See full writeup below.

---

## 10. Source validator alias-bypass closure verdict: **GENUINELY FIXED, confirmed**

Read `scripts/lint/check_source.py` in full, then independently exercised `check_source.check_file()` (imported directly, not via `main()`, so no tracked file was touched) against seven synthetic throwaway files under the scratchpad directory:

| Case | Result |
|---|---|
| `import os as operating_system; operating_system.system(...)` | **CAUGHT** — `forbidden call: os.system() (module 'os' imported as 'operating_system')` |
| `from os import system as run; run(...)` | **CAUGHT** — `forbidden call: os.system() imported as 'run' (from os import system as run)` |
| `import os as sys_ops; sys_ops.popen(...)` | **CAUGHT** |
| `from os import popen as p; p(...)` | **CAUGHT** |
| `import os; os.system(...)` (baseline, unaliased) | **CAUGHT** (regression-safe) |
| Two-hop indirection (`import os as x; y = x; y.system(...)`) | not caught — **explicitly out of the module's own documented scope** ("does not attempt to track... aliasing through indirection deeper than one hop") |
| `getattr(os, "system")(...)` dynamic dispatch | not caught — a fundamentally different bypass technique, also outside this module's documented, narrow, honestly-scoped AST check |

The specific finding this closure claims to fix — a single-hop import-alias bypass of `os.system`/`os.popen` detection — is genuinely fixed, both for `import os as X; X.system()` and `from os import system as X; X()` forms, and for both `system` and `popen`. The two residual bypass techniques are honestly out of scope per the module's own docstring, which explicitly disclaims being "a general-purpose static security scanner" — not manufactured as findings here.

---

## 11. Resource safety / cleanup verdict: **PASS**

- `grep -rn "prune\|rm -rf\|docker rm\|docker rmi\|volume rm\|network rm" scripts/ Makefile`: no global prune command anywhere; the only `rm -rf` is `Makefile`'s `clean` target removing local `__pycache__`/cache directories; the only `docker rm` is `-f` against exact, pattern-matched `maops-smoke-*`/`maops-security-*` container names.
- All test/validation scripts (`container_smoke.py`, `security_check.py`, `compose_integration.py`) generate a unique `uuid.uuid4().hex[:12]`-suffixed name per run and clean up via `try`/`finally`.

**Induced-failure test performed** (this review's own, using disposable resources, fully cleaned up afterward): brought up a baseline Compose project `maops-compose-reviewbase01` on a fixed host port, then attempted to bring up a second project `maops-compose-inducedfail02` on the *same* port — `state` and `app` for the second project came up healthy, but `gateway` genuinely failed (`port is already allocated`), producing a real, non-synthetic partial-failure state (3 containers total for P2: 2 healthy, 1 failed-to-start). Applying the same `docker compose -p <project> down -t 10 -v` teardown pattern the scripts use removed **exactly** P2's 3 containers, its named volume, and both its networks — while P1's identical-shaped resources were completely untouched (confirmed via `docker ps -a`/`docker volume ls`/`docker network ls` diffs before/after). Both projects were then cleanly torn down by this review, leaving zero residual Docker state.

---

## 12. Required commands

All executed for real in this review (not accepted from the implementation report):

| Command | Result |
|---|---|
| `make test` | `Ran 195 tests ... OK` |
| `make lint` | `check_source.py: OK (20 file(s) scanned under app/, gateway/, state/)` |
| `make dockerfile-check` | `check_dockerfile.py: OK (9 checks passed)` |
| `make compose-check` | `check_compose.py: OK (14 structural checks passed against the rendered compose config, version=0.3.0)` |
| `make quality` | subset of the above, PASS |
| `make build` | real `docker build --no-cache`, succeeded, image `maops-docker-platform:0.3.0` |
| `make inspect` | image inspect/ls/history printed and reviewed (Section 7) |
| `make smoke` | `smoke: PASS` (`/healthz` OK, `/readyz` correctly reports dependency-unavailable outside Compose, `/info` OK version=0.3.0, uid=10001) |
| `make security-check` | `security_check: PASS (22/22 checks passed)` |
| `make compose-test` | `compose_integration: PASS (55/55 inspection checks passed)` |
| `make release-check` | full pipeline PASS end-to-end (all of the above, sequentially) |
| `docker compose config` | rendered cleanly; three services, correct networks/configs/volumes/environment, matches source |

Additional direct, independent checks performed beyond the required list: adversarial malformed-state testing, forced-failure atomic-write testing, concurrency lock testing, config validation fuzzing, alias-bypass synthetic testing, induced-failure resource-cleanup testing, base-image digest resolution check, `/data` ownership check — all detailed in the relevant sections above.

---

## 13. Documentation verdict: **PASS, with two stale/overclaiming spots (Findings M-2, L-3)**

Cross-checked `README.md`, `docs/architecture.md`, `docs/security.md`, `docs/compose-platform.md`, `docs/networking.md`, `docs/configuration.md`, `docs/persistence.md`, `docs/roadmap.md` against the actual code and actual runtime behavior verified above.

All documents accurately describe Day 3 as implemented, correctly scope Day 4+ as not-yet-built, and correctly retire stale Day 2 topology language (e.g. `docs/architecture.md`'s "Two-package layout" → "Three-package layout", `docs/security.md`'s Day 2-only scope note replaced with an accurate three-service one). No Day 4+ functionality was found represented as implemented. No persistence overclaim found — `docs/persistence.md`'s "Concurrency scope" section is explicit and accurate about single-process-only guarantees.

**Two documentation issues found:**
- `docs/networking.md:86` claims a runtime proof that does not exist. See Finding M-2 below.
- `docs/compose-platform.md:65` still names a removed Day 2 constant as if it still governs gateway's outbound timeout. See Finding L-3 below.

---

## 14. Findings

### Finding M-1 (Medium) — `check_kernel_readonly_write_fails`'s liveness sub-check is hardcoded to `app.healthcheck`, not genuinely per-service

- **Location:** `scripts/verify/security_check.py:357-377` (function `check_kernel_readonly_write_fails`), specifically line 367: `run_docker(["exec", container_name, "python3", "-m", "app.healthcheck"])`. Reused generically for all three roles at `scripts/compose/compose_integration.py:528-543` (the `for name, container in containers.items():` loop, which includes `state` and `gateway`).
- **Reproduction:** started a disposable, uniquely-named container running the `state` role (`docker run ... maops-docker-platform:0.3.0 -m state`), then ran `docker exec <container> python3 -m app.healthcheck` — **exit code 0**, despite `app.healthcheck` nominally checking a completely different service's endpoint. Compared against `docker exec <container> python3 -m state.healthcheck` (the actually-correct probe for that role) — also exit code 0. Container cleaned up by this review afterward.
- **Actual result:** the "service remains functional after rejected write" proof for the `gateway` and `state` Compose-managed containers does not genuinely invoke that container's own healthcheck module; it happens to pass only because all three services' healthcheck modules independently converge on identical semantics (port 8080, path `/healthz`, response `{"status": "ok"}`).
- **Expected result:** section 6/9's "service remains functional after rejected write" proof should independently verify *that container's own* liveness, e.g. `python3 -m {role}.healthcheck`.
- **Impact:** not a security vulnerability today — the underlying claim (rootfs write rejected, service still alive) is still true, and independently reproduced by this review for all three roles. The impact is verification rigor: this specific sub-check is not actually discriminating per-service, so it would either silently mask a real per-service regression (e.g., if `gateway`'s or `state`'s own `/healthz` broke in a way `app`'s doesn't) or begin failing for a misleading reason if the three services' healthcheck conventions ever diverge (e.g., different default ports).
- **Recommended fix:** parameterize `check_kernel_readonly_write_fails(container_name, port, healthcheck_module="app")` (or infer the role from the container name/image `Cmd`) and pass the correct module name from `compose_integration.py`'s per-service loop.

### Finding M-2 (Medium) — `docs/networking.md:86` claims a runtime `docker network inspect` proof that does not exist

- **Location:** `docs/networking.md`, "## Runtime verification" section, line 86: "`backend`'s real `docker network inspect` output shows `Internal: true`." — listed alongside genuine `compose_integration.py` runtime proofs (DNS resolution checks, container network membership via `docker inspect`).
- **Reproduction:** `grep -n "network inspect\|Internal" scripts/compose/compose_integration.py scripts/compose/check_compose.py` — zero matches in `compose_integration.py` (the runtime script); `check_compose.py:313-314` does check `backend.get("internal") is not True`, but only against the *rendered, static* `docker compose config` output — an [A]-tier source/config check, never a live `docker network inspect` call against a real running network object.
- **Actual result:** `internal: true` for the `backend` network is verified only at [A]-tier (declared config), never at [C]-tier (live Docker runtime state), despite the doc placing this claim under "Runtime verification" alongside genuine [C]-tier proofs.
- **Expected result:** either the doc should describe this specific bullet as an [A]-tier config check (moved out of "Runtime verification"), or `compose_integration.py` should add a real `docker network inspect <network> --format '{{.Internal}}'` check against the live network.
- **Impact:** low-severity but a real precision violation of this project's own explicitly stated evidence-tier philosophy (`docs/security.md`: "A [C]-only claim is never presented as proof of enforcement without a matching [D] check" — the analogous discipline here is not conflating [A] with [C]). Does not affect actual runtime behavior; `backend` genuinely is `internal: true` in the real running Docker network (implied correctly by the config, and consistent with the genuinely-runtime-proven DNS-isolation checks that *are* present), just not proven the way the doc claims.
- **Recommended fix:** add the missing `docker network inspect` check to `compose_integration.py`, or correct the doc's tier claim.

### Finding L-1 (Low) — implementation report's new-file count is wrong

- **Location:** implementation session's summary report (not a repository file).
- **Reproduction:** `git ls-files --others --exclude-standard | wc -l` → 20.
- **Actual result:** headline claim "Files added: 16."
- **Expected result:** 20, matching the report's own explicit (20-entry) file list.
- **Impact:** none to the shipped artifact; a reporting-accuracy issue only, called out per this review's explicit brief.
- **Recommended fix:** none needed in the codebase; correct the reporting process for future days if this recurs.

### Finding L-2 (Low, informational-leaning) — `state/storage.py`'s directory-fsync failure handling is broader than its own comment claims

- **Location:** `state/storage.py:113-128` (`_fsync_dir`), specifically the bare `except OSError: pass` at line 125-126.
- **Reproduction:** code reading; the comment at lines 114-118 says the fallback is "skipped only if the platform genuinely doesn't support it (e.g. some overlay/test filesystems reject O_RDONLY fsync on a directory)," but the `except OSError` at line 125 catches *any* `OSError` during the actual `os.fsync(dir_fd)` call, not only an unsupported-operation-class error.
- **Actual result:** a genuine I/O failure during the post-rename directory fsync (e.g., `EIO` from a real failing disk) would be silently swallowed rather than surfaced, exactly like the documented "unsupported" case.
- **Expected result:** the comment's framing ("skipped only if... doesn't support it") slightly overstates the precision of what's actually caught.
- **Impact:** very low — by the time `_fsync_dir()` runs, `os.replace()` has already succeeded, so the counter value itself is safely on disk; only the crash-durability of the *directory entry* (not the data) is at any marginal risk, and `docs/persistence.md` already correctly describes this as "best-effort." Not a functional bug.
- **Recommended fix:** none required for this project's stated scope; optionally narrow the caught exception or log a warning on failure for future observability.

### Finding L-3 (Low) — `docs/compose-platform.md:65` references a removed Day 2 constant as if it still governs the gateway's outbound timeout

- **Location:** `docs/compose-platform.md`, "## Gateway responsibility" section, line 65: "Every outbound call uses a single bounded socket timeout (`UPSTREAM_TIMEOUT_SECONDS`, 3s) covering both connect and read."
- **Reproduction:** `grep -rn "UPSTREAM_TIMEOUT_SECONDS" gateway/*.py app/*.py` returns zero matches anywhere in the current source. `gateway/config.py:103` sets `upstream_timeout_seconds=platform_cfg.dependency_timeout_seconds` — the value now comes from `gateway/platform_config.py`'s `dependency_timeout_seconds` (default `3.0`, but genuinely overridable via `config/platform.json` without an image rebuild, range `(0, 30]`), not a fixed source constant. `docs/configuration.md`'s own table states this mechanism explicitly "replaces the Day 2 hardcoded `UPSTREAM_TIMEOUT_SECONDS` constant" — so this is an internal documentation inconsistency, not just a stale claim relative to the code.
- **Actual result:** a reader of `docs/compose-platform.md` alone would believe the gateway's outbound timeout is a fixed 3-second constant that cannot change without a rebuild; `docs/configuration.md` (correctly) says the opposite.
- **Expected result:** `docs/compose-platform.md:65` should say the timeout comes from `dependency_timeout_seconds` (mounted config, default `3.0`s, live-changeable via container recreation), matching `docs/configuration.md`, and drop the now-nonexistent `UPSTREAM_TIMEOUT_SECONDS` name.
- **Impact:** none to actual runtime behavior (the code is correct and was independently verified as config-driven in Section 5) — this is purely a documentation-accuracy gap, but exactly the kind of "incorrect configuration semantics" / "stale Day 2 statement not re-scoped for Day 3" this review was asked to flag.
- **Recommended fix:** update the sentence at `docs/compose-platform.md:65` to reference `dependency_timeout_seconds`/`config/platform.json` instead of the removed constant.

### Finding L-4 (Low, informational-leaning) — `StateStore` performs no filename validation of its own; relies entirely on its one caller

- **Location:** `state/storage.py` (the `StateStore` class) accepts a `filename`/path argument with no format validation anywhere in the class itself. The only validation is in the caller, `state/platform_config.py:56-64` (`_validate_state_filename`, the `^[A-Za-z0-9._-]{1,255}$` regex plus the `.`/`..` exact-match rejection).
- **Reproduction:** directly constructing `StateStore(data_dir, "../../../etc/passwd_evil")` at the storage layer succeeds without error — confirmed by direct instantiation in a scratch test. Today this is unreachable in practice: `state/config.py` is the only production caller, and it always sources the filename through `platform_config.load_platform_config()`, which does validate.
- **Actual result:** filename safety is enforced at a single point (the config loader), not defense-in-depth at the storage layer that actually touches the filesystem.
- **Expected result:** for a module explicitly designed around "never an arbitrary path" as a stated security property (`docs/persistence.md`), the class that performs the actual filesystem write would ideally re-validate or at least assert the invariant, so a future second caller (e.g. a Day 4+ change) couldn't silently reintroduce a traversal path.
- **Impact:** none today — no exploitable path exists; this is a structural/defense-in-depth observation, not a live vulnerability.
- **Recommended fix:** optional; if `StateStore` gains a second caller in a later day, either validate the filename in `StateStore.__init__` too, or keep it explicitly documented as "caller's responsibility" in the class's own docstring (it currently is not stated there, only in the caller).

---

## Release blockers

**None.** Both Medium findings are verification-rigor/documentation-precision issues, not exploitable vulnerabilities or functional defects, and all four Low findings are non-functional/reporting/defense-in-depth issues. Nothing here should block v0.3.0.

---

## Checksum reconciliation

Independently regenerated the full-tree checksum set (`find . -type f -not -path './.git/*' | sort | xargs sha256sum`), excluding `.git/` and `docs/engineering-reviews/day-03-*.md` per instruction, and diffed against the implementation baseline at `/tmp/maops-docker-day3-implementation.sha256` (138 files).

**Result: all 138 baseline files are byte-for-byte identical.** The only difference found was one *extra* file on disk not present in the baseline: `./.claude/scheduled_tasks.lock` — confirmed to be explicitly listed in `.git/info/exclude` (gitignored, not a tracked repository file) and is a Claude Code harness runtime artifact unrelated to the Day 3 implementation, not created or modified by this review's investigation of the implementation itself. **The implementation content this review examined is proven unmodified.**

---

## Final verdicts

- **Architecture:** PASS — exactly three services, one image, correct request chain, independently proven.
- **State security:** PASS — stdlib-only, no path traversal, no traceback disclosure, corruption handled safely and adversarially confirmed.
- **Persistence safety:** PASS — atomic write sequence confirmed exactly as claimed, including under forced-failure conditions; lock genuinely protects the full RMW sequence; scope honestly stated as single-process only.
- **Configuration security:** PASS — non-secret, meaningfully validated (adversarially confirmed for all three services), cannot influence upstream destinations, genuinely read-only at [C]+[D].
- **Image security:** PASS — digest-pinned, correct labels/version, non-root, no secrets, no dev content, recursive bytecode probe clean under `state/`.
- **Day 2 [D]-tier read-only finding:** CLOSED (genuinely automated), with Finding M-1 noting the liveness sub-check isn't genuinely per-service.
- **Source validator alias-bypass finding:** genuinely fixed and independently confirmed for both documented bypass forms.
- **Resource-cleanup discipline:** PASS, independently proven under a real induced partial-failure scenario.
- **Documentation:** accurate overall, one evidence-tier overclaim (Finding M-2) and one stale-constant reference (Finding L-3).

**Overall security/architecture verdict: PASS.** v0.3.0 is release-ready from this review's perspective; the Medium and Low findings should be tracked for a future day's cleanup pass but do not block release.

---

## Reviewer's note on method

This review's numeric reconciliation (Section 1), architecture verification (Section 2), container-security proofs (Section 6), image-security proofs (Section 7), Day 2 finding-closure verdicts (Section 9), source-validator alias testing (Section 10), and resource-safety induced-failure test (Section 11) were independently derived twice, by two separate lines of investigation working from the same repository state and the same `make release-check` evidence, and cross-checked against each other before this report was finalized — including a live reproduction of Finding M-1 against a real `state`-role container. Where they diverged (Findings L-3 and L-4, and the literal-under-`state/` nested-`__pycache__` probe in Section 7), both sets of findings were independently re-verified before inclusion here. No finding in this report rests on a single, unverified pass.
