# Supply Chain: SBOM and Vulnerability Scanning (Day 4)

## Scanner pinning

Both scanners are pinned by exact, immutable digest in
`security/scanners.lock` (`tag@sha256:<64 hex>`, never a bare tag, never
`latest`) - a small, public, version-controlled file, parsed and validated
by `scripts/security/scanner_lock.py` (`scripts/security/*.py` reads from
it; nothing hand-copies the digest string a second time).

Resolved for real against the live Docker Hub registry on 2026-08-20:

| Scanner | Pinned reference |
|---|---|
| Syft (SBOM) | `anchore/syft:v1.51.0@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0` |
| Trivy (vulnerabilities) | `aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969` |

Both were confirmed by independently pulling both the explicit version
tag and `:latest` on the same day and comparing `RepoDigests` - they
resolved to the identical digest, i.e. these genuinely are each tool's
current stable release, not an arbitrarily chosen older pin. Updating a
pin is an explicit, reviewed repository change to `security/scanners.lock`
- never an automatic "latest available" resolution at `make` time, and
never silently re-resolved on every invocation.

## Docker-socket isolation (no scanner ever touches the live daemon)

Neither `scripts/security/generate_sbom.py` nor
`scripts/security/vuln_scan.py` mounts `/var/run/docker.sock` (or any
Docker daemon socket) into a scanner container - both scan an **exported
image archive** instead:

```
docker save maops-docker-platform:<VERSION> -o <archive>.tar
docker run --rm ... -v <archive>.tar:/input.tar:ro ... <pinned scanner> ... docker-archive:/input.tar ...
```

This is validated two ways: `tests/test_generate_sbom.py` and
`tests/test_vuln_scan.py` mock `subprocess.run` and assert the
constructed `docker run` argv for each scanner never contains
`docker.sock` anywhere; and both scripts' actual source only ever mount
the read-only archive, a scratch/cache directory, and the output
directory - grep `scripts/security/*.py` directly to confirm.

Syft additionally runs with `--network none` (it needs no network for
package cataloging; the only thing it would otherwise reach for is its
own update-check ping, disabled via `SYFT_CHECK_FOR_APP_UPDATE=false`).
Trivy is **not** run with `--network none` - see "Vulnerability database:
not offline, and not claimed to be" below.

Both scanners run as the invoking host UID/GID (`--user`), with
`TMPDIR`/cache/`HOME` redirected into a scratch directory this project
mounts explicitly - both official scanner images have no writable
`/tmp`/`/.cache` for a non-root user otherwise (empirically confirmed:
without this, Syft failed outright with `mkdir /tmp/stereoscope-...:
permission denied`), and without it the generated artifact would be
written **root-owned** on the host, which `.claude/CLAUDE.md` requires
this project's own generated files never to be.

## Runtime decision: Distroless (see docs/build-security.md for the full rationale)

The Day 4 plan originally targeted `python:3.13-slim`. A real scan of
that candidate found 4 CRITICAL findings (all `perl-base`, none fixable)
and 38 fixable HIGH findings. The 38 fixable HIGH findings could have
been resolved by an in-Dockerfile remediation, but the 4 unfixed
CRITICAL findings could not - no newer `python:3.13-slim` digest existed
to refresh to, and this project's policy treats any CRITICAL as an
unconditional blocker. `python:3.13-slim` was rejected as the release
runtime, and `gcr.io/distroless/python3-debian13:nonroot` was adopted in
its place - same Python 3.13/Debian 13 family, no shell, no package
manager, no `perl-base`. This section documents the *current* scan
result for the real, adopted Distroless-based release image, not the
rejected slim candidate.

## SBOM generation (Syft, SPDX JSON)

`make sbom` (`scripts/security/generate_sbom.py`):

1. `docker save`s the exact `maops-docker-platform:<VERSION>` release
   image to a project-owned temporary archive.
2. Runs the pinned Syft container against that archive
   (`syft scan docker-archive:/input.tar -o spdx-json=...`).
3. Writes `artifacts/sbom/maops-docker-platform-<VERSION>.spdx.json`.
4. Cleans up the temporary archive/scratch directory; the SBOM itself is
   the documented, retained output of this target (git-ignored - see
   `.gitignore`).

`make sbom-check` (`scripts/security/check_sbom.py`) validates the
generated SBOM: valid JSON, a real SPDX document marker (`spdxVersion`
starts with `SPDX-`), a non-empty package inventory, plausible identity
traceability, a recorded Syft tool reference in `creationInfo.creators`,
and no obvious local-workstation-path or secret-shaped string leakage in
the raw document text.

