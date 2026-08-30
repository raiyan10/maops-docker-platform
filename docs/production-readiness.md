# Production readiness — Day 7 (v1.0.0 release-candidate preparation)

This document is Day 7's implementation-time **debt ledger** (every
still-relevant Low/Medium finding raised across Days 1-6's engineering
reviews, adjudicated) and the final **production-readiness contract**
this project ships as `v1.0.0`. It is release-*candidate* preparation
only — see [docs/releases/v1.0.0.md](releases/v1.0.0.md); the `v1.0.0`
Git tag and GitHub Release are not created by this document or by this
implementation pass.

`make release-check` remains the single authoritative local validation
contract (identical in CI — `.github/workflows/ci.yml`'s
`release-policy` job runs the same target). This document does not
define a second, competing validation universe — it explains what the
existing chain now covers and why.

## 1. Day 7 Mediums closed

Three Medium findings were explicitly carried from Day 6 into this
session and are now closed with real evidence (not merely documentation):

### 1.1 Runtime security-patch lifecycle

**Problem**: `security/runtime-patches.lock`'s emergency `libssl3t64`
Debian-security overlay (see
[docs/build-security.md](build-security.md) and
[docs/supply-chain.md](supply-chain.md)) had no automated tripwire for
its own exit condition — nothing detected when a future Distroless base
refresh already ships an equivalent-or-newer package, at which point the
overlay becomes redundant (or, worse, could silently downgrade the
runtime if the base ever shipped something *newer* than the overlay).

**Closure**: `scripts/security/patch_lifecycle_check.py`
(`make patch-lifecycle-check`, wired into `make release-check`).

- Derives the pinned final base's (repository, digest) directly from
  `docker/app/Dockerfile`'s own `FROM` text
  (`scripts/security/base_image_ref.py`) — never a second hand-copied
  digest constant, so the check cannot be tautological by construction.
- Independently `docker pull`s that exact digest, `docker create`s
  (never runs — Distroless has no shell) a throwaway container from it,
  and `docker cp`s out the real `/var/lib/dpkg/status.d/libssl3t64`
  metadata the base image itself ships.
- Compares the real observed base version against the lock's
  `LIBSSL_VULNERABLE_VERSION`/`LIBSSL_VERSION` using genuine Debian
  version-comparison semantics (`scripts/security/debian_version.py` —
  a from-scratch implementation of Debian Policy §5.6.12's algorithm,
  correctly handling `~deb13uN`-style revisions that plain
  string/tuple comparison gets wrong).
- Produces four distinguishable, independently unit-tested outcomes:
  **A** overlay still required (PASS), **B** base has caught up/overtaken
  the overlay -> overlay now redundant (FAIL, explicit removal/review
  required), **C** evidence could not be established at all (FAIL — never
  silently assumed still-required), **D** overlay still required but the
  lock's own recorded rationale has drifted from the real base (FAIL,
  prompting a lock update).
- Real evidence (this session, against the actual pinned base):
  `Version: 3.5.6-1~deb13u2` — matches `LIBSSL_VULNERABLE_VERSION`
  exactly, correctly classified **A-REQUIRED / PASS**.
