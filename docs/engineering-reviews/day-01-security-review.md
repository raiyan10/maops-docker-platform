# Day 1 Independent Architecture and Container Security Review

**Repository**: maops-docker-platform
**Branch**: feature/day-1-container-foundation
**Target**: v0.1.0
**Review date**: 2026-08-18
**Reviewer**: independent review session (does not trust the implementation
session's summary; every claim below was independently reproduced)

---

## 1. Executive summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 2 |

**Verdict: RELEASE-READY for v0.1.0.** Every security and architecture claim
made by the implementation session was independently reproduced from a cold
start — application behavior, PID 1 / signal handling, base image digest,
Dockerfile hardening, recursive build-context exclusion (via freshly created
nested probe files and a real `--no-cache` build), runtime hardening at the
Docker-configuration layer *and* the kernel/process layer, the healthcheck
fix, the 20/20 security checker, and Compose behavior. No fabricated
evidence, no overclaim stronger than its backing tier, and no Day 2+ feature
represented as implemented were found. Two Low findings are reported below;
neither blocks release.

---

## 2. Required commands — results

| Command | Result |
|---|---|
| `make test` | **PASS** — 34/34 tests, `OK` |
| `make lint` | **PASS** — `check_source.py: OK (6 file(s) scanned under app/)` |
| `make dockerfile-check` | **PASS** — `check_dockerfile.py: OK (9 checks passed)` |
| `make build` (`--no-cache`) | **PASS** — clean build against pinned digest |
| `make inspect` | Ran; see §7 (image size) |
| `make smoke` | **PASS** — `smoke: PASS` |
| `make security-check` | **PASS** — `security_check: PASS (20/20 checks passed)` |
| `docker compose config` | **PASS** — valid, one service |
| `docker compose up -d` / inspect / `down` | **PASS** — see §9 |

Additional independent runtime tests were performed outside the project's
own scripts (direct `docker run`, `docker exec`, `/proc/1/status` reads,
`docker stop` timing, induced-failure tests) — see sections below.

---

## 3. Application security

Read `app/server.py`, `app/config.py`, `app/healthcheck.py`,
`app/version.py`, `app/__main__.py`, `app/__init__.py` in full, and
independently exercised the running server (both via `tests/test_server.py`
and adversarial local requests against a real running container).

Confirmed:

- **Stdlib-only**: no third-party import anywhere in `app/`. `scripts/lint/check_source.py`'s AST-based scan confirms no `eval`/`exec`/`compile`/`__import__`, no `subprocess`/`pickle`/`ctypes` import, no `os.system`/`os.popen`, no `shell=True` — verified by reading the checker's logic (real `ast` parsing, not substring matching) and by re-running it (`OK`).
- **No shell/arbitrary command execution, no file/directory serving, no upload capability**: the route table (`ROUTES`) is a fixed dict of four static handlers; there is no filesystem-path-from-request code path anywhere in `server.py`.
- **No arbitrary environment dump**: `/info` is built only from `AppConfig` fields (`name`, `host`, `port`) and `platform.python_version()` — never `os.environ`. Independently confirmed by curling `/info` on a running container and diffing the field set against the container's actual environment (`PATH`, `PYTHONDONTWRITEBYTECODE`, etc. do not appear).
- **No traceback disclosure**: `send_error()` is overridden to always emit a fixed JSON body; `_dispatch()` catches `Exception` broadly around route handlers and returns a generic 500. Adversarial requests (`GET /does-not-exist`, `POST /healthz`) confirmed no `Traceback`/`.py` substrings in any response body.
- **Controlled 404 / 405**: unknown paths → `404 {"error": "not found"}`; known path + unsupported method → `405` with `Allow: GET, HEAD` header and JSON body. Verified live against a running container, not just by reading tests.
- **Deterministic JSON, correct Content-Type**: every response (including error paths) sets `Content-Type: application/json`; verified live.
- **`/info` safe fields**: exactly `name`, `version`, `python_version`, `host`, `port` — no secret-shaped field.
- **APP_HOST/APP_PORT/APP_NAME handling and APP_PORT validation**: `config.py` validates `1 <= port <= 65535`, rejects empty/whitespace/non-integer, strips surrounding whitespace. `tests/test_config.py` covers boundaries; independently spot-checked several of the same boundary values interactively — behavior matched.

No application-security findings.

---

## 4. Process model / PID 1

Independently built and ran the real image (not source inspection alone):

```
/proc/1/cmdline  ->  python3 -m app
docker inspect --format 'State.Pid'  ->  4593 (host-side PID of the container's PID 1)
```

`docker stop` (SIGTERM) timing, measured independently:

```
received signal 15, shutting down
server stopped
docker stop wall time: ~0.62s
ExitCode=0, Status=exited
```

Container was fully removed after `docker rm -f`; no residue. Bounded,
well inside Docker's default 10s SIGTERM→SIGKILL window. This matches
`docs/architecture.md`'s claim of "~0.4s" closely enough (timing varies run
to run; both are well within the grace window, and the doc doesn't claim a
precise reproducible number).

