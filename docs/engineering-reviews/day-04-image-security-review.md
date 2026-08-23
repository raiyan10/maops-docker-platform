# Day 4 Independent Container Image & Runtime Security Review

Repository: `maops-docker-platform`
Branch: `feature/day-4-build-security-reproducibility`
Target: `maops-docker-platform:0.4.0`
Reviewer: independent Day 4 container image and runtime security review
(review-only — no implementation was modified)
Scope: the built release image's Distroless/shellless contract, image
identity, ownership/immutability, per-role (`app`/`gateway`/`state`)
runtime hardening, the Day 3→Day 4 role-aware healthcheck-dispatch
finding closure, PID 1/SIGTERM behavior, `scripts/build/image_audit.py`'s
check quality, and build-context leakage. Does not re-litigate
deterministic-build/reproducibility claims (`docs/build-security.md`,
`scripts/build/reproducibility_check.py`) or SBOM/Trivy supply-chain
content (`docs/supply-chain.md`) — both already covered by
`docs/engineering-reviews/day-04-reproducibility-review.md`, whose PASS
verdicts on image identity/base-pin resolution/Dockerfile architecture
this review treats as established rather than re-deriving from scratch,
while independently re-verifying every claim that is actually this
review's own scope.

**Method**: nothing below was accepted on the strength of a script's own
PASS output. Every primary claim was independently re-derived: a fresh
`docker export`/`tar -tvf` full filesystem listing (not just exec
attempts) for shell/package-manager/pip absence; real, unhardened
(`docker run` with **no** `--read-only`/`--cap-drop`) write attempts as
UID 10001 against `app/`, `gateway/`, **and** `state/` source (the
project's own `image_audit.py` only ever probes `app/server.py`); three
independent `docker run` containers replicating `compose.yaml`'s exact
hardening flags per role; a real adversarial wrong-role dispatch of the
project's own `check_kernel_readonly_write_fails()` function against a
live `state`-role container; a full 3×3 cross-matrix of all three
healthcheck probe modules against all three running roles; independent
`docker stop`/SIGTERM timing for all three roles (the project's own
`security_check.py` only ever tests this for `app`); and a real polluted
`--no-cache` build against a distinctly-tagged throwaway image (never the
real release tag) with synthetic `__pycache__`/`.pyc`/`docs/`/`tests/`/
`.claude/`/`artifacts/` content injected at multiple nesting depths,
followed by full cleanup verified via `git status --short`. All seven
required gates (`make lint`, `make dockerfile-check`, `make build`,
`make image-audit`, `make smoke`, `make security-check`, `make
compose-test`) were run directly by this review a second time,
independently of whichever pass produced the numbers quoted below, and
reproduced the identical PASS/count results both times.

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 1 |
| Medium   | 2 |
| Low      | 3 |

---

## Required gates — results

All run directly against this repository's real working tree.

| Command | Result |
|---|---|
| `make lint` | **OK** (20 workload files + 7 tooling files) |
| `make dockerfile-check` | **OK** (10 checks) |
| `make build` (`--no-cache`) | **succeeded** — `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba`, matches the image already on disk (tree unchanged since the sibling reproducibility review's build) |
| `make image-audit` | **PASS, 19/19 checks** (independently re-derived — see §5 for a quality audit of what these 19 checks actually prove) |
| `make smoke` | **PASS** — single-role + multi-role chain (`state`→`app`→`gateway`) |
| `make security-check` | **PASS, 22/22 checks** — note: this gate only ever exercises the `app` role (single hardened container, no role parameter); gateway/state runtime hardening is proven only via `compose-test`, not a gap in this review's own coverage since this review independently re-proved all three roles directly (§3) |
| `make compose-test` | **PASS, 57/57 inspection checks** — all three real Compose-managed containers |

No test failures. No release blocker from the required-gates run itself.

---

## 1. Distroless / shellless contract

**Filesystem/export evidence** (not exec-only): `docker export` of a
throwaway container, full `tar -tvf` listing (2,750 entries), independently
grepped for shell/package-manager/pip-shaped names anywhere in the archive
— not merely the fixed paths the project's own checks probe:

```
$ tar -tf export.tar | grep -iE '(^|/)(sh|bash|dash|ash|apt|apt-get|dpkg|dpkg-query|pip|pip3|easy_install|python3-config)$'
NONE FOUND
```

Explicit path checks against the full listing: `bin/sh`, `bin/bash`,
`usr/bin/sh`, `usr/bin/bash`, `usr/bin/apt`, `usr/bin/apt-get`,
`usr/bin/dpkg`, `usr/bin/dpkg-query`, `usr/bin/pip`, `usr/bin/pip3`,
`usr/local/bin/pip`, `usr/local/bin/pip3` — **all absent**. No
`setuptools` directory anywhere in the listing.

Second independent line of evidence, real exec attempts (OCI-runtime-level
error, not a Python-level check):

```
$ docker exec <c> /bin/sh -c "echo probe"
OCI runtime exec failed: exec failed: unable to start container process:
exec: "/bin/sh": stat /bin/sh: no such file or directory
$ docker exec <c> /bin/bash -c "echo probe"
... exec: "/bin/bash": stat /bin/bash: no such file or directory
```

Genuinely "no such file" (not "permission denied") — the binary does not
exist, this is not a permissions block on an existing file.

`import pip` / `import setuptools` both raise `ImportError`. `ensurepip`
(the stdlib bootstrap module) is importable, but `ensurepip._bundled` —
the submodule that carries the actual pip/setuptools wheel payload
`ensurepip` needs to bootstrap a real installation — is **absent**
(`ModuleNotFoundError`), so even a hypothetical `ensurepip.bootstrap()`
call could not reconstitute a working `pip`. This closes a gap the
project's own `check_no_pip_or_setuptools()` doesn't address (it checks
`import pip`/`import setuptools` and four fixed binary paths, but not
whether `ensurepip` could rebuild one).

**L-1 (Low, informational): `/var/lib/dpkg/status.d/` package-metadata
directory is present in the final image**, inherited from the
`gcr.io/distroless/python3-debian13:nonroot` base itself (confirmed via
the same export listing — 70+ per-package `status.d/<pkg>` and
`<pkg>.md5sums` files, e.g. `python3.13-venv`, `libssl3t64`, `tzdata`).
This is Distroless's own upstream Bazel-generated package-provenance
metadata (used for SBOM/license accounting), **not** executable
package-manager tooling — no `dpkg`/`apt`/`apt-get` binary exists anywhere
in the export (confirmed above), and this directory cannot be invoked to
install/query/modify packages. Not a defect introduced by this project's
own Dockerfile (nothing in the builder stage touches `/var/lib/dpkg`), and
`check_no_package_manager()`'s scope (binary presence, not database-file
presence) is a reasonable reading of "no package manager" — but the review
brief's "no accidental builder shell/package-manager leakage" instruction
specifically asked not to rely solely on "command not found," and a full
filesystem walk is exactly what surfaces this: the final image is not
*entirely* free of package-manager-shaped content, only free of the
functional tooling. Flagged for completeness, not a release blocker.

Python version and Debian release, independently confirmed via exec (not
merely quoted from `docs/build-security.md`):

```
$ docker exec <c> /usr/bin/python3.13 --version
Python 3.13.5
$ docker exec <c> /usr/bin/python3.13 -c "import sys; print(sys.version)"
3.13.5 (main, Jul 15 2026, 20:25:40) [GCC 14.2.0]
$ cat /etc/os-release  (read via Python, no shell)
PRETTY_NAME="Distroless"
NAME="Debian GNU/Linux" ID="debian" VERSION_ID="13"
VERSION="Debian GNU/Linux 13 (trixie)"
$ cat /etc/debian_version
13.6
```

Matches `docs/build-security.md`/`docs/supply-chain.md`'s claims (Python
3.13/Debian 13 "trixie" family) exactly, and matches the sibling
reproducibility review's independent digest resolution
(`sha256:ed7cd592...6a6c`, the `linux/amd64` manifest under the pinned
index digest `sha256:4376456c1d...4bea`).

