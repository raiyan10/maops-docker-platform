# Day 6 Final Post-Release Verification Record — v0.6.0

Repository: `maops-docker-platform`
Branch: `main`
Role: independent post-release verification. **`v0.6.0` is already
released.** This record is evidence-only: no implementation file,
workflow, test, tag, or GitHub Release asset was modified, moved,
recreated, or clobbered. Nothing was committed, pushed, tagged, or
released as part of producing this document. The only file created is
this one.
Date: 2026-08-27.

> **Correction / addendum (2026-08-27).** This record has been corrected
> based on newly discovered authoritative GitHub Actions evidence. §2.4
> previously stated that automatic merged-`main` push CI did not
> materialize and provisionally attributed this to a GitHub-side trigger
> anomaly; that statement and conclusion are withdrawn — see §2.4 for the
> corrected account. A new §6 documents post-release evidence-commit CI
> (a real, later, single-attempt failure on a documentation-only commit,
> resolved on rerun with no implementation change), and a new finding
> DAY6-POST-M2 (§7.2) is added. `v0.6.0` itself is unaffected — see §10.

---

## 1. Release identity

| Item | Value |
|---|---|
| Release tag | `v0.6.0` |
| Reviewed merged-main commit | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Annotated tag object | `af4f92646045ba1b04cb201490332e8fd000c50f` |
| Dereferenced remote tag points to | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |

The annotated tag object dereferences to exactly the merged-`main` commit
under review. No divergence between the tag target and the reviewed
commit exists.

---

## 2. CI / release history (preserved accurately)

This section preserves the real, non-linear path this release actually
took — including its two genuine CI failures — rather than presenting a
retroactively clean narrative.

### 2.1 PR failures

- **Run `32938805880` — FAILED.**
  Root cause: the GitHub default Buildx `docker` driver is incompatible
  with the deterministic `type=docker` archive exporter required for
  reproducible-build verification.
- **Run `32960673438` — FAILED.**
  Root cause: a post-restart runc/cgroup-v2 resource-update race
  encountered during the persistent reliability scenario.

### 2.2 Successful PR run

- **Run `32967457379` — PASS.** First fully green run on the feature
  branch after both root causes above were fixed.

### 2.3 Final pre-merge validation

- **Final implementation PR validation — run `32985020258` — PASS.**
- **Final review-head PR validation — run `32987406224` — PASS.**

PR #6 was then merged to `main` (merge commit
`eb043b4e9a62df8717399c9ab136fb722dc9bd0b`).

### 2.4 Automatic merged-main push CI (corrected)

Authoritative GitHub Actions evidence now proves that the automatic
`push`-triggered CI run on `main` for the merge commit **did** run:

| Field | Value |
|---|---|
| Run | `32990848068` |
| Event | `push` |
| Branch | `main` |
| headSha | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Quality (fast, Docker-free static checks) | SUCCESS |
| Release policy (build, security, reliability, reproducibility, supply chain) | SUCCESS |

This run validated exactly the reviewed merged-`main` release commit,
`eb043b4e9a62df8717399c9ab136fb722dc9bd0b`.

An earlier version of this record stated that automatic push CI "did
not materialize" and provisionally attributed this to a GitHub-side
trigger anomaly. **That statement and that provisional conclusion are
withdrawn.** The earlier CLI queries used to investigate this
temporarily returned "no runs found" for the merge commit; later GitHub
Actions history exposed the actual successful run above. This is
recorded accurately as **delayed run visibility / an initially
incomplete observation**, not as a confirmed or suspected GitHub trigger
bug — no specific GitHub-internal cause is claimed for the earlier query
gap.

### 2.5 Additional explicit merged-main validation via `workflow_dispatch`

In addition to the automatic push CI run confirmed in §2.4, merged-`main`
was also explicitly validated through CI's manual entry point:

| Field | Value |
|---|---|
| Run | `33037379041` |
| Event | `workflow_dispatch` |
| Branch | `main` |
| SHA | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Quality | PASS |
| Release policy | PASS |

This run is **additional explicit validation** of the same
merged-`main` commit — it does not close an evidentiary gap, since §2.4
already establishes that the exact same commit received a real,
automatic, green push-triggered CI run.

---

## 3. Pre-tag dry run