- Tests: `tests/test_debian_version.py` (14 tests — the Debian Policy
  canonical tilde-ordering example, epoch/revision/upstream precedence,
  this project's own real version pair), `tests/test_patch_lifecycle_check.py`
  (14 tests — all four classifications, precedence between B and D, a
  non-tautology proof that the same fixed inputs produce four genuinely
  different outcomes as only the observed base version varies).

**Exit condition** (documented in `scripts/security/patch_lifecycle_check.py`'s
own module docstring and `security/runtime-patches.lock`'s header
comment): the overlay should be removed from `docker/app/Dockerfile` and
`security/runtime-patches.lock` once `patch-lifecycle-check` reports
classification **B** (base >= patched version) — at that point the
pinned Distroless base itself ships a fixed `libssl3t64`, and keeping the
overlay in place would be redundant at best.

### 1.2 Release-consumer `SHA256SUMS` layout (DAY6-POST-M1)

**Problem**: the real, already-published `v0.6.0` release's `SHA256SUMS`
recorded CI workspace-relative paths (`release-evidence/sbom/...`,
`release-evidence/security/...`), while GitHub Releases serve attached
assets flat. A consumer downloading all assets into one directory and
running the documented `sha256sum -c SHA256SUMS` got a hard failure —
not a checksum-integrity defect (the hashes were independently verified
correct), but a real release-engineering usability defect.

**Closure**: `scripts/release/prepare_release_bundle.py`
(`make release-bundle`, wired into `make release-check`;
`release.yml`'s `publish` job now runs it and attaches
`release-bundle/*` verbatim instead of computing checksums inline).

- Stages a flat, basename-only bundle directory from the nested
  `sbom/`/`security/` evidence tree (shared by both the local
  `artifacts/` layout and CI's downloaded `release-evidence/` layout).
- Writes `SHA256SUMS` using GNU-coreutils text-mode format, hashing the
  files as they actually exist on disk at write time.
- Independently proves the real, unmodified `sha256sum -c SHA256SUMS`
  succeeds in that directory via a real subprocess call — never a
  Python-side hash reimplementation standing in for that external-tool
  proof.
- Defense in depth: `validate_manifest_entries_are_bare_basenames`
  re-parses whatever `SHA256SUMS` is handed to verification and rejects
  any entry that is not a bare basename (a path separator or a
  directory-traversal token), so a hand-tampered manifest can never
  smuggle a nested/internal-CI-path or path-traversal reference past
  verification even though this project's own writer never produces one.
- Tests (`tests/test_prepare_release_bundle.py`, 13 tests, using the
  real `sha256sum` binary): the golden path against a real staged
  bundle; a missing source asset fails to even stage; an asset deleted
  after the manifest was written fails verification; a renamed asset
  fails; a tampered (content-modified) asset fails; two different
  sources colliding on the same basename are rejected; a hand-tampered
  manifest with a `../../etc/passwd`-style traversal entry is rejected;
  a hand-tampered manifest reproducing the exact real `v0.6.0` regression
  shape (`release-evidence/sbom/...`) is rejected; a malformed manifest
  line is rejected; a missing manifest is rejected.

### 1.3 Post-restart cgroup-v2 race classifier (DAY6-POST-M2)

**Problem**: Day 6's `_is_transient_cgroup_update_race` classifier
(`scripts/reliability/reliability_check.py`) only recognized the exact
`cgroup.controllers` signature GitHub run `32960673438` hit. A later
post-release evidence-commit CI run (`33059581018`, attempt 1) — which
occurred immediately after a genuine Scenario 1 OOM crash and automatic
restart — hit a closely related but distinct variant referencing
`memory.max` instead, which the narrow classifier correctly (per its own
design) refused to retry, failing the run.

**Status: CODE-LEVEL CLOSED — LIVE RECURRENCE CONFIRMATION PENDING.**
The classifier code itself is genuinely and conservatively correct (see
below and the independent Day 7 reliability-adversarial review,
`docs/engineering-reviews/day-07-reliability-adversarial-review.md`,
finding `DAY7-REL-M2`) and is backed by deterministic, independently
re-run positive and negative unit tests. What is **not yet** true is a
real, live-Docker recurrence of either accepted signature actually
triggering the new retry path end to end on a GitHub-hosted runner since
this Day 7 fix landed — the only real-Docker evidence in hand for the
`memory.max` variant specifically is the *original failing occurrence*
(run `33059581018`) that motivated the fix. Per this project's own
`[A]`/`[B]`/`[C]`/`[D]` evidence-tier discipline (see `docs/security.md`),
the classifier's logic is `[A]`/`[B]`-tier real (source-derived and
independently re-verified against real log-derived fixtures), but the
retry path's *live discriminating power* is not yet `[D]`-tier proven
against a fresh real occurrence — declaring this unconditionally
"CLOSED" would overstate that distinction. The residual gap is
evidence, not a known code defect: the classifier itself is not held
open by this finding.

The classifier now requires, conservatively, ALL of: the
`runc did not terminate successfully` wrapper phrase; a real
`openat2 <path>: no such file or directory` match (genuine ENOENT
semantics, not merely the words appearing anywhere); the missing path's
directory containing a real `/cgroup/` hierarchy segment; and the
missing path's basename being one of a small, explicitly enumerated,
deliberately restricted set — `{cgroup.controllers, memory.max}` — never
a broad "any cgroup-shaped filename" wildcard. Extending this set again
requires a new, independently observed real GitHub Actions failure, not
speculation.

- Preserves the original `cgroup.controllers` variant unchanged.
- Accepts the newly evidenced `memory.max` variant.
- Bounded monotonic retry deadline, exact `HostConfig` post-update
  verification, and already-applied-despite-nonzero-exit handling are
  all unchanged (`update_container_resources_verified`).
- New tests (`tests/test_reliability_check.py`): positive coverage for
  both accepted signatures (`test_real_github_run_32960673438_error_is_
  classified_as_transient`, `test_real_github_run_33059581018_memory_max_
  error_is_classified_as_transient`); negative discrimination proving the
  restriction is deliberate — an otherwise-identical error naming
  `pids.max` (never observed, not accepted) is rejected, an accepted
  filename outside a real cgroup path is rejected, and a real `openat2`
  failure that is not ENOENT (e.g. permission denied) is rejected; plus a
  full `update_container_resources_verified` retry-and-verify proof for
  the new `memory.max` variant. Deterministic positive/negative coverage
  passes (`python3 -m unittest tests.test_reliability_check`), and a real
  end-to-end `python3 scripts/reliability/reliability_check.py` run
  against the built release image still passes in full — but on this
  project's own local Docker Desktop install, both the Scenario 2 shrink
  and restore `docker update` calls succeeded in exactly one attempt
  each, meaning the transient-retry branch itself did not fire during
  that run (consistent with the classifier's own documentation that this
  race is a GitHub-hosted-runner-specific phenomenon, not reproducible
  against local Docker Desktop).

**Exit condition for full closure**: the next genuine GitHub-hosted-
runner recurrence of either accepted signature (`cgroup.controllers` or
`memory.max`) should be preserved as the final `[D]`-style live
confirmation that the widened classifier retries and succeeds against a
real occurrence, not merely against synthetic, log-derived fixtures —
mirroring how the original `cgroup.controllers` finding was closed by
citing the real `gh run view` output for run `32960673438`. The absence
of a recurrence since this fix landed is not, by itself, a reason to
synthesize a live race artificially or to weaken/skip the real
end-to-end `reliability_check.py` run — see this project's Docker safety
and evidence-tier conventions in `.claude/CLAUDE.md`.

## 2. Also materially closed this session: Day 4 image-audit base-digest tautology

Flagged explicitly by this session's own historical-debt review:
`scripts/build/image_audit.py`'s `check_final_base_is_approved_distroless`
had never actually compared anything — it only asserted that
`docker image inspect`'s `RootFS.Layers` on the *built release image*
was non-empty, a check that could not fail even if the base were wrong.
It carried at Medium severity since Day 4 (`day-04-reproducibility-review.md`),
was explicitly reaffirmed (not downgraded) in `day-05-release-readiness.md`
§8, and was silently absent from Day 6's own closed-Medium tally with no
adjudicated closure anywhere.

**Closure**: the check now independently `docker pull`s the exact pinned
base (derived the same way `patch_lifecycle_check.py` derives it — see
§1.1), and asserts that base image's own `RootFS.Layers` is a genuine
ordered *prefix* of the built release image's own `RootFS.Layers` — real
cross-checked evidence that the release image's actual on-disk layer
content was built FROM those exact base layers, not merely that the
Dockerfile text says so. New Docker-free unit coverage
(`tests/test_image_audit.py`, 9 tests) exercises the decision logic
directly (matching prefix passes; a diverged, shorter, or empty base
layer list all fail; pull/inspect failures and non-JSON output all fail
clearly) — this function had zero unit tests before this session.

The function's sibling Day-4-carried gaps (its `/app/`-only
source-immutability probe scope; `check_trivy_report.py`'s
malformed-report-shape/severity-case handling) were not touched — see §3
for their disposition.

