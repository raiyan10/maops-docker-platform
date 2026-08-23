# Day 4 Release Review — v0.4.0 (Build/Image Security & Reproducibility)

**Reviewer role:** independent Day 4 Docker Build and Release Reviewer
(review only — no implementation files modified by this review).
**Reviewed:** `feature/day-4-build-security-reproducibility` working tree,
against `main`.
**Review date:** 2026-08-23.
**Image under review:** `maops-docker-platform:0.4.0`,
`Id=sha256:c0b5a441cc6b787ec24fb1877459bc337b0ff513eb581a5f3c076fa87896c6a6`.

All evidence below was independently reproduced in this review session —
every `make` target was actually executed, image/container facts were
independently re-derived with raw `docker inspect`/`docker run`/
`docker buildx imagetools inspect` calls (not merely read from the
project's own script output), and a fresh Trivy vulnerability-database
scan was run rather than relying on any previously generated report.

---

## 1. Environment note (not a repository defect)

This session's shell PATH resolves the bare `docker` command to
`~/.local/bin/docker`, a wrapper that execs `docker.exe` (Docker Desktop's
Windows-side client) rather than the WSL2-native `/usr/bin/docker`
binary. Through that wrapper, `docker compose config` renders build
contexts as Windows UNC paths
(`\\wsl.localhost\Ubuntu\home\...`) instead of POSIX paths, which made
`make compose-check` fail spuriously on the very first run of this
review. Switching `PATH` to prefer `/usr/bin/docker` (the WSL2-native
Docker Desktop integration binary, same `desktop-linux` backend) resolved
this immediately and every subsequent gate ran clean. This exact fault
class (Windows-hosted Docker Desktop reached via `npipe` with imperfect
WSL2 path/UID translation) is also independently documented in this
session's own prior `docs/engineering-reviews/day-04-supply-chain-review.md`,
which hit a related bind-mount permission variant of the same root cause.
**This is a local Docker CLI/PATH configuration artifact of this
workstation, not a defect in any reviewed script, Makefile target, or
the Dockerfile.** All results in this report were gathered with the
native Docker CLI on PATH.

---

## 2. Full gate table

Every target below was run for real in this session (not assumed from
prior artifacts). All passed.

| Gate | Result | Notes |
|---|---|---|
| `make test` | PASS | 295 tests, `OK` |
| `make lint` | PASS | `check_source.py`: 20 workload + 7 tooling files scanned, clean |
| `make dockerfile-check` | PASS | 10/10 checks against `docker/app/Dockerfile` |
| `make compose-check` | PASS | 14/14 structural checks (after the PATH fix in §1) |
| `make quality` | PASS | test + lint + dockerfile-check + compose-check |
| `make build` | PASS | deterministic BuildKit build, `--no-cache`, tagged `maops-docker-platform:0.4.0` |
| `make inspect` | PASS | full `docker image inspect`/`ls`/`history` printed and independently re-parsed |
| `make image-audit` | PASS | 19/19 checks |
| `make smoke` | PASS | single-role + multi-role chain, both PASS |
| `make security-check` | PASS | 22/22 checks, [A]/[B]/[C]/[D] evidence tiers all present |
| `make compose-test` | PASS | 57/57 inspection checks |
| `make reproducibility-check` | PASS | STRONG evidence level, exact image-ID equality |
| `make sbom` | PASS | SPDX 2.3 SBOM generated via pinned Syft |
| `make sbom-check` | PASS | valid, non-empty, traceable |
| `make vuln-scan` | PASS | fresh Trivy DB pull + scan, policy satisfied |
| `make supply-chain-check` | PASS | sbom + sbom-check + vuln-scan |
| `make release-check` | PASS | full composite, ran end-to-end to completion, ended in a clean `docker compose config` render |
| `docker compose config` | PASS | exit 0, renders without error |

`make release-check` was run twice in this session: once concurrently
with a deliberate `make clean` safety test (see §6), which caused one
expected, self-induced collision (a `make clean` invocation removed the
in-flight `maops-image-audit-*` container that concurrent run was still
using — this is `make clean`'s naming-prefix scoping working exactly as
designed, not a defect); and once cleanly on its own to full completion,
which is the PASS recorded above.

---

## 3. Release image identity — independently verified

| Claim | Verified value | Method |
|---|---|---|
| `VERSION` | `0.4.0` | `cat VERSION`; matches `README.md` |
| Image tag | `maops-docker-platform:0.4.0` | `docker image ls` |
| Image ID | `sha256:c0b5a441cc6b787ec24fb1877459bc337b0ff513eb581a5f3c076fa87896c6a6` | `docker image inspect .Id` |
| OCI `image.version` label | `0.4.0` | matches `VERSION`, checked independently |
| OCI `image.source` label | `https://github.com/raiyan10/maops-docker-platform` | matches `git remote -v` exactly |
| `Config.User` | `10001:10001` | raw `docker image inspect` |
| `Entrypoint` | `["/usr/bin/python3.13"]` (absolute) | raw `docker image inspect` |
| `Cmd` | `["-m", "app"]` | raw `docker image inspect` |
| Python version | `3.13.5` | `docker run --entrypoint /usr/bin/python3.13 ... --version` |
| Shellless | confirmed | `docker run --entrypoint /bin/sh` -> `exec: "/bin/sh": stat /bin/sh: no such file or directory` |
| Package-manager-free | confirmed | `/usr/bin/dpkg`, `/usr/bin/apt` both absent (same failure mode) |
| pip-free | confirmed | `python3.13 -m pip --version` -> `No module named pip` |
| Content size | 22,534,957 bytes (~22.5 MB) | `docker image inspect .Size` |
| Reported disk usage | 90.4 MB | `docker image ls` (includes shared base-layer accounting) |

### Base identity chain

- **Builder** — `python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`.
  Independently resolved via `docker buildx imagetools inspect --raw`:
  the digest is live and reachable, tags `python 3.13.15-slim-trixie`
  (Debian 13 "trixie"). This stage is filesystem-preparation only and
  contributes no layers to the final image (confirmed: none of the
  builder's own package layers appear in the final image's `RootFS`).
- **Distroless final base (index)** — `gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`.
- **linux/amd64 runtime manifest, resolved from that index** — independently
  confirmed via `docker buildx imagetools inspect --raw` to be exactly
  `sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c`,
  matching the Dockerfile's own documented claim verbatim.
- The runtime image's actual Python interpreter is `/usr/bin/python3.13`,
  version `3.13.5`, exactly as documented, with source label, builder
  digest, and Distroless digest all independently reconfirmed truthful —
  not merely trusted from Dockerfile comments.

---

## 4. One image / three roles — independent proof

The project's own `scripts/smoke/container_smoke.py` (`make smoke`) and
`scripts/compose/compose_integration.py` (`make compose-test`) both
exercise all three roles from the exact release image and both passed.
In addition, this review independently built and exercised the chain
**outside both the project's smoke script and Compose**, using hand-run
`docker network create` / `docker run` commands against the exact image:

```
network: maops-review-net-<uuid>
gateway published on <dynamic port>
state uid: 10001
app uid: 10001
gateway uid: 10001
--- gateway -> app -> state chain ---
{"status": "ok"}          # gateway /healthz
{"value": 0}               # gateway -> app -> state GET /state
{"value": 1}               # POST /state/increment
{"value": 2}               # POST /state/increment
{"value": 2}               # GET /state confirms persistence
```

All three roles (`state`, `app`, `gateway`) ran as the same
`maops-docker-platform:0.4.0` image, under UID 10001 in every case, with
the full `gateway -> app -> state` chain functioning end-to-end via a
hand-built Docker network with no Compose involvement. All three
containers and the network were removed by this review on completion.

---

## 5. Reproducibility

`make reproducibility-check` (run inside the full `release-check`
composite) performed two independent, clean, `--no-cache` BuildKit
builds and reported:

```
reproducibility_check: image ID A = sha256:c0b5a441cc6b787ec24fb1877459bc337b0ff513eb581a5f3c076fa87896c6a6
reproducibility_check: image ID B = sha256:c0b5a441cc6b787ec24fb1877459bc337b0ff513eb581a5f3c076fa87896c6a6

reproducibility_check: exact image ID equality:   PASS
reproducibility_check: RootFS diff-ID equality:   PASS
reproducibility_check: Config/OCI-label equality: PASS
reproducibility_check: normalized filesystem manifest (24 entries): PASS

reproducibility_check: PASS - STRONG evidence level
```

Both build A and build B's image ID are **identical to the actual release
image ID** built by `make build` in this same session
(`sha256:c0b5a441...96a6`). This is the exact two-build identity the
review scope requires — the script's `exact_match` boolean is a hard
`AND` term in `all_passed`, with no silent fallback path to a
normalized-only "STRONG" verdict when exact ID equality fails (read
directly from `scripts/build/reproducibility_check.py`). This run
achieved the exact match; STRONG evidence level was earned, not a
fallback default.

---

## 6. Supply chain — SBOM and vulnerability policy (fresh scan)

- **SBOM**: `make sbom` -> `make sbom-check` PASS. SPDX 2.3, 38 packages,
  independently re-parsed from the raw JSON (`spdxVersion=SPDX-2.3`,
  `packages=38`), generated by the pinned
  `anchore/syft:v1.51.0@sha256:678bfa565b60f747aac0f8e964fe5588a24445b8d0a480e91f6efd70020dfbb0`.
- **Vulnerability scan**: `make vuln-scan` was run with a genuinely
  fresh Trivy vulnerability database — `scripts/security/vuln_scan.py`
  creates its cache directory inside a fresh `tempfile.TemporaryDirectory()`
  every invocation, so Trivy has no persisted DB to reuse and must
  re-download it (confirmed by wall-clock: the scan took ~80s, consistent
  with a real DB fetch, not a cache hit). Scanner:
  `aquasec/trivy:0.74.0@sha256:62b1e65e8869bc4b4c6aa4fa2b21595256c7c2f6018a9d9ad61caf87187c1969`.

**Current vulnerability counts (this session's fresh scan):**

| Severity | Count | Policy |
|---|---|---|
| Critical | **0** | any -> FAIL (not triggered) |
| High, fixable | **0** | any -> FAIL (not triggered) |
| High, no fix available | 15 | reported, non-blocking |
| Medium | 44 | reported, non-blocking |
| Low | 51 | reported, non-blocking |
| Unknown | 12 | reported, non-blocking |

All 15 unfixed-High findings are Debian 13 "trixie" packages
(`libpython3.13-stdlib`/`-minimal`, `python3.13-venv`, `libssl3t64`,
`libncursesw6`, `libtinfo6`) each explicitly reported by Trivy as having
no fixed version currently published — none are silently ignored or
suppressed; the project's `.trivyignore`-free policy (per
`scripts/security/check_trivy_report.py`) surfaces every one by name.
Independently cross-parsed the raw
`artifacts/security/trivy-0.4.0.json` and confirmed the same severity
histogram (`{'MEDIUM': 44, 'LOW': 51, 'UNKNOWN': 12, 'HIGH': 15}`,
Critical absent) as `vuln_scan`'s own printed summary — not merely
trusting the script's stdout.

**Policy result: `vuln_scan: PASS`.** Per the review's explicit gate
("Critical > 0 or fixable High > 0 -> NOT READY"), this run's Critical=0
and fixable-High=0 means the fresh-scan gate is satisfied. Note this
review's own MEDIUM/LOW/UNKNOWN counts differ slightly from the
previously-committed `docs/supply-chain.md` figures and this session's
own earlier `day-04-supply-chain-review.md` (which recorded
`UNKNOWN=9`, now `12`) — this is expected and explicitly disclosed by
the tooling itself: vulnerability *results* are time-varying against a
live upstream DB even though the underlying image is provably
byte-identical (§5). This drift is itself independent confirmation that
this session's scan was genuinely fresh, not a replay of a cached
report.

`make supply-chain-check` (sbom + sbom-check + vuln-scan) PASS.

---

## 7. `release-check` exit status

`make release-check` (quality + build + inspect + image-audit + smoke +
security-check + compose-test + reproducibility-check + sbom +
sbom-check + vuln-scan, followed by `docker compose config`) was run to
full completion in this session with no `make: *** Error` anywhere in
the log and the log ending in a clean, complete `docker compose config`
YAML render. **Exit: PASS (0).**

---

## 8. Resource cleanup / `make clean` safety audit

A safety rig was built to independently prove `make clean`'s scope
before invoking it:

1. An **unrelated control container/network/volume**
   (`review-control-container`/`-net`/`-vol`, no `maops-` prefix).
2. A **normal (non `-p maops-compose-*`) `docker compose up -d` dev
   stack** (`docker compose -p maops-review-normaldev -f compose.yaml up
   -d`), producing containers `maops-review-normaldev-{app,gateway,state}-1`
   and volume `maops-review-normaldev_state_data`.
3. An **induced leftover** `maops-smoke-inducedleftover-review` container,
   simulating a failed self-cleanup.
4. The pre-existing **v0.1.0/v0.2.0/v0.3.0/v0.4.0** images on this host.

After `make clean`:

- The induced leftover (`maops-smoke-inducedleftover-review`) was
  removed, exactly as intended.
- (Self-induced collision, not a defect: a concurrently-running
  background `make release-check`'s own `maops-image-audit-*` container
  was also caught and removed by the same prefix match, since it was
  genuinely a live `maops-image-audit-*`-named container at that
  instant — this is the intended scoping rule working correctly, and
  the affected `release-check` run was simply re-run cleanly afterward.)
- `review-control-container`, `review-control-net`, `review-control-vol`
  (no project prefix) were **untouched** — container still running,
  `docker exec` still responsive.
- `maops-review-normaldev-{app,gateway,state}-1` and
  `maops-review-normaldev_state_data` (a normal dev stack, **not**
  `-p maops-compose-*`) were **untouched** — all three containers
  remained `Up`/`healthy` and the volume was retained.
- `maops-docker-platform:0.1.0`, `:0.2.0`, `:0.3.0`, `:0.4.0` — all
  **retained**, confirming `make clean` never removes release images of
  any version.
- No `docker system prune`/broad prune of any kind was invoked at any
  point in this review.

All rig resources were removed by this review afterward (`docker compose
down -v` for the dev stack, explicit `rm`/`network rm`/`volume rm` for
the control resources).

---

## 9. Generated artifacts

- `artifacts/` and `.cache/` are both listed in `.gitignore`; `git
  status --short artifacts/` reports nothing even though
  `artifacts/sbom/maops-docker-platform-0.4.0.spdx.json` and
  `artifacts/security/trivy-0.4.0.json` exist on disk — confirmed
  ignored, not merely assumed.
- No `.tar` files remain anywhere in the repository tree after
  `release-check` completed (`find . -name '*.tar'` empty); `docker
  save` archives are written to a `tempfile.TemporaryDirectory()` in both
  `generate_sbom.py` and `vuln_scan.py` and are removed by the `with`
  block on every exit path.
- `.cache/build/` is empty after `make build` (`rm -f
  $(BUILD_TAR)` runs unconditionally after `docker load`).
- No scanner container or scanner-created network remained after any
  `sbom`/`vuln-scan` run (`docker ps -a`/`docker network ls`, filtered
  and unfiltered, both clean).

---

## 10. Claude infrastructure count

- **Agents**: exactly 5 — `compose-platform-engineer.md`,
  `container-security-reviewer.md`, `docker-architect.md`,
  `docker-test-engineer.md`, `release-engineer.md`. No extras.
- **Skills**: exactly 4 — `compose-validation`,
  `container-security-validation`, `docker-build-validation`,
  `release-readiness`. No extras.

---

## 11. Day 5+ boundary

Confirmed absent from this repository at HEAD of this review:

- No `.github/` directory (no CI workflow of any kind).
- No GHCR or Docker Hub publish step (`docker push`) anywhere in
  `Makefile`/`scripts/`.
- No Cosign, sigstore, or attestation/SLSA tooling or references in any
  script, Makefile, or Dockerfile.
- No `restart:` policy in `compose.yaml`.
- No resource limits (`mem_limit`, `cpus`, `deploy.resources`) in
  `compose.yaml`.
- No Prometheus, Grafana, Kubernetes, or `kubectl`/`helm` references in
  any implementation file (two hits only in `docs/engineering-reviews/`
  for Day 1/Day 2, both prior reviews' own "explicitly out of scope"
  boundary notes, not implementation).

`docs/roadmap.md` itself states this boundary explicitly for Day 4
("Still no CI, no container registry, no cryptographic build
provenance/attestation/signing, no resource limits, no restart-policy
engineering — all explicitly Day 5+ scope").

---

## 12. Branch state

All Day 4 work (`docs/build-security.md`, `docs/supply-chain.md`,
`scripts/build/`, `scripts/security/`, `security/scanners.lock`, the
Distroless two-stage `docker/app/Dockerfile`, and all associated tests)
is present only as **uncommitted working-tree changes** on
`feature/day-4-build-security-reproducibility` — nothing from this
day's scope has been committed yet. `git status --short` shows 32
modified tracked files and 20 new untracked paths, none staged. This
review created no commits and modified no implementation files.

---

## 13. Findings carried forward from this session's own prior reviews (independently re-verified, non-blocking)

Two findings already surfaced by this session's own earlier specialist
reviews (`day-04-image-security-review.md`, `day-04-test-review.md`)
were independently re-derived from source in this review rather than
merely cited:

- **Healthcheck role-mismatch detection gap.** `app/healthcheck.py`,
  `gateway/healthcheck.py`, and `state/healthcheck.py` are behaviorally
  identical: each just asserts its own `/healthz` returns HTTP 200 with
  `{"status": "ok"}`. Confirmed by reading all three `_route_healthz`
  handlers in `app/server.py`, `gateway/server.py`, `state/server.py` —
  all three literally `return 200, {"status": "ok"}`, with no
  role-identifying field. This means the role-aware `HEALTHCHECK`
  dispatch (`compose.yaml` selecting `app.healthcheck` /
  `gateway.healthcheck` / `state.healthcheck` per service) cannot detect
  a hypothetical role/container mismatch — it would report healthy
  against any of the three roles equally. In the actual shipped
  deployment this is not exploitable (each container's `command:` is
  fixed by `compose.yaml`, so no mismatch actually occurs at runtime),
  but the mechanism's implicit claim of role verification does not hold
  up under adversarial testing. Non-blocking; recommended follow-up.
- **`image_audit.py`'s `check_final_base_is_approved_distroless()` does
  not check what its name/docstring claim.** `EXPECTED_FINAL_BASE_DIGEST`
  and `EXPECTED_FINAL_BASE_REPO` (lines 64-65) are defined but never
  referenced by the function body, which only asserts `docker image
  inspect --format {{json .RootFS.Layers}}` succeeds and is non-empty —
  true of any image whatsoever, not a base-identity check. The actual
  base-image guarantee for this release comes from Docker refusing to
  build if the Dockerfile's pinned `FROM ...@sha256:...` digest doesn't
  resolve (a build-time guarantee, independently confirmed for this
  exact release build in §3) — so the shipped image's base identity is
  not actually at risk, but this specific named audit check is
  effectively dead code relative to its stated purpose. Non-blocking;
  recommended follow-up (either wire the constants into a real
  comparison or rename/narrow the check's claim).

Two further Medium/Low findings from `day-04-supply-chain-review.md`
(policy-checker failure-mode on malformed non-dict report shapes;
case-sensitive severity string comparison) were not independently
re-derived in this session due to time budget — flagged here only as
carried-forward, unverified-by-this-review pointers, not as this
review's own findings.

---

## 14. Release blockers

**None.** Every gate the review scope required was run fresh in this
session and passed, image identity and base identity were independently
verified byte-for-byte and digest-for-digest, the one-image/three-role
chain was proven both via the project's own tooling and via this
review's own hand-built Docker network, reproducibility achieved exact
two-build image-ID equality with no fallback, the fresh vulnerability
scan reports zero Critical and zero fixable High, `make clean` was
proven not to touch unrelated resources, prior version images, or a
normal (non-project-prefixed) dev stack, generated artifacts are
correctly gitignored with no leftover archives or containers, and the
Claude agent/skill counts and Day 5+ scope boundary are both exactly as
specified. The two findings in §13 are real but do not change the
security or functional posture of the shipped `v0.4.0` image and are
recommended as non-blocking follow-up work.

---

## RELEASE-READY FOR v0.4.0