| Field | Value |
|---|---|
| Run | `33037905027` |
| Event | `workflow_dispatch` |
| Branch | `main` |
| SHA | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Validate | SUCCESS |
| Publish GitHub Release | SKIPPED |

State before the dry run: `v0.6.0` tag absent, `v0.6.0` GitHub Release
absent.
State after the dry run: `v0.6.0` tag absent, `v0.6.0` GitHub Release
absent.

**This proves the main-bound dry run was non-publishing** — `validate`
ran and passed against the real merged-`main` commit, and `publish` did
not execute, with no side effect (no tag, no release) left behind by the
dry run itself.

---

## 4. Real release

The annotated `v0.6.0` tag was created on exactly
`eb043b4e9a62df8717399c9ab136fb722dc9bd0b` (tag object
`af4f92646045ba1b04cb201490332e8fd000c50f`).

The tag push automatically triggered the release workflow:

| Field | Value |
|---|---|
| Run | `33045245535` |
| Event | `push` |
| headBranch | `v0.6.0` |
| headSha | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Validate | SUCCESS |
| Publish GitHub Release | SUCCESS |

### GitHub Release

- name: `maops-docker-platform v0.6.0`
- draft: `false`
- prerelease: `false`

### Published assets

- `maops-docker-platform-0.6.0.spdx.json`
- `trivy-0.6.0.json`
- `SHA256SUMS`

---

## 5. Checksum verification

Published `SHA256SUMS` contained:

```
3bf0761d9a66d1252944027ef42d985844d05a8040562562d24ddebce2fe39cd  release-evidence/sbom/maops-docker-platform-0.6.0.spdx.json
71ca5800c4e3dd49b7e1d901c7212d1103154aebacbf4135e956ff7d7145a6bf  release-evidence/security/trivy-0.6.0.json
```

Downloaded release assets were:

- `maops-docker-platform-0.6.0.spdx.json`
- `trivy-0.6.0.json`
- `SHA256SUMS`

Running the normal, unmodified verification command:

```
sha256sum -c SHA256SUMS
```

**failed** — not because the asset bytes are wrong, but because
`SHA256SUMS` references internal CI workspace-relative paths
(`release-evidence/sbom/...`, `release-evidence/security/...`) that do
not exist in the flat GitHub Release download layout (all three assets
land in the same directory with no `release-evidence/` subtree).

Independent basename-based verification was then performed by matching
each downloaded file's actual basename against its corresponding hash
line in `SHA256SUMS`:

| Asset | Expected SHA-256 | Actual SHA-256 | Result |
|---|---|---|---|
| `maops-docker-platform-0.6.0.spdx.json` | `3bf0761d9a66d1252944027ef42d985844d05a8040562562d24ddebce2fe39cd` | `3bf0761d9a66d1252944027ef42d985844d05a8040562562d24ddebce2fe39cd` | PASS |
| `trivy-0.6.0.json` | `71ca5800c4e3dd49b7e1d901c7212d1103154aebacbf4135e956ff7d7145a6bf` | `71ca5800c4e3dd49b7e1d901c7212d1103154aebacbf4135e956ff7d7145a6bf` | PASS |

**Conclusion**: the published asset bytes match the published
cryptographic hashes exactly. The defect is **filename/path
representation only** — not a checksum-integrity, SBOM-integrity, or
vulnerability-report-integrity failure of any kind.

---

## 6. Post-release evidence-commit CI

After this Day 6 evidence record was first committed
(`09f04a99725216b07ef30b5dfc644a35d5bb4a37` — documentation/evidence
only, added **after** the immutable `v0.6.0` release commit), automatic
push CI ran against that evidence commit.

| Field | Value |
|---|---|
| Run | `33059581018` |
| Event | `push` |
| Branch | `main` |
| headSha | `09f04a99725216b07ef30b5dfc644a35d5bb4a37` |

### 6.1 Attempt 1 — FAILURE

| Field | Value |
|---|---|
| run_attempt | 1 |
| Overall | FAILURE |
| Quality | PASS |
| Release policy | FAIL |

Before the failure, the following completed successfully:

- 622 unit tests passed
- source lint passed
- Dockerfile structural checks passed
- Compose structural checks passed
- workflow policy checks passed
- build/security work progressed
- reliability Scenario 1 completed successfully

