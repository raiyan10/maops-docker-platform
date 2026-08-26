# Day 6 Workflow / Supply-Chain Security Review — v0.6.0

Repository: `maops-docker-platform`
Branch: `feature/day-6-cicd-release-engineering`, PR #6
Target: `v0.6.0`
Reviewer: `container-security-reviewer` (review 2 of 5). Read-only
inspection only — no implementation file, workflow, test, or doc was
modified; nothing was committed, pushed, merged, tagged, or dispatched.
The only file created by this review is this one.
Date: 2026-08-26.

## Scope and method

This review is narrowly scoped to the **workflow/supply-chain** trust
boundary of Day 6 — `.github/workflows/ci.yml`, `.github/workflows/
release.yml`, action-pin integrity, the emergency `libssl3t64`
(CVE-2026-14456) Debian-security overlay and its verification chain, and
scanner isolation — not general runtime hardening (already covered by
prior Day 1–5 security reviews and unchanged this day) and not release
publication logic itself (`release-engineer`'s domain). Continuity was
established by reading `docs/engineering-reviews/day-05-release-
readiness.md` and `docs/engineering-reviews/day-06-bootstrap-readiness.md`
first; neither was modified.

Files read: `.github/workflows/ci.yml`, `.github/workflows/release.yml`,
`docker/app/Dockerfile`, `security/runtime-patches.lock`, `security/
scanners.lock`, `scripts/security/runtime_patch_lock.py`, `scripts/
security/check_sbom.py`, `scripts/security/check_trivy_report.py`,
`scripts/security/generate_sbom.py`, `scripts/security/vuln_scan.py`,
`scripts/build/image_audit.py`, `scripts/ci/check_workflows.py`, `scripts/
lint/check_dockerfile.py` (patch-stage cross-check sections), `docs/
security.md`, `docs/supply-chain.md`, `docs/build-security.md`, `docs/
ci-cd.md`, `tests/test_runtime_patch_lock.py`, `tests/test_check_sbom.py`.

Commands run: `python3 scripts/ci/check_workflows.py` (local, PASS,
12/12); `gh pr checks 6`; `gh run view` on all three cited run IDs
(`32938805880`, `32960673438`, `32967457379`); `gh api repos/actions/
{checkout,setup-python,upload-artifact,download-artifact}/git/refs/tags/
<tag>` to independently re-resolve all four pinned action SHAs against
the live GitHub API; text searches for `secrets.`, `continue-on-error`,
`|| true`, `pull_request_target`, `docker login`/`push`/registry hostnames
across both workflow files; direct inspection of `scripts/security/
vuln_scan.py`'s/`generate_sbom.py`'s constructed `docker run`/`docker
save` argv.

## 1. CI/CD trust-boundary findings (Day 6)

**Info — `pull_request_target` correctly absent.** Neither workflow file
uses `pull_request_target` anywhere (`.github/workflows/ci.yml:18`, only
`pull_request`/`push`/`workflow_dispatch` triggers declared). Confirmed by
direct text search, not by trusting the header comment at `ci.yml:9-15`
that explains the choice. A PR from a fork runs the PR branch's own
workflow file with the base repository's default read-only token and zero
secrets — the correct, safer shape.

**Info — `permissions: contents: read` is the sole scope in `ci.yml`.**
`.github/workflows/ci.yml:31-32` declares `contents: read` once, at
workflow level, with no job-level override on either `quality` or
`release-policy`. No `write`/`admin` scope exists anywhere in this file —
confirmed by pattern search and by `check_workflows.py`'s
`check_ci_permissions()` (part of the 12/12 PASS reproduced above).

**Info — `release.yml`'s `contents: write` is correctly confined to
`publish`, unreachable from `workflow_dispatch`.** Workflow-level
`permissions: contents: read` (`release.yml:32-33`); `validate` job
re-states `contents: read` explicitly (`release.yml:39-40`); `publish`
alone carries `permissions: contents: write` (`release.yml:149-150`), and
`publish`'s `if:` (`release.yml:144-148`) requires `success() &&
github.event_name == 'push' && startsWith(github.ref, 'refs/tags/')` —
`workflow_dispatch` never sets `event_name` to `'push'`, so this is a
platform-level structural guarantee, not merely a convention. The
`GH_TOKEN`/`TAG` env vars (`release.yml:151-153`) exist only inside the
`publish` job's own scope, never visible to `validate`. This exact shape
is independently, statically enforced by `check_workflows.py`'s
`check_release_permissions_scoped()` and
`check_manual_dispatch_cannot_publish()` (confirmed present in the 12/12
local PASS). The day-06-bootstrap-readiness.md review already traced this
same chain in more granular detail (its §6); this review's independent
re-read reaches the identical conclusion and finds nothing to add or
subtract from it.

**Info — no secret/token exposure beyond the one expected credential.**
`grep -n "secrets\."` across both files returns exactly one hit:
`release.yml:152` (`GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`), scoped inside
`publish`'s job-level `contents: write` permission. No
`ACTIONS_STEP_DEBUG`/`ACTIONS_RUNNER_DEBUG` step-debug enablement, no
`docker login`, no registry hostname (`ghcr.io`, `docker.io/`,
`public.ecr.aws`, `azurecr.io`) anywhere in either file — matching Day 6's
documented scope decision (`docs/ci-cd.md`, "Why registry publication is
out of scope") that this project's only delivery destination is a GitHub
Release, never a container registry.

**Info — no `continue-on-error`/`|| true` disguising a gate.** Neither
pattern appears in either file (`grep` returned no hits). Every `make
release-check`/`make quality` step's exit code is the job's real exit
code.

## 2. Action supply-chain pinning findings (Day 6)

**Info — all five distinct `uses:` references, across both files, are
pinned to a genuine full 40-character commit SHA**, independently
re-verified against the live GitHub API in this session (not merely
copied from the prior bootstrap-readiness review):

| Action | Pinned tag (comment) | Committed SHA | Live API result |
|---|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` | match |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` | match |
| `actions/upload-artifact` | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | match |
| `actions/download-artifact` | v8.0.1 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` | match |

(`ci.yml:40,45,68,73,134`; `release.yml:43,51,128,156,159`.) All four are
official `actions/*`-namespace, GitHub-maintained actions — no
third-party action was introduced anywhere in either file, so there is no
extra supply-chain trust surface to justify. `check_workflows.py`'s
`check_uses_pinned_to_full_sha()` statically enforces the 40-hex-char
shape as a repository policy, and this review's own independent live-API
re-resolution is a stronger, non-static confirmation that the pinned
value is genuinely the commit those tags point to today (not merely
"looks like 40 hex characters").

## 3. Supply-chain scanner isolation findings (Day 4 baseline, re-verified this day)

**Info — Syft/Trivy are never given the Docker socket.** Direct
inspection of the constructed argv in `scripts/security/vuln_scan.py:85-97`
shows `docker run --rm -v <archive>:/input.tar:ro -v <cache>:/cache -v
<output>:/output --user <uid>:<gid> <trivy_image> image --input
/input.tar ...` — three bind mounts (a read-only exported tar archive, a
scratch cache dir, an output dir), no `/var/run/docker.sock` anywhere, and
`generate_sbom.py` follows the identical `docker save`-then-scan pattern
(`generate_sbom.py:62-68,123-124`). Both scan a `docker save` archive of
the exact release image, never the live daemon.

**Info — both scanner images are pinned by exact digest.**
`security/scanners.lock` pins `SYFT_IMAGE=anchore/syft:v1.51.0@sha256:
678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0` and
`TRIVY_IMAGE=aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595
256c7c2f6018a9d9ad61caf87187c1969` — both `tag@sha256:<64 hex>` form,
neither `latest` nor a bare tag. Both hex digests independently confirmed
64 characters in this session.

## 4. Vulnerability policy findings

**Info — the vulnerability policy is unweakened by the CVE-2026-14456
remediation.** `scripts/security/check_trivy_report.py:96-107`'s
`evaluate_policy()` is unchanged: any CRITICAL fails, any HIGH with a
`FixedVersion` fails, HIGH-without-fix and lower severities are
non-blocking-but-reported. No `.trivyignore` file exists in the
repository, no CVE-ID allowlist/exception table exists in this script or
elsewhere, and `docs/ci-cd.md`'s "Real example: a blocking finding fixed,
not the gate weakened" section documents that the finding was fixed by
patching the image, not by suppressing the finding — consistent with what
the code itself shows.

## 5. Debian Security libssl overlay — provenance and integrity findings

**Info — exact provenance is fully pinned.** `security/runtime-patches.
lock` records `LIBSSL_PACKAGE=libssl3t64`, `LIBSSL_VULNERABLE_VERSION=
3.5.6-1~deb13u2`, `LIBSSL_VERSION=3.5.7-1~deb13u2`, `LIBSSL_SUITE=
trixie-security`, an immutable `snapshot.debian.org` URL fixed at
timestamp `20260825T185058Z` (not a moving mirror path), a 64-hex-char
`LIBSSL_DEB_SHA256`, and the exact `.deb` size in bytes. `docker/app/
Dockerfile`'s `security-patch` stage (`Dockerfile:63-97`) fetches this
exact URL via `ADD --checksum=sha256:916f7f40...2467d` — a BuildKit
frontend-level integrity gate that fails the build outright if the
downloaded bytes don't match, not a post-hoc/optional check.

**Info — this is real binary-payload integrity verification, not
spoofable metadata**, confirmed via three independent layers, each
targeting a different part of the claim:

1. `scripts/lint/check_dockerfile.py` (`[A]`) cross-checks the
   Dockerfile's own `ADD --checksum=` URL/SHA256 against
   `runtime-patches.lock`'s `LIBSSL_URL`/`LIBSSL_DEB_SHA256` and confirms
   `COPY --from=security-patch` actually copies the patched payload into
   the final stage (`check_dockerfile.py:349-454`) — a Dockerfile that
   downloaded the right bytes but forgot to copy them anywhere is still
   caught.
2. `scripts/build/image_audit.py:511-526` (`[B]`) reads the **built
   image's own** `/var/lib/dpkg/status.d/libssl3t64` and confirms the
   reported `Version:` matches `LIBSSL_VERSION`.
3. `scripts/build/image_audit.py:529-582` (`[D]`) computes the **live
   content SHA256** of `libssl.so.3`/`libcrypto.so.3` inside the running
   container and compares against `LIBSSL_SO_SHA256`/
   `LIBCRYPTO_SO_SHA256` (pinned in the lock file, themselves already
   verified against the official `.deb`), and separately execs Python's
   `ssl` module inside the container to confirm it loads, reports the
   patched OpenSSL version token, and successfully constructs an
   `SSLContext`.

Layer 2 alone would be spoofable (a status.d entry is just metadata that
could in principle be hand-written); layer 3's content-hash comparison is
the layer that actually proves the real fixed binary is present, and the
runtime `ssl.OPENSSL_VERSION` check proves the *loaded* library, not just
a file sitting unused on disk, reflects the patch. This is a materially
stronger proof than "the lock file claims a version" and is correctly
labeled `[B]`/`[D]` in `docs/security.md`'s own Day 6 section
(`docs/security.md:379-406`), not overstated as `[C]`.

**Info — build-time hard failure on corruption/tampering, verified in the
source itself.** `runtime_patch_lock.py`'s `parse_runtime_patch_lock()`
(`runtime_patch_lock.py:52-82`) raises `RuntimePatchLockError` on any
malformed line, duplicate key, missing required key, a `*_SHA256` value
that is not exactly 64 lowercase hex characters, or a non-`https://` URL —
this is exercised directly by `tests/test_runtime_patch_lock.py`'s
`test_short_sha256_is_rejected`/`test_non_hex_sha256_is_rejected`/
`test_non_https_url_is_rejected`, and the same file's
`test_real_repository_lock_file_parses_and_pins_libssl` asserts the *real*
committed `security/runtime-patches.lock` parses cleanly — not merely a
synthetic fixture. Separately, `ADD --checksum=` is a BuildKit-frontend-
enforced integrity gate independent of this Python parser: a corrupted or
substituted `.deb` at the pinned URL fails the build before any
`dpkg-deb` extraction happens.

**Medium — no automated drift/staleness/regression detection for the
overlay's own continued correctness once the upstream base changes.**
Every proof above verifies the overlay is *currently* internally
consistent (Dockerfile pin == lock file == built image), but nothing in
`runtime_patch_lock.py`, `check_dockerfile.py`, or `image_audit.py`
detects two related future failure modes: (a) if a later, well-justified
bump of the pinned Distroless base digest (per `docs/build-security.md`'s
own "Policy for future days" re-verification note) already ships
`libssl3t64` at `LIBSSL_VERSION` or newer, this overlay stage still runs
unconditionally and re-copies the same or a stale version over it with no
warning that the overlay has become redundant; (b) more seriously, if a
future base bump ships a libssl newer than `3.5.7-1~deb13u2` (fixing
additional CVEs beyond CVE-2026-14456), this overlay would silently
*downgrade* that file to the pinned `3.5.7-1~deb13u2` payload with no
build-time or audit-time check comparing the base image's own pre-overlay
libssl version against the lock file's pinned version. `image_audit.py`'s
`check_libssl_status_d_reports_fixed_version`/
`check_libssl_payload_hashes_match_lock` only assert "does the final image
contain exactly `LIBSSL_VERSION`" — they do not assert "is `LIBSSL_VERSION`
still >= whatever the base image would otherwise ship." This is a real,
if narrow, gap: the overlay's correctness today is proven exhaustively;
its correctness *after the next base-digest bump* depends entirely on a
human remembering to re-evaluate/remove it, with no automated tripwire.
Recommend adding a check (either in `check_dockerfile.py` or as a new
`image_audit.py` check, run against the `security-patch`/base stages
directly) that fails loudly if the overlay's pinned version is not
strictly newer than whatever the base image's own (unpatched) dpkg
status would report, so a future contributor bumping the base pin is
forced to consciously decide whether this stage is still needed rather
than silently carrying it forward or silently downgrading a newer base.

**Low — ongoing manual supply-chain burden, acknowledged but worth
flagging explicitly.** This overlay is a legitimate, well-executed
emergency mechanism, but it establishes a new, open-ended maintenance
obligation: every future Distroless base-digest re-verification (already
documented as a required manual step in `docs/build-security.md`) must
now also manually re-check whether each active entry in
`security/runtime-patches.lock` is still needed, still current, or has
itself been superseded by a newer Debian Security release. Nothing in
the repository's tooling currently tracks "how many active overlays exist
and when were they last re-justified" — for a single-package overlay this
is a small burden; if this mechanism is reused for future CVEs without an
explicit expiry/re-review discipline (e.g. a comment or CI check requiring
re-justification after N days, or removal once the CVE is independently
confirmed fixed upstream), it will accumulate silently. Not blocking for
v0.6.0 — recommend documenting an explicit review cadence (e.g. "re-check
every active `runtime-patches.lock` entry at every future base-digest
re-verification," which `docs/build-security.md`'s existing policy
section could absorb with one added sentence) rather than leaving the
obligation implicit.

## 6. SBOM/Trivy visibility findings

**Info — the SBOM is genuinely patch-version-aware, not merely trusted to
be.** `scripts/security/check_sbom.py:112-129` loads
`runtime_patch_lock.py`, locates the `libssl3t64` package entry(ies) in
the generated SPDX SBOM, and fails if `LIBSSL_VERSION` (`3.5.7-1~deb13u2`)
is not among the reported `versionInfo` values — this is enforced as a
hard failure ("a patched filesystem with stale SBOM metadata is treated
as a failure, not a soft warning" — correctly reflected in the code, not
just the docstring), and is directly exercised in `tests/
test_check_sbom.py` (confirmed present and testing both the passing and
version-mismatch paths).

**Info — Trivy visibility is real, not hoped-for**, per the evidence
chain in `docs/ci-cd.md`'s "Real example" section: `make release-check`'s
`supply-chain-check` stage genuinely failed locally pre-overlay
(`HIGH-with-fix=1`, CVE-2026-14456) and the same real Trivy scan against
the patched image is what the passing CI run (`32967457379`) and local
evidence rely on — this is a real scanner re-run against the patched
archive, not an assumption that patching the filesystem must have fixed
the finding. This review did not itself re-run `make vuln-scan` against a
freshly built image (out of scope for a read-only workflow review with a
2-minute default Bash timeout budget for Docker-heavy operations), so
this finding is based on the documented evidence chain and the passing CI
run's own artifact upload (`ci-release-evidence-<sha>` containing
`artifacts/security/`), not an independent re-scan performed in this
session — recorded honestly as a review-scope limitation rather than
overstated as directly re-verified.

