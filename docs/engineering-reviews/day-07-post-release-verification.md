# Day 7 Final Post-Release Verification Record — v1.0.0

Repository: `maops-docker-platform`
Branch: `main`
Role: independent post-release verification. **`v1.0.0` is already
released.** This record is evidence-only: no implementation file,
workflow, test, tag, or GitHub Release asset was modified, moved,
recreated, or clobbered. Nothing was committed, pushed, tagged, or
released as part of producing this document. The only file created is
this one.
Date: 2026-08-31.

---

## 1. Release identity

| Item | Value |
|---|---|
| Release tag | `v1.0.0` |
| PR | #7 (`feature/day-7-final-hardening-production-readiness` -> `main`) |
| PR merge commit | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Annotated tag object | `c1dfce4870c5848fa8a69c1a5f6d7842ba0d39b6` |
| Dereferenced remote tag points to | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Previous immutable release commit (`v0.6.0`, unaffected) | `eb043b4e9a62df8717399c9ab136fb722dc9bd0b` |

The annotated tag object is a distinct SHA from the commit it points at
— `c1dfce487...` is the tag object itself; `45614d1d2...` is the commit
it dereferences to, which is also the PR #7 merge commit. These two SHAs
are not interchangeable and are not confused anywhere in this record.

Local verification performed in this session:

```
$ git cat-file -p c1dfce4870c5848fa8a69c1a5f6d7842ba0d39b6
object 45614d1d2b845ef27a3a3a61ae086a45a048ae61
type commit
tag v1.0.0
...

$ git rev-parse v1.0.0^{commit}
45614d1d2b845ef27a3a3a61ae086a45a048ae61

$ git rev-parse HEAD
45614d1d2b845ef27a3a3a61ae086a45a048ae61
```

The local annotated tag object dereferences to exactly the PR #7 merge
commit, and local `main` is at that same commit. This session had no
authenticated remote access to independently re-run `git ls-remote`
against `origin`; the remote-side tag/branch values below (§6) are taken
as given, verified release evidence per this task's brief, not
re-derived by this document.

---

## 2. PR validation

| Field | Value |
|---|---|
| PR | #7 |
| Branch | `feature/day-7-final-hardening-production-readiness` -> `main` |
| CI run | `33307569947` |
| Result | SUCCESS |
| Quality | PASS |
| Release policy | PASS |
| Merged | 2026-08-30T11:04:06Z |

---

## 3. Merged-main validation

Automatic `push`-triggered CI on `main` for the merge commit:

| Field | Value |
|---|---|
| Run | `33307992295` |
| Event | `push` |
| Branch | `main` |
| headSha | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Run attempt | 1 |
| Result | SUCCESS |
| Quality | PASS |
| Release policy | PASS |
| Artifact | `ci-release-evidence-45614d1d2b845ef27a3a3a61ae086a45a048ae61` |

Unlike `v0.6.0` (see `docs/engineering-reviews/day-06-post-release-
verification.md` §2.4), this run's existence required no correction —
it was directly observed on the first query, on attempt 1, with no
"delayed run visibility" gap to record.

---

## 4. Release dry run

Pre-tag `workflow_dispatch` dry run of the release workflow:

| Field | Value |
|---|---|
| Run | `33308284528` |
| Event | `workflow_dispatch` |
| Branch | `main` |
| headSha | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Result | SUCCESS |
| Validate | PASS |
| Publish GitHub Release | SKIPPED |

The workflow's own annotation explicitly stated that `workflow_dispatch`
cannot publish a GitHub Release, create a tag, or publish a container
image. This is the correct, intended behavior — the same non-publishing
dry-run contract already established and verified for `v0.6.0`.

State immediately before the real tag was created:

- remote `v1.0.0` tag: absent
- GitHub Release `v1.0.0`: absent
- `main`: clean
- `VERSION`: `1.0.0`

---

## 5. Real release

The real tag-triggered release workflow run:

| Field | Value |
|---|---|
| Run | `33309078803` |
| Event | `push` |
| Head branch | `v1.0.0` |
| headSha | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Run attempt | 1 |
| Conclusion | SUCCESS |
| Validate (release-policy gates + tag/version/history) | PASS |
| Publish GitHub Release | PASS |

The workflow annotation stated:

> TAG RELEASE - real release event for tag v1.0.0 at commit
> 45614d1d2b845ef27a3a3a61ae086a45a048ae61

---

## 6. GitHub Release

| Field | Value |
|---|---|
| Release name | `maops-docker-platform v1.0.0` |
| Tag | `v1.0.0` |
| Published | 2026-08-30T12:42:32Z |
| Draft | `false` |
| Prerelease | `false` |
| targetCommitish | `main` |

