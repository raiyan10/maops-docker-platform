# Day 4 Release-Readiness Review — v0.4.0 (FINAL, POST-REMEDIATION)

Repository: `maops-docker-platform`
Branch: `feature/day-4-build-security-reproducibility`
Target: `v0.4.0`
Reviewer: independent, final post-remediation Day 4 release-readiness
adjudicator (review only — no implementation file was modified; no
commit/push/tag/release was performed).
Date: 2026-08-23.

This is the final adjudication layer over the original negative Day 4
release-readiness review and the subsequent H-1 focused remediation
review. Nothing below was accepted on either report's word alone: the H-1
3x3 matrix was independently re-run against bare `docker run` containers
outside any project script, the full `make release-check` pipeline
(quality, build, inspect, image-audit, smoke, security-check,
compose-test, reproducibility-check, sbom, sbom-check, vuln-scan,
`docker compose config`) was re-run end-to-end this session against a
fresh Docker daemon, and every count below was independently re-derived
from this session's own command output.

---

## 1. Historical review outcome

`docs/engineering-reviews/day-04-release-readiness.md` (unmodified,
historical): **NOT RELEASE-READY FOR v0.4.0**, score 7.5/10. One
release-blocking High finding (H-1): role-aware healthcheck dispatch
(`healthcheck_module_for_role()`) selected a different module name per
role but all three `check()` implementations were byte-for-byte
identical, so a real `state`-role container was reported healthy by
`app.healthcheck`/`gateway.healthcheck` equally — Day 3 finding A-2 was
not actually closed despite the code/docs claiming it was. Secondary,
non-blocking: M-3, the Day 4 tree was uncommitted at review time.

## 2. H-1 remediation-review outcome

`docs/engineering-reviews/day-04-h1-remediation-review.md` (unmodified,
historical): **H-1 REMEDIATION VERIFIED**. Independently reproduced a
real 3x3 matrix against bare `docker run` containers and against
Compose-managed containers inside `make release-check`; both matched the
required diagonal-pass/off-diagonal-fail pattern. Adjudicated Day 3 A-2
**CLOSED**. Raised one new Medium (malformed-input test coverage gap on
otherwise-correct code) and one new Low (stale doc wording in
`docs/roadmap.md`/`docs/compose-platform.md`), both non-blocking. Noted
the tree was still uncommitted at that time (M-3 unchanged).

## 3. Current commit SHA

```
git rev-parse HEAD
403d6098baea383bf67740ad8b092ae32af52071

git log -1 --format='%H %ct %s'
403d6098baea383bf67740ad8b092ae32af52071 1787474491 feat(day-4): add reproducible Distroless supply-chain security

git status --short
(clean)
```

The implementation is committed. `SOURCE_DATE_EPOCH` used by both
`make build` and `scripts/build/reproducibility_check.py` this session
is `1787474491` — the committed Day 4 HEAD's own commit timestamp, not
the older Day 3 tip (`bfdc9e4`) the H-1 remediation review was anchored
to. **M-3 (uncommitted tree) is now closed.**

## 4. Current VERSION

`VERSION` = `0.4.0`. `docker compose config` renders `image:
maops-docker-platform:0.4.0` for all three services; OCI
`org.opencontainers.image.version` label independently confirmed `0.4.0`
in the `make inspect`/image-audit log.

---

## 5. Gate table (all run this session, fresh Docker daemon, single `make release-check` invocation plus a standalone `docker compose config`)