## 3. Historical debt ledger (Days 1-6)

Every still-relevant Low/Medium (and any surviving High) finding from
`docs/engineering-reviews/*.md`, adjudicated. "Confirmed" means checked
against the current repository state during this Day 7 session, not
merely trusted from an earlier report.

### Day 1
| Finding | Disposition |
|---|---|
| M-1 release: version literals duplicate `VERSION` uncross-checked | **CLOSED** — `VERSION` is the single derived source everywhere (Makefile, build-arg, labels, smoke). |
| M-2 test: healthcheck-invocation regression only caught by slow security-check | **CLOSED** — `check_dockerfile.py::check_healthcheck()` now statically enforces the exact HEALTHCHECK CMD in the fast `lint`/`quality` gate (Day 4). |
| M-3 test: no automated Compose-hardening regression test | **CLOSED** — `check_compose.py` now has extensive structural security/resource/restart/network checks (Days 2/3/5). |
| L-1/L-2 security: pre-container checks unguarded; healthcheck negative-path doc gap | **ACCEPTED** — low impact, never re-flagged, never caused a real failure. |
| L-1..L-5 test, L-1 release: coverage/doc nits | **ACCEPTED** — explicitly reconfirmed non-blocking in `day-02-release-readiness.md` §5. |

### Day 2
| Finding | Disposition |
|---|---|
| M-1 compose: `depends_on` ordering never proven at runtime | **CLOSED by Day 3** — `compose_integration.py` real ordering proof. |
| M-1 security: Compose read-only rootfs proven only at [C] | **CLOSED by Day 3** — `[D]` kernel-level real-write-rejection proof (`docs/networking.md`). |
| L-1 compose: no cross-check `UPSTREAM_HOST` names a real service | **CLOSED by Day 3**. |
| L-2 compose / L-1 security: smoke script has no gateway coverage | **ACCEPTED** — Compose test already covers gateway; explicitly accepted `day-02-release-readiness.md`. |
| M-1 test: external test-count claim inaccurate | **ACCEPTED** — doc-precision nit only. |
| L-2..L-5 test: coverage/edge-case nits | **ACCEPTED**. |