## 7. Final runtime shell/package-manager absence after the overlay

**Info — the overlay does not reintroduce a shell or package manager into
the final stage.** `docker/app/Dockerfile`'s `security-patch` stage
(lines 63-97) is a separate, non-final build stage based on
`python:3.13-slim` (already used identically as the `builder` stage);
only seven explicit `COPY --from=security-patch` instructions
(`Dockerfile:169-176`) — the two shared libraries, the `engines-3/`
plugin directory, doc/lintian-override files, and the two dpkg status.d
metadata files — cross into the final Distroless stage. No `dpkg`, `apt`,
or shell binary is among them, and `image_audit.py`'s existing
`check_no_shell`/`check_no_package_manager` checks (unchanged this day,
run as part of `make image-audit`, part of `make release-check`) continue
to cover the final image post-overlay. `scripts/lint/check_dockerfile.py`
additionally enforces that the only permitted `ADD` with a remote URL
anywhere in the file is the one inside the `security-patch` stage
(`check_dockerfile.py:349-427`), preventing a future edit from smuggling
a second remote fetch into the final stage under cover of this
established pattern.

## 8. Historical CI evidence — reproduced, not merely trusted

`gh run view` was independently re-run against all three cited run IDs
in this session:

- `32938805880` — **FAILED**, `Release policy` job, `make release-check`
  exit code 2, `Docker exporter is not supported for the docker driver`.
  Matches the documented Buildx-driver-portability root cause exactly.
