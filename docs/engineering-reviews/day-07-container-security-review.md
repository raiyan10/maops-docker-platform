# Day 7 / v1.0.0 — Container Security and Runtime Patch Lifecycle Review

**Reviewer**: `container-security-reviewer` (independent pass)
**Scope**: current uncommitted working tree on
`feature/day-7-final-hardening-production-readiness`. This review does not
rely on, and did not read, any other Day 7 review document. All claims
below were independently re-derived from source, real `docker`
inspection, and real command execution against a container built from
this working tree during this session (not merely re-stated from
`docs/production-readiness.md`, which was read only as a pointer to what
to go verify).

**Method**: read `docker/app/Dockerfile`, `compose.yaml`,
`scripts/security/{debian_version,base_image_ref,patch_lifecycle_check,
runtime_patch_lock,check_trivy_report}.py`, `scripts/build/image_audit.py`
(and its diff), `security/runtime-patches.lock`, `security/scanners.lock`,
`tests/test_{debian_version,patch_lifecycle_check,image_audit}.py`, both
GitHub Actions workflow files, and relevant docs. Then: ran the full new
unit-test suites, built `maops-docker-platform:1.0.0` from this working
tree via `make build`, ran `make image-audit` and
`scripts/security/patch_lifecycle_check.py` for real against the built
image, started throwaway hardened and unhardened containers of my own
(unique `maops-day7-*`/`maops-day7-hardened-*` names) to independently
re-derive `[D]` kernel-state and `[D]` write-rejection/write-success
evidence rather than trusting prior narrative, and inspected the real
`artifacts/security/trivy-1.0.0.json` report directly. Every throwaway
container created during this review was removed in this session
(`maops-day7-secreview-*`, `maops-day7-hardened-*`); `patch_lifecycle_check.py`
and `image-audit` clean up their own `maops-patch-lifecycle-*`/
`maops-image-audit-*` containers via their existing `try`/`finally`
logic, independently confirmed empty afterward (`docker ps -a`). No
implementation file was modified, and nothing was committed, pushed, or
tagged.

---

## 1. Core security model

All of the following were re-derived from a real container this session
started from the working tree's own build (`maops-docker-platform:1.0.0`,
built via `make build` from the current Dockerfile), not from a prior
day's already-published image and not merely re-asserted from docs.