**Verdict: confirmed at the kernel/process level, not just source.**

---

## 5. Base image

```
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a
```

Independently verified:

- `docker pull` of the exact digest reference succeeds and resolves.
- `docker image inspect` on the resolved image: `Architecture=amd64`, `Os=linux` — matches the project's `amd64` build target.
- `docker run --rm <digest> python3 --version` → `Python 3.13.15` — matches the Dockerfile comment's claim exactly.
- Digest syntax is well-formed (`image:tag@sha256:<64 hex chars>`); not a placeholder/fabricated value — it resolves to a real, pullable Docker Hub manifest.

**Verdict: confirmed, not fabricated.**

---

## 6. Dockerfile security

Verified through source **and** the built image (`docker image inspect`):

| Property | Source (Dockerfile) | Image inspection |
|---|---|---|
| Non-root final user | `USER 10001:10001` | `Config.User = "10001:10001"` |
| No sudo | absent | — |
| No `:latest` | digest-pinned | — |
| No remote `ADD` | none present (only `COPY`) | — |
| No secret-bearing ARG/ENV | none (checked by `check_dockerfile.py`'s regex over `PASSWORD/SECRET/TOKEN/API_KEY/PRIVATE_KEY/ACCESS_KEY/CREDENTIAL`) | `Config.Env` contains only base-image build-time vars (`PATH`, `GPG_KEY`, `PYTHON_VERSION`, `PYTHON_SHA256` — all public, non-secret, inherited from the official `python` image) plus `PYTHONDONTWRITEBYTECODE=1`/`PYTHONUNBUFFERED=1` |
| Explicit WORKDIR | `WORKDIR /app` | `WorkingDir = "/app"` |
| Explicit EXPOSE | `EXPOSE 8080` | `ExposedPorts = {"8080/tcp": {}}` |
| HEALTHCHECK | present, not NONE | `Config.Healthcheck.Test` present |
| Exec-form runtime command | `ENTRYPOINT ["python3", "-m", "app"]` | `Entrypoint = ["python3","-m","app"]` |
| OCI labels accurate | title/description/version/licenses only; `image.source` **intentionally omitted** with an explanatory comment (no GitHub repo yet) | `Labels` contains exactly those four keys — no invented `source` label |
| Minimal image contents | only `app/`, `VERSION`, base Python runtime | confirmed in §8 (no repo files, no dev tooling) |

No unnecessary package installation (no `apt-get`/`pip install` beyond the
base image's own build). `check_dockerfile.py` (re-run independently) also
confirms no `--privileged`/`setuid`/`setcap` string anywhere.

**Verdict: no Dockerfile-security findings.**

---

## 7. Build context / recursive cache exclusion — independently reproduced

Per the review scope, this was **not accepted from the implementation
session's prior log**. A fresh probe was created and a fresh `--no-cache`
build was run in this session:

```
app/__pycache__/review_probe.pyc
app/nested/deep/__pycache__/review_probe.pyc
```

(Note: running `make test` locally also organically populated
`app/__pycache__/*.cpython-*.pyc` real bytecode files alongside the probe —
both were present at build time, giving two independent categories of
leak-candidate content.)

```
docker build --no-cache -f docker/app/Dockerfile -t maops-docker-platform:0.1.0 .
...
#8 [internal] load build context
#8 transferring context: 323B done
```

323 bytes transferred confirms `.dockerignore` excluded the cache content
**at the context-transfer stage**, before it ever reached the daemon. The
built image was then independently exported (`docker export` + `tar -tvf`,
not `docker run --entrypoint find`, to get an unfiltered listing of every
byte in every layer) and scanned:

- Zero `.pyc`/`.pyo`/`__pycache__` entries anywhere under `app/` in the exported image.
- A secondary check via `docker run --rm --entrypoint find <image> /app -iname '*.pyc' -o -iname '__pycache__'` returned nothing, exit 0.
- Repository-only files (`.git`, `.github`, `.claude`, `docs/`, `tests/`, `README.md`, `scripts/`, `compose.yaml`, `.dockerignore`) confirmed absent from the image (anchored grep against the full export listing, avoiding false positives from base-image paths that happen to contain substrings like `scripts/`).

One harmless observation, not a defect: `app/app/nested/` and
`app/app/nested/deep/` exist in the built image as **empty directories**
(zero files, zero bytes) — Docker's `COPY` preserves the directory
skeleton even though `.dockerignore` excluded the `__pycache__` contents
within it. This discloses no information and contains no bytecode; it does
not contradict the "zero probe/cache leakage" finding.

All probe files and the `app/nested/` directory were removed after
verification; confirmed absent via `find` before proceeding.

**Verdict: the recursive-exclusion defect the implementation session claims
to have fixed is genuinely fixed. Independently reproduced with a fresh
probe and a fresh `--no-cache` build, not accepted on the prior session's
say-so.**

---

## 8. Runtime hardening — independently launched, not accepted from source alone

Independently ran (not via `scripts/verify/security_check.py` — a separate,
manually-issued `docker run`):

```
docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges:true ...
```

Results:

```
id -u/-g inside container:        uid=10001(appuser) gid=10001(appgroup)
write to /etc/... :                Read-only file system (exit 2)
write to /app/... :                Read-only file system (exit 2)
/healthz before write attempt:     {"status": "ok"}
/healthz after write attempt:      {"status": "ok"}   <- service kept serving
docker inspect HostConfig:         ReadonlyRootfs=true CapDrop=[ALL]
                                    SecurityOpt=[no-new-privileges:true]
                                    Privileged=false PidMode= NetworkMode=bridge
.Mounts:                           [] (no Docker socket, no host bind mount)
State.Health.Status (after ~8s):   healthy
```

**Verdict: confirmed — UID/GID non-root, real rejected write, service stays
operational, no privileged/host-namespace/socket exposure.**

---

## 9. Effective capability state — kernel-level, not HostConfig-only

Read `/proc/1/status` directly inside the running hardened container
(`docker exec <container> cat /proc/1/status`), independent of
`security_check.py`:

```
CapInh: 0000000000000000
CapPrm: 0000000000000000
CapEff: 0000000000000000
CapBnd: 0000000000000000
CapAmb: 0000000000000000
NoNewPrivs: 1
```

This is strictly stronger evidence than `security_check.py` itself
collects: the script only asserts `CapEff`/`CapPrm`/`CapBnd` are zero; this
independent read additionally confirms `CapInh` and `CapAmb` are also zero
(not claimed anywhere, but true). No gap between "requested configuration"
(`--cap-drop ALL` in `HostConfig`) and "actual kernel-enforced state" (all
five capability sets empty) was found.

**Verdict: effective/permitted/bounding capabilities are genuinely zero at
the kernel level, not merely requested.**

---

## 10. no-new-privileges

- **Docker-runtime tier**: `HostConfig.SecurityOpt = ["no-new-privileges:true"]` (both via manual `docker inspect` and via `security_check.py`'s own check).
- **Kernel/process tier**: `/proc/1/status`'s `NoNewPrivs: 1`, read directly, independent of the project's own scripts.

**Verdict: confirmed at both tiers, matching `docs/security.md`'s claim
exactly.**

---

## 11. Healthcheck

`docker/app/Dockerfile`:
```
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python3", "-m", "app.healthcheck"]
```
`compose.yaml`'s `healthcheck.test` matches the same command.

Independently verified this is the fixed package-invocation form
(`python3 -m app.healthcheck`, which requires `/app` to be on `sys.path`
and imports `app.config` as part of package `app` — this only works when
invoked as `-m app.healthcheck` with `/app`'s parent on the path, exactly
what `WORKDIR /app` + the package layout provides), not the previously
broken bare-script form. Live-tested:

- `docker compose up -d` → `State.Health.Status` reached `healthy` within ~1s (well inside the 5s `start_period`), confirmed independently.
- `security_check.py`'s own `check_runtime_healthy` reached `healthy` too.
- **Negative path, tested directly** (not just "proven implicitly" as `docs/security.md` states): ran `app.healthcheck.check()`'s underlying module directly against a port with nothing listening (`APP_PORT=59999`, nothing bound). Result: **exit code 1**, correctly signaling failure. See §14 Finding L-2 for a nuance in *how* that exit code is produced.

**Verdict: healthcheck fix confirmed live, not just by text presence in the
Dockerfile.**

---

## 12. Security checker (`scripts/verify/security_check.py`)

Read in full and re-run independently. It performs exactly 20 checks,
matching its own "20/20" claim:

- 6 checks before any container exists (2×`[A]` source, 3×`[B]` image inspection, 1×`[B]` regression self-test).
- 2 more `[B]` checks requiring the running hardened container to `docker cp` its `/app` tree out (recursive-bytecode-clean, repo-files-absent).
- 8×`[C]` Docker-runtime-inspection checks.
- 4×`[D]` kernel/process checks.

Specifically verified per the review scope:

- **Recursive image-leakage detection is genuinely recursive**: `recursive_find_forbidden_bytecode()` uses `Path.rglob("*")`, not a one-level `os.listdir()`. Confirmed by reading the code and by the synthetic-fixture regression test (`regression_prove_recursive_detection()`), which builds a `__pycache__` nested three directories deep in an isolated temp tree and asserts the scanner catches it — re-run independently, passed (`synthetic fixture correctly detected: ['a/b/c/__pycache__', 'a/b/c/__pycache__/probe.cpython-313.pyc']`).
- **Capability checks inspect live state**: `check_kernel_capabilities_effective()` reads `/proc/1/status` inside the container via `docker exec`, not `docker inspect`'s `HostConfig`. Confirmed correctly labeled `[D]`.
- **Read-only check causes a real failed write**: `check_kernel_readonly_write_fails()` actually executes `sh -c 'echo probe > /etc/maops-readonly-probe'` inside the container and checks the exit code, then re-probes `/healthz` to confirm the service is still up. This is a real attempted action, not an inference from configuration.
- **No-new-privileges has runtime/process evidence**: `[C]` checks `HostConfig.SecurityOpt`; `[D]` separately reads `/proc/1/status`'s `NoNewPrivs`. Correctly split across tiers, not conflated.
- **Cleanup is reliable — with one caveat**: the container-lifecycle portion (`start_hardened_container` through the four `[D]` checks) is wrapped in `try/.../finally: cleanup(container_name)`, confirmed reliable by inducing a failure (see §14 Finding L-1).

**No check found stronger than its declared evidence tier.** The
`[A]`/`[B]`/`[C]`/`[D]` labeling throughout the script is accurate.

---

## 13. Compose

```
docker compose config    -> valid, one service ("app"), no other services
docker compose up -d     -> Container maops-docker-platform-app Started
docker inspect <cid>:
  ReadonlyRootfs=true
  CapDrop=[ALL]
  SecurityOpt=[no-new-privileges:true]
  Privileged=false
  PidMode=  (not host)
  NetworkMode=maops-docker-platform_default  (not host)
  Mounts=[]  (no Docker socket, no unnecessary writable volume — compose.yaml
              declares no `volumes:` key at all)
curl through the published port -> {"status":"ok"}
State.Health.Status (after ~8s) -> healthy
docker compose down     -> container + network fully removed, verified via
                            `docker compose ps -a`, `docker ps -a --filter`,
                            `docker network ls --filter` all returning empty
```

**Verdict: one service, all declared hardening flags are effective on the
real container, health works, clean teardown.**

---

## 14. Findings

### L-1 (Low) — `security_check.py`'s pre-container checks are not exception-guarded, unlike `container_smoke.py`

- **File/function**: `scripts/verify/security_check.py`, `main()`, lines ~414–419 (`check_image_user`, `check_image_healthcheck`, `check_image_labels`, `regression_prove_recursive_detection`) — these run **before** the `try:`/`finally: cleanup(...)` block that starts at line ~421.
- **Reproduction**: monkeypatched `read_version()` to return a nonexistent version tag and called `main()` directly.
- **Actual result**: `check_image_user()` calls `docker_json(["image", "inspect", ...])` against a nonexistent image, which raises an uncaught `RuntimeError` with a raw Python traceback to stderr. `main()` exits via the uncaught exception (exit code 1, but not via the script's own controlled `security_check: FAIL (n/m checks failed)` summary format). By contrast, `scripts/smoke/container_smoke.py` wraps its entire Docker-lifecycle logic (including the equivalent `docker run` failure) in `try/except SmokeTestError/finally`, producing a clean `smoke: FAIL: docker run failed: ...` message — confirmed by an equivalent induced-failure test.
- **Expected result**: symmetric, controlled failure reporting in both scripts, since both scripts document themselves as producing a clean pass/fail summary.
- **Impact**: **No resource leak occurs** — empirically confirmed no container is created (and thus none is orphaned) when this exception path triggers, since it happens strictly before `start_hardened_container()`. This is a robustness/UX/consistency gap, not a security defect and not a cleanup defect. It would only surface in practice if `make security-check` is run without a matching image having been built first (the `release-check` chain always builds first, so this is not hit on the documented release path).
- **Recommended fix**: wrap the pre-container checks in the same `try/except`, or move `docker_json` error handling into a shared helper that appends a failed `CheckResult` instead of raising, so a missing/misnamed image always produces the script's own controlled `FAIL` summary rather than a raw traceback.

### L-2 (Low) — `docs/security.md`'s healthcheck negative-path description is incomplete

- **File**: `docs/security.md`, "Healthcheck" section; related code: `app/healthcheck.py`, `check()`.
- **Reproduction**: ran `app.healthcheck.check()` (via `python3 -m app.healthcheck`) against a port with nothing listening.
- **Actual result**: exit code is **1** as required — but only because Python's interpreter defaults to exit code 1 on an uncaught exception. `check()` only explicitly catches `json.JSONDecodeError`; a connection-level failure (`ConnectionRefusedError`, or a timeout) is not caught, so it propagates as an uncaught `OSError` and prints a full Python traceback to stderr before the process exits.
- **Expected result** (per `docs/security.md`'s own description): "it treats any non-200 status, any non-JSON body, or any `status != 'ok'` field as failure and exits 1" — this describes only three of the actual failure modes and implies deliberate handling of all of them.
- **Impact**: **None at the security level** — `HEALTHCHECK` only consumes the exit code, and a HEALTHCHECK `CMD`'s stdout/stderr is only visible via `docker inspect`'s health log to a principal who already has Docker access to the container (no untrusted party ever sees this traceback). This is a documentation-precision and code-robustness nit, not a security or availability finding.
- **Recommended fix**: either wrap `conn.request`/`conn.getresponse()` in a `try/except OSError: return False` in `check()` for a clean failure path, or narrow the doc's claim to acknowledge that connection-level failures are handled via Python's default uncaught-exception exit behavior rather than explicit logic.

No Critical, High, or Medium findings. No application-security, base-image,
Compose, or build-context-leakage findings of any severity.

---

## 15. Image size — factual reporting, no causal claim endorsed

Independently rebuilt image (`--no-cache`), inspected fresh:

```
docker image inspect .Size:        42,997,676 bytes   (implementation claimed 42,997,617)
docker image ls:                   CONTENT SIZE 43MB   DISK USAGE 176MB
docker system df -v:               176MB total, 175.8MB "shared" size for this image row
docker info:                       Storage Driver: overlayfs, driver-type: io.containerd.snapshotter.v1
python:3.13-slim base image alone: also reports ~178MB DISK USAGE independently
```

Observations, reported factually:

- The `.Size` figure from a fresh, independent `--no-cache` rebuild differs from the implementation session's reported figure by 59 bytes (42,997,676 vs. 42,997,617) — consistent with normal non-reproducible build metadata (BuildKit attestation/manifest timestamps) across separate build invocations on different days, **not** evidence of tampering. This repository does not yet claim build reproducibility — `docs/security.md` explicitly lists reproducibility verification as Day 4, not-yet-implemented scope.
- This Docker installation uses the containerd snapshotter (`driver-type: io.containerd.snapshotter.v1`), under which `docker image ls`'s "CONTENT SIZE" (compressed content-store size, ~43MB) and "DISK USAGE" (unpacked layer size on the snapshotter) are different accounting bases; the base `python:3.13-slim` image independently shows a comparably large DISK USAGE (178MB) on its own, consistent with most of the app image's 176MB DISK USAGE being inherited, non-deduplicated-in-this-view base-image layers rather than anything specific to the application's own ~43MB of added content.
- This explanation is **consistent with the observed evidence** (matching driver type, matching base-image-alone size) but was not verified against an authoritative Docker/containerd specification document in this session. Per the review scope's instruction, this is reported as a plausible, evidence-consistent explanation — **not** asserted as conclusively established. Both the 43MB and 176MB figures should be reported side-by-side without further causal claims beyond what's stated here.
- Neither figure is currently asserted anywhere in the repository's own committed documentation (`README.md`, `docs/*.md`) — the specific byte counts exist only in the implementation session's out-of-band summary, not in anything a reader of this repository would see. **No documentation overclaim exists on this point.**

---

## 16. Resource safety / cleanup

- Repo-wide search for `docker system prune`, `docker container prune`, `docker image prune`, `docker volume prune`, broad `docker rm`/`docker rmi`, and unsafe `rm -rf` patterns: **zero matches** anywhere in `scripts/`, `Makefile`, or `.claude/`.
- `Makefile`'s `clean` target only removes local `__pycache__`/`.pytest_cache`/`.mypy_cache`/`.ruff_cache` directories and any leftover containers matching the project's own deterministic `maops-smoke-*`/`maops-security-*` naming — read and confirmed matches the CLAUDE.md-documented scope exactly.
- Induced a real smoke-test failure (nonexistent image tag) and a real security-check failure (same technique): both left **zero** leftover `maops-*` containers, confirmed via `docker ps -a --filter`.
- Full `docker compose down` teardown confirmed to remove both the container and its network.

**Verdict: no resource-safety findings. Self-cleanup is reliable across
every failure mode exercised, including the one identified in L-1 (which
doesn't leak a container — it just doesn't report as gracefully).**

---

## 17. Claude infrastructure

- Confirmed exactly **5 agents** (`compose-platform-engineer`, `container-security-reviewer`, `docker-architect`, `docker-test-engineer`, `release-engineer`) and **4 skills** (`compose-validation`, `container-security-validation`, `docker-build-validation`, `release-readiness`) under `.claude/`.
- `.dockerignore` excludes `.claude` and `.claude/**` explicitly — confirmed absent from the built image in §7's export scan.
- Repo-wide grep for prune/unsafe-cleanup language inside `.claude/agents/` and `.claude/skills/`: no matches. The only prune-related text in the repository is `CLAUDE.md`'s *prohibition* of global prune, which is correctly restrictive, not permissive.
- `release-engineer.md` explicitly instructs itself not to invent an explanation for `docker image ls` vs. `docker history` discrepancies and to confirm nothing in the repo asserts CI/registry existence prematurely — consistent with what this review independently found in §15 and throughout.
- These are correctly scoped as developer tooling only; none of them are runtime product features, and none entered the image.

---

## 18. Documentation cross-check

Cross-checked `README.md`, `docs/architecture.md`, `docs/security.md`,
`docs/roadmap.md` against actual, independently-reproduced behavior.

- No overclaims found. Every specific behavioral claim (endpoints, status codes, non-root UID/GID, capability state, read-only enforcement, healthcheck timing, PID 1, graceful shutdown) was independently reproduced and matched.
- No Day 2+ feature is represented as implemented; `docs/roadmap.md` and `docs/security.md`'s "Day 1 limitations" section are explicit and consistent about what is *not* yet built (vulnerability scanning/SBOM, resource limits, CI, multi-stage build, build reproducibility).
- Base image / version references are current and internally consistent (`VERSION` = `0.1.0`, matches image tag, matches OCI label, matches Dockerfile comment date 2026-08-18, matches this review's date).
- One minor inaccuracy: §14 Finding L-2 — `docs/security.md`'s healthcheck negative-path description is incomplete (doesn't mention connection-level failures), though the actual behavior (exit 1) is correct.

---

## 19. Strongest three areas

1. **Kernel/process-level security verification discipline.** The `[A]/[B]/[C]/[D]` evidence-tier framework is not just documented — it's actually honored throughout `scripts/verify/security_check.py`, and this review's own independent `/proc/1/status` reads (§9) found the kernel-level claims to be *conservative*, not overstated (the script doesn't even claim `CapInh`/`CapAmb`, which are also genuinely zero).
2. **Recursive build-context exclusion, proven with a fresh adversarial probe.** This review did not accept the prior nested-`__pycache__` fix on faith — it created new probe files, ran a real `--no-cache` build, and independently exported and scanned the resulting image byte-for-byte. Zero leakage, confirmed fresh.
3. **Honest scope discipline.** Every project-local script (`check_source.py`, `check_dockerfile.py`) documents its own real scope rather than implying broader coverage, and `docs/security.md`/`docs/roadmap.md` are explicit and repeated about what Day 1 does *not* yet implement. No instance of a later day's feature being described as done.

---

## 20. Release blockers

**None.** Both findings are Low severity, have no security impact, and do
not block v0.1.0.

---

## 21. Final verdict

**Security verdict: PASS.** Every runtime security claim in this
repository was independently re-derived — not merely re-read — at the
evidence tier the repository itself claims, and every tier's claim held.

**Architecture verdict: SOUND.** The Docker/Compose responsibility split
described in `docs/architecture.md` is real in the built artifacts, the
PID 1 / signal-handling design was verified live (not from source alone),
and the recursive `.dockerignore` fix was independently reproduced with a
fresh adversarial probe rather than accepted from the implementation
session's log.

**Release readiness for v0.1.0: READY.** 34/34 tests, 9/9 Dockerfile
checks, 20/20 security checks, clean Compose lifecycle, zero
Critical/High/Medium findings, two Low findings with no security impact
and clear, narrow recommended fixes for a future day.

---

## 22. Checksum integrity

Regenerated at the end of this review:

```
find . -type f -not -path './.git/*' -not -path './docs/engineering-reviews/*' \
  -print0 | sort -z | xargs -0 sha256sum
```

Compared against the pre-review manifest at
`/tmp/maops-docker-day1-implementation.sha256`: **identical**. (Locally
generated `__pycache__` bytecode from this review's own `make test` /
healthcheck runs was produced and removed before this final comparison —
those are gitignored/dockerignored build artifacts, not implementation
source, and are not part of either checksum set.) The only file added by
this review is this document itself,
`docs/engineering-reviews/day-01-security-review.md`.