- `32960673438` — **FAILED**, `Release policy` job, `make release-check`
  exit code 2, after the Buildx fix succeeded — matches the documented
  Scenario 2 transient cgroup/runc race.
- `32967457379` — **PASSED**, both jobs green, `ci-release-evidence-
  b90732cbebdb1b80c7c50c8bd0300cb22e7f871f` artifact uploaded. This is
  the current HEAD's latest green evidence.

`python3 scripts/ci/check_workflows.py` was independently re-run against
the current checkout in this session and returned `OK (12/12 policy
checks passed)`, matching `docs/ci-cd.md`'s claimed count.

## 9. Evidence-labeling findings

No `[C]`-only claim in `docs/security.md`'s Day 6 section or
`docs/ci-cd.md` was found presented as `[D]` kernel-enforcement proof.
`docs/security.md:379-406` explicitly separates `[A]` (Dockerfile/lock
cross-check), `[B]` (built-image dpkg status.d), and `[D]` (live
content-hash + runtime `ssl` module check), and explicitly calls out that
a `[B]`-only claim ("status.d says 3.5.7") is "never presented as proof
the real binaries were replaced." This is the correct, non-overstated
labeling this project's evidence-tier discipline requires. The CI/CD
permission and pinning claims in `docs/ci-cd.md` are `[A]`/config-and-
static-analysis claims (what the committed YAML declares and what
`check_workflows.py` statically verifies against it) and are described as
such, not overstated as runtime kernel enforcement — appropriately, since
a GitHub Actions permission/token scope is not a kernel-level property in
this project's `[D]` sense to begin with; it is real `[C]`-grade
"platform enforces what was configured" evidence (GitHub's own token-
scoping guarantee), which this document does not mislabel as anything
stronger.