### Day 3
| Finding | Disposition |
|---|---|
| A-1 `schema_version` boolean bypass (Medium) | **CLOSED** — explicit `isinstance(..., bool)` rejection in all three `platform_config.py` modules. |
| A-2 healthcheck hardcoded to `app.healthcheck` role (Medium) | **CLOSED** — role-aware dispatch + `EXPECTED_ROLE` check, independently reproduced (9-cell matrix), see also H-1 (Day 4). |
| A-3 `docs/networking.md` [A]-vs-[C] overclaim | **CLOSED** — doc now describes the real `[C]`-tier live check. |
| A-4 `docs/compose-platform.md` stale constant reference | **CLOSED**. |
| A-5 `compose_integration.py` no SIGTERM handling (Medium) | **CLOSED** — `_install_sigterm_handler()`/`_TerminatedError`, dedicated unit test. |
| **A-6 cross-hop timeout stacking (Medium)** | **CLOSED by Day 5** — `gateway_upstream_timeout_seconds >= state_dependency_timeout_seconds + timeout_safety_margin_seconds` enforced at config-load time; `reliability_check.py` prints "A-6 closed" against a real `docker pause state` adversarial proof. |
| A-7 implementation-report file-count headline | **ACCEPTED** — reporting-only nit. |
| Persistence/networking/security L-findings (diagnosability/doc-precision gaps) | **ACCEPTED** — never regressed, behavior independently confirmed correct. |