**Distroless verdict: PASS.** **Shell-absence verdict: PASS**
(export-listing evidence + exec-error evidence agree). **Package-manager
verdict: PASS for executable tooling, L-1 informational for residual
metadata.** **Pip/setuptools verdict: PASS**, including the deeper
`ensurepip._bundled` check this review added independently.

---

## 2. Image identity

| Property | Value | Verdict |
|---|---|---|
| Tag / Image ID | `maops-docker-platform:0.4.0` / `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` | matches `make build` output exactly |
| Final base digest | `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea` (Dockerfile pin) | PASS — index digest genuine `sha256:`, independently re-resolved by the sibling review |
| Python version | `3.13.5` | PASS — matches "Expected interpreter: `/usr/bin/python3.13`" |
| Debian release | `13` ("trixie") | PASS |
| `ENTRYPOINT` | `["/usr/bin/python3.13"]` | PASS — exec form, no shell wrapper |
| `CMD` | `["-m", "app"]` | PASS |
| `USER` | `10001:10001` | PASS |
| `WORKDIR` | `/app` | PASS |
| `HEALTHCHECK` | `CMD ["/usr/bin/python3.13","-m","app.healthcheck"]`, interval=10s timeout=3s start_period=5s retries=3 | PASS (image-level default; Compose overrides per role, see §3) |
| OCI labels | `title`, `description`, `licenses=MIT`, `version=0.4.0`, `source=https://github.com/raiyan10/maops-docker-platform` | PASS — `source` independently cross-checked against `git remote get-url origin` (`git@github.com:raiyan10/maops-docker-platform.git` → normalized match) |