Scenario 1 proved (real Docker behavior, not simulated):

- a real transient PID 1 OOM crash
- exactly one automatic restart
- `state` returned healthy
- `app` readiness recovered
- `gateway` readiness recovered
- persisted state survived
- a full `gateway -> app -> state` request succeeded after recovery

Immediately afterward, Scenario 2 attempted:

```
docker update <state-container> --memory 6m --memory-swap 6m
```

Docker/runc returned:

```
runc did not terminate successfully:
openat2 .../memory.max: no such file or directory
```

The reliability harness classified this error as non-retryable, and the
release policy job failed as a direct result.

The later `upload-artifact` warning that `artifacts/sbom` and
`artifacts/security` were absent is downstream fallout from
release-check terminating early — it is **not** a separate root cause.

### 6.2 Attempt 2 — SUCCESS (single rerun)

The failed run was rerun **once**, using GitHub's failed-job rerun
mechanism, on the same Actions run and the same exact commit:

| Field | Value |
|---|---|
| Run | `33059581018` (same run) |
| run_attempt | 2 |
| headSha | `09f04a99725216b07ef30b5dfc644a35d5bb4a37` (same commit) |
| Overall | SUCCESS |
| Quality | SUCCESS |
| Release policy | SUCCESS |
| Artifact | `ci-release-evidence-09f04a99725216b07ef30b5dfc644a35d5bb4a37` |

No repository implementation changed between attempt 1 and attempt 2.
Attempt 2 is **not** described as "fixing" the problem — nothing was
fixed. The single successful rerun strengthens the evidence that
attempt 1 was an environment-sensitive post-container-restart
runc/cgroup-v2 synchronization race, of the same general class already
documented in §2.1's second PR failure. It does **not** prove the
reliability harness is sufficiently robust against this class of race —
see DAY6-POST-M2 (§7.2) below.

---

## 7. Post-release findings

### 7.1 DAY6-POST-M1 — Severity: Medium

**Title**: `SHA256SUMS` uses CI workspace-relative paths instead of
release asset basenames.

**Impact**: A consumer who downloads all GitHub Release assets into a
single flat directory (the normal GitHub Release download experience)
cannot directly run `sha256sum -c SHA256SUMS`, because the checksum file
references `release-evidence/sbom/...` and
`release-evidence/security/...` — paths that only existed inside the
CI runner's workspace — while the GitHub Release itself serves the
assets flat, with no subdirectory structure.

This is explicitly:

- **NOT** a checksum-integrity failure (§5 — hashes match exactly).
- **NOT** an SBOM-integrity failure.
- **NOT** a vulnerability-report-integrity failure.
- **NOT** a reason to mutate `v0.6.0` in any way.

It **is** a release-consumption usability / release-engineering defect:
the checksum manifest's own internal paths don't match the layout it
ships in.

**Disposition**: carried to Day 7 final hardening, not fixed against
`v0.6.0`.

**Required Day 7 closure**:

1. Generate `SHA256SUMS` using release asset basenames only, e.g.:
   ```
   <hash>  maops-docker-platform-1.0.0.spdx.json
   <hash>  trivy-1.0.0.json
   ```
2. Add automated proof that a consumer-style flat release directory
   (assets placed in one directory with no `release-evidence/` subtree,
   mirroring an actual GitHub Release download) passes
   `sha256sum -c SHA256SUMS` unmodified.

### 7.2 DAY6-POST-M2 — Severity: Medium

**Title**: Post-restart cgroup race classifier is narrower than observed
GitHub runner failure variants.

**Explanation**: Day 6 introduced bounded retry handling for a known
runc/cgroup-v2 post-restart synchronization race. The classifier
deliberately required a narrow signature involving:

- a runc failure
- `cgroup.controllers`
- "no such file or directory"

The post-release evidence-commit run (§6.1) exposed a closely related
real GitHub runner variant involving:

- a runc failure
- `memory.max`
- "no such file or directory"

Because `memory.max` was not part of the accepted transient signature,
the operation failed immediately rather than entering the bounded retry
path.

This is **not** evidence that arbitrary `docker update` errors should be
retried, and it is **not** a runtime application failure. It is
classified as reliability-test / runner-interaction hardening debt in
the reliability harness's failure classifier.