### Day 4
| Finding | Disposition |
|---|---|
| H-1 role-aware healthcheck dispatch (High) | **CLOSED** — h1-remediation branch, independently reverified via a live 3x3 matrix outside any project script. |
| **M-1 `image_audit.py` base-digest tautology** | **CLOSED THIS SESSION** — see §2 above. |
| **M-2 `image_audit.py` zero unit tests** | **CLOSED THIS SESSION** (for the tautology function specifically) — `tests/test_image_audit.py` added; the rest of `image_audit.py` remains covered only by `make image-audit`'s real-Docker proof, unchanged from Day 4. |
| M-3 `SOURCE_DATE_EPOCH` anchored to a commit not containing the built tree | **CLOSED** — a working-tree/uncommitted-state artifact of review timing, not a code defect; `Makefile`'s epoch derivation is mechanically correct at any real commit/tag time. |
| supply-chain [Medium] `check_trivy_report.py` fails unsafely on non-dict-shaped JSON | **ACCEPTED, still open** — not touched this session; genuine but narrow (requires a malformed scanner output, never observed from the pinned Trivy digest); out of this session's three mandatory Mediums. |
| supply-chain [Low] case-sensitive severity comparison | **ACCEPTED, still open** — same reasoning; real Trivy output is always uppercase. |
| `image_audit.py` immutability probe scoped to `/app/app/` only | **ACCEPTED, still open** — narrow test-coverage gap (the real Day 4 image-level-immutability property does hold for `gateway/`/`state/` too, by identical `COPY` semantics; only the probe's own path list is narrow). |
| `check_dockerfile.py` / reproducibility-manifest untested branches (uid/gid axis) | **ACCEPTED, still open** — coverage-only, no known behavioral gap. |
| H-1-remediation M-1 (malformed-input coverage gap on already-correct role-discrimination code) | **ACCEPTED, still open** — coverage-only. |
| H-1-remediation L-1 (stale A-2/H-1 doc wording) | **CLOSED by Day 5** — `day-05-release-readiness.md` §8 last row. |

### Day 5
| Finding | Disposition |
|---|---|
| M-1 (resource-restart) `with_memory_shrink_restored` warning-only restore is accidental, not designed | **CLOSED by Day 6** — both shrink and restore now route through `update_container_resources_verified`, which raises on any unverified state. |
| L-1 (resource-restart) `check_compose.py` resource check has no lower-bound sanity check | **ACCEPTED, still open** — mitigated one gate later by `reliability_check.py`'s real-container exact-equality check. |
| M-1 (test-adversarial) zero persisted unit coverage for `check_compose.py`'s new resource/restart/stop-grace-period checks | **CLOSED** — `tests/test_check_compose.py` now has dedicated classes for all three. |
| L-2 (test-adversarial) gateway platform_config missing `-Infinity` parity | **CLOSED** — added to `tests/test_gateway_platform_config.py`. |
| L-1 (failure-recovery) RestartCount-resets-on-manual-start doc gap | **ACCEPTED** — doc-completeness only, not misleading. |
| L-2 (failure-recovery) no Docker-free SIGTERM/`_TerminatedError` test in `reliability_check.py` | **CLOSED by Day 6**. |
| release-security review's Low relabeling of the image_audit tautology | **REJECTED relabeling** — held at Medium; see §2 (now closed at that severity). |

### Day 6
| Finding | Disposition |
|---|---|
| Bootstrap-readiness F-1 (dry-run had no ref/branch validation) | **CLOSED** — `validate_dispatch_ref()`, exact match against `refs/heads/main`, 6+ dedicated tests, unconditional fail-fast workflow step. |
| F-2 (Medium) `check_no_manufactured_pass()` only matches literal `\|\| true` | **ACCEPTED, still open** — confirmed unchanged; not exploited against any real workflow file in this repository; hardening it is a `check_workflows.py`-only improvement out of this session's three mandatory Mediums, deliberately not undertaken to avoid unrelated scope creep. |
| F-3 (Medium) `check_no_registry_publication()` fixed 6-pattern allowlist | **ACCEPTED, still open** — same reasoning; `release.yml` itself is confirmed clean of any registry-publish command. |
| F-4 (Low) `check_required_triggers()` doesn't reject a broadened tag pattern | **ACCEPTED, still open** — hygiene-only; the actual publish-permission guard (`publish` job's `if:`) is independent and unaffected. |
| Workflow-security review Medium (runtime-patch drift tripwire) | **CLOSED THIS SESSION** — see §1.1. |
| Release-engineering review #1 (main-only dry-run enforcement, Medium) | **CLOSED** — same as bootstrap F-1. |
| Release-engineering review #2 (expensive-gates-before-cheap-check ordering, Low) | **CLOSED** — context validation now runs immediately after "Report run mode", before the Buildx builder / `make release-check`. |
| Release-engineering review #3 (no dedicated `--clobber`/force-tag regression check, Low) | **CLOSED THIS SESSION** — `check_workflows.py`'s new `check_no_release_clobber()` (Day 7, `DAY7-RELENG-L1`) now automatically asserts the `publish` job's pre-`gh release create` existing-release guard is present, runs before `gh release create`, and that `gh release create` is never invoked with `--clobber` anywhere in either workflow file; `tests/test_check_workflows.py::NoReleaseClobberTests` covers the positive case and the negative cases (missing guard, `--clobber` present, guard placed after `gh release create`, a guard outside the `publish` job not falsely satisfying the requirement, and a harmless `--clobber` mention in a comment not false-positiving). |
| Release-engineering review #4 (pattern-based, not a real YAML parser) | **ACCEPTED** — an explicit, deliberate scope choice per `check_workflows.py`'s own docstring. |
| Test-adversarial review L-4/L-5 (retry path/`.lower()` normalization) | **CLOSED THIS SESSION** — the classifier rewrite (§1.3) replaces the ad hoc substring checks with the `openat2`/path-context/enumerated-filename design; case-sensitivity is no longer a factor since matching is now structural, not a bare `.lower()` substring check. |
| **DAY6-POST-M1** (SHA256SUMS paths) | **CLOSED THIS SESSION** — see §1.2. |
| **DAY6-POST-M2** (cgroup classifier narrowness) | **CODE-LEVEL CLOSED THIS SESSION — LIVE RECURRENCE CONFIRMATION PENDING** — see §1.3. |