Published assets, exactly:

- `maops-docker-platform-1.0.0.spdx.json` — 1,659,273 bytes
- `SHA256SUMS` — 187 bytes
- `trivy-1.0.0.json` — 611,856 bytes

A first `gh release view` attempt encountered a TLS handshake timeout. A
normal, unmodified retry succeeded immediately and returned the complete
release. **This is classified only as a transient client/network
observation** — it is not a release defect, it did not require any
release mutation, and it has no bearing on the release's validity.

---

## 7. Published consumer verification

A fresh temporary directory was created and the release assets were
downloaded from the actual GitHub Release using:

```
gh release download v1.0.0
```

Exactly three files were received:

- `SHA256SUMS`
- `maops-docker-platform-1.0.0.spdx.json`
- `trivy-1.0.0.json`

Published `SHA256SUMS` content (basenames only):

```
77d2061172783e39f501e5a83a071a36264f6331c592aca14f6f3160d37603f3  maops-docker-platform-1.0.0.spdx.json
fe750db3f8d72fd6cf74f811dd8d84f2b85259f7eddd4b3f88670091a43487b6  trivy-1.0.0.json
```

Running the standard, unmodified verification command against the
downloaded flat directory:

```
$ sha256sum -c SHA256SUMS
maops-docker-platform-1.0.0.spdx.json: OK
trivy-1.0.0.json: OK
```

**This is the definitive post-publication closure evidence for the
Day 6 `SHA256SUMS` release-consumer defect (DAY6-POST-M1).**

### Explicit contrast with `v0.6.0`

`v0.6.0`'s asset bytes/hashes were always valid (independently confirmed
in `docs/engineering-reviews/day-06-post-release-verification.md` §5),
but its `SHA256SUMS` contained CI-internal nested workspace paths
(`release-evidence/sbom/...`, `release-evidence/security/...`) and
therefore could not be consumed directly from GitHub's flat release
download layout — a real consumer running the literal, unmodified
`sha256sum -c SHA256SUMS` command would have seen it fail on path
lookup, not on hash mismatch.

`v1.0.0` fixes this: `SHA256SUMS` now records basenames only, and this
section proves the *actual downloaded consumer experience* — not a
re-derived or synthetic check, but a real `gh release download` into an
empty directory followed by the literal standard command, both of which
succeeded unmodified. DAY6-POST-M1 is closed by this evidence.

---

## 8. Tag integrity

| Item | Value |
|---|---|
| Local annotated tag object | `c1dfce4870c5848fa8a69c1a5f6d7842ba0d39b6` |
| Remote annotated tag object | `c1dfce4870c5848fa8a69c1a5f6d7842ba0d39b6` |
| Remote dereferenced tag | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Local `main` | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Remote `main` | `45614d1d2b845ef27a3a3a61ae086a45a048ae61` |
| Working tree before this evidence | clean |

The local-side values (tag object, `main` HEAD, clean working tree) were
independently re-verified in this session (§1). The remote-side values
are taken as given, previously verified release evidence per this task's
brief — this session had no authenticated remote access to
independently re-query `origin`. Local and given-remote values agree in
full: the released tag points exactly to the reviewed, merged, `main`
release commit, with no divergence anywhere in the chain.

---

## 9. Day 7 finding status

This section summarizes the independent review / remediation / final
adjudication record already present in this repository (five independent
Day 7 reviews plus
`docs/engineering-reviews/day-07-v1.0-release-readiness.md`, the final
adjudication). It does not re-derive new findings — it reports the
already-recorded dispositions.

- **No Critical release-blocking finding remained** across any of the
  five independent Day 7 reviews, before or after remediation.
- **No High release-blocking finding remained** across any of the five
  independent Day 7 reviews, before or after remediation.
- **DAY7-REL-M1** (a failed `docker unpause` during the reliability
  pause proof could unconditionally clear `state_is_paused`, risking a
  hung/leaked teardown) — **remediated before release**: routed through
  a dedicated `_unpause_state_container` helper that only clears the
  flag on a genuine success, with a bounded second attempt in the outer
  teardown `finally`; covered by dedicated deterministic tests; real
  `reliability_check.py` remains 32/32 PASS.
- **DAY7-OPS-M1** (the Day 6 accepted-debt table omitted the
  originally-assigned severities for F-2/F-3/F-4) — **remediated before
  release**: the table now explicitly labels each row with its
  historical severity (F-2 Medium, F-3 Medium, F-4 Low); their
  `ACCEPTED, still open` dispositions are unchanged — this was a
  ledger-completeness fix, not a re-adjudication.