All confirmed via `docker image inspect --format {{json .Config}}` on the
real, freshly-built (`--no-cache`) image, not read from documentation.

---

## 3. Ownership / immutability and per-role runtime hardening

### 3a. Image-level immutability (bare `docker run`, **no** `--read-only`)

As the real runtime UID 10001 (no `--user` override needed — it's the
image's own default `USER`), against a container started with **zero**
hardening flags:

```
append app/server.py       -> rejected: [Errno 13] Permission denied
create /app/newfile         -> rejected: [Errno 13] Permission denied
append gateway/server.py    -> rejected: [Errno 13] Permission denied
append state/server.py      -> rejected: [Errno 13] Permission denied
create /app/gateway/newfile -> rejected: [Errno 13] Permission denied
create /app/state/newfile   -> rejected: [Errno 13] Permission denied
```

Ownership independently confirmed root-owned (`uid=0 gid=0`) on
`app/server.py`, `app/__main__.py`, `gateway/server.py`,
`gateway/__main__.py`, `state/server.py`, `state/__main__.py`,
`VERSION` — **all six source-tree probes**, not just `app`'s (see M-1
below: the project's own `image_audit.py` only probes `app/server.py`).

`/data`: `uid=10001 gid=10001` on the same non-read-only container; a real
write/readback/remove cycle succeeds — this is the one deliberate
exception, confirmed working under the same bare-run conditions the
source-immutability proof used.

**Persistence across real container recreation** (not merely a
write-then-read in the same container — the review brief specifically
asks that "persisted state works," which this review reads as surviving
container lifecycle, not just a single process's file handle): a second,
independent probe created a uniquely-named Docker volume, mounted it at
`/data` on a fresh container, wrote a marker file (`persisted-value-42`)
via the runtime UID, removed that container entirely (`docker rm`, not
`stop`), started a brand-new container against the *same* volume, and
confirmed the marker file was still present with byte-identical content —
`/data` ownership was re-confirmed `10001:10001` in the fresh container
too. This is a from-first-principles reproduction (a bare volume, no
Compose) of the same property `compose_integration.py`'s own
`state_data`-volume recreation/down-up proof already covers for the real
`state` service (see required-gates output above: "value survived state
container recreation", "value survived a full compose down/up with the
volume retained") — independent confirmation the property is genuine at
the image/volume-mechanics level, not an artifact of Compose's own
orchestration.

**Source ownership verdict: PASS (independently extended to all three
roles). Source write-rejection verdict: PASS. `/data` write proof: PASS.
`/data` persistence proof: PASS (cross-recreation, independent of
Compose).**

### 3b. Per-role runtime hardening (three independent `docker run`s replicating `compose.yaml`'s real flags)

`docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges:true maops-docker-platform:0.4.0 -m <role>`, for each of `app`/`gateway`/`state`:

| Role | UID:GID | PID 1 cmdline | CapEff/Prm/Bnd | NoNewPrivs | ReadonlyRootfs | Rootfs write | Own healthcheck after write | Privileged/hostPID/hostNet | Docker socket |
|---|---|---|---|---|---|---|---|---|---|
| `app` | 10001:10001 | `/usr/bin/python3.13 -m app` | all `0` | `1` | `true` | rejected (`Errno 30`) | exit 0 | false / `""` / `bridge` | none |
| `gateway` | 10001:10001 | `/usr/bin/python3.13 -m gateway` | all `0` | `1` | `true` | rejected (`Errno 30`) | exit 0 | false / `""` / `bridge` | none |
| `state` | 10001:10001 | `/usr/bin/python3.13 -m state` | all `0` | `1` | `true` | rejected (`Errno 30`) | exit 0 | false / `""` / `bridge` | none |

All nine cells independently confirmed via `/proc/1/status`,
`/proc/1/cmdline`, `docker inspect .HostConfig`, and a real attempted
write to `/etc/maops-review-probe` (rejected with `[Errno 30] Read-only
file system`), followed by a real `docker exec <c> /usr/bin/python3.13 -m
<role>.healthcheck` — each role's own correct healthcheck module,
confirmed to exit 0. This independently reproduces `compose_integration.py`'s
57/57 real Compose-managed proof (§ required gates above), via a wholly
separate mechanism (bare `docker run` matching Compose's flags, not
Compose itself).

**Capability state verdict: PASS (all three roles). NoNewPrivs verdict:
PASS (all three roles). UID/GID verdict: PASS (all three roles).
`ReadonlyRootfs` / rootfs-write proof: PASS (all three roles). No
privileged / host PID / host network / Docker socket: PASS (all three
roles).**

---

## 4. Role-aware healthcheck dispatch — Day 3 finding closure, challenged

This is the central claim this review was asked to stress-test: Day 3
found `check_kernel_readonly_write_fails()`'s "service kept serving"
continuation probe always invoked `app.healthcheck` regardless of which
role's container was actually being checked
(`day-03-security-review.md` M-1 / `day-03-test-review.md` A-2). Day 4's
claimed fix is `healthcheck_module_for_role()`
(`scripts/verify/security_check.py:409-425`), used by
`compose_integration.py:693` (`sc.check_kernel_readonly_write_fails(container,
0, role=name)` in a loop over the three real service containers) and
covered by five unit tests in `tests/test_security_check.py` that mock
`subprocess`/`run_docker` and assert the correct module *name* appears in
the recorded argv.

**This dispatch is real at the source level — it genuinely selects a
different Python module name per role — but it has zero actual
discriminating power at runtime, and this review proved it does not.**

Root cause: `app/healthcheck.py`, `gateway/healthcheck.py`, and
`state/healthcheck.py` are functionally **identical** probes — each opens
an `http.client.HTTPConnection` to `127.0.0.1:8080` (each role's own
`DEFAULT_*_PORT` is `8080`), `GET`s `/healthz`, and accepts any `200`
response whose JSON body is `{"status": "ok"}`. Each role's own
`_route_healthz()` (`app/server.py:91-94`, `gateway/server.py:105-108`,
`state/server.py:53-56`) returns the exact same literal body,
`{"status": "ok"}` — nothing role-identifying. Since `docker exec` runs
the probe *inside* whichever container is targeted, and that container's
own server (whatever role it actually is) is listening on the same
loopback port with the same generic response, **any of the three
healthcheck modules passes against any of the three roles**, because the
module name only selects which import path drives an otherwise
role-agnostic HTTP GET against the target container's own loopback.

**Real adversarial proof** (not a mock — a live container, the project's
own unmodified `check_kernel_readonly_write_fails()` loaded via the
identical `importlib.util.spec_from_file_location` pattern its own test
suite uses):

```
container running the STATE role (confirmed: /proc/1/cmdline = "/usr/bin/python3.13 -m state")

CORRECT dispatch, role="state":
[D:kernel/process] PASS ... state service keeps serving: ...
  (probed via /usr/bin/python3.13 -m state.healthcheck)

ADVERSARIAL dispatch, role="app" (WRONG — this container is running state, not app):
[D:kernel/process] PASS ... app service keeps serving: ...
  (probed via /usr/bin/python3.13 -m app.healthcheck)
```

The wrong-role call reports **PASS** and claims "app service keeps
serving" against a container that is not running `app` at all — the exact
failure mode the fix is documented as closing.

**Full 3×3 cross-matrix**, each of the three healthcheck modules run via
`docker exec` against each of the three real running roles:

| container running → / probed with ↓ | `app` | `gateway` | `state` |
|---|---|---|---|
| `app.healthcheck` | exit 0 | exit 0 | exit 0 |
| `gateway.healthcheck` | exit 0 | exit 0 | exit 0 |
| `state.healthcheck` | exit 0 | exit 0 | exit 0 |

All nine combinations pass. The dispatch mechanism has **no** genuine
discriminating power — `healthcheck_module_for_role()`'s own unit tests
(`tests/test_security_check.py::CheckKernelReadonlyWriteFailsDispatchTests`)
only assert the correct module *name* appears in a mocked `subprocess`
call's argv; none of them exercise a real container, so none of them
could have caught this.

### H-1 (High): the Day 3→Day 4 role-aware healthcheck-dispatch "fix" does not close the finding it claims to close

**Location**: `scripts/verify/security_check.py:409-425`
(`healthcheck_module_for_role`, `check_kernel_readonly_write_fails`),
`scripts/compose/compose_integration.py:685-693` (real call site and its
own comment: *"role=name closes Day 3 finding A-2 ... the 'service kept
serving' half now genuinely probes *this* container's own role"*),
`app/healthcheck.py`, `gateway/healthcheck.py`, `state/healthcheck.py`.

**Reproduction**: see the adversarial dispatch and 3×3 matrix above —
reproducible on demand against any freshly started containers of this
image.

**Expected**: per the function's own docstring ("This mapping is the
single source of truth for the dispatch-by-role behavior... Closes Day 3
finding A-2") and `compose_integration.py`'s own comment, invoking the
wrong role's healthcheck module against a real container of a different
role should fail, or at minimum behave differently than invoking the
correct one — that is the entire premise of "role-aware."

**Actual**: it never fails. Every healthcheck module accepts every role's
container, because all three probes are byte-identical in behavior (same
port, same path, same expected body) and are always executed *inside* the
target container via `docker exec`, so the probe's role identity is never
actually load-bearing.

**Impact**: this is a verification-integrity gap, not a live runtime
security defect — the actual hardening properties this review
independently re-proved in §3 (UID, capabilities, `NoNewPrivs`, read-only
rootfs, no privileged/host-PID/host-network/Docker-socket) are all real
and correctly enforced per role, and Docker's own native
per-service `HEALTHCHECK` (declared separately in `compose.yaml`, one
`test:` per service, confirmed correct and independently exercised to
`healthy` status for all three services in §"required gates") is
unaffected by this finding — that mechanism inherently only ever probes
the container it's declared on, regardless of this dispatch function.
What *is* affected: if a future regression ever caused
`compose_integration.py`'s loop to check the wrong container against the
wrong expected role (a copy-paste error, a container-name/role-label
mismatch, exactly the class of bug Day 3's original finding was about),
this specific check would not catch it — it would report PASS either way,
identical to the pre-Day-4 hardcoded-`app.healthcheck` behavior it was
built to replace. The claimed regression protection does not exist.

**Recommended fix**: either (a) make the probe genuinely role-discriminating
— e.g., have each role's `/healthz` body include its own role name (`{"status":
"ok", "role": "state"}`) and have `check_kernel_readonly_write_fails()`
assert the response's `role` field matches the expected role, not just that
*some* 200/`{"status":"ok"}` response arrived; or (b) honestly narrow the
docstring/comments to describe what this check actually proves (that
*some* HTTP service is still listening and responding after the rejected
write — a real and useful liveness proof) and stop describing it as
role-discriminating, moving the actual "correct role is running" proof to
where it's genuinely enforced instead (Docker's own native per-service
`HEALTHCHECK.Test`, which `security_check.py`'s `check_image_healthcheck`
and `check_compose.py` already verify statically per service and which
this review independently confirmed reaches `healthy` for all three real
Compose services).

---

## 5. `scripts/build/image_audit.py` quality audit

19 `results.append(...)` calls in `main()`, confirmed by direct count —
matches the script's own claimed "19/19".

| Check | Real or tautological? |
|---|---|
| `check_image_user`, `check_image_healthcheck`, `check_image_labels`, `check_entrypoint_and_default_cmd`, `check_oci_source_truthful` | Real — each compares against a concrete expected value (`10001:10001`, the exact healthcheck test array, `VERSION`, the exact git remote URL) |
| `check_packages_present` | Real — asserts six specific file paths exist |
| `check_data_directory`, `check_data_writable_by_runtime_uid` | Real — genuine stat + genuine write attempt |
| `check_no_secret_or_key_shaped_files`, `check_no_setuid_setgid`, `check_no_world_writable_source` | Real, narrowly scoped as documented (filename-pattern / mode-bit checks, not content scanning — honestly disclosed in the docstrings) |
| `check_no_shell`, `check_no_package_manager`, `check_no_pip_or_setuptools`, `check_expected_python_executable` | Real and genuinely falsifiable (exec-based, asserts the OCI runtime's own "no such file" error) — but narrow: fixed lists of absolute paths (four each for shell/apt/pip), not a full filesystem walk. This review's own export-listing pass in §1 is a broader net than what `make image-audit` runs, and did not find anything these checks missed, but the coverage gap is real (a shell/package-manager binary at an unlisted path would not be caught by `make image-audit` alone) |
| **`check_final_base_is_approved_distroless`** | **Tautological — confirmed.** `grep -n EXPECTED_FINAL_BASE_DIGEST scripts/build/image_audit.py` shows the constant defined once (line 64) and referenced once more only inside this function's own docstring text (a comment, not a comparison). The function body only asserts `docker image inspect ... RootFS.Layers` succeeds and is non-empty — true for *any* successfully built image, regardless of base. This is the same M-1 the sibling `day-04-reproducibility-review.md` already identified in the build-reproducibility context; independently re-confirmed here because it's directly this review's own scope ("verify Distroless-specific checks are meaningful and not tautological"). The real, effective enforcement of this property lives in `scripts/lint/check_dockerfile.py`'s `check_from()` (confirmed correct via its own test suite), not here. |

**M-1 (Medium, carried forward): `check_final_base_is_approved_distroless`
does not check the digest it names.** See table above for full detail;
same root cause and same recommended fix as the sibling reproducibility
review's M-1 (rewrite the docstring to describe what it actually proves,
or implement a real independent base-identity cross-check).

**M-2 (Medium): `image_audit.py`'s own immutability probe only covers
`app/`, not `gateway/`/`state/`.** `check_source_not_writable_by_runtime_uid`
(`scripts/build/image_audit.py:244-285`) probes exactly two paths:
`/app/app/server.py` (append) and `/app/newfile` (create). It never
probes anything under `/app/gateway/` or `/app/state/`, despite the
image shipping all three role's source in every build and the review
brief's explicit expectation that "relevant app/gateway/state source
remains root-owned" be proven. This review independently proved (§3a)
that `gateway/server.py` and `state/server.py` genuinely are root-owned
and write-rejected — so the *property* holds — but `make image-audit`
itself would not catch a regression that left `gateway/` or `state/`
source group/world-writable while `app/` stayed correctly locked down;
only this review's own broader probe would. Recommended fix: extend
`check_source_not_writable_by_runtime_uid` to probe one representative
file under each of `app/`, `gateway/`, and `state/`.

**L-2 (Low, informational): `FORBIDDEN_REPO_FILES`
(`scripts/verify/security_check.py:216`, reused by `image_audit.py`)
omits `Makefile` and a few other real repo-root files.** The set is
`{".git", ".claude", ".github", "tests", "docs", "README.md", "scripts",
"compose.yaml", ".dockerignore"}` — compared against the actual repo root
(`ls -la`), it does not include `Makefile`, `security/`, `artifacts/`, or
`.gitignore`. None of these currently leak (the builder stage's explicit
`COPY app/ ./app/` / `COPY gateway/ ./gateway/` / `COPY state/ ./state/`
/ `COPY VERSION ./VERSION` already structurally excludes everything not
named, and `.dockerignore` is a second layer of defense — confirmed in
§6), so this is not masking a live leak. But the set's own name and
purpose ("repository-only files absent from image") implies
comprehensiveness it doesn't currently have; a future Dockerfile change
introducing a broader `COPY . .` would not be caught by this check for
`Makefile`/`security/`/`artifacts/` specifically. Recommended fix: add
the missing repo-root entries to the set.

**L-3 (Low, informational): the build-context nested-leakage probe found
two empty directory entries leak into the image, though zero file
content.** A second, independently-constructed adversarial build (nested
`__pycache__` content injected several directories deep under `app/`,
e.g. `app/deep/nested/__pycache__/...`) found that while the injected
`.pyc`/`__pycache__` content itself was correctly excluded, the
now-otherwise-empty ancestor directories that existed only to contain it
(`app/app/deep/`, `app/app/deep/nested/`, 0 bytes each) were still
present in the built image's export listing. `.dockerignore`'s
`**/__pycache__/` and `**/*.pyc` patterns correctly exclude the bytecode
itself but have no pattern matching the now-empty parent directories.
Zero security/content impact (no bytes, no code, no metadata beyond a
directory inode) and does not affect this review's PASS verdict on
build-context leakage (§6) — flagged only because the review brief asked
for a genuinely adversarial nested-content probe, and this is the one
imperfection it actually turned up. Recommended fix (cosmetic):
`.dockerignore` could add an explicit `**/deep/` pattern if this specific
probe path is ever formalized as a repeatable test fixture, though this
is not a real repository directory today and the finding is purely a
byproduct of this review's own synthetic test content.

No new test-coverage finding is filed here for `image_audit.py`'s absent
unit tests — the sibling reproducibility review already filed this as its
own M-2 (`docs/engineering-reviews/day-04-reproducibility-review.md`),
and it directly explains why both M-1 above and this review's M-2 went
undetected until independent review: a test mirroring
`test_check_dockerfile.py::test_wrong_final_digest_is_rejected` would have
caught the base-digest tautology, and a parametrized-over-role test would
have caught the `gateway`/`state` source-probe gap.

**Image-audit quality verdict: mostly real and falsifiable; two Medium
gaps (M-1 tautological base check, M-2 single-file source-probe scope)
and two Low/informational gaps (L-2 incomplete forbidden-file set, L-3
empty-directory build-context residue) — none masking an actual live
defect (this review independently verified all underlying properties
hold today), but the Medium pair reduces `make image-audit`'s
regression-catching power below what its own docstrings claim.**

---

## 6. Build context — nested-leakage probe

`docker/app/Dockerfile`'s builder stage does `COPY app/ ./app/`,
`COPY gateway/ ./gateway/`, `COPY state/ ./state/`, `COPY VERSION
./VERSION` only — no blanket `COPY . .` — so `docs/`, `tests/`,
`.claude/`, and `artifacts/` are structurally excluded regardless of
`.dockerignore`. This review tested the meaningful nested-leakage
surface directly: real `__pycache__`/`.pyc` content injected *inside* the
three COPYed trees (where `.dockerignore` is the only defense), plus
top-level `docs/`, `tests/`, `.claude/`, `artifacts/` content (to confirm
the structural-exclusion claim rather than assume it).

Injected: `app/__pycache__/nested/deep/fake.cpython-313.pyc` (plus real
`.pyc` files already present from local interpreter runs —
`app/__pycache__/*.cpython-314.pyc` — left in place as an incidental,
realistic bonus case), `gateway/__pycache__/fake.pyc`,
`state/some_module.pyc` (top-level, no `__pycache__` wrapper),
`docs/nested/leak-marker-docs.txt`, `tests/nested/leak-marker-tests.txt`,
`.claude/nested/leak-marker-claude.txt`,
`artifacts/nested/leak-marker-artifacts.txt`.

Built a **separately-tagged** throwaway image
(`maops-review-buildctx-probe:latest` — never the real
`maops-docker-platform:0.4.0` release tag) with `docker buildx build
--no-cache` against this polluted tree, using the real
`docker/app/Dockerfile`. A full recursive `Path('/app').rglob('*')` walk
inside a running container of that probe image, searching for every
injected marker name plus any `__pycache__` directory or `.pyc`/`.pyo`
file anywhere under `/app`:

```
found: NONE
present under /app: NONE   (checked .git, .claude, .github, tests, docs, artifacts, security, .cache)
```

Cleanup: probe container removed, `maops-review-buildctx-probe:latest`
image removed, all seven injected files/directories deleted, and
`git status --short` diffed byte-for-byte against the pre-injection
baseline — **exact match**, confirming no leftover pollution.

**Build-context verdict: PASS.** Neither the explicit `COPY` scoping nor
`.dockerignore` allowed any nested generated/repository content to enter
a real build of this Dockerfile, at any depth tested.

---

## 7. PID 1 / SIGTERM — all three roles

The project's own `security_check.py::check_lifecycle_docker_stop` only
ever tests this for `app` (the one role `make security-check` runs). This
review independently extended the same real `docker stop` proof to
`gateway` and `state`:

| Role | PID 1 cmdline | PID 1 comm | PIDs in `/proc` | `docker stop` elapsed | ExitCode | Status |
|---|---|---|---|---|---|---|
| `app` | `/usr/bin/python3.13 -m app` | `python3.13` | `[1, <exec-probe>]` | 0.77s | 0 | exited |
| `gateway` | `/usr/bin/python3.13 -m gateway` | `python3.13` | `[1, <exec-probe>]` | 0.78s | 0 | exited |
| `state` | `/usr/bin/python3.13 -m state` | `python3.13` | `[1, <exec-probe>]` | 0.90s | 0 | exited |

All three: PID 1 is the Python interpreter directly (`cmdline` confirms
the exact exec-form entrypoint, no shell/`tini`/`dumb-init`/`docker-init`
wrapper anywhere — `/proc` never shows more than PID 1 plus the transient
`docker exec` probe's own PID), SIGTERM is genuinely handled (well under
the 10s grace period, not a SIGKILL-after-timeout fallback), and exit is
clean (`ExitCode=0`).

**PID 1 verdict: PASS (all three roles). SIGTERM verdict: PASS (all three
roles, independently proven — not merely inferred from `app`'s automated
coverage).**

---

## Summary verdicts

- **Distroless verdict**: PASS
- **Shell-absence verdict**: PASS (export-listing + exec-error evidence)
- **Package-manager verdict**: PASS for executable tooling; L-1
  informational (residual upstream `dpkg` metadata directory, no tooling)
- **Pip/setuptools verdict**: PASS (including `ensurepip._bundled` absence)
- **UID/GID all roles**: PASS — 10001:10001 confirmed for `app`,
  `gateway`, `state` independently, both bare `docker run` and real
  Compose-managed containers
- **Source ownership**: PASS — root-owned, independently confirmed on
  `app/`, `gateway/`, **and** `state/` source (broader than
  `image_audit.py`'s own single-file probe — see M-2)
- **Source write rejection**: PASS — real rejected writes on all three
  roles' source, with no `--read-only` flag present (image-level
  property, not merely Compose's)
- **`/data` write proof**: PASS — 10001:10001-owned, real write/readback/
  remove succeeds; persistence independently reproven across real
  container recreation on a bare volume (not merely within one
  container's lifetime)
- **Rootfs write proof**: PASS — real rejected write to `/etc/...` under
  full Compose-equivalent hardening, all three roles
- **Role-aware healthcheck closure**: **FAILED under adversarial
  challenge — H-1.** The dispatch selects the correct module name per
  role but has zero actual discriminating power at runtime; a deliberate
  wrong-role call against a real container reports PASS
- **Capability state**: PASS — `CapEff=CapPrm=CapBnd=0` on all three roles
- **NoNewPrivs**: PASS — `1` on all three roles
- **PID 1**: PASS — bare Python interpreter, no wrapper, all three roles
- **SIGTERM**: PASS — clean, fast exit, all three roles (independently
  extended beyond the project's own `app`-only automated coverage)
- **Image-audit quality**: mostly real; M-1 (tautological base-digest
  check) and M-2 (single-file source-ownership probe, `app/` only) both
  reduce its claimed regression-catching power without currently masking
  a live defect
- **Build-context verdict**: PASS — nested `__pycache__`/`.pyc`/`docs/`/
  `tests/`/`.claude/`/`artifacts/` content cannot enter a real build,
  proven via a separately-tagged adversarial build, not assumed from
  `.dockerignore`'s text
- **Cleanup**: every container/image this review created used a unique,
  project-prefixed name and was removed on completion (`docker rm -f`/
  `docker rmi`); the build-context probe's injected repository files were
  fully deleted and `git status --short` confirmed byte-identical to the
  pre-injection baseline; the real `maops-docker-platform:0.4.0` release
  image was left in place per this project's own convention; no other
  Docker resource was touched; no `sudo`, no global prune

---

## Release blockers

**None that block the image's actual runtime security posture** — every
hardening property this review independently tested (non-root UID/GID,
dropped capabilities, `NoNewPrivs`, read-only rootfs, source immutability
across all three roles, no privileged/host-PID/host-network/Docker-socket,
clean SIGTERM, genuine Distroless/shellless base) held under direct,
adversarial, first-hand verification for `app`, `gateway`, and `state`
alike.

**H-1 should be treated as a real, if narrow, gap before this finding-closure
claim is repeated in release notes or future-day docs**: the Day 3→Day 4
role-aware healthcheck dispatch does not provide the regression protection
its own code comments and docstrings claim. It does not need to block
v0.4.0's actual runtime hardening — that is independently sound — but the
*claim* that Day 3 finding A-2 is closed is not currently true in the way
the project's own comments assert, and should either be fixed (make the
probe genuinely role-discriminating) or the claim narrowed to what the
check actually proves.

M-1 and M-2 (image-audit quality) are process/coverage gaps consistent
with the sibling reproducibility review's own M-1/M-2 findings on the same
script — not independently release-blocking, but worth closing together
with those. L-2 (incomplete `FORBIDDEN_REPO_FILES` set) and L-3
(empty-directory build-context residue) are informational only — neither
masks a live leak or defect.

---

## Final image-security verdict

**IMAGE-SECURITY PASS, WITH ONE HIGH FINDING (H-1) ON VERIFICATION
INTEGRITY.**

The release image (`maops-docker-platform:0.4.0`,
`sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba`)
is genuinely Distroless and shellless, genuinely immutable at the
application-source level for all three roles independent of Compose's own
`read_only: true`, and genuinely hardened at runtime (non-root,
zero-capability, no-new-privileges, read-only rootfs, no host-namespace or
Docker-socket exposure) for `app`, `gateway`, and `state` alike, with
clean, bounded, shell-free SIGTERM handling on all three. Every one of
these properties was independently re-derived by this review using
first-hand `docker export`/`docker run`/`docker exec`/`docker inspect`
evidence, not accepted from the project's own script output.

The one substantive finding this review surfaced under deliberate
adversarial pressure — H-1 — is that the specific mechanism built to close
a named Day 3 finding (role-aware healthcheck dispatch) does not actually
close it: it selects the right module name per role but cannot detect a
role/container mismatch, because all three healthcheck probes are
behaviorally identical. This is a verification-honesty gap, not a live
exploitable weakness in the shipped container — but it is exactly the kind
of gap this review was asked to find, and it should be fixed or its claim
narrowed before it is cited again as closed.