**Summary**: of the historical findings surveyed, the three Day-7-mandated
Mediums (§1.1/§1.2) and the flagged Day 4 tautology (§2) are closed this
session with real evidence and new tests; §1.3 (`DAY6-POST-M2`) is
code-level closed with live-recurrence confirmation pending (see §1.3
for the precise disposition). The Day 7 independent-review remediation
pass also closed the `--clobber`-regression gap (`DAY7-RELENG-L1`, above).
A small number of narrowly scoped `check_workflows.py` coverage gaps
(F-2/F-3/F-4) and `check_trivy_report.py`/`image_audit.py` coverage nits
remain **ACCEPTED, open** — none are exploited against any file this
repository actually ships, none are among this session's mandated
Mediums, and fixing them was judged unrelated scope creep rather than
genuine v1.0.0 release-readiness work. Everything else surveyed is
independently confirmed **CLOSED** against the current repository state.

## 4. Final production-readiness contract

`make release-check` is the single authoritative gate, identical locally
and in CI (`.github/workflows/ci.yml`'s `release-policy` job runs the
same target). It now composes, in dependency order:

```
quality (test -> lint -> dockerfile-check -> compose-check -> workflow-check)
  -> build -> inspect -> image-audit -> smoke -> security-check
  -> compose-test -> reliability-check -> reproducibility-check
  -> supply-chain-check (sbom -> sbom-check -> vuln-scan)
  -> patch-lifecycle-check -> release-bundle
```

