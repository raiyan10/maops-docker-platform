# Day 4 Independent Supply-Chain Review — v0.4.0

**Reviewer role:** independent Day 4 software supply-chain reviewer (review only — no implementation files modified).
**Scope:** `security/scanners.lock`, `scripts/security/*.py`, `docs/supply-chain.md`, `docs/build-security.md`, generated artifacts, and the `make sbom` / `make sbom-check` / `make vuln-scan` / `make supply-chain-check` / `make release-check` gates.

## Severity counts for review findings

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 1 |
| Informational (environment, not code) | 1 |

## Scanner-lock verdict: PASS

`security/scanners.lock` pins both scanners as exact `tag@sha256:<64-hex>` references:

- `SYFT_IMAGE=anchore/syft:v1.51.0@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0`
- `TRIVY_IMAGE=aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`

Both digests are exactly 64 lowercase hex characters (independently counted). `scripts/security/scanner_lock.py`'s parser was adversarially challenged beyond its own test suite with 10 synthetic malformed cases: digest-only (no tag), uppercase hex digest, 65-char digest, 63-char digest, `sha512:` algorithm, a tag substring merely containing "latest" (`:latestx`, correctly *accepted* — only the literal `:latest` suffix is rejected), a second `@` in the value, an empty value, and a line with no `=`. Every genuinely malformed case was correctly rejected; the one edge case that was accepted (`:latestx`) is correct behavior, not a gap. The existing unit suite (`tests/test_scanner_lock.py`, 10 tests) also passes and independently re-validates the real shipped lock file.

## Scanner-pin verdict: PASS

Both pinned images are already present locally, letting the pin be checked against real registry-resolved content rather than trusting the lock file's own comment:

```
anchore/syft:latest    678bfa565b60   116MB
anchore/syft:v1.51.0   678bfa565b60   116MB   <- same image ID as :latest
aquasec/trivy:0.74.0   62b1e65e8869   254MB
aquasec/trivy:latest   62b1e65e8869   254MB   <- same image ID as :latest
```

Local image IDs match the locked digests exactly and confirm the lock file's claim that the pinned tag and `:latest` resolved identically on 2026-08-20. No stale or incorrect scanner identity found.

## Docker-socket isolation: PASS

Read `generate_sbom.py` and `vuln_scan.py` directly (not the docstrings): neither constructs a `docker run` argv containing `docker.sock` or any daemon-socket path. Both mount only: the `docker save` archive (`:ro`), a scratch/cache directory, and an output directory. `tests/test_generate_sbom.py` / `tests/test_vuln_scan.py` mock `subprocess.run` and assert `docker.sock` is absent from argv and the archive mount ends in `:ro` — re-ran both (4 tests), all pass. Independently re-confirmed by running the real pipeline: `docker save` succeeded (22,489,600-byte archive), and the eventual failure (below) was a cache-directory *write* permission error inside the scanner container, never a socket-related error — consistent with the archive-only design.

## SBOM validity: PASS (independently re-derived, not a fresh scan — see Required Gates)

Parsed `artifacts/sbom/maops-docker-platform-0.4.0.spdx.json` directly with `json.load`, not through `check_sbom.py`:

- Valid JSON; `spdxVersion` = `SPDX-2.3`.
- `packages`: **38 entries, 38 unique names, zero duplicates** — independently counted, not trusting the "38" in `docs/build-security.md`. This matches the documented figure exactly.
- Python-identity packages present: `libpython3.13-minimal`, `libpython3.13-stdlib`, `python3.13-minimal`, `python3.13-venv` (dpkg-status-derived, consistent with the Distroless `python3-debian13` claim).
- `creationInfo.creators` = `["Organization: Anchore, Inc", "Tool: syft-1.51.0"]` — real Syft tool traceability.
- `DESCRIBES` relationship present (`SPDXRef-DOCUMENT -> SPDXRef-DocumentRoot-Image--input.tar`); 2,251 `CONTAINS` relationships.
- No `/home/`/`/Users/` path leakage found by independent grep; no secret-shaped strings (private-key headers, AKIA-prefixed keys, `password=`/`secret=`) found by independent grep.
- Identity to `maops-docker-platform:0.4.0` is **filename + package-content plausibility only** — no `RepoTag`/`RepoDigest`/`imageID` string appears anywhere in the document (independently grepped, zero hits). This is exactly what `docs/build-security.md` and `check_sbom.py`'s own docstring already disclose honestly (Syft's SPDX `versionInfo` digest is computed from the archive's config blob, not `docker image inspect .Id`) — not a hidden gap, an accurately-labeled weaker-than-cryptographic identity signal.
- No individual SPDX File entries were required or expected for `app/`/`gateway`/`state`'s plain `.py` modules (no `dist-info`/`egg-info`), matching ordinary Syft cataloger behavior — correctly not treated as a defect.