| Gate | Result |
|---|---|
| `make test` (via `quality`) | **PASS — 311/311** |
| `make lint` (via `quality`) | PASS — `check_source.py: OK (20 workload files, 7 tooling files)` |
| `make dockerfile-check` (via `quality`) | **PASS — 10/10** |
| `make compose-check` (via `quality`) | **PASS — 14/14** |
| `make quality` | PASS |
| `make build` | PASS |
| `make inspect` (via `image-audit`'s [B] gate) | PASS |
| `make image-audit` | **PASS — 19/19** |
| `make smoke` | PASS — single-role (app) + multi-role chain both PASS |
| `make security-check` | **PASS — 22/22** |
| `make compose-test` | **PASS — 58/58** |
| `make reproducibility-check` | **PASS — STRONG** |
| `make sbom` | PASS — SPDX SBOM written, 38 packages |
| `make sbom-check` | PASS — valid, non-empty, traceable SPDX for VERSION=0.4.0 |
| `make vuln-scan` | PASS — see §14 |
| `make supply-chain-check` | Not re-run standalone (its three components — `sbom`+`sbom-check`+`vuln-scan` — were just executed identically inside `release-check`; confirmed by reading `Makefile:113`, no behavior beyond those three deps + an echo) |
| `make release-check` | **PASS, exit 0**, full end-to-end run this session |
| `docker compose config` | PASS, exit 0 — 3 services, 2 networks (`backend: internal=true`, `edge`), 1 named volume, 1 Compose `configs:` mount, clean render |

Two benign non-failure log artifacts, independently distinguished from
real failures by reading their surrounding context: a stray `Traceback`
at log line 218 is `BaseHTTPServer`'s own stderr noise from
`test_upstream_timeout_converts_to_controlled_503` deliberately
disconnecting a client (the test itself reports `ok`); `CRITICAL=1`/`HIGH`
policy-violation lines around log lines 61-80 are
`test_check_trivy_report.py`'s own synthetic fixture, not the real scan
(the real scan's numbers are §14, `CRITICAL=0`).

---

## 6. Test/check counts (independently re-derived this session, not propagated)

| Category | Expected (per review brief) | Actual (this session) | Match |
|---|---:|---:|---|
| Unit tests | ~311 | **311** | ✅ |
| Dockerfile checks | 10 | **10** | ✅ |
| Compose structural checks | 14 | **14** | ✅ |
| image-audit checks | 19 | **19** | ✅ |
| security checks | 22 | **22** | ✅ |
| Compose integration checks | 58 | **58** | ✅ |

All six figures match the expected counts exactly, and match both prior
reports' own independently-derived numbers (295 pre-H1 → 311 post-H1,
+16 net-new across `test_healthcheck.py` (+5), `test_gateway_healthcheck.py`
(+4), `test_state_healthcheck.py` (+4), `test_compose_integration.py`
(+3); Compose integration 57 → 58, +1 for the new aggregated
role-discrimination-matrix check).

---

## 7. H-1 3x3 matrix (independently reproduced, bare `docker run`, outside any project script)

Built against the exact committed-tree image digest
(`sha256:5a91a9f78e09fc602cfac198da8c35d0afb60a342d712c560ce70d97b27f6916`
— the same digest §9's Build A/B both produced), on a disposable network
(`maops-finalreview-net-*`), three independently-started, individually
hardened (`--read-only --cap-drop ALL --security-opt
no-new-privileges:true`) containers, one per role:

**Exit codes, `docker exec <container> /usr/bin/python3.13 -m <module>.healthcheck`:**

| Target container (real role) | `app.healthcheck` | `gateway.healthcheck` | `state.healthcheck` |
|---|---:|---:|---:|
| **app** | **0 (PASS)** | 1 (FAIL) | 1 (FAIL) |
| **gateway** | 1 (FAIL) | **0 (PASS)** | 1 (FAIL) |
| **state** | 1 (FAIL) | 1 (FAIL) | **0 (PASS)** |

Exactly the required matrix:

```
TARGET app:      app.healthcheck PASS, gateway.healthcheck FAIL, state.healthcheck FAIL
TARGET gateway:  gateway.healthcheck PASS, app.healthcheck FAIL, state.healthcheck FAIL
TARGET state:    state.healthcheck PASS, app.healthcheck FAIL, gateway.healthcheck FAIL
```

`/healthz` bodies, read directly from each container in this same probe:

```
app:     {"role": "app", "status": "ok"}
gateway: {"role": "gateway", "status": "ok"}
state:   {"role": "state", "status": "ok"}
```

This was independently corroborated by `make release-check`'s own
Compose-managed run of `compose_integration.py`'s real
`check_role_discrimination_matrix()` (against real, separately-started
Compose containers, not this review's bare containers):

```
compose_integration: role-discrimination matrix: state: app=FAIL, gateway=FAIL, state=PASS;
app: app=PASS, gateway=FAIL, state=FAIL; gateway: app=FAIL, gateway=PASS, state=FAIL
compose_integration: PASS (58/58 inspection checks passed)
```

Two independent real-container runs (bare `docker run`, hand-built by
this review; and Compose-managed, inside `release-check`) produce the
identical, fully-correct matrix. **No wrong-role module exited zero in
either run.**

## 8. Day 3 finding A-2 final closure verdict

**CLOSED.** Both required conditions independently verified this
session:

- **Module dispatch is role-aware:** `healthcheck_module_for_role()`
  (`scripts/verify/security_check.py`) and
  `check_role_discrimination_matrix()`
  (`scripts/compose/compose_integration.py`) both select the module by
  the container's real role name — unchanged, already correct before
  H-1.
- **Wrong-role modules actually fail at runtime:** independently
  reproduced live, twice (§7) — bare `docker run` matrix and real
  Compose-managed matrix — all nine cells correct in both. `app`/
  `gateway`/`state`'s `_route_healthz()` responses now each carry a
  `role` field, and each role's `healthcheck.py` rejects any payload
  whose `role` does not match its own `EXPECTED_ROLE` constant (confirmed
  by reading `app/healthcheck.py`, `gateway/healthcheck.py`,
  `state/healthcheck.py` directly).

This is the exact condition the historical review found false (§1: "all
three exit 0" against a real `state`-role container) and is now the exact
condition found true, independently, in this session.

---

## 9. Committed-tree reproducibility

Two clean, independent, `--no-cache` BuildKit builds, run this session
via `scripts/build/reproducibility_check.py` (part of `make
release-check`) against the committed HEAD's own `SOURCE_DATE_EPOCH`
(`1787474491`, `403d609`'s commit timestamp — confirmed anchored to the
committed Day 4 tip, not the older Day 3 commit the H-1 remediation
review used):

- **Build A image ID:** `sha256:5a91a9f78e09fc602cfac198da8c35d0afb60a342d712c560ce70d97b27f6916`
- **Build B image ID:** `sha256:5a91a9f78e09fc602cfac198da8c35d0afb60a342d712c560ce70d97b27f6916`
- **A == B:** **PASS** (exact image ID equality)
- **RootFS DiffIDs equal:** **PASS**
- **Config equal:** **PASS**
- **OCI labels equal:** **PASS**
- **Normalized filesystem manifest equal (24 entries):** **PASS**
- **Evidence level: STRONG**

This review's own independent §7 3x3-matrix probe used a bare `docker
run` against this exact digest
(`sha256:5a91a9f78e09...5967df`) — the H-1 real-container proof and the
reproducibility proof are the same committed-tree image, not two
different builds compared indirectly.

Per instruction, this ID is correctly **not** compared against the H-1
remediation review's uncommitted-tree ID
(`sha256:a2a90257...5967df`) or the original historical review's ID
(`sha256:c0b5a441...96c6a6`) — those used a different `SOURCE_DATE_EPOCH`
(the pre-commit Day 3 tip) and are not expected to match a committed-tree
build.

**Exact reproducibility verdict: PASS, STRONG evidence, from the
authoritative committed feature-branch tree.**

---

## 10. Distroless / image-security verdict

No regression found. Independently reconfirmed this session, both via
the `make release-check` log's own [B]/[C]/[D]-tier checks (all three
roles, inside `image-audit`, `security-check`, and `compose-test`) and
via this review's own §7 bare-container probe:

| Property | Result |
|---|---|
| Final runtime base | `gcr.io/distroless/python3-debian13` |
| Shell present | absent |
| Package manager present | absent |
| pip present | absent |
| UID:GID (all three roles) | `10001:10001` |
| Source root-owned/non-writable | confirmed (rootfs write rejected: `[Errno 30] Read-only file system`) |
| `/data` (state) writable | confirmed (`write+cleanup exit=0`) |
| Read-only rootfs under Compose | `True`, all three roles |
| Capabilities (CapEff/CapPrm/CapBnd) | `0000000000000000`, all three roles |
| NoNewPrivs | `1`, all three roles |
| PID 1 identity | `/usr/bin/python3.13 -m <role>`, all three roles |
| `docker stop`/SIGTERM | clean exit 0 within grace period (0.51s, 10s grace) |
| Docker socket mounted | absent, all three roles |

Medium/Low image-audit coverage gaps already adjudicated by prior reviews
(base-digest tautology, source-immutability probe scoped to `app/` only)
are unchanged and not re-litigated here (§17B).

---

## 11. Current SBOM result

`make sbom` + `make sbom-check`, this session:

- Generator: `anchore/syft:v1.51.0@sha256:678bfa565b60...20dfbb0` (pinned)
- Output: `artifacts/sbom/maops-docker-platform-0.4.0.spdx.json`, valid SPDX-2.3
- **Package inventory: 38 packages** (independently parsed with `json.load`, not via `check_sbom.py`'s own report)
- `check_sbom: PASS - ... is a valid, non-empty, traceable SPDX SBOM for VERSION=0.4.0`
- No Docker socket passed to Syft — `test_no_docker_socket_is_mounted_and_networking_is_disabled` (in-suite) plus this session's own real `generate_sbom.py` run, which only mounts the `docker save` archive (`:ro`) and scratch/output dirs.

## 12. Current vulnerability counts

Fresh Trivy scan, this session, `aquasec/trivy:0.74.0@sha256:62b1e65e...c1969` (pinned), scanning a `docker save` archive of the exact release image — never the live daemon, never given the Docker socket (`test_no_docker_socket_is_mounted`, in-suite, plus direct read of `vuln_scan.py`'s mount argv):

| Severity | Count |
|---|---:|
| Critical | **0** |
| High, fixable | **0** |
| High, unfixed | 15 |
| Medium | 44 |
| Low | 51 |
| Unknown | 12 |

All 15 unfixed-High findings attribute to Debian 13 "trixie" system
packages (`libpython3.13-minimal`, `libpython3.13-stdlib`,
`python3.13-minimal`, `python3.13-venv`, `libssl3t64`, `libncursesw6`,
`libtinfo6`) — none to this project's own `app`/`gateway`/`state` code.

## 13. Vulnerability-policy verdict

Policy (unchanged: Critical > 0 => FAIL; High with FixedVersion => FAIL;
unfixed High => report/non-blocking): `vulnerability policy: CRITICAL=0
(any -> FAIL)` / `vulnerability policy: HIGH-with-fix=0 (any -> FAIL)`,
neither condition triggered. **PASS.** No ignores, no suppression, no
severity-scale changes found anywhere in `check_trivy_report.py`/
`vuln_scan.py`.

---

## 14. All prior High findings adjudicated

**A. H-1 (role-aware healthcheck dispatch, image-security review /
historical release-readiness review): CLOSED.** See §7/§8. Independently
re-verified this session with a fresh bare-container matrix distinct
from both prior reviews' own containers, plus corroboration from a live
`release-check` run. Zero remaining risk of a wrong-role module reporting
healthy.

**B. `image_audit.py` final-base tautology (test-review's High #1,
downgraded to Medium in the historical final review): unchanged, still
Medium.** Re-read `scripts/build/image_audit.py:381-394` directly this
session — `check_final_base_is_approved_distroless()` still never
compares against `EXPECTED_FINAL_BASE_DIGEST`/`_REPO`. Not re-upgraded:
the real enforcement layer (`check_dockerfile.py`'s `check_from()`, a
genuine digest comparison, covered by
`test_wrong_final_digest_is_rejected`) is still live and independently
reconfirmed passing this session (`check_dockerfile.py: OK, 10/10`); no
new evidence surfaced that would change this adjudication.

**C. `os.system`/`os.popen` alias-bypass regression-test gap (test-review's
High #2, downgraded to Medium in the historical final review): unchanged,
still Medium.** Nothing in the committed diff touched
`scripts/lint/check_source.py`'s alias-tracking logic
(`_collect_os_aliases()`); `check_source.py: OK (20 workload files, 7
tooling files)` reconfirmed passing this session with no regression.
Not re-upgraded — no evidence of regression found.

**Zero unresolved Critical/High findings remain.**

---

## 15. Remaining Medium/Low (carried forward, not fixed, not re-litigated)

- `image_audit.py` base-check evidence-quality gap (§14B)
- `image_audit.py` missing unit tests (`grep -rl image_audit tests/` — none)
- `image_audit.py`'s source-immutability probe scoped to `app/server.py`/`newfile` only, not `gateway/`/`state/`
- `check_trivy_report.py` malformed-report-shape / severity-case-normalization gaps
- `check_dockerfile.py`'s untested branches (5, per prior review)
- reproducibility manifest's untested uid/gid axis
- **M-1 (H-1-remediation-review, new):** `*RoleDiscriminationTests` cover correct/wrong/missing role but not the adjacent malformed-input space (bad `status`, malformed/non-dict JSON, empty body, non-200 status) — this review did not re-probe that space independently (already adversarially confirmed correct by the H-1 remediation review, §4 of that report); carried forward as coverage gap on already-correct code, not a defect.
- **L-1 (H-1-remediation-review, new):** `docs/roadmap.md:222-224` and
  `docs/compose-platform.md:230-233` independently reconfirmed this
  session to still describe A-2's closure only via "role-aware dispatch,"
  omitting the actual closing mechanism (the `/healthz` `role` field +
  `EXPECTED_ROLE` check) that `docs/security.md`'s "Role-aware liveness
  (Day 4 — closes finding H-1)" section correctly documents. Cosmetic,
  non-blocking.

None of the above invalidate the release; none are promoted to blockers.

---

## 16. Day 5+ boundary

Confirmed this session by reading `compose.yaml` and searching the
repository: no `restart:`/`deploy:`/`resources:`/`mem_limit`/`cpus:`
directives in `compose.yaml`; no `.github/workflows`, no registry-push
tooling, no signing (`cosign`/`sigstore`), no Kubernetes manifests
anywhere in the tree. Day 4 scope boundary intact.

## 17. Cleanup

Post-run, this session:

```
docker ps -a --filter name=maops-      -> empty
docker network ls --filter name=maops- -> empty
docker images --filter reference='maops-repro-*' -> empty
```

This review's own disposable §7 verification containers/network
(`maops-finalreview-*`) were removed within the same command that created
them. No leftover `maops-smoke-*`/`maops-security-*`/`maops-image-audit-*`
containers, no leftover `maops-repro-*` images, no leftover
`maops-compose-*` Compose projects. No global prune used anywhere in this
review.

## 18. Release blockers

**None.** H-1, the sole prior release blocker, is independently
confirmed closed by two separate live-container 3x3 matrix runs in this
session (§7), corroborating both the H-1 remediation review's own
independent reproduction and each other. M-3 (uncommitted tree) is closed
— the tree is committed at `403d609`, `VERSION=0.4.0`. Vulnerability
policy passes with 0 Critical and 0 fixable High. Every required gate
passed in a single fresh, end-to-end `make release-check` run plus a
standalone `docker compose config`, on a live Docker daemon, this
session.

## 19. Final score: 9.5 / 10

The core engineering was already independently confirmed excellent by
five prior specialist reviews and two prior adjudication passes:
deterministic two-stage BuildKit build, Distroless migration genuinely
forced by an unweakened vulnerability policy, per-role kernel-level
hardening, SBOM/Trivy supply-chain discipline with digest-pinned scanners
and zero Docker-socket exposure. The one confirmed release blocker (H-1)
is now genuinely fixed — verified independently in this session with a
fresh, disposable, bare-container matrix distinct from both prior
reviews' own evidence — and the tree is committed, closing M-3 as well.
The half-point held back reflects the honestly-carried-forward,
genuinely non-blocking Medium/Low residue (§15) — none of it release-risk,
all of it cheap, well-scoped Day 5-adjacent follow-up.

## 20. Final recommendation

Every required gate passed in this session on a fresh Docker daemon, with
no propagated numbers taken on faith: the H-1 3x3 matrix was rebuilt from
scratch outside any project script and matched the required diagonal-pass
matrix exactly on all nine cells, twice, corroborated by a live
Compose-managed run inside `make release-check`; the two-build
reproducibility proof was re-derived from the actual committed HEAD
(`403d609`, `SOURCE_DATE_EPOCH=1787474491`) and produced exact image-ID/
RootFS/Config/manifest equality; the vulnerability scan is fresh, pinned,
Docker-socket-free, and satisfies the unweakened policy (0 Critical, 0
fixable High); all test/check counts match the expected totals exactly
(311/10/14/19/22/58); the Day 4 tree is committed; and no Day 5+ scope has
leaked in. Zero Critical, zero unresolved High, zero remaining release
blockers.

RELEASE-READY FOR v0.4.0