## Summary of findings by severity

- **Critical**: none.
- **High**: none.
- **Medium**: one — no automated drift/staleness/downgrade-protection
  check for the `security/runtime-patches.lock` overlay against a future
  base-image digest bump (§5).
- **Low**: one — the overlay mechanism creates an open-ended manual
  supply-chain-maintenance obligation with no tracked review cadence
  (§5).
- **Info**: all trust-boundary, pinning, scanner-isolation, vulnerability-
  policy, SBOM/Trivy-visibility, shell-absence, and evidence-labeling
  checks passed with no gap found (§1–§4, §6–§9).

## Recommended remediation order

1. (Medium, pre-tag not blocking, but recommended before this mechanism
   is reused for a second CVE) Add a build-time or `image_audit.py` check
   that fails/warns if a future base-digest bump would make the
   `runtime-patches.lock` overlay redundant or would cause it to
   downgrade a newer base-shipped package version — the fix can be as
   small as comparing the base image's own pre-overlay dpkg version
   against `LIBSSL_VERSION` and asserting the overlay version is strictly
   newer.
2. (Low, documentation-only) Add one sentence to `docs/build-security.md`'s
   existing base-pin refresh policy requiring every active
   `runtime-patches.lock` entry to be explicitly re-justified (kept,
   updated, or removed) at every future base-digest re-verification,
   closing the open-ended-burden gap before a second overlay entry is
   ever added.