- **DAY7-RELENG-L1** (the release workflow's existing-release-clobber
  guard had no automated regression check) — **remediated before
  release**: `scripts/ci/check_workflows.py` gained a dedicated
  `check_no_release_clobber` policy check (workflow-policy checks went
  from 13 to 14, both PASS), backed by a dedicated test class covering
  the valid case, a missing-guard case, a `--clobber`-flag case, and a
  false-positive-avoidance case.
- **All small Day 7 documentation findings selected for remediation
  were closed before release**: DAY7-OPS-L1 (coverage-mapping ownership
  split made explicit), DAY7-ARCH-L1 (stale test-count corrected),
  DAY7-SEC-L1 (`[A]`/`[B]`/`[C]`/`[D]` evidence-tier labels added to the
  patch-lifecycle section), DAY7-RELENG-I1 (redundant trailing
  `docker compose config` render removed from `release-check`).

### DAY6-POST-M2 / DAY7-REL-M2 — evidence-nuance preserved exactly

**Status: CODE-LEVEL CLOSED — LIVE RECURRENCE CONFIRMATION PENDING.**
This is **not** silently converted to fully CLOSED by this record, and
must not be by any future one either, until the specific evidence
described below actually occurs.

- The classifier (`_is_transient_cgroup_update_race`) supports both
  historically observed transient post-restart cgroup-v2
  resource-file-disappearance signatures: `cgroup.controllers` (Day 6,
  GitHub run `32960673438`) and `memory.max` (Day 6 post-release
  evidence-commit run `33059581018`).
- Positive and negative deterministic unit tests prove the classifier's
  behavior precisely: it requires the literal `runc did not terminate
  successfully` phrase, a genuine `openat2 ... : no such file or
  directory` ENOENT match, a `/cgroup/` path-context segment, and an
  explicit non-wildcard filename allowlist — unrelated runc/Docker
  errors are proven not to match.
- Real reliability validation (`make reliability-check`, 32/32 PASS)
  passes against real Docker behavior.
- Neither the final local validation session nor the release CI runs
  cited in this document (§2-§5) necessarily produced another genuine
  transient cgroup-update race during their own Scenario 2 execution —
  the retry branch simply was not naturally exercised by those
  particular runs.
- A future, naturally occurring GitHub-hosted-runner recurrence of
  either accepted signature remains the one piece of useful, real
  `[D]`-style confirmation that would upgrade this item to unqualified
  CLOSED.
- **No recurrence should ever be manufactured merely to close this
  note.** Fabricating one would produce evidence that looks like `[D]`
  tier but is not — it would prove nothing about the classifier's
  behavior under genuine, unprompted runner conditions.
- This caveat did **not** block `v1.0.0`. It is an evidence-tier
  limitation on code that is independently confirmed correct and
  thoroughly tested, not an unresolved functional or security defect.

### Historical accepted debt remains visible

