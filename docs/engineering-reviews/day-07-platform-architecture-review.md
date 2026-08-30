# Day 7 Platform Architecture Review — v1.0.0 Working Tree

**Reviewer role**: MAOps Docker Architect (independent review)
**Scope**: the current UNCOMMITTED working tree on
`feature/day-7-final-hardening-production-readiness`, verified directly
against `git status`/`git diff`, real file contents, a real `docker
build`, real invocations of `scripts/build/image_audit.py` and
`scripts/security/patch_lifecycle_check.py` against the freshly built
image, `docker compose config`, `scripts/lint/check_dockerfile.py`,
`scripts/compose/check_compose.py`, `scripts/ci/check_workflows.py`, and
a full local `python3 -m unittest discover` run (677 tests, all passing).
This review does not read or incorporate any prior Day 7 review document
(none exists yet in `docs/engineering-reviews/`) and does not rely on any
other agent's summary.

## 1. Base image strategy / three-stage build

The Dockerfile (`docker/app/Dockerfile`) is unchanged by this Day 7
working tree (no diff at all — `git diff docker/app/Dockerfile` is
empty). It still has exactly three `FROM` lines:

- `python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder`
- `python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS security-patch`
- `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`

All three are digest-pinned, never `:latest`. Independently re-verified
this session (not trusted from the Dockerfile's own comments):

- `docker pull python:3.13-slim@sha256:ffb752e...c6e30a` succeeds — the
  pinned builder/security-patch digest is still resolvable.
- `docker pull gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c...884bea`
  succeeds — the pinned final-base digest is still resolvable.
- `docker buildx imagetools inspect gcr.io/distroless/python3-debian13:nonroot`
  (the live, moving tag) today resolves to index digest
  `sha256:f3d5ddc6...` / linux/amd64 manifest `sha256:2da46b94...`, and a
  fresh `docker pull python:3.13-slim` today resolves to
  `sha256:7ce4b6df...` — both **different** from the pinned digests. This
  is expected, not a defect: digest-pinning is specifically designed to
  survive a moving tag advancing over time, and both pinned digests
  remain valid, pullable, and unchanged (see DAY7-ARCH-I1 below for the
  one thing worth noting about this drift).
- A real, non-cached-assumption `docker build -f docker/app/Dockerfile
  --build-arg VERSION=1.0.0 -t maops-docker-platform:1.0.0 .` succeeds
  end to end, and a subsequent real `scripts/build/image_audit.py` run
  against that exact built image passes 22/22 checks, including the new
  base-layer-prefix cross-check (`base_layer_count=48
  image_layer_count=61 prefix_match=True`) and the security-patch
  content-hash/OpenSSL-runtime-load checks.
- No `RUN` instruction exists in the final (Distroless) stage — confirmed
  by direct inspection of the Dockerfile text; `RUN` only appears in the
  `builder` and `security-patch` stages, both of which have real
  coreutils/dpkg tooling and never enter the final image's own layers
  beyond the explicit `COPY --from=` lines.
- The `security-patch` stage's `ADD --checksum=` URL/SHA256
  (`916f7f40b34a06e6ebfaefcdab331bff458328411da672598f126a760472467d`,
  the `snapshot.debian.org` archive URL) matches
  `security/runtime-patches.lock`'s `LIBSSL_URL`/`LIBSSL_DEB_SHA256`
  exactly, byte for byte. `check_dockerfile.py` (`make dockerfile-check`)
  passes 12/12 checks against the current Dockerfile.
- Day 7's new `scripts/security/patch_lifecycle_check.py`
  (`make patch-lifecycle-check`) was run for real against the actual
  pinned base this session: it independently `docker pull`s the pinned
  final-base digest, `docker create`s a throwaway container, `docker cp`s
  the real `/var/lib/dpkg/status.d/libssl3t64` metadata, and correctly
  classifies the overlay **A-REQUIRED / PASS** (`Version: 3.5.6-1~deb13u2`
  matches the lock's recorded `LIBSSL_VULNERABLE_VERSION` exactly, still
  older than the overlay's `3.5.7-1~deb13u2`). This is genuine [B]-tier
  evidence, not a duplicated constant — `base_image_ref.py` derives the
  (repo, digest) pair straight from the Dockerfile's own `FROM` text via
  the same parser `check_dockerfile.py` uses, so the check cannot be
  tautological by construction.

No findings in this section. The base-image strategy, digest pinning,
security-patch overlay, and the absence of `RUN` in the final stage are
all exactly as documented, and are backed this session by real Docker
evidence rather than by trusting the Dockerfile's own comments.

## 2. Build context and layering

`.dockerignore` (unchanged by this Day 7 diff) still uses genuinely
recursive patterns (`**/__pycache__/`, `**/__pycache__/**`, `**/*.pyc`,
`**/*.pyo`, `**/*.pyd`, plus recursive `.git/**`, `.github/**`,
`.claude/**`, `tests/**`, `docs/**`, `artifacts/**`, `security/**`), with
an explicit header comment calling out why a one-level glob is
insufficient. Layer ordering in the Dockerfile is also unchanged: the
builder stage copies `app/`/`gateway/`/`state/`/`VERSION` and prepares
`/data` before the final stage's `COPY --from=` steps; the security-patch
overlay files are copied after the application source (a reasonable,
low-churn-last ordering, though with `--no-cache` builds as the
project's own release-build policy, layer-cache reuse is not the primary
optimization target here anyway).

No findings in this section — nothing in Day 7's uncommitted changes
touches `.dockerignore` or the Dockerfile's `COPY`/layer order at all.

## 3. PID 1 / process design

Unchanged by this Day 7 working tree. `ENTRYPOINT ["/usr/bin/python3.13"]`
(exec form, absolute interpreter path, no shell) with per-role `command:`
overrides (`-m app` / `-m gateway` / `-m state`) in `compose.yaml`
(verified via `docker compose config` — each of the three services'
rendered `command:` is exactly `["-m", "<role>"]`, and `HEALTHCHECK`/
`healthcheck:` both invoke the role-specific `<role>.healthcheck` module
with the same absolute interpreter path). Day 7 does not touch
`app/server.py`, `gateway/server.py`, `state/server.py`, or any signal
handling — no diff exists against any of the three roles' server
modules, so the previously-reviewed SIGTERM/SIGINT design (a real
non-deadlocking handler, `HTTPServer.shutdown()` called from a thread
other than `serve_forever()`'s own) is carried forward unchanged, and
remains what actually makes `compose.yaml`'s `stop_grace_period: 10s`
(also unchanged, confirmed present and identical across all three
services in the rendered `docker compose config` output) meaningful
rather than a silent reliance on the Docker daemon's own
SIGKILL-after-grace-period fallback.

No findings in this section.

## 4. OCI metadata

Unchanged Dockerfile `LABEL` block: `org.opencontainers.image.title`,
`.description`, `.version` (derived from the `VERSION` build-arg — this
session's real `image_audit.py` run confirms the built image's version
label equals `1.0.0`, matching the repository-root `VERSION` file, which
Day 7's diff bumps from `0.6.0` to `1.0.0`), `.licenses="MIT"`, and
`.source="https://github.com/raiyan10/maops-docker-platform"` are all
present. `git remote -v` confirms `origin` is genuinely
`git@github.com:raiyan10/maops-docker-platform.git`, and
`image_audit.py`'s `check_oci_source_truthful` (real [B]-tier check, run
this session) independently cross-checks the built image's label against
the real git remote and passes. The GitHub repository genuinely exists
by Day 7 (unlike the Day 1 state this agent's own standing brief
describes) — the `.source` label is accurate, not invented, and is
correctly still omitted-if-untrue logic (it isn't omitted here because it
doesn't need to be: the repo is real).

No findings in this section.

## 5. Docker-vs-Compose boundary

Confirmed via `git diff compose.yaml`: the **only** change in this Day 7
working tree is the `VERSION` default bump (`${VERSION:-0.6.0}` ->
`${VERSION:-1.0.0}`) applied identically to all three services' `build.args.VERSION`
and `image:` tag. No topology change, no new `ports:`, no new
`read_only`/`cap_drop`/`security_opt`/resource-limit/restart/
`stop_grace_period` field, no new network, no new volume, no new
`configs:` entry. `docker compose config` (rendered this session)
confirms: exactly 3 services (`state`, `app`, `gateway`); one image
(`maops-docker-platform:1.0.0`) used for all three roles; `gateway` is
the sole service with a `ports:` entry, bound `127.0.0.1:${GATEWAY_HOST_PORT:-8080}:8080`
(loopback-only); `edge` connects `gateway`+`app`; `backend` connects
`app`+`state` and is `internal: true`; `gateway` has no `backend`
network membership and `state` has no `edge` membership, so `gateway`
cannot reach `state`; `state_data` is `state`'s only volume; all three
services mount `config/platform.json` read-only via `configs:`; `cpus:
0.5`, `mem_limit: 128m` (rendered as `134217728` bytes), `pids_limit: 64`,
`restart: on-failure:3`, and `stop_grace_period: 10s` are present and
identical across all three services. `scripts/compose/check_compose.py`
independently confirms 17/17 structural checks pass against this exact
rendered config. **No topology drift was introduced by Day 7.**

The three Day 7 image-level/delivery-plane concerns this review examined
(`patch-lifecycle-check`, `release-bundle`, the `image_audit.py`
base-layer cross-check) are all correctly implemented as `scripts/`
tooling invoked through `Makefile` targets, never as new `compose.yaml`
fields or new Dockerfile instructions that would blur the
image-vs-runtime boundary. `release-bundle`'s output directory
(`release-bundle/`) is correctly `.gitignore`d (Day 7 adds exactly this
one line to `.gitignore`) and is not baked into the image.

No findings in this section.

## 6. Day 7 architectural additions — layering assessment

### 6.1 Runtime patch lifecycle validation
(`scripts/security/patch_lifecycle_check.py`, `scripts/security/base_image_ref.py`,
`scripts/security/debian_version.py`)

Cleanly layered: `base_image_ref.py` derives the pinned final-base
(repo, digest) from the Dockerfile's own text using the identical parser
`check_dockerfile.py` uses (no second hand-copied constant);
`debian_version.py` is a pure, Docker-free, independently-tested
Debian-Policy-§5.6.12 version comparator; `patch_lifecycle_check.py`
composes both plus real `docker pull`/`create`/`cp`/`rm` calls (always
via a unique `maops-patch-lifecycle-<uuid>` container name, cleaned up in
a `finally` block — consistent with this project's Docker-safety
convention) into a four-way classification (A-REQUIRED / B-REDUNDANT /
C-INDETERMINATE / D-METADATA-DRIFT) that fails loudly rather than
defaulting to "assume still required" on missing evidence. Verified this
session with a real invocation against the real pinned base: correctly
produced **A-REQUIRED / PASS**. `scripts/build/image_audit.py`'s own
base-layer-prefix check (§6.3 below) reuses the same `base_image_ref.py`
module rather than re-deriving the pin a third way — genuine code reuse,
not duplicated logic. This is squarely delivery/runtime-plane-appropriate
tooling: it never touches `docker/app/Dockerfile`, `compose.yaml`, or any
GitHub Actions workflow YAML directly; it is wired in only via
`Makefile`'s `patch-lifecycle-check` target, called from
`make release-check`.

### 6.2 Release bundle staging / consumer checksum validation
(`scripts/release/prepare_release_bundle.py`)

Also cleanly layered. It is a release-plane concern (post-build artifact
packaging), not a Dockerfile/Compose concern, and is correctly
implemented as a standalone script invoked by both `Makefile`'s
`release-bundle` target and `.github/workflows/release.yml`'s `publish`
job — the workflow YAML itself contains no bundling/checksum logic (the
prior `v0.6.0` design's `find ... | xargs sha256sum > SHA256SUMS` inline
shell has been fully removed from the workflow and replaced with a call
to this script). The script's own basename-validation
(`_validate_asset_basename`, rejecting any `/`, `.`, or `..` component)
and its independent re-parse-and-reject step
(`validate_manifest_entries_are_bare_basenames`) applied even to a
caller-supplied `SHA256SUMS` are genuine defense in depth against exactly
the `v0.6.0` regression shape this closes (a manifest referencing
`release-evidence/sbom/...`-style nested CI paths). The real proof step
(`verify_release_bundle`) shells out to the actual `sha256sum -c` binary
rather than reimplementing hash verification in Python — consistent with
this project's "real external tool, not a Python-side stand-in" proof
philosophy. Verified this session with a live run (synthetic SBOM/Trivy
placeholder files staged into a scratch directory): produced a real,
unmodified `sha256sum -c SHA256SUMS` PASS.

### 6.3 Improved final-base provenance / image audit
(`scripts/build/image_audit.py`'s `check_final_base_is_approved_distroless`)

This is a genuine, well-scoped improvement over the prior Day 4 check,
which asserted only that `docker image inspect`'s `RootFS.Layers` on the
built release image was non-empty — a check that could never fail even
if the base were wrong. The new version independently `docker pull`s the
exact pinned base (via the shared `base_image_ref.py`), inspects that
base's own `RootFS.Layers`, and asserts it is a genuine ordered prefix of
the built release image's own `RootFS.Layers`. Verified this session
against a real, freshly built `1.0.0` image: `base_layer_count=48
image_layer_count=61 prefix_match=True` — real, non-tautological
evidence. New unit coverage (`tests/test_image_audit.py`, 9 tests,
confirmed by direct execution) exercises the decision logic
Docker-free (matching prefix passes; diverged/shorter/empty base layer
lists all fail; pull/inspect/non-JSON failures all fail clearly) — this
function had zero unit tests before this session, confirmed by `git log`
history showing no prior `tests/test_image_audit.py` file.

### 6.4 Conservative post-restart cgroup retry classifier
(`scripts/reliability/reliability_check.py::_is_transient_cgroup_update_race`)

The diff replaces a three-substring `and` check with a structural
`openat2 <path>: no such file or directory` regex match plus explicit
path-context (`/cgroup/` segment) and an enumerated accepted-filename set
(`{cgroup.controllers, memory.max}`). This is a real hardening, not a
loosening: the new version is strictly more discriminating than the old
one (it now also rejects a `permission denied` variant and a
same-basename-but-wrong-path variant that the old substring check would
arguably have been more permissive about, since it never checked path
context at all). New tests (confirmed present and passing:
`test_real_github_run_33059581018_memory_max_error_is_classified_as_transient`,
`test_unrelated_cgroup_controller_filename_is_deliberately_not_transient`,
`test_memory_max_outside_a_real_cgroup_path_is_not_transient`,
`test_openat2_without_enoent_wording_is_not_transient`, plus a full
`update_container_resources_verified` retry-and-verify test for the new
`memory.max` variant) genuinely discriminate the accepted/rejected cases
rather than merely re-asserting the happy path. This remains
`reliability_check.py`'s own internal classifier logic — it does not leak
into `compose.yaml`'s `restart:`/`stop_grace_period:` fields, which
Day 7 leaves untouched (confirmed by the `compose.yaml` diff in §5).

### 6.5 Production-readiness documentation (`docs/production-readiness.md`)

Independently checked against the actual repository state rather than
accepted at face value, this document's claims held up under direct
verification in every case checked this session: the `make
release-check` composition order matches the actual `Makefile` diff; the
three closed-Medium narratives (§1.1/§1.2/§1.3) match the actual code
diffs and real tool output described above; the "Day 4 tautology" closure
narrative (§2) matches the actual `image_audit.py` diff and a real
passing run; the historical debt table's `CLOSED`/`ACCEPTED` dispositions
for Days 1–6 were spot-checked against `docs/engineering-reviews/day-06-post-release-verification.md`
§7.1/§7.2 (source of DAY6-POST-M1/M2) and matched exactly (same finding
titles, same root causes, same real GitHub run IDs). One factual
inaccuracy was found on close inspection — see DAY7-ARCH-L1 below — but
it is narrow (a test-count claim, not a substantive disposition claim)
and does not undermine the document's overall adjudication.

## 7. Delivery-plane / runtime-plane boundary

`.github/workflows/ci.yml` is untouched by this Day 7 diff (`git diff`
shows no changes to it). `.github/workflows/release.yml`'s only Day 7
change is replacing an inline `find ... | xargs sha256sum > SHA256SUMS`
shell fragment with a call to `scripts/release/prepare_release_bundle.py`
— this is a **reduction** in workflow-YAML-embedded logic, not an
addition, and is exactly the direction this project's "Makefile is
authoritative, CI orchestrates it" principle calls for. Neither workflow
file contains a hand-rolled `docker build`/`docker buildx build` command
anywhere — both `ci.yml`'s `release-policy` job and `release.yml`'s
`validate` job create a job-scoped Buildx builder and then simply invoke
`make release-check` (which itself invokes `make build`, which itself
runs the project's own `docker buildx build ... --output
type=docker,rewrite-timestamp=true,...` — unchanged Makefile logic).
`scripts/ci/check_workflows.py` (`make workflow-check`) independently
confirms 13/13 policy checks pass, run this session against the current
`.github/workflows/{ci.yml,release.yml}`. No registry-publish step exists
in either workflow (no `docker push`, no `docker/login-action`, no
`docker/build-push-action`) — confirmed by direct inspection of both
files' full contents, not merely by trusting `check_workflows.py`'s own
`check_no_registry_publication` pattern list. No Kubernetes, Terraform,
Cosign/SLSA, cloud-infrastructure, or observability-stack content
appears anywhere in this Day 7 diff (confirmed by reading every changed/
added file: `docs/roadmap.md`, `docs/releases/v1.0.0.md`,
`docs/production-readiness.md`, `.claude/agents/release-engineer.md`,
`.claude/skills/release-readiness/SKILL.md`, and all new `scripts/`
files) — all explicitly and repeatedly disclaim this as out of scope in
their own text.

No findings in this section.

---

## Findings

### DAY7-ARCH-L1

**Severity**: Low
**Title**: `docs/production-readiness.md` overstates `tests/test_debian_version.py`'s test count by one
**Evidence**: `docs/production-readiness.md` §1.1 states "Tests:
`tests/test_debian_version.py` (15 tests — the Debian Policy canonical
tilde-ordering example, epoch/revision/upstream precedence, this
project's own real version pair)...". Running `python3 -m unittest
tests.test_debian_version -v` this session shows exactly 14 tests, all
passing (confirmed by direct enumeration of `CompareDebianVersionsTests`'
test methods and the real `unittest` run output: "Ran 14 tests in
0.004s — OK"). The other three test-count claims in the same section/
document (`test_patch_lifecycle_check.py` 14, `tests/test_image_audit.py`
9, `tests/test_prepare_release_bundle.py` 13) were independently
verified accurate.
**Impact**: Purely a documentation-precision nit — every actual test in
`tests/test_debian_version.py` passes and genuinely covers what the
document describes (tilde ordering, epoch/revision/upstream precedence,
the real vulnerable/patched version pair); no test is missing, mislabeled,
or fabricated. This is the same class of finding this project's own
historical debt ledger has previously and explicitly accepted as
non-blocking (e.g. Day 3's A-7 "implementation-report file-count
headline" and Day 2's "external test-count claim inaccurate", both
`ACCEPTED`).
**Required remediation**: Correct "15 tests" to "14 tests" in
`docs/production-readiness.md` §1.1 the next time that file is touched;
no code change required.
**Release-blocking**: NO

### DAY7-ARCH-I1

**Severity**: Info
**Title**: Both pinned base-image digests are now older than the live moving tags (expected, not a defect)
**Evidence**: This session's real registry checks: `docker buildx
imagetools inspect gcr.io/distroless/python3-debian13:nonroot` resolves
today to index digest `sha256:f3d5ddc6...`/linux-amd64 manifest
`sha256:2da46b94...`, and `docker pull python:3.13-slim` resolves today
to `sha256:7ce4b6df...` — both different from the Dockerfile's pinned
`sha256:4376456c...884bea` (final) and `sha256:ffb752e1...c6e30a`
(builder/security-patch) digests, which were resolved and documented on
2026-08-18/2026-08-20 per the Dockerfile's own comments. Both pinned
digests remain independently pullable and unchanged this session (`docker
pull` against each exact pinned digest succeeds and reports the same
digest back).
**Impact**: None currently. This is the expected, intended behavior of
digest pinning (a moving tag advances; a pinned digest does not), and
`scripts/security/patch_lifecycle_check.py` already provides the
project's real tripwire for the one thing that actually matters here —
whether the *currently pinned* Distroless digest still requires the
`libssl3t64` overlay (it does, confirmed A-REQUIRED this session). This
finding does not indicate that the pinned digests are broken, wrong, or
about to disappear from the registry. It is recorded only because a
future base-image refresh decision (moving either pin forward) would
need its own independent re-resolution and re-verification against
`docs/build-security.md`'s documented decision process — exactly as this
agent's own standing brief requires — and because `patch_lifecycle_check.py`
intentionally validates only the currently pinned digest, not whether a
newer available upstream Distroless build might already ship the fixed
`libssl3t64` and make the overlay avoidable sooner.
**Required remediation**: None required for v1.0.0. If a future base
refresh is undertaken, it must independently re-resolve both digests
(not trust this review's date) and document the decision in
`docs/build-security.md`, per this project's existing base-image-refresh
discipline.
**Release-blocking**: NO

---

## Final Verdict

**APPROVE**

- Critical: 0
- High: 0
- Medium: 0
- Low: 1 (DAY7-ARCH-L1 — a documentation test-count off-by-one, non-blocking)
- Info: 1 (DAY7-ARCH-I1 — expected base-image tag/digest drift, no action required)

From the platform architecture perspective, this working tree is
suitable to proceed toward v1.0.0. The three-stage Dockerfile, PID 1
process model, OCI metadata, `.dockerignore`/layering discipline, and the
full Compose topology (exactly three services, one shared image, the
`gateway -> app -> state` chain, loopback-only host publication, network
segmentation, named-volume persistence, resource limits, restart policy,
and `stop_grace_period`) are all unchanged by this Day 7 diff and were
independently reverified this session against real `docker build`,
`docker compose config`, `scripts/build/image_audit.py`,
`scripts/security/patch_lifecycle_check.py`, `scripts/lint/check_dockerfile.py`,
`scripts/compose/check_compose.py`, `scripts/ci/check_workflows.py`, and
a full local test run (677/677 passing) — not merely trusted from
`docs/production-readiness.md`'s own narrative. Day 7's three new
architectural additions (runtime patch lifecycle validation, release
bundle staging with real consumer-checksum verification, and the
materially improved base-layer provenance check) are all cleanly layered
delivery/release-plane tooling that does not blur the Dockerfile/Compose/
CI boundaries this project maintains, and no scope drift (Kubernetes,
registry publication, Cosign/SLSA, cloud infrastructure, or an
observability stack) was introduced anywhere in this diff.

DAY 7 PLATFORM ARCHITECTURE REVIEW COMPLETE
