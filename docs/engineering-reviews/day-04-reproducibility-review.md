# Day 4 Independent Build Security & Reproducibility Review

Repository: `maops-docker-platform`
Branch: `feature/day-4-build-security-reproducibility`
Target: v0.4.0
Reviewer: independent Day 4 build-security/reproducibility review agent
(review-only)
Scope: `docker/app/Dockerfile`, `scripts/build/reproducibility_check.py`,
`scripts/build/image_audit.py`, `scripts/lint/check_dockerfile.py`,
`docs/build-security.md`, `docs/supply-chain.md`, and the real deterministic
BuildKit build pipeline they claim to prove — per `.claude/CLAUDE.md` and
`docs/roadmap.md`'s Day 4 scope. Does not re-litigate SBOM/vulnerability
scan content or Compose network/persistence findings, which belong to
other reviews.

This review did not trust `reproducibility_check.py`'s or `image_audit.py`'s
PASS output at face value. Every primary claim below was independently
re-derived: two of this reviewer's own `--no-cache` builds with distinct
tags outside the checker's own code path, independent digest resolution
of both base images against the live registries via
`docker buildx imagetools inspect`, real adversarial mutant-image builds
(changed file content, changed file mode, changed file ownership) fed
through the checker's own manifest-extraction code, a real A/B comparison
of `--output ...,rewrite-timestamp=true` present vs. absent to determine
whether it is actually load-bearing, and a real `.dockerignore` challenge
using deeply nested generated artifacts injected into `artifacts/`,
`.cache/`, and `security/` before rebuilding and diffing the resulting
image ID and a full in-container filesystem walk.

**Review-environment note**: the Docker daemon was not running at the
start of this review (WSL2, Docker Desktop not started, no `docker` binary
on `PATH`). This reviewer started Docker Desktop and added a `docker` ->
`docker.exe` shim under `~/.local/bin` to reach the daemon from WSL for
the duration of this review. This is a review-environment setup action
only, not a repository change. One side effect: `make compose-check`
fails in this specific shimmed environment with a Windows UNC-path
mismatch on the `config/platform.json` Compose `configs:` source path —
this is an artifact of invoking `docker compose` through `docker.exe` from
WSL, not a Day 4 defect, and `compose-check` is not one of Day 4's
required commands (`docs/networking.md`/`docs/configuration.md` territory,
already covered by the Day 3 reviews). It is not counted as a finding.

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 3 |
| Low      | 1 |

No Critical or High findings. The core claim — that two independent,
clean, `--no-cache` builds of the exact release Dockerfile produce a
byte-identical image — reproduced exactly, repeatedly, and under
adversarial testing designed to find a hole in the proof. All findings
below are gaps in the *strength/honesty of the supporting evidence*, not
defects in the deterministic-build mechanism itself.

---

## 1. Version / base image pins

**VERSION**: confirmed `0.4.0` (`cat VERSION`, currently uncommitted — see
M-3 below).

**Builder base**: `docker/app/Dockerfile` pins
`python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`.
Independently re-resolved against the live registry
(`docker buildx imagetools inspect python:3.13-slim`): the bare `3.13-slim`
tag **currently still resolves to this exact index digest** — no drift.

**Distroless final base**: `docker/app/Dockerfile` pins
`gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`.
Independently resolved this exact digest directly
(`docker buildx imagetools inspect gcr.io/distroless/python3-debian13:nonroot@sha256:4376...bea`):
it is an OCI **image index**, and its `linux/amd64` manifest resolves to

```
sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c
```

— an **exact match** to both the value this review was asked to confirm
and the value documented in the Dockerfile's own comments and
`docs/build-security.md`. Distinguishing the two digests explicitly: the
pinned `sha256:4376...bea` is the **index digest** (multi-arch manifest
list); `sha256:ed7cd5...6a6c` is the **linux/amd64 architecture manifest
digest** it resolves to — these are different objects at different levels
of the OCI content-addressing tree, and the Dockerfile correctly pins the
index, letting BuildKit select the matching-architecture manifest
underneath it.

**Verdict: PASS.** Both pins are genuine, well-formed, immutable
`sha256:` digest references (not tags), and both independently
re-resolved to the exact values this review was asked to verify.