3. (Non-blocking, informational) When the real `workflow_dispatch`
   release dry-run is executed post-merge (the documented, expected
   sequencing gap — not penalized here), independently re-confirm that
   `validate`'s `make release-check` run against `main` reproduces the
   same `check_workflows.py` 12/12 and vulnerability-policy PASS this
   review observed against the feature branch, since a dry run is the
   first time this exact workflow file executes with `main`'s own commit
   history as `origin/main` reference.

None of the above blocks merge from this reviewer's lens: the
trust-boundary architecture (`pull_request` only, `contents: read`
default, `contents: write` confined and structurally unreachable from
`workflow_dispatch`), the action-pin integrity (independently
re-verified against the live GitHub API), the scanner isolation (no
Docker socket, exact-digest pins), the vulnerability-policy integrity (no
`.trivyignore`, no weakening), and the libssl overlay's binary-level
integrity verification are all real, correctly evidenced, and correctly
labeled.

## Verdict

**APPROVE WITH CONDITIONS** (from the container-security-reviewer's
workflow/supply-chain lens only — this does not adjudicate reliability,
compose-platform, test, or release-publication concerns owned by sibling
reviewers).

**Pre-tag conditions** (do not block merge of PR #6; do block treating
`v0.6.0` as final release-ready without addressing or explicitly
accepting):

1. Execute the mandatory post-merge, pre-tag `workflow_dispatch` dry run
   on `main` (expected, not yet possible pre-merge — not a defect of this
   PR) and confirm it reproduces this review's `check_workflows.py`
   12/12 and vulnerability-policy PASS results against `main`'s own
   history.
2. Explicitly accept or address the Medium finding (§5) — a future
   base-digest bump silently downgrading or redundantly overlaying
   `libssl3t64` — before this overlay mechanism is reused for a second
   CVE remediation.