**Required Day 7 remediation** (must remain conservative):

- recognize only strongly evidenced transient post-restart cgroup-v2
  disappearance signatures
- include observed controller/resource-file disappearance such as
  `cgroup.controllers` and `memory.max` where safe
- preserve bounded monotonic retry
- preserve exact `HostConfig` post-update verification
- unrelated runc/Docker errors must still fail immediately
- retry deadline exhaustion must still fail
- add deterministic unit tests for each accepted and rejected signature
- add/retain real Docker reliability evidence

**Disposition**: carried to Day 7 final hardening as DAY6-POST-M2, not
fixed against `v0.6.0`.

---

## 8. Day 7 carried Medium items

Three Medium items are carried into Day 7 final hardening:

1. **Runtime-patch/base-overlay drift tripwire.**
   `security/runtime-patches.lock` currently has no automated future
   base/overlay drift tripwire to detect when a future Distroless base
   refresh makes the security overlay redundant or conflicting (see
   `docs/engineering-reviews/day-06-release-readiness.md` §16 item 1 and
   `docs/engineering-reviews/day-06-workflow-security-review.md` §10).

2. **`SHA256SUMS` consumer-path usability — DAY6-POST-M1 (§7.1).**
   `SHA256SUMS` records CI workspace-relative paths rather than flat
   release asset basenames. The hashes themselves were independently
   verified correct (§5).

3. **Post-restart cgroup race classifier coverage — DAY6-POST-M2
   (§7.2).** The bounded retry classifier handles the known
   `cgroup.controllers` race but does not cover the newly observed
   closely related `memory.max` disappearance variant.

None of these three items is marked closed by this record.

---

## 9. Immutability

**`v0.6.0` must remain immutable.** Specifically, this record does not,
and future work must not, without explicit separate authorization:

- force-move the `v0.6.0` tag,
- delete/recreate the `v0.6.0` tag,
- delete/recreate the `v0.6.0` GitHub Release,
- clobber any published `v0.6.0` release asset, or
- silently rewrite the published `SHA256SUMS`.

DAY6-POST-M1 (§7.1) and DAY6-POST-M2 (§7.2) are preserved as **Day 7
remediation items**, applied to the *next* release's checksum generation
and reliability-harness classifier respectively — not as retroactive
edits to `v0.6.0`'s already-published assets or already-executed release
automation.

The post-release evidence-commit CI failure documented in §6.1 occurred
on `09f04a99725216b07ef30b5dfc644a35d5bb4a37`, a documentation/evidence
commit **after** the immutable `v0.6.0` release commit. It does not
touch, retrigger, or invalidate the `v0.6.0` release itself.

---

## 10. Final Day 6 status

`v0.6.0` is not invalidated by the post-release evidence-commit CI
failure in §6.1. The actual immutable release commit,
`eb043b4e9a62df8717399c9ab136fb722dc9bd0b`, had:

- successful final PR CI (§2.3),
- successful automatic merged-main push CI, run `32990848068` (§2.4),
- successful additional manual merged-main CI, run `33037379041` (§2.5),
- successful pre-tag dry run, run `33037905027` (§3), and
- successful tag-triggered release validation/publication, run
  `33045245535` (§4).

The later transient failure occurred on the separate
documentation/evidence commit `09f04a99725216b07ef30b5dfc644a35d5bb4a37`
(§6), added after the release commit — and that exact same commit passed
on the single rerun (§6.2).

| Item | Status |
|---|---|
| Day 6 `v0.6.0` | **RELEASED** |
| Release artifact integrity | **VERIFIED** |
| Release automation | **VERIFIED** |
| Pre-tag non-publication behavior | **VERIFIED** |
| Remaining blocking issue for `v0.6.0` | **NONE** |

Carried Medium technical debt into Day 7 (three items — see §8 for
detail):

1. Runtime-patch/base-overlay drift tripwire (§8 item 1).
2. `SHA256SUMS` consumer-path usability defect — DAY6-POST-M1 (§7.1 /
   §8 item 2).
3. Post-restart cgroup race classifier coverage — DAY6-POST-M2 (§7.2 /
   §8 item 3).

Day 6 is complete and may be frozen after this evidence is committed.

---

DAY 6 v0.6.0 POST-RELEASE VERIFICATION COMPLETE