| Property | Evidence tier | Result |
|---|---|---|
| Builder base pinned by digest | [A] | `python:3.13-slim@sha256:ffb752e1...` — digest-pinned, used identically for `builder` and `security-patch` stages |
| Distroless final base pinned by digest | [A]/[B] | `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c...` — digest-pinned; `make image-audit`'s real independent pull + `RootFS.Layers` prefix-match check (Day 7 rewrite, see §4) confirms the built image genuinely derives from that exact base |
| Final runtime UID:GID | [B]+[D] | `Config.User` = `10001:10001`; real `docker exec ... os.getuid()/getgid()` on a running (unhardened) container = `10001`/`10001` |
| Shell absent | [D] | `docker exec <container> /bin/sh -c "echo x"` → `exec: "/bin/sh": stat /bin/sh: no such file or directory` (reproduced live this session, both on an unhardened `docker run` container and via `make image-audit`'s own probe) |
| Package manager absent | [D] | `/usr/bin/dpkg`, `/usr/bin/apt-get` both fail "no such file or directory" on real `docker exec` |
| pip/setuptools absent | [D] | `import pip` / `import setuptools` both raise `ModuleNotFoundError` on real `docker exec` |
| Read-only root filesystem | [C]+[D] | `docker inspect` (started with `--read-only --cap-drop=ALL --security-opt no-new-privileges:true`, replicating `compose.yaml`'s three security fields) → `ReadonlyRootfs=true`; real `open('/etc/probe','w')` → `OSError: [Errno 30] Read-only file system` |
| `cap_drop: ALL` | [C]+[D] | `HostConfig.CapDrop=[ALL]`; real `/proc/1/status` read from inside the container → `CapInh/CapPrm/CapEff/CapBnd/CapAmb` all `0000000000000000` |
| `no-new-privileges` | [C]+[D] | `HostConfig.SecurityOpt=[no-new-privileges:true]`; real `/proc/1/status` → `NoNewPrivs: 1` |
| Non-privileged / no host namespaces | [C] | `Privileged=false`, `PidMode=` (empty, not `host`), `NetworkMode=bridge` (not `host`) |
| No Docker socket mount | [C] | `docker inspect --format '{{json .Mounts}}'` on the running container → `[]` (compose test scenario has zero bind mounts; only `state`'s named `state_data` volume and the `configs:` mount exist project-wide, neither is a docker.sock) |
| Writable `/data` is deliberate and constrained | [B]+[D] | `/data` owned `10001:10001` at image level (`--chown` explicit only on that one `COPY --from=builder`); real write to `/data/probe` on an *unhardened* container succeeds, while the same container's writes to `/app/app/server.py` and `/app/newfile` both raise `PermissionError` — proving the `/data` exception is real and narrow, not a general relaxation |
| Application source not runtime-writable, independent of `read_only:` | [B]+[D] | Confirmed on a container started with **no** hardening flags at all (`docker run -d --name <probe> "$IMAGE"`, no `--read-only`, no `--cap-drop`, no `--security-opt`): `open('/app/app/server.py','a')` and `open('/app/newfile','w')` both raise `PermissionError` — a property independent of and in addition to `compose.yaml`'s `read_only: true` |
| Platform config read-only | [A] | `compose.yaml`'s `configs:` mount for `/etc/maops/platform.json` in `app`/`gateway`/`state` is unchanged this session (only `VERSION`-default bumps in the diff); Day 3's already-established `[C]`/`[D]` proof pattern (`Mounts[].RW==false` + real rejected write) is unaffected by any Day 7 change |
| Scanner isolation | [A] | `security/scanners.lock` pins both Syft and Trivy by exact `tag@sha256:<64 hex>` digest; `generate_sbom.py`/`vuln_scan.py` contain zero references to `docker.sock`/`docker run` invocations that would mount it (grep-confirmed) |
| SBOM/vuln-report traceability | [B] | `artifacts/security/trivy-1.0.0.json`'s `Metadata.RepoTags` includes `maops-docker-platform:1.0.0` exactly, and `check_trivy_report.py`'s own `validate_report()` cross-checks this when given an expected-image argument |

No regression found in any of the above versus the established Day 1–6
baseline; `compose.yaml`'s only diff this session is the `VERSION`
default bump `0.6.0` → `1.0.0` in three places — no hardening field
(`read_only`, `cap_drop`, `security_opt`, resource limits, restart
policy) was touched.

---

## 2. Vulnerability policy

`scripts/security/check_trivy_report.py`'s policy function
(`evaluate_policy`) is unchanged this session and still enforces exactly:

- any `CRITICAL` → fail (`PolicyResult.passed` requires `not self.critical`)
- any `HIGH` **with** `FixedVersion` set → fail (`fixable_high`)
- `HIGH` **without** a fix → collected into `unfixed_high`, printed,
  never blocking
- everything else (`MEDIUM`/`LOW`/`UNKNOWN`) → counted and printed, never
  blocking

No `.trivyignore` file exists anywhere in the repository (confirmed via
`find`), no CVE allowlist/exception mechanism exists in the script, and no
severity is silently rewritten.

Ran `scripts/security/check_trivy_report.py artifacts/security/trivy-1.0.0.json`
directly against the real, already-generated v1.0.0 report this session:

```
vulnerability policy: CRITICAL=0 (any -> FAIL)
vulnerability policy: HIGH-with-fix=0 (any -> FAIL)
vulnerability policy: HIGH-without-fix=17 (reported, non-blocking)
  ... 17 CVEs listed (libexpat1, libncursesw6, libpython3.13-*, libsqlite3-0, libtinfo6, python3.13-*) ...
vulnerability policy: other severities (reported, non-blocking): {'MEDIUM': 62, 'LOW': 51, 'UNKNOWN': 9}
check_trivy_report: PASS
```

This matches the claimed v1.0.0 evidence exactly: **Critical = 0, fixable
High = 0, 17 unfixed High genuinely visible and reported (not hidden, not
treated as remediated)**. Independently confirmed no `libssl`/`openssl`
finding of any severity appears in the report at all — direct evidence
that the Day 6 `security-patch` overlay actually removed the CVE-2026-14456
finding from the scanned artifact, not merely that the Dockerfile claims
to apply a patch.

---

## 3. Patch lifecycle — primary Day 7 review

Deep review of `scripts/security/debian_version.py`,
`base_image_ref.py`, `patch_lifecycle_check.py`, `runtime_patch_lock.py`,
`security/runtime-patches.lock`, the Dockerfile integration, and both new
test files, against the ten specific verification points requested:

1. **Base identity from real Dockerfile evidence, not a duplicated
   constant** — CONFIRMED. `base_image_ref.get_final_stage_base_ref()`
   parses `docker/app/Dockerfile`'s own text using the exact same
   `parse_instructions` function `scripts/lint/check_dockerfile.py`
   itself uses, and takes the **last** `FROM` line's `repo@digest`. There
   is a second, legitimate "is this digest actually the one we approved"
   constant (`check_dockerfile.py`'s own `EXPECTED_FINAL_DIGEST`/
   `EXPECTED_FINAL_REPO`), but that answers a genuinely different
   question and is not what `patch_lifecycle_check.py`/`image_audit.py`
   consume — they consume `base_image_ref.py`'s live-parsed value. Not
   tautological.

2. **Actual pinned base inspected independently** — CONFIRMED. Re-ran
   `scripts/security/patch_lifecycle_check.py` live this session; it
   performed a real `docker pull` of
   `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c...`, a
   real `docker create` + `docker cp` of
   `/var/lib/dpkg/status.d/libssl3t64` out of that pulled (not the
   already-built release) image, and reported the real observed
   `Version: 3.5.6-1~deb13u2`.

3. **No shell inside Distroless is assumed** — CONFIRMED. Evidence
   extraction uses `docker create` (never `docker run`/`exec` into the
   base) + `docker cp`, exactly the no-shell-compatible pattern
   `image_audit.py` already established; the module docstring explicitly
   notes this.

4. **Real `libssl3t64` package metadata extracted from the base** —
   CONFIRMED, both `Package:` and `Version:` lines are regex-extracted
   from the real `dpkg` status file bytes copied out of the pulled base,
   not from any project-side constant.

5. **Debian version comparison correctness** — CONFIRMED sufficient for
   this domain. `debian_version.py` implements Debian Policy §5.6.12's
   epoch/upstream/revision algorithm faithfully (`_order`, `_verrevcmp`,
   tilde-sorts-before-everything, digit-run numeric comparison ignoring
   leading zeros), matching `dpkg --compare-versions` semantics. 15 unit
   tests independently re-run this session (all pass), including the
   Debian Policy Manual's own canonical tilde-ordering example and this
   project's exact real vulnerable/patched pair. The module deliberately
   raises `DebianVersionError` on malformed input rather than guessing —
   correctly treated by the caller as classification **C**, never as
   "assume older."

6. **Four lifecycle states** — CONFIRMED, all four are implemented and
   independently distinguishable in `classify_patch_lifecycle`:
   - **A-REQUIRED**: `base < patched` AND `base == recorded vulnerable
     version` → PASS.
   - **B-REDUNDANT**: `base >= patched` → explicit FAIL (never silent
     pass), and this branch is checked **before** D, so a base that has
     caught up is always flagged as redundant even if the recorded
     vulnerable-version metadata is *also* stale (verified by
     `test_case_b_redundancy_takes_precedence_over_metadata_drift`) —
     correctly the more urgent of the two facts (a stale-but-still-older
     base is merely inaccurate bookkeeping; a caught-up base is an active
     downgrade risk).
   - **C-INDETERMINATE**: `base_version is None` (extraction failed),
     unexpected package name, or an unparseable version string → FAIL.
   - **D-METADATA-DRIFT**: still older than patched, but
     `base_version != vulnerable_version_recorded` → FAIL, prompting a
     lock update.
   29 unit tests across `test_debian_version.py` (15) and
   `test_patch_lifecycle_check.py` (14) re-run this session, all pass,
   including an explicit non-tautology proof
   (`test_check_is_not_tautological_across_all_four_branches`) that the
   same fixed lock-derived constants produce all four different real
   outcomes purely as a function of the independently observed base
   version.

7. **Metadata disagreement cannot silently pass** — CONFIRMED (see D
   above; `main()`'s `if not passed: return 1` unconditionally fails the
   process on any of B/C/D).

8. **Immutable Debian-security URL/checksum guarantees preserved** —
   CONFIRMED. The Dockerfile's `security-patch` stage uses
   `ADD --checksum=sha256:916f7f40...` against a `snapshot.debian.org`
   fixed-timestamp URL (`.../20260825T185058Z/...`), which is an
   immutable archive path, not a moving "current" mirror; BuildKit itself
   refuses the build if the downloaded bytes' SHA256 doesn't match. The
   checksum and URL in the Dockerfile match `security/runtime-patches.lock`'s
   `LIBSSL_DEB_SHA256`/`LIBSSL_URL` exactly (byte-for-byte compared by
   inspection this session). `runtime_patch_lock.py`'s own parser
   additionally rejects any non-well-formed 64-hex-char SHA256 or
   non-`https://` URL at load time.

9. **Patch payload and dpkg metadata remain genuine, not spoofed** —
   CONFIRMED via real evidence gathered this session, not merely trusted:
   ran `make image-audit` against the actual `maops-docker-platform:1.0.0`
   image built from this working tree; it independently re-computed the
   content hash of `libssl.so.3`/`libcrypto.so.3` **inside the built
   image** and compared against `security/runtime-patches.lock`'s
   `LIBSSL_SO_SHA256`/`LIBCRYPTO_SO_SHA256` — **PASS** ("real fixed
   libssl3t64 binary payload is present (content-hash match)").

10. **Final runtime proves actual OpenSSL 3.5.7 functionality** —
    CONFIRMED, and genuinely functional rather than a string read: the
    same `image-audit` run executed Python's real `ssl` module *inside
    the built image* and confirmed
    `ssl.create_default_context()` succeeds and
    `ssl.OPENSSL_VERSION == 'OpenSSL 3.5.7 9 Jun 2026'` — this only
    succeeds if the process actually dynamically loaded the patched
    shared library at runtime, not merely if `dpkg` metadata claims a
    version.

**Conclusion**: the Day 7 patch-lifecycle validator genuinely closes the
carried-forward Day 6 Medium ("no automated tripwire for the overlay's
own exit condition"). It is wired into `make patch-lifecycle-check`,
which is itself part of `make release-check`'s dependency chain — the
single authoritative gate both locally and in CI
(`.github/workflows/ci.yml`'s `release-policy` job, triggered on every
`pull_request`, every `push` to `main`, and `workflow_dispatch`). Re-ran
it live this session against the real pinned base:
`patch_lifecycle_check: PASS (A-REQUIRED)`.

---

## 4. Base provenance (`image_audit.py` Day 7 change)

Reviewed the diff to `scripts/build/image_audit.py`'s
`check_final_base_is_approved_distroless`. The prior (Day 4) version only
asserted `docker image inspect` on the **built release image itself**
returned a non-empty `RootFS.Layers` — a check that could never fail even
if the wrong base had been used, since it never compared anything against
independent evidence. This was correctly flagged as partially
tautological in this project's own historical debt ledger.

The Day 7 rewrite:

- Derives `(base_repo, base_digest)` from the real Dockerfile text via
  `base_image_ref.py` (never a second hand-copied constant — same
  non-tautology argument as §3.1).
- Independently `docker pull`s that **exact** digest (a fresh pull, not
  reuse of any layer cache from building the release image).
- Compares that pulled base image's own `RootFS.Layers` against the
  **built release image's** `RootFS.Layers`, asserting the base's layer
  list is nonempty and is a genuine ordered **prefix** of the built
  image's layer list.
- Fails clearly (never silently passes) on: pull failure, either
  `docker image inspect` failing, non-JSON output, an empty base layer
  list, a base layer list longer than the image's, or any layer-content
  divergence at any position within the prefix.

Re-ran this live this session (`make image-audit` against
`maops-docker-platform:1.0.0`, built from the current working tree):

```
[AUDIT:image-policy] PASS built image RootFS genuinely begins with the
pinned base gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c...'s
exact layer set: base_layer_count=48 image_layer_count=61 prefix_match=True
```

`tests/test_image_audit.py` (9 tests, all independently re-run this
session, all pass) exercises this decision logic adversarially without a
live daemon: exact prefix match passes; a diverged layer at any position
fails; a shorter image-than-base layer list fails; an empty base layer
list fails; pull failure, either inspect failure, and non-JSON output all
fail clearly. This is genuine coverage of the discriminating case the
prior version could never have caught (a "different, superficially
similar base" test explicitly exercises this).

**Conclusion**: this is a real, materially improved, non-tautological
check. It closes the historically carried Day 4 Medium.

---

## 5. Findings

No Critical, High, or Medium findings were identified. Everything
reviewed either already had genuine, independently-reproducible evidence
behind it, or is explicitly out of this agent's scope (Day 5
reliability/resource-limit values, Day 6 CI trust-boundary mechanics
which were spot-checked and found unchanged/correct, and
`scripts/release/prepare_release_bundle.py`, which is
`release-engineer`'s domain — reviewed only far enough to confirm it
introduces no docker.sock mount, no shell invocation, and no
path-traversal risk in its own basename validation, which it does not).

### DAY7-SEC-L1
**Severity**: Low
**Title**: New Day 7 patch-lifecycle documentation section omits the
project's own `[A]`/`[B]`/`[C]`/`[D]` evidence-tier labels
**Evidence**: `docs/build-security.md`'s new "Day 7: runtime
security-patch lifecycle tripwire" section describes exactly what
`patch_lifecycle_check.py` proves (Dockerfile-derived identity, a real
`docker pull`+`docker cp` of the base's dpkg metadata, a genuine Debian
version comparison) but never tags any sentence with `[A]`/`[B]`/`[C]`/`[D]`
the way the analogous Day 6 section in `docs/security.md` ("Day 6
addition: emergency Debian-security overlay evidence chain") explicitly
does, and the way `patch_lifecycle_check.py`'s own docstrings do
(`extract_base_package_metadata`'s docstring literally says "Real
[B]/[D]-tier evidence gathering"). No claim in the new prose is
inaccurate or overstated — this is a labeling-consistency gap, not a
mislabeled or overclaimed proof.
**Impact**: A future reader of `docs/build-security.md` alone (without
also reading `docs/security.md`'s established convention) has no
in-context signal for which parts of the Day 7 tripwire are `[A]` (parsed
from the Dockerfile) versus `[B]`/`[D]` (real image/kernel evidence) —
purely a documentation-discoverability issue, not a technical gap.
**Required remediation**: add `[A]`/`[B]`/`[D]` tags to the relevant
sentences in `docs/build-security.md`'s new section, consistent with
`docs/security.md`'s existing Day 6 convention.
**Release-blocking**: NO

### DAY7-SEC-I1
**Severity**: Informational
**Title**: Core security model unchanged and re-verified against v1.0.0
**Evidence**: See §1. Every `[A]`/`[B]`/`[C]`/`[D]` property this agent
owns (non-root `10001:10001`, `cap_drop: ALL` at the kernel level,
`no-new-privileges` at the kernel level, real rootfs-write rejection, the
narrow `/data` exception, image-level source immutability independent of
`read_only:`, shell/package-manager/pip absence) was independently
re-derived this session against a container built from the current
working tree, not merely re-asserted from a prior day's already-built
image or from documentation.
**Impact**: None — positive confirmation.
**Required remediation**: None.
**Release-blocking**: NO

### DAY7-SEC-I2
**Severity**: Informational
**Title**: Vulnerability policy contract unweakened; v1.0.0 evidence
matches the claimed numbers exactly
**Evidence**: See §2. `evaluate_policy()`'s Critical/fixable-High/
unfixed-High/lower-severity semantics are byte-for-byte unchanged from
prior days; no `.trivyignore` exists; direct execution against the real
`artifacts/security/trivy-1.0.0.json` reproduces Critical=0, fixable
High=0, 17 unfixed High (all genuinely visible in the printed output, not
merely counted), and independently confirms zero libssl/openssl findings
remain (the Day 6 overlay's real effect on the scanned artifact).
**Impact**: None — positive confirmation.
**Required remediation**: None.
**Release-blocking**: NO

### DAY7-SEC-I3
**Severity**: Informational
**Title**: CI/CD trust boundary (Day 6) reconfirmed unchanged and correct
**Evidence**: `.github/workflows/ci.yml` uses `pull_request`/`push`/
`workflow_dispatch` only (never `pull_request_target`), has exactly one
`permissions:` block (`contents: read`) with no job-level override.
`.github/workflows/release.yml`'s sole `contents: write` scope is on the
`publish` job, gated by
`if: success() && github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')`
— unreachable from `workflow_dispatch`. Every `uses:` line in both files
is pinned to a 40-hex-character commit SHA (verified by direct grep, not
by trusting `check_workflows.py`). The only credential referenced
anywhere is `secrets.GITHUB_TOKEN`, scoped to the `publish` job; no step
echoes a secret or enables step-debug logging.
**Impact**: None — positive confirmation. (Out of this agent's primary
Day 7 mandate, but explicitly requested by the reviewer's own persona
description; spot-checked for completeness since `.github/workflows/
release.yml` is in this session's diff.)
**Required remediation**: None.
**Release-blocking**: NO

---

## 6. Finding counts

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 1 |
| Informational | 3 |

---

## 7. Explicit answers

**Is the temporary libssl3t64 overlay now governed by a credible
automatic EXIT condition?**

Yes. `scripts/security/patch_lifecycle_check.py` independently and
automatically re-derives the pinned base's real, currently-shipped
`libssl3t64` version on every run (never trusting a cached/prior
observation), classifies it against the lock file's own recorded
rationale using genuine Debian version-comparison semantics, and
**fails the process** (non-zero exit) the moment the base is no longer
older than the overlay's patched version (classification B) — or the
moment the lock's own documented rationale drifts from reality
(classification D) — rather than silently continuing to trust stale
metadata. This is wired into `make patch-lifecycle-check`, itself part of
`make release-check`'s dependency chain, which is the single
authoritative gate run identically by a local developer and by
`.github/workflows/ci.yml`'s `release-policy` job on every pull request,
every push to `main`, and every `workflow_dispatch` run. The tripwire is
therefore automatic-**detection** (a human still performs the actual
Dockerfile/lock-file edit to remove the overlay), not automatic
**removal** — which is the correct, conservative design for a build-time
security exception: it can never silently persist past its own
documented justification, and it can never silently miss the base
catching up or overtaking the overlay.

**Is v1.0.0 acceptable from the container-security perspective?**

Yes. Every property this agent independently owns — non-root execution,
capability dropping, `no-new-privileges`, read-only rootfs with a single
deliberate and narrowly-proven `/data` exception, image-level source
immutability, shell/package-manager/pip absence, scanner isolation, and
the vulnerability policy's Critical/fixable-High gate — was re-verified
this session with real `[B]`/`[C]`/`[D]` evidence against a container
built from the current working tree, not merely re-read from
documentation, and nothing regressed relative to the established Day 1–6
baseline. The Day 7-specific work (the patch-lifecycle tripwire and the
base-provenance rewrite) is genuine, non-tautological, adversarially
tested, and independently reproduced live in this session with the same
result the project's own tooling reports (`A-REQUIRED`/`PASS`,
`prefix_match=True`). The one finding raised (DAY7-SEC-L1) is a
documentation-labeling consistency nit with no effect on any actual
security property or proof and does not block release.

---

## Final verdict

**APPROVE**

DAY 7 CONTAINER SECURITY REVIEW COMPLETE
