# Build Security and Reproducibility (Day 4)

## Runtime decision: Distroless, not python:3.13-slim

The Day 4 plan originally targeted `python:3.13-slim` as the (only)
runtime base, hardened via a focused remediation pass. A real Trivy scan
of that candidate (see `docs/supply-chain.md` for the full historical
numbers) found:

- **4 CRITICAL** findings, all in `perl-base`, **none with a fixed
  version available** from Debian.
- **38 HIGH** findings with a fixed version available (would have been
  resolved by the planned remediation).

The focused remediation could have reduced fixable HIGH to zero, but the
**4 unfixed CRITICAL `perl-base` findings could not be resolved** - no
newer `python:3.13-slim` digest existed to refresh to, and this project's
policy (`docs/supply-chain.md`) treats any CRITICAL finding as an
unconditional release blocker, with no carve-out for "no fix available."
`python:3.13-slim` was therefore **rejected as the release runtime**.

The approved replacement is `gcr.io/distroless/python3-debian13:nonroot`:
same Python 3.13 / Debian 13 "trixie" family (so no application
compatibility risk), no shell, no package manager, and - critically - no
`perl-base` at all (Distroless images ship only what a Python runtime
actually needs, not a general-purpose Debian userland). A real scan of
this candidate found:

- **0 CRITICAL**
- **15 HIGH, none with a fixed version available** (reported,
  non-blocking under this project's policy)
- **0 fixable HIGH**

This genuinely **passes** the existing, unweakened vulnerability policy -
see `docs/supply-chain.md` for the full current numbers and policy text.
Real runtime testing (non-root UID/GID, source immutability, `/data`
persistence, config read-only, capability/NoNewPrivs enforcement, PID 1
identity, all three roles, the full `gateway -> app -> state` chain, Day 3
network topology/isolation, SBOM generation, vulnerability-policy
enforcement, and exact build reproducibility) was independently re-run
against the Distroless candidate before adoption, not merely assumed from
the scan result alone - every one of those checks is what `make
release-check` runs today, against the real Distroless-based release
image.

**Old base (rejected):**
`python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a`

**New final runtime base (adopted):**
`gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`
(index digest; resolves to a `linux/amd64` manifest digest of
`sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c`)

## Two-stage build

`docker/app/Dockerfile` is now a two-stage build - not because the
application gained a compile step, but because the shellless final
runtime cannot prepare its own filesystem:

1. **Builder stage** (`FROM python:3.13-slim@sha256:ffb752...30a AS
   builder`) - filesystem preparation only. Copies `app/`, `gateway/`,
   `state/`, `VERSION` into `/app`, and `mkdir -p /data && chown
   10001:10001 /data` (the `state` role's persisted-volume mount point -
   see `docs/persistence.md`). No package is installed beyond what
   `python:3.13-slim` already ships; this stage exists purely because it
   still has a shell/coreutils to run `mkdir`/`chown` with. The builder's
   own Python runtime, `apt`/`dpkg` database, and everything else it
   carries never enters the final image - only the specific files/
   directories named in each `COPY --from=builder` do.
2. **Final stage** (`FROM
   gcr.io/distroless/python3-debian13:nonroot@sha256:4376...bea`) - the
   real release runtime. `COPY --from=builder` pulls in exactly the
   prepared application source and `/data`; nothing else. No `RUN`
   instruction exists in this stage (`scripts/lint/check_dockerfile.py`'s
   `check_no_run_in_final_stage()` enforces this statically) - the
   Distroless runtime has no shell/coreutils to run one against, so a
   `RUN` here would either break the build outright or (in the pathological
   case BuildKit somehow tolerated it) be silently meaningless.

**A real, empirically-discovered subtlety**: `COPY --from=<stage>` does
**not** preserve the source stage's file ownership by default - it
resets to root:root the same as a build-context `COPY` would (confirmed
directly: the builder's `chown 10001:10001 /data` was **not** carried
over by a bare `COPY --from=builder /data /data`, and `/data` came out
root-owned, breaking `state`'s write). The `/data` copy therefore carries
its own explicit `--chown=10001:10001`:

```
COPY --from=builder --chown=10001:10001 /data /data
```

The application-source `COPY --from=builder` instructions deliberately
carry **no** `--chown` - their default root-ownership is exactly the
image-level-immutability property the next section describes, not an
oversight.

## The `nonroot` Distroless tag is not this project's identity source

Distroless's `nonroot` variant ships its own baked-in `USER 65532:65532`
("nonroot") identity. This Dockerfile does **not** rely on that - it sets
its own explicit `USER 10001:10001` after the `COPY` instructions,
preserving this project's runtime UID/GID contract exactly as it existed
under the `python:3.13-slim` runtime. The Distroless image has no
`/etc/passwd` entry for `10001`, which is fine: a numeric `USER
UID:GID` needs no `/etc/passwd` lookup, and the `nonroot` tag is used only
for its minimal, shell-free *content*, never as an identity source.

## Deterministic build strategy (unchanged mechanism, still verified after migration)

`make build` still uses `docker buildx build` with BuildKit's own
reproducible-builds export mode - the two-stage Dockerfile needed no
change to this invocation, since BuildKit already builds every stage and
discards non-final-stage layers from the export regardless of stage
count:

```
docker buildx build --no-cache \
    --build-arg VERSION=<VERSION> \
    --build-arg SOURCE_DATE_EPOCH=<current commit's own timestamp> \
    --output type=docker,rewrite-timestamp=true,name=maops-docker-platform:<VERSION>,dest=<tar> \
    -f docker/app/Dockerfile .
docker load -i <tar>
```

`--output type=docker,rewrite-timestamp=true` normalizes every layer's
file timestamps and the image config's `Created` field to
`SOURCE_DATE_EPOCH` at export time. `SOURCE_DATE_EPOCH` is `git log -1
--format=%ct` - the current commit's own timestamp, never the wall
clock, never embedded anywhere in the image as a label. See the original
Day 4 investigation notes below for what was tried and why this specific
mechanism (tar export + `docker load`, not a direct `type=docker`
output) is what actually works against this project's `docker`-driver
BuildKit install.

### What was actually tried, and why this specific approach was adopted

This project's Docker Desktop / BuildKit install uses the `docker` buildx
driver (BuildKit embedded in the Docker daemon), not the
`docker-container` driver. Three approaches were empirically tested
against this real environment before adopting the one that works:

1. **Plain `docker build --build-arg SOURCE_DATE_EPOCH=<epoch>`, no
   `rewrite-timestamp`.** The image config's `Created` field matched
   exactly between two builds, but a `RUN` layer that legitimately writes
   real files with real wall-clock mtimes (e.g. `useradd`/`groupadd`
   under the pre-Distroless single-stage design) changed diff IDs between
   runs - BuildKit did not rewrite those mtimes without the exporter
   option below.
2. **`docker buildx build --output type=docker,rewrite-timestamp=true`
   (the default docker-driver output path, no `dest=`).** Failed
   outright: `ERROR: exporter option "rewrite-timestamp" conflicts with
   "unpack"` - the `docker` driver's default `type=docker` output path
   implies `unpack=true`, which BuildKit's own exporter refuses to
   combine with `rewrite-timestamp=true`.
3. **`docker buildx build --output type=docker,rewrite-timestamp=true,dest=<tar>` +
   `docker load -i <tar>`** (adopted). Exporting to a tarball first
   sidesteps the `unpack` conflict entirely.

`make build` and `scripts/build/reproducibility_check.py` both use
approach 3, unchanged by the Distroless migration.

## Reproducibility proof: STRONG (exact image-ID equality) - re-verified after migration

`scripts/build/reproducibility_check.py` (`make reproducibility-check`)
performs two clean, independent, `--no-cache` builds of the identical
source tree with identical build inputs, using two uniquely tagged
disposable images (never the real `maops-docker-platform:<VERSION>`
release image), and asserts:

1. **Exact image ID equality** (`docker image inspect --format {{.Id}}`).
2. **RootFS diff-ID equality** (`docker image inspect --format
   {{json .RootFS}}`) - every layer's content hash matches exactly.
3. **Config/OCI-label equality** (`docker image inspect --format
   {{json .Config}}`) - entrypoint, cmd, user, healthcheck, exposed
   ports, env, and every OCI label match exactly.
4. **A normalized, content-addressed filesystem manifest of `/app`**,
   independently extracted from a live container of each build via a
   small in-container Python walk (path, type, POSIX mode, uid, gid,
   symlink target, and a SHA-256 content hash per file - deliberately
   excluding mtime). The extraction now execs the absolute
   `/usr/bin/python3.13` interpreter (the Distroless final runtime has no
   shell to resolve a bare `python3` name against `PATH`) - the manifest
   algorithm itself is unchanged.

All four independently agree after the migration to the two-stage
Distroless Dockerfile, exactly as they did before it - re-verified, not
assumed to still hold. **What this does NOT prove**: reproducibility
across a different Docker/BuildKit version, a different host OS/
architecture, or a different `SOURCE_DATE_EPOCH` value.

## Image-level immutability (application source ownership) - re-verified after migration

Independent of, and in addition to, `compose.yaml`'s `read_only: true`
rootfs hardening (see `docs/security.md`), the final stage's application
source (`app/`, `gateway/`, `state/`, `VERSION` under `/app`) is owned by
**root** (the `COPY --from=builder` default when no `--chown` is given),
not by the non-root `10001:10001` runtime user. This is a deliberate
second, independent layer of defense:

- **Compose read-only rootfs** (`read_only: true`) is a *runtime*
  property: it can be disabled by whoever runs the container.
- **Image-level ownership** is a *build-time* property baked into the
  image itself: even a container started with a bare `docker run` and no
  hardening flags at all still cannot have its shipped source code
  modified by the non-root process.

**Proven, not asserted** (`scripts/build/image_audit.py`'s
`check_source_not_writable_by_runtime_uid`, run against a plain
`docker run` container with no `--read-only`, no `--cap-drop`, no
hardening flags whatsoever - and, Day 4, via a stdlib-only Python probe
rather than a shell, since the Distroless runtime has none):

```
$ docker run --rm maops-docker-platform:0.4.0 &  # any role; probed via docker exec below
$ docker exec <container> /usr/bin/python3.13 -c "open('/app/app/server.py','a').write('x')"
Traceback (most recent call last):
  ...
PermissionError: [Errno 13] Permission denied: '/app/app/server.py'
```

`/data` (the `state` role's persisted-volume mount point) remains the one
deliberate exception - owned by `10001:10001` and genuinely writable,
carried over from the builder stage via the explicit `--chown` documented
above.

## Base image pin and refresh policy

`docker/app/Dockerfile` now has two digest-pinned `FROM` lines to keep
current:

```
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder
...
FROM gcr.io/distroless/python3-debian13:nonroot@sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea
```

**Digest re-verification performed for this release (2026-08-20):**
`docker buildx imagetools inspect gcr.io/distroless/python3-debian13:nonroot`
was independently re-run against the live registry immediately before
writing this Dockerfile, and resolved to index digest
`sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea`
with a `linux/amd64` manifest digest of
`sha256:ed7cd592da15a32d0c7a0a7649f4d2e46b5b381a78a11ab3924ea3ce39c06a6c` -
**identical** to the architecture spike's own resolution. No pin drift
occurred between the spike and this implementation. The builder pin
(`python:3.13-slim@sha256:ffb752...30a`) is unchanged from its own prior
Day 4 re-verification and still resolves identically.

**Policy for future days:** independently re-resolve both pins' current
registry digests before treating a release as final. If either has
moved, inspect the new digest, rebuild, re-run the full test/security/
reproducibility/SBOM/vulnerability gate against it, and only then update
the pin - never move a pin on a bare "the tag moved" signal without
re-verifying every gate. Never change the Python major/minor version
solely to chase a scanner result, and never revert to a non-Distroless
runtime without an equally explicit, re-verified decision.

## Deferred to a later day

Cryptographic build provenance/attestation (SLSA, in-toto), image
signing (Cosign, keyless or otherwise), and any CI-driven build pipeline
remain explicitly out of scope for Day 4 - see `docs/roadmap.md`. This
day's evidence is local, reproducible, and independently re-verifiable by
anyone with this repository and Docker, which is a real and valuable
property on its own, but is not the same claim as a signed, third-party-
attested provenance record.