**L-1 (Low, informational)**: the live `gcr.io/distroless/python3-debian13:nonroot`
*tag* has moved since the pin was recorded — it currently resolves to
index digest `sha256:6bfc400d0a6d89f50f5bbc0a4b4ff57214ae5c01647c3a74c2a0c8d830b4cc00`,
not the pinned `sha256:4376...bea`. This does not affect this build or
this review's PASS verdict — digest pinning is specifically immune to tag
movement, and the pinned digest still resolves correctly and immutably to
the exact content described above. It is a signal that
`docs/build-security.md`'s own stated "Policy for future days" (re-resolve
both pins before the next release) will have real work to do at the start
of Day 5, not a Day 4 defect.

---

## 2. Dockerfile architecture

Audited `docker/app/Dockerfile` line-by-line and cross-checked every claim
against `scripts/lint/check_dockerfile.py` (which independently re-derives
the same properties by parsing the raw Dockerfile text, not by trusting
comments) and against `docker image inspect`/real in-container probes on
the actual built image.

| Property | Verdict | Evidence |
|---|---|---|
| Exactly two `FROM`, justified two-stage design | PASS | builder stage exists solely because Distroless has no shell to `mkdir`/`chown` with; final stage does no further filesystem prep beyond `COPY --from` |
| Builder pinned by digest | PASS | `python:3.13-slim@sha256:ffb752...30a`, independently re-resolved (§1) |
| Runtime pinned by digest | PASS | `gcr.io/distroless/...@sha256:4376...bea`, independently re-resolved (§1) |
| No mutable-only `FROM` | PASS | both `FROM` lines carry `@sha256:...`, neither is a bare tag |
| Final stage is Distroless | PASS | confirmed via real exec probes below |
| No `RUN` in final stage | PASS | statically true (Dockerfile has one `RUN`, in the builder stage only); `check_dockerfile.py`'s `check_no_run_in_final_stage` enforces this and its own test suite (`tests/test_check_dockerfile.py::CheckNoRunInFinalStageTests`) proves it actually rejects a `RUN` placed after the final `FROM` |
| No shell wrapper | PASS | `ENTRYPOINT ["/usr/bin/python3.13"]` is exec-form JSON array |
| Explicit `/usr/bin/python3.13` entrypoint | PASS | confirmed via `docker image inspect --format {{json .Config}}`: `"Entrypoint":["/usr/bin/python3.13"]` |
| `CMD ["-m","app"]` | PASS | confirmed: `"Cmd":["-m","app"]` |
| Absolute Python healthcheck | PASS | confirmed: `"Healthcheck":{"Test":["CMD","/usr/bin/python3.13","-m","app.healthcheck"],...}` |
| `USER 10001:10001` | PASS | confirmed: `"User":"10001:10001"` |
| `WORKDIR /app` | PASS | both stages; confirmed `"WorkingDir":"/app"` on final image |
| Truthful OCI metadata | PASS | `image_audit.py`'s `check_oci_source_truthful` independently cross-checks `org.opencontainers.image.source` against the real `git remote get-url origin` — PASS, both equal `https://github.com/raiyan10/maops-docker-platform` |
| VERSION-derived label | PASS | image's `org.opencontainers.image.version` label = `0.4.0`, matching the repository-root `VERSION` file used as the single `--build-arg VERSION=...` source |
| Application source root-owned | PASS | real write-attempt probe (`image_audit.py`'s `check_source_not_writable_by_runtime_uid`, run against a bare `docker run` with **no** `--read-only`/`--cap-drop`/hardening flags) — both an append to `/app/app/server.py` and a new-file creation under `/app` were rejected with `PermissionError`; independently reconfirmed by this review's own manifest walk showing `uid:0, gid:0` on every file under `/app/app` |
| `/data` intentionally `10001:10001` | PASS | `check_data_directory` confirms ownership; `check_data_writable_by_runtime_uid` confirms an actual write there succeeds |
| Builder content does not leak unintentionally | PASS | `check_image_content_recursive` (reused from `security_check.py`) found no nested `__pycache__`/`.pyc` and no repository-only files; the builder's own Python interpreter, `apt`/`dpkg` database, and shell are absent from the final image (confirmed directly — see §2a) |

**§2a — real exec-based confirmation that the final stage is genuinely
Distroless**, not merely tagged as one: `docker exec <container> /bin/sh -c
"echo probe"` and the same for `/bin/bash` both failed with "no such file
or directory" (an OCI runtime-level failure, not a Python-level check);
`/usr/bin/apt`, `/usr/bin/apt-get`, `/usr/bin/dpkg`, `/usr/bin/dpkg-query`
all failed the same way; `import pip` / `import setuptools` both raised
`ImportError`; and `sys.executable` reported `/usr/bin/python3.13`,
Python `3.13`. All of this was re-run by this review via `make
image-audit` against the real, freshly built `maops-docker-platform:0.4.0`
release image (19/19 checks passed) — see §9.

**COPY ownership behavior, verified empirically** (not merely quoted from
the Dockerfile's own comments): `COPY --from=builder` does **not** by
default preserve the source stage's ownership. This review confirmed this
directly by building an "owner-mutant" variant (`COPY --from=builder
--chown=10001:10001 /app/app ./app/` instead of the default, unchowned
`COPY --from=builder /app/app ./app/`) and diffing the resulting
filesystem manifest against the real control build: every file under
`/app/app` flipped from `uid:0,gid:0` to `uid:10001,gid:10001` with
**identical content hashes**, proving the ownership axis is genuinely
independent of, and correctly *not* applied to, the application-source
`COPY` instructions in the real Dockerfile, while `/data`'s own explicit
`--chown=10001:10001` is the one deliberate exception — confirmed present
and effective.

**Verdict: PASS.** Every architectural claim in `docs/build-security.md`
about the Dockerfile reproduced exactly under independent, real,
in-container verification.

---

## 3. SOURCE_DATE_EPOCH

`Makefile` and `scripts/build/reproducibility_check.py` both compute
`SOURCE_DATE_EPOCH` identically: `git log -1 --format=%ct`, falling back
to a fixed sentinel `0` only if `git` is unavailable — never `date +%s`,
never a hostname, random number, temp filename, or absolute host path.
Confirmed by reading both `Makefile:14` and
`reproducibility_check.py:compute_source_date_epoch()`, and by
`tests/test_reproducibility_check.py::ComputeSourceDateEpochTests`, whose
`test_never_calls_the_wall_clock` and
`test_falls_back_to_fixed_zero_when_git_unavailable` cases exist
specifically to guard this. All ran and passed under this review (§9).

**Verdict on the mechanism: PASS.** The epoch source is exactly what is
claimed.

**M-3 (Medium): the epoch/`Created` identity currently anchors to a commit
that does not contain the tree actually being built.** At review time,
`git log -1` resolves to commit `bfdc9e4` ("docs(day-3): add v0.3.0
release evidence", timestamp `1787215216` = `2026-08-20T08:40:16Z`) — but
`git show bfdc9e4:VERSION` is `0.3.0`, and `bfdc9e4`'s tree contains none
of the Day 4 work at all. The actual repository working tree has
substantial **uncommitted** changes to every file the Dockerfile `COPY`s
into the image: `VERSION` (`0.3.0` → `0.4.0`), `docker/app/Dockerfile`
itself (the entire two-stage/Distroless design is new, uncommitted),
`app/platform_config.py`, `gateway/platform_config.py`, and
`state/platform_config.py` (confirmed via `git status --short -- app/
gateway/ state/ VERSION docker/`). The built image's `Config.Created`
field is `2026-08-20T08:40:16Z` — i.e. it carries the timestamp identity
of commit `bfdc9e4`, a commit that (a) predates all Day 4 work and (b)
does not contain the exact bytes that were actually built.

**What input source tree is actually being reproduced**: precisely the
current, uncommitted **working directory** — not any specific commit.
This does **not** undermine the core reproducibility proof itself: build
A and build B both read from the identical on-disk working tree
regardless of its commit status, so "build A == build B" remains a true
and meaningful claim, and this review's own from-scratch rebuilds
confirmed it independently (§4). What it does undermine is the *narrower*
claim that the release image's `SOURCE_DATE_EPOCH`/`Created` timestamp
traces to "the current commit" in any way that would let a third party
check out `bfdc9e4`, rebuild, and get this same image — they would not,
because `bfdc9e4`'s tree lacks the Day 4 Dockerfile and source changes
entirely.

- **Impact**: process/provenance-honesty gap, not a build-determinism
  defect. Until the Day 4 tree is committed, a statement like "this image
  was built from commit `bfdc9e4`" would be false, even though the image
  itself is genuinely, repeatedly, byte-for-byte reproducible from the
  current working tree.
- **Recommended fix**: commit the Day 4 working tree before cutting the
  v0.4.0 release build (standard practice this project already follows
  for prior days, per `git log`'s Day 1/2/3 merge commits), then re-run
  `make build`/`make reproducibility-check` against the new commit. The
  image content will not change (it's a function of the tree, which is
  already fixed), but its `SOURCE_DATE_EPOCH`/`Created` identity will then
  honestly correspond to the commit that produced it.

---

## 4. Two independent builds

Ran `make reproducibility-check` (`scripts/build/reproducibility_check.py`)
**twice**, then performed this reviewer's own fully independent pair of
builds outside the checker's code path, with distinct tags, using the
exact same invocation `make build` uses:

```
docker buildx build --no-cache \
    --build-arg VERSION=0.4.0 --build-arg SOURCE_DATE_EPOCH=1787215216 \
    --output type=docker,rewrite-timestamp=true,name=<tag>,dest=<tar> \
    -f docker/app/Dockerfile .
```

| Build | Image ID |
|---|---|
| `make build` (real release build) | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| `reproducibility_check.py` run 1, build A | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| `reproducibility_check.py` run 1, build B | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| `reproducibility_check.py` run 2, build A | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| `reproducibility_check.py` run 2, build B | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| **This reviewer's independent Build A** (`maops-repro-reviewer-<ts>-x`) | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| **This reviewer's independent Build B** (`maops-repro-reviewer-<ts>-y`) | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |

**Exact-ID equality across all seven independent build invocations: PASS.**

Additional independent comparisons performed on this reviewer's own
Build A vs. Build B (not the checker's internal comparison — separately
computed via `docker image inspect`):

- **RootFS DiffIDs**: `{{json .RootFS}}` byte-identical, `diff` clean (54
  layers).
- **Image config**: `{{json .Config}}` byte-identical, `diff` clean —
  entrypoint, cmd, user, healthcheck, exposed ports, env, and all five OCI
  labels match exactly.
- **Entrypoint/command/healthcheck**: covered by the Config comparison
  above; individually confirmed as `/usr/bin/python3.13`, `["-m","app"]`,
  and the absolute-path healthcheck respectively.
- **History**: `docker history` on the release image shows Distroless's
  own ~50 base-layer bazel-build history entries (immutable, inherited
  from the pinned base) followed by this project's own `ARG`/`LABEL`/
  `WORKDIR`/`ENV`/`COPY`×5/`USER`/`EXPOSE`/`HEALTHCHECK`/`ENTRYPOINT`/`CMD`
  layers — meaningful in that it confirms no extra, unexpected layer was
  introduced and no builder-stage layer leaked into the final image's
  history.
- **Normalized filesystem manifest**: see §5.

**Verdict: PASS — STRONG.** All four independent comparison axes agree,
across seven separate build invocations spanning three different code
paths (`make build`, the checker's own two runs, and this reviewer's own
from-scratch pair).

---

## 5. Filesystem manifest — discriminating power

Audited `scripts/build/reproducibility_check.py`'s `_MANIFEST_SCRIPT` (the
in-container Python walker). It records, per path: `path`, `type`
(`file`/`dir`/`symlink`), POSIX `mode`, `uid`, `gid`, symlink `target`
(when applicable), and a SHA-256 content hash (for regular files only) —
deliberately excluding `mtime`, which is exactly the axis
`rewrite-timestamp=true` legitimately normalizes rather than preserves.
This matches every field §5 of the review brief asked for.

The project's own `tests/test_reproducibility_check.py::ManifestScriptTests`
already contains synthetic mutation tests for differing content, differing
mode, mtime-exclusion, and symlink-target recording — all five ran and
passed under this review (§9). This review went further and adversarially
tested the **real, end-to-end path** (real mutant images, not just the
walker script in isolation) by building three real single-property mutant
variants of the actual release Dockerfile and running the checker's own
`extract_filesystem_manifest()` against real running containers of each,
diffed against a real control build:

| Mutation | Real image built | Manifest diff detected? | Detail |
|---|---|---|---|
| Changed file content (`printf "mutant-marker" >> /app/app/server.py` in the builder stage) | `sha256:31af1462e1...` (image ID differs from control) | **YES** | `sha256` field on `/app/app/server.py` changed from `65c2d820...` to `9bdb9011...`; every other file/entry identical |
| Changed file mode (`chmod 644` vs. control's `755` on `/app/app/server.py`) | `sha256:3502f2df75...` | **YES** | `mode` field changed from `493` (`0o755`) to `420` (`0o644`); content hash unchanged, confirming the mode axis is independently discriminated from content |
| Changed file ownership (`--chown=10001:10001` on the `/app/app` `COPY --from=builder`, vs. control's default root ownership) | `sha256:85fbeaf839...` | **YES** | `uid`/`gid` on every file and the directory itself under `/app/app` changed from `0:0` to `10001:10001`; content hashes unchanged |

A fourth variant (`chmod 600` instead of `644` on the same file) was also
built; that mutant's container **could not even reach a running state** —
`/app/app/__main__.py`'s import of `server.py` raised `PermissionError`
inside the container at UID 10001, because a root-owned, mode-`600` file
is unreadable to a non-owning UID. This is not itself a manifest-detection
result (the walker never got to run), but it is independent corroboration
in the opposite direction: the manifest-checked properties (ownership,
mode) are load-bearing enough at runtime that a sufficiently severe
mutation crashes the container outright, rather than silently passing.

**Verdict: PASS.** The manifest has genuine, independently re-verified
discriminating power on content, mode, and ownership — the three
properties most likely to regress silently — and does not accidentally
hide any of them.

---

## 6. `rewrite-timestamp` claim

Confirmed `docker buildx build ... --output type=docker,rewrite-timestamp=true,...`
is genuinely accepted and genuinely executed by the current environment
(Docker Desktop, `docker`-driver BuildKit v0.32.2 — matching this
project's own documented empirical basis in
`reproducibility_check.py`'s docstring). Direct evidence from this
review's own `make build` run: BuildKit's `--progress=plain` output
explicitly printed
`#24 rewriting layers with source-date-epoch 1787215216 (2026-08-20 08:40:16 +0000 UTC)`,
and the resulting image's `Config.Created` field is exactly
`2026-08-20T08:40:16Z` — the epoch, not the real wall-clock build time
(builds were run on `2026-08-23`, three days later).

**This review did not assume `SOURCE_DATE_EPOCH` and `rewrite-timestamp`
are both load-bearing merely because the command contains them — it
empirically tested each in isolation:**

- **`SOURCE_DATE_EPOCH` alone, without `rewrite-timestamp`**: ran two
  `--no-cache` builds with the identical `SOURCE_DATE_EPOCH` build-arg but
  no `rewrite-timestamp=true` output option. Result: **the two builds
  produced different image IDs** (`sha256:9519ec62ae...` vs.
  `sha256:05e7b58c52...`) and genuinely different `RootFS` layers (the
  five newest layers, corresponding to the `COPY`/`RUN` instructions,
  diverged) — **even though both builds' `Config.Created` field still
  matched exactly** (`2026-08-20T08:40:16Z`). This is a precise, empirical
  reproduction of the exact historical finding `docs/build-security.md`
  itself documents (its "approach 1" experiment): BuildKit's frontend
  gives the `SOURCE_DATE_EPOCH` build-arg special handling for the image
  config's `Created` field, but does **not**, on its own, retroactively
  normalize the real wall-clock file mtimes `COPY`/`RUN` instructions
  produce — that normalization is exactly what `rewrite-timestamp=true`
  additionally provides.
- **`rewrite-timestamp=true` with `SOURCE_DATE_EPOCH`** (the actual `make
  build`/checker invocation): confirmed reproducible across seven
  independent build invocations (§4).

**Verdict: PASS — both are genuinely load-bearing, independently
confirmed by isolating each rather than trusting the command line.**
`SOURCE_DATE_EPOCH` alone normalizes only the image config's `Created`
label; `rewrite-timestamp=true` is what actually makes the layer content
itself (and therefore the image ID) reproducible. Removing either changes
real, observable behavior.

---

## 7. Final image

| Property | Value |
|---|---|
| Image ID | `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba` |
| `docker image ls` DISK USAGE (virtual, includes shared base layers) | `90.4MB` |
| `docker image inspect .Size` (content size) | `22534944` bytes (`22.5MB`) |
| RootFS layer count | `54` |
| `Config.Created` | `2026-08-20T08:40:16Z` (== `SOURCE_DATE_EPOCH`, see §3/§6) |

**Deliberately not conflated**, per the review brief: `docker image ls`'s
`DISK USAGE` column (`90.4MB`) and `docker image inspect`'s `.Size`
content-size field (`22.5MB`, i.e. `22534944` bytes) are different
measurements — the former includes the Distroless base's own already-
present-on-disk shared layers counted at full size by that CLI view; the
latter is the image's own content-addressed size. This review used
`docker image inspect --format {{.Size}}` as the authoritative content
size, not `docker image ls`'s DISK USAGE column.

This reviewer's independently rebuilt images (§4) matched this exact ID,
confirming the real, tagged `maops-docker-platform:0.4.0` release image
genuinely matches the independently reproduced deterministic identity —
not merely a same-named different image.

**Verdict: PASS.**

---

## 8. Generated artifacts / build context

Reviewed `.dockerignore`: excludes `.git`, `.github`, `.claude`, `tests`,
`docs`, `artifacts`, `.cache`, `security` (all with explicit `/**`
recursive-at-any-depth patterns, per the file's own documented lesson from
a prior review finding about non-recursive glob semantics), plus
`**/*.tar`, `**/*.tar.gz`, and Python cache directories at any depth.

**Adversarially challenged this directly**, not just read the file: created
deeply nested generated-artifact content —
`artifacts/sbom/nested/deep/fake.spdx.json`, a 512KB binary file at
`.cache/scratch/nested/bigfile.bin`, and `security/nested/leftover.tmp` —
then reran the exact `make build` invocation with `--no-cache`. Result:
**the resulting image ID was byte-identical to the clean control build**
(`sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba`),
and a full recursive filesystem walk from `/` inside a running container
of that image found **zero** occurrences of any of the three injected
filenames. `.dockerignore` genuinely prevents these paths — including
nested ones several directories deep — from entering the build context at
all, not merely from being copied by a specific `COPY` instruction.

Separately confirmed (static read, consistent with `docs/supply-chain.md`'s
claims): `grep -rn "docker.sock\|privileged\|network_mode" scripts/security/*.py`
returns nothing — neither `generate_sbom.py` nor `vuln_scan.py` mounts the
Docker socket or the live daemon anywhere in their actual source, matching
`tests/test_vuln_scan.py`'s `RunTrivyDockerSocketIsolationTests` (which
ran and passed, §9). Both scanners are pinned by exact digest in
`security/scanners.lock`.

**Verdict: PASS.** SBOM/Trivy/generated artifacts cannot affect
application image reproducibility and cannot enter the Docker build
context, even nested arbitrarily deep.

---

## 9. Required commands — results

All run directly against this repository's real working tree (Docker
Desktop started for this review, see the environment note above).

| Command | Result |
|---|---|
| `make test` (`python3 -m unittest discover -s tests -t . -v`) | **295 tests, OK** |
| `make lint` | **OK** (20 workload files + 7 tooling files scanned) |
| `make dockerfile-check` | **OK** (10 checks passed) |
| `make build` | **succeeded**, produced `sha256:2dcc39a9bd27...`, `rewrite-timestamp` genuinely applied (§6) |
| `make image-audit` | **PASS, 19/19 checks** |
| `make reproducibility-check` | **PASS**, run twice, both times exact-ID/RootFS/Config/manifest equality all PASS |

`tests/test_reproducibility_check.py` (8 tests) and
`tests/test_check_dockerfile.py` (21 tests) were also run individually and
passed in full, including their own synthetic mutation-detection cases.

`make compose-check` was also run out of due diligence (not on Day 4's
required list): it produced one failure, a Windows-UNC-path mismatch on
the Compose `configs:` source path attributable to this review's
`docker.exe`-via-WSL shim, not a project defect (see environment note
above).

---

## 10. Findings

### M-1 (Medium): `image_audit.py`'s Distroless base-digest check does not check the digest it names

**Location**: `scripts/build/image_audit.py:381-394`
(`check_final_base_is_approved_distroless`), and the unused
`EXPECTED_FINAL_BASE_DIGEST`/`EXPECTED_FINAL_BASE_REPO` constants at
lines 64-65.

**Reproduction**: read the function body — it calls `docker image inspect
<image> --format {{json .RootFS.Layers}}` and asserts only that the
command succeeds and its output is non-empty. `grep -n
EXPECTED_FINAL_BASE_DIGEST scripts/build/image_audit.py` shows the
constant is defined and referenced once more only inside this function's
own docstring text (as a comment referring to a *different* module's
constant, `check_dockerfile.py`'s), never in an actual comparison
anywhere in the file.

**Expected**: per the function's own name and docstring ("Proves the
release image was really built FROM the approved Distroless digest, not
merely that the Dockerfile text claims it was"), it should independently
cross-check the built image's actual base identity against
`EXPECTED_FINAL_BASE_DIGEST`.

**Actual**: it only asserts that `docker image inspect`'s `RootFS.Layers`
field is inspectable and non-empty — true for **any** successfully built
image regardless of what base it was built from. No comparison against
the expected digest, or against anything else, ever occurs.

**Impact**: a future Dockerfile change to a different, unapproved final
base image would still receive a `PASS` verdict from this specific check,
which is presented (both in its name and its docstring) as independent
runtime proof of base-image identity. In this project's actual layered
defense, the real, effective protection for this property is
`scripts/lint/check_dockerfile.py`'s `check_from()`, which **does**
genuinely parse the Dockerfile source and compare its final `FROM`
digest against `EXPECTED_FINAL_DIGEST` (confirmed correct and effective —
`tests/test_check_dockerfile.py::CheckFromTests::test_wrong_final_digest_is_rejected`
passed) — so the property this check claims to prove is, in practice,
actually enforced elsewhere. But `image_audit.py`'s own claim to
independently re-prove it via image inspection is false, and if
`check_dockerfile.py` were ever skipped or its check weakened,
`image_audit.py` would not catch a base-image substitution as its
docstring claims it would.

**Recommended fix**: either (a) rewrite the docstring/check name to
honestly describe what it verifies (RootFS inspectability as a build
sanity check), and rely on `check_dockerfile.py`'s static check as the
documented, authoritative source of this specific proof; or (b) implement
a real independent cross-check — e.g., diffing the image's base-layer
`DiffID`s against `docker buildx imagetools inspect`'s resolved layer
digests for the pinned reference, or reading build provenance/SBOM output
if attestations are ever enabled — and note in the docstring that a plain
`docker image inspect` on a built image does not, by itself, retain "which
FROM produced this" as first-class metadata.

### M-2 (Medium): no unit test coverage exists for `scripts/build/image_audit.py`

**Location**: `tests/` (absence).

**Reproduction**: `find tests -iname '*image_audit*'` returns nothing;
`grep -rn image_audit tests/` returns nothing. Every other Day 4 script
has a companion test file: `test_reproducibility_check.py`,
`test_generate_sbom.py`, `test_vuln_scan.py`, `test_check_sbom.py`,
`test_check_trivy_report.py`, `test_scanner_lock.py`,
`test_check_dockerfile.py`, plus the pre-existing `test_check_source.py`,
`test_compose_integration.py`, and `test_security_check.py`.

**Expected**: consistent with this project's own established testing
culture (every other new Day 4 script is covered).

**Actual**: `image_audit.py` — the script specifically responsible for
this project's release-image policy audit — has zero test coverage.

**Impact**: this absence directly correlates with M-1 going undetected —
a test asserting `check_final_base_is_approved_distroless`'s behavior
against a mocked wrong-digest scenario (mirroring how
`test_check_dockerfile.py::test_wrong_final_digest_is_rejected` already
does for the static check) would have caught the docstring/implementation
mismatch before this review found it.

**Recommended fix**: add `tests/test_image_audit.py` covering at minimum:
the checks whose logic is nontrivial (`check_oci_source_truthful`'s git-
remote URL normalization, `check_source_not_writable_by_runtime_uid`'s
and `check_no_shell`'s pass/fail branch logic via mocked `subprocess.run`
output), and a regression test that would have failed against the current
`check_final_base_is_approved_distroless` implementation.

### M-3 (Medium): reproducible-build epoch/`Created` identity is anchored to a commit that does not contain the tree being built

See §3 above for full detail. **Location**: working-tree state (`VERSION`,
`docker/app/Dockerfile`, `app/platform_config.py`,
`gateway/platform_config.py`, `state/platform_config.py` all uncommitted);
`Makefile:14`'s `SOURCE_DATE_EPOCH` derivation is mechanically correct but
currently resolves against the wrong (stale, pre-Day-4) commit as a side
effect of the tree not yet being committed.

**Impact**: process/provenance-honesty gap, not a build-determinism
defect — reproducibility across builds (build A == build B) is unaffected
and independently proven regardless of commit state (§4). **Recommended
fix**: commit before the release build; re-verify identity is unchanged
in content, only in its now-correct commit anchor.

### L-1 (Low, informational): live Distroless `nonroot` tag has moved past the pinned digest

See §1 above. Not a defect — digest pinning is specifically immune to
this — but flagged as a signal for the next day's base-refresh policy
check per `docs/build-security.md`'s own stated process.

---

## Release blockers

**None.** All three Medium findings are evidence-quality/process-honesty
gaps, not defects in the deterministic-build mechanism, the Dockerfile's
architecture, or the release image's actual content/security posture —
every one of those reproduced correctly under this review's own
independent, adversarial verification. M-3 (commit before building the
real release artifact) is the one item this reviewer would want closed
before this specific image is presented as "the v0.4.0 release build",
since right now no commit's tree actually matches what was built — but it
blocks *process cleanliness*, not the technical reproducibility claim
itself, which holds regardless.

---

## Summary verdicts

- **Dockerfile verdict**: PASS
- **Builder pin verdict**: PASS (independently re-resolved, no drift)
- **Distroless pin verdict**: PASS (index digest and linux/amd64 manifest
  digest both independently confirmed exact matches; live tag has since
  moved, digest pin unaffected — L-1)
- **SOURCE_DATE_EPOCH verdict**: mechanism PASS; commit-anchor honesty
  gap — M-3
- **Build A ID**: `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba`
- **Build B ID**: `sha256:2dcc39a9bd27899f64793e57e7e092f8654b2cded21a074139bfad8f03ca1dba`
  (identical across all seven independent build invocations performed in
  this review, including two performed entirely outside the project's own
  checker code)
- **Exact-ID equality**: PASS
- **RootFS equality**: PASS
- **Config equality**: PASS
- **Labels equality**: PASS
- **Normalized manifest equality**: PASS, and independently re-verified to
  have genuine discriminating power against real content/mode/ownership
  mutant images (§5)
- **Checker mutation/discrimination quality**: PASS (own synthetic test
  suite passed; this review's independent real-image adversarial testing
  corroborated it end-to-end)
- **Build-context verdict**: PASS (nested generated artifacts under
  `artifacts/`, `.cache/`, `security/` provably cannot enter the build or
  affect the image)
- **Cleanup verdict**: PASS — every temporary image/container this review
  created was uniquely, deterministically named and removed; the real
  `maops-docker-platform:0.4.0` release image was left in place per this
  project's own stated convention
- **Release blockers**: none technical; M-3 (commit before the real
  release build) is a process item, not a defect
- **Final reproducibility verdict**: the claim that the final
  Distroless-based MAOps image is exactly reproducible is **independently
  confirmed true** — proven across seven separate build invocations, three
  different code paths, and adversarial testing specifically designed to
  find a hole in the proof, none of which did.

REPRODUCIBILITY PASS