Mapped against this project's own full coverage requirement:

| Area | Covered by |
|---|---|
| Unit tests | `make test` (677 tests as of this session) |
| Source lint | `make lint` |
| Dockerfile policy | `make dockerfile-check` |
| Compose structural policy | `make compose-check` |
| Workflow policy | `make workflow-check` |
| Deterministic image build | `make build` (BuildKit, `SOURCE_DATE_EPOCH`-normalized) |
| Image audit (incl. real base-layer cross-check, §2) | `make image-audit` |
| Vulnerability policy (0 Critical, 0 fixable High) | `make vuln-scan` |
| SBOM | `make sbom` / `make sbom-check` |
| Integration (real Compose stack) | `make compose-test` |
| Health/readiness | `make compose-test` (startup ordering, network/topology behavior, stop/degrade/recover integration); `make reliability-check` (pause behavior, OOM/crash/restart behavior) |
| Persistence | `make compose-test` (persistence across recreation/down-up); `make reliability-check` (persistence across fault/recovery lifecycle, intentional-stop/restart semantics) |
| Network isolation | `make compose-check`, `make compose-test` |
| Reliability (real OOM crash/restart, real pause) | `make reliability-check` |
| Resource limits | `make reliability-check` (real `docker inspect`/cgroup v2) |
| Restart semantics | `make reliability-check` |
| Reproducibility | `make reproducibility-check` |
| Runtime security-patch lifecycle | `make patch-lifecycle-check` (§1.1) |
| Release bundle preparation | `make release-bundle` (§1.2) |
| Consumer checksum verification | `make release-bundle` (real `sha256sum -c`) |
| Release-context validation | `scripts/release/check_release_context.py` (`release.yml`'s `validate` job) |
| Supply-chain/release evidence generation | `make supply-chain-check` |

No area on this list is validated by a second, competing tool — every
row above is `make release-check`'s own dependency chain, not a parallel
validation path.

## 5. Operational reference (pointers, not duplication)

This project already documents operational behavior in focused,
topic-specific docs — this section only indexes them, per the existing
convention of extending rather than duplicating:

| Question | See |
|---|---|
| Start / stop the platform | [docs/compose-platform.md](compose-platform.md) (`docker compose up -d` / `down`) |
| Verify liveness vs. readiness | [docs/reliability.md](reliability.md) §"Health vs. readiness" |
| What is exposed to the host | [docs/networking.md](networking.md) (`gateway` only, loopback `127.0.0.1:8080`) |
| Which networks exist / what's isolated | [docs/networking.md](networking.md) (`edge`, `backend: internal: true`; `gateway`/`state` share no network) |
| Where state persists | [docs/persistence.md](persistence.md) (`state_data` named volume) |
| Recovery / restart-exhaustion behavior | [docs/reliability.md](reliability.md) (real OOM-kill + automatic bounded restart, `on-failure:3`, intentional-stop does not auto-restart) |
| Resource limits / graceful shutdown | [docs/reliability.md](reliability.md) (CPU 0.5, memory 128 MiB, PIDs 64, `stop_grace_period: 10s`) |
| Image security model / vulnerability policy | [docs/build-security.md](build-security.md), [docs/supply-chain.md](supply-chain.md) |
| Security overlay purpose / exit condition | [docs/supply-chain.md](supply-chain.md), §1.1 above |
| Reproducible-build model | [docs/build-security.md](build-security.md) |
| CI model / release dry-run / real tag release | [docs/ci-cd.md](ci-cd.md) |
| How a consumer downloads assets and verifies `sha256sum -c SHA256SUMS` | §1.2 above, [docs/releases/v1.0.0.md](releases/v1.0.0.md) |
| What remains intentionally out of scope | [docs/roadmap.md](roadmap.md) ("Out of scope" per day), this document §1/§3 |