**Day 4 (Distroless) identity check**: the release image ships no
`dpkg`/`apt` executable, but Distroless images still carry the dpkg
*status database* (`/var/lib/dpkg/status.d/`), which Syft's dpkg
cataloger reads directly. `check_sbom.py` therefore accepts any
package name *containing* "python" (case-insensitive) - e.g.
`python3.13-minimal`, `libpython3.13-stdlib` - rather than requiring the
exact literal `python` name Debian's own `python:*-slim` images happened
to also carry. This project's release image currently produces **38
packages** in the SBOM inventory (base-files, libc6, libssl3t64,
python3.13-minimal, libpython3.13-stdlib, tzdata, ... - a full dpkg-
status-derived inventory of the Distroless runtime's actual OS-level
content). This count is a scan-time observation, not a pinned pass
criterion - `check_sbom.py` asserts non-emptiness and identity
traceability, never an exact package count.

**Be accurate about what the SBOM's `versionInfo` digest proves.** Syft's
SPDX output records a `checksums`/`versionInfo` SHA-256 for the scanned
`docker-archive:` source, computed from the archive's own config blob.
This does **not** always equal `docker image inspect`'s `.Id` on this
project's Docker Desktop/containerd-image-store setup - a real,
independently observed discrepancy (this project's own Day 3 release
review already documented an analogous four-way size/digest mismatch
across different `docker` CLI surfaces for the same image). `check_sbom.py`
therefore does not assert digest equality as its identity proof; it uses
the weaker, honestly-stated "python package present + filename encodes
VERSION" signal instead, and says so in its own docstring.

## Vulnerability scanning (Trivy, JSON)

`make vuln-scan` (`scripts/security/vuln_scan.py`):

1. `docker save`s the exact release image to a temporary archive (same
   pattern as SBOM generation).
2. Runs the pinned Trivy container against that archive
   (`trivy image --input /input.tar --format json --output ...`).
3. Writes `artifacts/security/trivy-<VERSION>.json`.
4. Validates the report (`scripts/security/check_trivy_report.py`'s
   `validate_report()` - valid JSON, `SchemaVersion` present, `Results`
   present, and `Metadata.RepoTags` includes the exact expected image tag)
   and enforces the vulnerability policy (`evaluate_policy()`) against it,
   in the same run.

### Vulnerability database: not offline, and not claimed to be

Unlike Syft, **Trivy needs real network access** to fetch/refresh its
vulnerability database (a real, multi-hundred-megabyte download on first
use). This project does not claim vulnerability scanning is offline or
fully deterministic - only the *image* is deterministic (see
`docs/build-security.md`); the *scan results* are a function of the
vulnerability database snapshot at scan time. A later scan of the exact
same immutable image (byte-identical, per `make reproducibility-check`)
may legitimately report different - typically more - CVEs as new ones
are discovered and published upstream. This is expected, is not evidence
the image changed, and is stated here explicitly rather than left
implicit.

## Vulnerability policy

Enforced by `scripts/security/check_trivy_report.py`'s `evaluate_policy()`
(covered by `tests/test_check_trivy_report.py`'s synthetic-fixture tests,
proving actual discriminating power for each branch - a real CVE in the
application image is never required merely to test policy logic):

- **Any CRITICAL finding -> FAIL.** No carve-out for "no fix available" -
  a CRITICAL finding blocks the gate unconditionally.
- **Any HIGH finding WITH a `FixedVersion` available -> FAIL.** A fix
  exists and was not applied.
- **HIGH findings with no fix available -> reported prominently, but
  non-blocking.** Nothing this project's maintainers control can resolve
  them yet.
- **Lower severities (MEDIUM/LOW/UNKNOWN) -> reported, not a release
  blocker.**

This project **never** silently ignores a finding, **never** auto-
generates a `.trivyignore`, and **never** manufactures an exception. If a
release-blocking finding is genuinely unavoidable (no fix exists, and
the base-image digest is already current - see the base-refresh check in
`docs/build-security.md`), the correct response is to report it plainly
and stop, per `.claude/CLAUDE.md`'s explicit instruction for exactly this
situation - not to weaken the policy.

### Historical result: python:3.13-slim candidate (scanned 2026-08-20, rejected)

Before the Distroless migration, `make vuln-scan` **genuinely failed**
against the then-planned `python:3.13-slim` runtime:

- **4 CRITICAL** findings, all in `perl-base` (a base-OS package from
  `python:3.13-slim`, not this project's own code), **none with a fixed
  version available** from Debian.
- **38 HIGH** findings **with a fixed version available**.
- 13 further HIGH findings with no fix available.
- 66 LOW / 65 MEDIUM / 3 UNKNOWN.

The base-image digest was already current (independently re-resolved
against the live registry the same day) - there was no newer
`python:3.13-slim` digest to refresh to that would resolve the 4
CRITICAL findings, and an in-Dockerfile `apt-get upgrade` step to chase
the 38 fixable HIGH findings was deliberately not pursued (out of this
day's authorized scope, and doing so unreviewed would be exactly the
kind of silent policy-weakening this document commits not to do). This
result is preserved here as the historical record of *why* the runtime
decision changed - see `docs/build-security.md`.

### Current result: gcr.io/distroless/python3-debian13:nonroot (scanned 2026-08-20)

`make vuln-scan` **passes** against the adopted Distroless-based release
image:

- **0 CRITICAL** findings.
- **0 HIGH findings with a fixed version available.**
- **15 HIGH findings with no fixed version available** (reported,
  non-blocking under the stated policy) - across
  `libncursesw6`/`libtinfo6` (CVE-2025-69720), `libpython3.13-minimal`/
  `libpython3.13-stdlib`/`python3.13-minimal`/`python3.13-venv`
  (CVE-2026-11940, CVE-2026-15308, CVE-2026-7210), and `libssl3t64`
  (CVE-2026-14456).
- 44 MEDIUM / 51 LOW / 9 UNKNOWN (reported, non-blocking).

**These are scan-time values, not timeless promises.** Trivy's
vulnerability database changes over time; a later scan of this exact,
byte-identical image (per `make reproducibility-check`) may legitimately
report different - typically more - findings as new CVEs are published,
including against the same `libpython3.13-*`/`python3.13-*` packages
already carrying unfixed HIGH findings today. No `.trivyignore` exists,
no exception was manufactured, and no finding above was suppressed -
`make vuln-scan` passes purely because the current, real scan result
against the current, real image satisfies the unweakened policy
(Critical=0, fixable High=0). If a future scan surfaces a new CRITICAL or
fixable HIGH finding, the correct response remains: report it plainly and
stop, per `.claude/CLAUDE.md` - never silence it.

### Day 6 emergency remediation: CVE-2026-14456 (libssl3t64)

By 2026-08-26, Debian Security had published a fix for `libssl3t64`
(`3.5.7-1~deb13u2`), and Trivy correctly reclassified CVE-2026-14456 from
"no fix available" to "fixed" - which, unweakened, is exactly the
release-blocking case this policy has always enforced (**any HIGH finding
WITH a `FixedVersion` available -> FAIL**):

```
CRITICAL=0
HIGH-with-fix=1   (CVE-2026-14456 libssl3t64 3.5.6-1~deb13u2 -> 3.5.7-1~deb13u2)
HIGH-without-fix=16
```

The upstream `gcr.io/distroless/python3-debian13:nonroot` image at this
project's pinned digest had not yet incorporated the Debian Security fix,
with no ETA, and this project's policy forbids both waiting indefinitely
and weakening the policy itself (no `.trivyignore`, no CVE allowlist - see
"Vulnerability policy" above). The remediation actually applied was a
narrow, checksum-pinned Debian-security package overlay on top of the
still-pinned Distroless base - see `docs/build-security.md`'s "Day 6:
emergency Debian-security overlay" section and `security/runtime-patches.lock`
for the full package provenance, verification, and Dockerfile design.

After the overlay, `make vuln-scan` against the same release image (now
containing the real fixed `libssl3t64` payload, not merely updated
metadata) reports:

```
CRITICAL=0
HIGH-with-fix=0
HIGH-without-fix=16
```

`scripts/security/check_trivy_report.py` itself was **not modified** -
the policy is identical before and after; only the image's actual,
verifiable content changed. This is documented here as a supply-chain
event distinct from `check_trivy_report.py`'s own logic: the policy did
its job correctly (it caught a real, newly-fixable vulnerability), and
the response was to fix the image, not to soften the check.

## Generated artifacts

`artifacts/sbom/` and `artifacts/security/` (both git-ignored - see
`.gitignore`) hold the documented, retained outputs of `make sbom` and
`make vuln-scan` respectively. Intermediate `docker save` archives and
scanner scratch/cache directories are always cleaned up in the same run
that created them (`tempfile.TemporaryDirectory()`-scoped in both
scripts) - never left behind, and never written anywhere but a disposable
temp location plus the one documented artifact file.

## Day 6: CI-enforced, not merely deferred

CI-enforced supply-chain gating was Day 4 future scope; Day 6
(`docs/ci-cd.md`) closed it - `.github/workflows/ci.yml`'s `release-policy`
job and `.github/workflows/release.yml`'s `validate` job both run `make
release-check` (which includes `supply-chain-check`: `sbom` -> `sbom-check`
-> `vuln-scan`) on GitHub-hosted runners, uploading the generated SBOM and
Trivy report as CI/release evidence. The policy itself (Critical=0,
fixable-High=0) is unchanged and unweakened by running in CI - see
`docs/ci-cd.md` for the workflow design.

## Deferred to a later day

A container registry, cryptographic attestation of the SBOM/vulnerability
report themselves, and automatic scanner-pin updates remain explicitly out
of scope - see `docs/roadmap.md`.
