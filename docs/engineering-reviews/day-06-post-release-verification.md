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

### 2.4 Missing automatic push CI on `main`

The expected automatic `push`-triggered CI run on `main` for the merge
commit did **not** materialize as a workflow run. This was investigated,
not assumed:

- The CI workflow is active.
- `push` trigger branches include `[main]`.
- No `paths-ignore` filter exists that would exclude the merge.
- The merge SHA (`eb043b4e9a62df8717399c9ab136fb722dc9bd0b`) is correct
  and matches the dereferenced tag.
- No skip-CI token (`[skip ci]`/`[ci skip]`) is present in the merge
  message.
- The merge was performed outside the Actions `GITHUB_TOKEN` context
  (i.e., not by a GitHub Actions-authored push), which is a documented
  GitHub Actions condition under which some automatic `push`-triggered
  workflow runs can fail to fire.
- The exact root cause was **not** established with certainty.

This is recorded as a **GitHub-side trigger anomaly / exact cause
undetermined** — it is explicitly **not** described as a confirmed GitHub
bug, since the precise mechanism was not isolated.

### 2.5 Merged-main validation via `workflow_dispatch`

Because automatic push CI did not fire, merged-`main` was explicitly
validated through CI's supported manual entry point:

| Field | Value |
|---|---|
| Run | `33037379041` |
| Event | `workflow_dispatch` |
| Branch | `main` |
| SHA | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |
| Quality | PASS |
| Release policy | PASS |

This closes the evidentiary gap left by §2.4: the exact merged-`main`
commit that was later tagged did receive a real, green, full CI run —
just via `workflow_dispatch` rather than the anomalous `push` trigger.

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

## 6. Post-release finding

### DAY6-POST-M1 — Severity: Medium

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

---

## 7. Existing carried Medium (preserved, not closed)

In addition to DAY6-POST-M1, the pre-existing, already-documented Day 6
nonblocking Medium debt remains open and is preserved here unchanged:

- `security/runtime-patches.lock` currently has no automated future
  base/overlay drift tripwire to detect when a future Distroless base
  refresh makes the security overlay redundant or conflicting (see
  `docs/engineering-reviews/day-06-release-readiness.md` §16 item 1 and
  `docs/engineering-reviews/day-06-workflow-security-review.md` §10).

This item is **not** marked closed by this record.

---

## 8. Immutability

**`v0.6.0` must remain immutable.** Specifically, this record does not,
and future work must not, without explicit separate authorization:

- force-move the `v0.6.0` tag,
- delete/recreate the `v0.6.0` tag,
- delete/recreate the `v0.6.0` GitHub Release,
- clobber any published `v0.6.0` release asset, or
- silently rewrite the published `SHA256SUMS`.

DAY6-POST-M1 (§6) is preserved as a **Day 7 remediation item**, applied
to the *next* release's checksum generation — not as a retroactive edit
to `v0.6.0`'s already-published assets.

---

## 9. Final Day 6 status

| Item | Status |
|---|---|
| Day 6 `v0.6.0` | **RELEASED** |
| Release artifact integrity | **VERIFIED** |
| Release automation | **VERIFIED** |
| Pre-tag non-publication behavior | **VERIFIED** |
| Remaining blocking issue for `v0.6.0` | **NONE** |

Carried technical debt into Day 7:

1. Runtime-patch/base-overlay drift detection — Medium (§7).
2. `SHA256SUMS` consumer-path usability defect — DAY6-POST-M1, Medium
   (§6).

Day 6 is complete and may be frozen after this evidence is committed.

---

DAY 6 v0.6.0 POST-RELEASE VERIFICATION COMPLETE