`make sbom-check` (pure Python, no Docker) run directly against this file: **PASS**.

## Trivy / vulnerability-policy: PASS (independently re-derived, not a fresh scan — see Required Gates)

Independently re-parsed `artifacts/security/trivy-0.4.0.json` (not trusting `check_trivy_report.py`'s own summary) and recomputed severity counts from raw `Vulnerabilities[].Severity`/`FixedVersion` fields in Python:

| Severity | Independently counted |
|---|---|
| Critical | **0** |
| High (total) | **15** |
| High — fixable (`FixedVersion` set) | **0** |
| High — unfixed | **15** |
| Medium | **44** |
| Low | **51** |
| Unknown | **9** |
| Total findings | 119 |

This matches `docs/supply-chain.md`'s stated current-result numbers exactly, and matches `check_trivy_report.py`'s own `evaluate_policy()` output byte-for-byte when run directly against the same file (`python3 scripts/security/check_trivy_report.py artifacts/security/trivy-0.4.0.json maops-docker-platform:0.4.0` → same 15 CVE IDs, same buckets). `Metadata.RepoTags` = `["maops-docker-platform:0.4.0"]`, correctly satisfying `validate_report()`'s identity check. `Metadata.ImageID` (`sha256:73dfddf5...`) does **not** match the live `docker image inspect .Id` (`sha256:2dcc39a9...`) for the same image — this is the same known Docker-surface digest-mismatch phenomenon already disclosed for the SBOM; `validate_report()` correctly does not rely on `ImageID` equality, only on `RepoTags` containment, avoiding this known-unstable field.

**Vulnerability policy applied exactly as specified, with no weakening**: Critical>0→FAIL (0 present, satisfied), High-with-fix→FAIL (0 present, satisfied), unfixed High reported non-blocking (15, correctly non-blocking), Medium/Low/Unknown reported non-blocking. Policy passes on its own merits against real, current scan data — no `.trivyignore`, no suppressed CVE, no severity rewrite found anywhere in the codebase.

## Policy-checker adversarial result: PASS with one Medium and one Low finding

All 6 required discriminating tests reproduced independently (beyond the existing `tests/test_check_trivy_report.py` suite, which also passes: 12 tests):

| Test | Result |
|---|---|
| One Critical | Correctly FAILS (`policy.passed=False`) |
| One fixable High | Correctly FAILS |
| One unfixed High | Correctly policy-passes, reported | 
| Clean report | Correctly passes |
| Malformed JSON | Correctly rejected by `validate_report()` |
| Missing `Results`/`SchemaVersion` | Correctly rejected by `validate_report()` |

Additional adversarial probing beyond the required list surfaced two real gaps:

**[MEDIUM] `validate_report()`/`evaluate_policy()` do not "fail safely" on a syntactically-valid-but-wrong-shaped top-level document.** A JSON document that parses successfully but whose top level is a list or string (e.g. `[1,2,3]`, `"just a string"`) raises an unhandled `AttributeError: 'list'/'str' object has no attribute 'get'` from `validate_report()`, rather than the module's own documented behavior ("reports it plainly and exits non-zero"). Separately, a `Results` list containing a non-dict element (e.g. `[null, {...}]`) passes `validate_report()`'s `isinstance(results, list)` check with zero findings, then raises an unhandled `AttributeError` inside `evaluate_policy()` (called immediately afterward by both `vuln_scan.py` and `check_trivy_report.py`'s `main()`), instead of being caught as a validation finding. Neither case produces an incorrect PASS (an unhandled exception still exits non-zero), but neither "fails safely" with an actionable message as required by this review's adversarial test and as the module's own docstring promises. This requires a malformed/unexpected scanner-output shape to trigger and is not exercised by real, well-formed Trivy JSON — but real Trivy output is exactly the kind of external, untrusted input this checker's own docstring says it must not choke on.

**[LOW] `evaluate_policy()`'s severity comparison is case-sensitive.** A synthetic finding with `"Severity": "critical"` (lowercase) is silently bucketed into `other_counts` (non-blocking) instead of tripping the CRITICAL branch — confirmed independently: `policy.passed=True` for a lowercase-severity Critical. Real Trivy JSON always emits uppercase severity constants per its schema, so this is not presently exploitable against genuine scanner output, but the policy engine has no defense-in-depth `.upper()` normalization, which is out of step with the project's stated "never silently downgrade a finding" philosophy.

Boolean/empty-string/missing `FixedVersion` edge cases were also independently tested (`False`, `""`, key entirely absent, whitespace-only `"   "`) — all correctly collapse to "no fix" except the whitespace-only case, which is (correctly) treated as a truthy fix-version string; this matches Trivy's real behavior (empty string, never whitespace, for "no fix") and does not create an incorrect pass.

## Base comparison (python:3.13-slim vs. Distroless): PASS

`docs/build-security.md` and `docs/supply-chain.md` present the `python:3.13-slim` candidate exclusively as a rejected, historical Day 4 investigation ("was rejected as the release runtime", "Historical result... scanned 2026-08-20, rejected") — never as the active runtime, and the Dockerfile's actual final `FROM` is `gcr.io/distroless/python3-debian13:nonroot@sha256:...` (confirmed by reading the Dockerfile directly). No document claims Distroless has "zero vulnerabilities" — both docs state precisely "0 CRITICAL... 15 HIGH, none with a fixed version available... 0 fixable HIGH", which is an accurate, bounded claim, not a blanket zero-vulnerability claim.

## Network behavior: PASS

`generate_sbom.py`'s `run_syft()` passes `--network none` (confirmed by reading source) — consistent with the claim that Syft's package cataloging needs no network access. `vuln_scan.py`'s `run_trivy()` has no such flag, and this was independently confirmed to matter: a live run of `make vuln-scan` in this session successfully downloaded Trivy's ~109 MiB vulnerability DB over the network (progress reached 100%) before failing at a later, unrelated local cache-write step (see Required Gates) — real evidence that Trivy genuinely requires and uses network access, exactly as documented. `docs/supply-chain.md` explicitly and correctly distinguishes the deterministic, reproducible image build from the time-varying vulnerability database/scan results ("a later scan... may legitimately report different — typically more — CVEs... this is expected and is not evidence the image itself changed").

## Artifact hygiene: PASS

`.gitignore` ignores `artifacts/` (line 35) and `.cache/` (line 36); `security/scanners.lock` itself remains tracked, as intended. `git status` confirms `artifacts/` is untracked/ignored in this working tree. No generated SBOM or Trivy report exists anywhere under `docker/app/` or is `COPY`'d by the Dockerfile (confirmed by reading the full Dockerfile — only `app/`, `gateway/`, `state/`, `VERSION`, and `/data` are copied). No stray `docker save` tar archives or `maops-sbom-*`/`maops-vuln-*` temp directories were found under `/tmp` after either a successful or a failed run — `tempfile.TemporaryDirectory()` cleanup was independently confirmed to fire even on the failure paths exercised in this session (both scripts failed mid-run multiple times during this review with zero leftover temp directories). No stray `maops-*` containers or networks were left behind (`docker ps -a`, `docker network ls` both clean after this review's activity).

## Documentation accuracy: PASS

Cross-checked every quantitative claim in `docs/supply-chain.md` and `docs/build-security.md` against independently-parsed artifact content and source code (not against each other): scanner digests, package count (38), vulnerability counts (0/15/0/44/51/9), Docker-socket-free design, `--network none` for Syft, Trivy's real network dependency, the historical `python:3.13-slim` rejection numbers (4 Critical/38 fixable High, presented only as historical), and the current Distroless numbers — every one matched. The one place both docs are appropriately conservative rather than overclaiming is the SBOM/Trivy image-identity caveat (Syft's `versionInfo` digest and Trivy's `Metadata.ImageID` both independently confirmed in this review to disagree with live `docker image inspect .Id` — exactly as the docs already disclose, not a newly discovered gap).

## Required Gates: could not be completed to a trustworthy fresh run in this session — environment fault, not a code defect

**[INFORMATIONAL]** `make sbom`, `make vuln-scan`, `make supply-chain-check`, and `make release-check` were all actually executed in this review session and all failed — but at points that independently and consistently trace to this session's specific Docker Desktop (WSL2/`npipe`) environment, not to the reviewed scripts' logic:

- `make sbom` / `make supply-chain-check`: fails inside the pinned Syft container with `mkdir /scratch/cache: permission denied` — the scratch directory the script creates and bind-mounts (owned by the invoking host UID) is not writable by that same UID once seen from inside the container.
- `make vuln-scan`: Trivy's vulnerability-DB download over the network **succeeded in full** (108.95 MiB, reaching 100%), then failed with `mkdir /cache/db: permission denied` — the identical class of bind-mount ownership fault, on a different mount.
- Independently reproduced with a minimal `busybox` probe outside any project script: a freshly-created, host-owned directory bind-mounted with `--user $(id -u):$(id -g)` is not writable inside the container, and in one probe a bind-mount source path that demonstrably exists on the host (`ls -la` confirmed it seconds earlier) was reported by the Docker daemon as not existing at all. A separate, unrelated Day 3 script (`scripts/compose/check_compose.py`, run inside `make release-check`'s `compose-check` step) independently failed with a **different symptom of the same underlying cause**: it received a Windows-style UNC path (`\\wsl.localhost\Ubuntu\...`) instead of the expected POSIX path for a bind-mounted config file — confirming this session's Docker daemon is a Windows-hosted Docker Desktop instance reached via `npipe` with imperfect WSL2 path/UID translation, not a native Linux daemon.
- None of these failures involve the Docker socket, any code path this review found reason to distrust, or any incorrect pass — every failure is a hard, visible, correctly-propagated non-zero exit.
- `make release-check`'s **quality** stage (295 unit tests across the full test suite, plus `check_source.py` and `check_dockerfile.py`) completed and **passed in full** before the environment-specific `compose-check` failure was hit — this includes all Docker-free security/lock/policy tests exercised above.

Given this, the literal instruction to "generate a fresh SBOM"/"generate a fresh vulnerability report" could not be satisfied by invoking a new scan in this session. In its place, this review independently re-parsed and re-counted the existing, real scanner-output artifacts (`artifacts/sbom/maops-docker-platform-0.4.0.spdx.json`, `artifacts/security/trivy-0.4.0.json`, both dated 2026-08-20) directly from raw JSON — never trusting the docs' or the tooling's own summarized numbers — and cross-validated those independent counts against `check_sbom.py`/`check_trivy_report.py` run directly (both Docker-free operations, both succeeded). All independently-derived counts agreed exactly with the documented figures.

**Recommendation** (not a code blocker): re-run `make sbom`, `make vuln-scan`, and `make release-check` to a full fresh completion in a properly WSL2-integrated Docker Desktop environment (or a native Linux Docker host) before treating this specific gate-run as certified — the existing artifacts are real prior output of these exact scripts and show every control working as designed, but this session could not itself reproduce that success end-to-end.

## Release blockers

None found in the reviewed supply-chain implementation, docs, or artifacts. The two code findings above (Medium: policy-checker does not fail safely on malformed non-dict report shapes; Low: case-sensitive severity comparison) are real but do not currently produce an incorrect pass against genuine Trivy output, and are recommended for a follow-up hardening pass rather than blocking v0.4.0. The environment-level gate-execution failure in this session is a review-completeness limitation to disclose, not a repository defect to fix.

## Final supply-chain verdict

SUPPLY-CHAIN PASS