`v1.0.0` does **not** claim zero technical debt. The following
historical, narrow, non-blocking items remain open and visible in
`docs/production-readiness.md`'s ledger, each with its correct original
severity (per DAY7-OPS-M1's ledger-completeness fix):

- **F-2 (Medium)** — `check_no_manufactured_pass()` only matches a
  literal `\|\| true` masked-failure pattern; a differently-formatted
  idiom would not be caught. Accepted, still open.
- **F-3 (Medium)** — `check_no_registry_publication()` uses a fixed
  allowlist rather than exhaustive registry-publish detection. Accepted,
  still open.
- **F-4 (Low)** — `check_required_triggers()` does not reject an
  arbitrarily broadened tag-trigger pattern. Accepted, still open;
  hygiene-only, independent of the actual publish-permission guard.
- Additional carried Day 1-6 accepted items (the Day 5 resource-limit
  lower-bound gap, `check_trivy_report.py`'s malformed-JSON-shape/
  case-sensitivity gaps, `image_audit.py`'s `/app/`-only immutability
  probe scope, and the Docker-Desktop-specific `RestartCount`-reset
  documentation scope) remain accepted for the same reasons the Day 7
  production-readiness review found sufficient — each is narrow,
  honestly scoped in its own documentation, and mitigated by an adjacent
  gate later in the same validation chain.

None of this debt is release-blocking, and none of it is claimed to be
resolved by `v1.0.0`.

---

## 10. Screenshot evidence

All twelve screenshots listed in this task's brief were verified present
under `docs/images/day-07/` in this session:

| File | Size (bytes) | What it proves |
|---|---|---|
| `01-pr7-final-ci-green.png` | 53,463 | PR #7's final CI run (run `33307569947`) green, both Quality and Release policy jobs PASS, immediately pre-merge. |
| `02-pr7-merged.png` | 83,612 | PR #7 shown merged into `main`, corroborating the merge commit `45614d1d2b845ef27a3a3a61ae086a45a048ae61` and merge timestamp `2026-08-30T11:04:06Z`. |
| `03-main-ci-green.png` | 51,507 | The automatic `push`-triggered CI run on `main` (run `33307992295`) for the merge commit, green, no visibility gap. |
| `04-release-dry-run-green.png` | 57,863 | The pre-tag `workflow_dispatch` dry run (run `33308284528`) overall SUCCESS. |
| `05-release-dry-run-publish-skipped.png` | 50,867 | The same dry run's job detail showing `Validate` PASS and `Publish GitHub Release` explicitly SKIPPED — proving the non-publishing dry-run contract. |
| `06-v100-tag-release-run.png` | 61,753 | The real tag-triggered release workflow run (run `33309078803`) on head branch `v1.0.0`, `Validate` and `Publish GitHub Release` both PASS. |
| `07-v100-github-release.png` | 69,733 | The published GitHub Release page: name `maops-docker-platform v1.0.0`, tag `v1.0.0`, draft `false`, prerelease `false`. |
| `08-v100-release-assets.png` | 51,768 | The exact three published release assets and their listed sizes. |
| `09-v100-consumer-checksums.png` | 30,309 | The downloaded `SHA256SUMS` content, showing basename-only entries (the DAY6-POST-M1 fix). |
| `10-v100-consumer-verification.png` | 24,487 | The literal `sha256sum -c SHA256SUMS` command run against the freshly downloaded flat directory, both entries `OK`. |
| `11-v100-tag-commit-integrity.png` | 31,332 | The annotated tag object `c1dfce487...` dereferencing to commit `45614d1d2...`, confirming the tag/commit relationship is not confused. |
| `12-v100-main-tag-integrity.png` | 32,276 | Local/remote `main` and the dereferenced tag all agreeing on `45614d1d2b845ef27a3a3a61ae086a45a048ae61`. |

No screenshot was missing; none is invented or assumed beyond what its
filename and this session's file-existence check support.

---

## 11. Immutability

**`v1.0.0` must remain immutable.** This record does not, and future
work must not, without explicit separate authorization:

- force-move the `v1.0.0` tag,
- delete/recreate the `v1.0.0` tag,
- delete/recreate the `v1.0.0` GitHub Release,
- clobber any published `v1.0.0` release asset, or
- silently rewrite the published `SHA256SUMS`.

`v0.6.0` (commit `eb043b4e9a62df8717399c9ab136fb722dc9bd0b`) remains
separately immutable and is untouched by this record.

---

## 12. Final release status

Based on the evidence assembled in this document:

- **`v1.0.0` release is valid.** PR #7 CI, merged-main CI, the
  non-publishing dry run, and the real tag-triggered release workflow
  all independently passed against the same commit,
  `45614d1d2b845ef27a3a3a61ae086a45a048ae61`.
- **The tag is immutable and correctly targeted.** The annotated tag
  object (`c1dfce4870c5848fa8a69c1a5f6d7842ba0d39b6`) dereferences to
  exactly that commit, matching local `main`, given remote `main`, and
  the given remote dereferenced tag with no divergence.
- **GitHub Release publication succeeded.** The release is published,
  not a draft, not a prerelease, with exactly the three intended assets
  at the sizes recorded in §6.
- **Actual consumer checksum verification succeeded.** A real
  `gh release download v1.0.0` into a fresh directory, followed by the
  literal, unmodified `sha256sum -c SHA256SUMS` command, passed for both
  non-`SHA256SUMS` assets — closing DAY6-POST-M1 with real, not
  re-derived, evidence.
- **Project 3 can be frozen once this evidence document is committed and
  its resulting `main` CI run is confirmed green.** That commit and its
  CI have not yet occurred as of this document's creation — this
  document does not claim Project 3 is already frozen, only that every
  release-validity condition checked here currently holds.
- One evidence-tier item remains explicitly open and non-blocking:
  DAY6-POST-M2 / DAY7-REL-M2, CODE-LEVEL CLOSED with live-recurrence
  confirmation pending (§9). This repository does not claim zero
  technical debt.

---

DAY 7 v1.0.0 POST-RELEASE VERIFICATION COMPLETE
