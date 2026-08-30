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

## Two-stage build (Day 4; a third stage was added Day 6 - see below)

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

## Day 6: emergency Debian-security overlay (`security-patch` stage)

`gcr.io/distroless/python3-debian13:nonroot` at the exact digest pinned
above ships `libssl3t64 3.5.6-1~deb13u2`, vulnerable to CVE-2026-14456 (a
HIGH-severity, fixable finding - see `docs/supply-chain.md`'s
vulnerability policy). Debian Security had already published the fix
(`3.5.7-1~deb13u2`) at the time this was discovered, but the upstream
Distroless rebuild had not yet incorporated it, with no ETA. This
project's policy forbids both waiting indefinitely on an upstream rebuild
and weakening the vulnerability policy itself (no `.trivyignore`, no CVE
allowlist, no severity rewrite - see `.claude/CLAUDE.md`), so a third
build stage was added:

```
FROM python:3.13-slim@sha256:ffb752...30a AS security-patch
ADD --checksum=sha256:<verified> https://snapshot.debian.org/.../libssl3t64_3.5.7-1~deb13u2_amd64.deb /tmp/libssl3t64.deb
RUN dpkg-deb -x ... && dpkg-deb -e ...   # extract real payload + real control metadata
```

**What this is, precisely**: the *exact*, official, checksum-pinned
Debian Security package (`libssl3t64_3.5.7-1~deb13u2_amd64.deb`),
downloaded via BuildKit's `ADD --checksum=sha256:...` (the frontend
itself refuses the build if the downloaded bytes don't match - no
`apt-get upgrade` against a moving repository, no unverified
curl-pipe-to-anywhere), from an **immutable** `snapshot.debian.org`
archive URL (a fixed timestamp, `20260825T185058Z`, not a mutable
"current" mirror path). `dpkg-deb -x` extracts the real binary payload
(`libssl.so.3`, `libcrypto.so.3`, the `engines-3/` plugins); `dpkg-deb -e`
extracts the package's own real `control`/`md5sums` metadata, copied
verbatim (only renamed, per Distroless's own `status.d/<pkg>` /
`status.d/<pkg>.md5sums` layout) into the final stage - never a
hand-written or minimal status.d entry invented to satisfy a scanner.

**What this is not**: a base-image migration. The Distroless digest
pinned above is unchanged. The final runtime is accurately described as
"pinned Distroless Python Debian 13 + pinned Debian-security libssl3t64
overlay" - never as byte-identical to upstream Distroless, and never
described as a base-image swap.

**Verification performed before pinning** (see `security/runtime-patches.lock`
and `docs/supply-chain.md` for the full record): the package's SHA256 was
computed from the downloaded `.deb` and independently cross-checked
against the SHA256 published in Debian Security's own signed
`trixie-security`/`main`/`binary-amd64` `Packages` index at the same
snapshot timestamp - not merely trusted from the download in isolation.
`security-tracker.debian.org` independently confirms trixie is vulnerable
at `3.5.6-1~deb13u2` and fixed in `trixie-security` at `3.5.7-1~deb13u2`.

**Three layers of automated proof this overlay is real, not metadata
spoofing**:

1. `scripts/lint/check_dockerfile.py` (source/config, `[A]`) - the
   `security-patch` stage reuses the same digest-pinned `python:3.13-slim`
   builder (no new base image), its `ADD --checksum=` is cross-checked
   against `security/runtime-patches.lock`'s pinned URL/SHA256, and the
   final stage is checked to actually `COPY --from=security-patch` the
   patched libraries and dpkg metadata.
2. `scripts/build/image_audit.py` (image/kernel inspection, `[B]`/`[D]`) -
   against the **built image**: dpkg `status.d` reports the fixed
   `Version:`, the two shared libraries' live content hashes match the
   hashes pinned in `runtime-patches.lock` (themselves already verified
   against the official `.deb` - no re-download at audit time), and
   Python's own `ssl` module actually loads, reports the patched OpenSSL
   version, and successfully constructs an `SSLContext`.
3. `scripts/security/check_sbom.py` - the generated SBOM's `libssl3t64`
   `versionInfo` must include the patched version; a patched filesystem
   with stale SBOM metadata is treated as a failure, not a soft warning.

The build remains strongly reproducible (`make reproducibility-check`):
`ADD --checksum=` fetches the identical, cryptographically-pinned bytes
on every build, so two independent `--no-cache` builds still produce an
exact image-ID match.

## Day 7: runtime security-patch lifecycle tripwire

The overlay above is a deliberate, **temporary** exception, not a
permanent fixture. Its explicit exit condition: once the pinned
Distroless base itself ships a `libssl3t64` build at least as new as the
overlay's own patched version (`security/runtime-patches.lock`'s
`LIBSSL_VERSION`), the overlay becomes redundant and must be explicitly
removed from `docker/app/Dockerfile` and `security/runtime-patches.lock`
- keeping it in place past that point would be dead weight at best, and
could silently *downgrade* the runtime if the base ever shipped something
newer than the overlay.

`scripts/security/patch_lifecycle_check.py` (`make patch-lifecycle-check`,
part of `make release-check`) is the automated tripwire for exactly this
condition - a fourth layer of proof alongside the three already listed
above, following this project's own `[A]`/`[B]`/`[C]`/`[D]` evidence-tier
discipline (`docs/security.md`; see also that document's "Day 6 addition:
emergency Debian-security overlay evidence chain" for the same discipline
applied to the overlay's payload itself):

- **`[A]` source/static evidence** - it derives the pinned final base's
  (repository, digest) directly from this Dockerfile's own real, parsed
  `FROM` text (`scripts/security/base_image_ref.py` - never a second
  hand-copied digest constant, so the check cannot become tautological by
  construction).
- **`[B]` image inspection / package-metadata evidence** - it
  independently `docker pull`s that exact pinned digest (a fresh pull,
  not a reuse of any build-time layer cache), `docker create`s (never
  `docker run`/`exec` - Distroless has no shell) a throwaway container
  from that pulled base, and `docker cp`s out its own real
  `/var/lib/dpkg/status.d/libssl3t64` metadata - the same layout this
  project's own overlay writes to - to read the REAL `libssl3t64` version
  that base currently ships. This is `[B]`-tier evidence specifically:
  genuine image/package-metadata inspection of the independently pulled
  base, not merely a claim re-stated from the Dockerfile's own comments.
  This tripwire does not itself perform a `[D]` kernel/runtime proof (no
  process is exec'd, no live library is loaded) - the `[D]`-tier proof
  that the *built release image's own* patched OpenSSL binaries actually
  load and function at runtime is a separate, already-established check
  (`image_audit.py`'s content-hash and `ssl` module checks, `docs/
  security.md`'s Day 6 section) and is unaffected by this tripwire.

That real, `[B]`-tier observed version is compared against
`security/runtime-patches.lock`'s recorded
`LIBSSL_VULNERABLE_VERSION`/`LIBSSL_VERSION` using genuine Debian
version-comparison semantics (`scripts/security/debian_version.py` -
Debian Policy §5.6.12's algorithm, not string/tuple comparison, which
gets `~deb13uN`-style revisions wrong), producing one of four outcomes:

- the base is still older than the patched version, and matches the
  lock's own recorded vulnerable version -> overlay still **required**,
  PASS;
- the base is now at or past the patched version -> overlay now
  **redundant**, FAIL (explicit review/removal required - never silently
  passes);
- the base's real version could not be established at all (pull/extract
  failure, unparseable version) -> FAIL (never silently assumed
  still-required);
- the base is still older than the patched version, but does not match
  the lock's recorded vulnerable version -> the lock's own documented
  rationale has drifted from reality -> FAIL (prompting a lock update).

See `docs/production-readiness.md` §1.1 for the real evidence this
produced against the actual pinned base as of this writing, and
`tests/test_debian_version.py`/`tests/test_patch_lifecycle_check.py` for
the Docker-free unit coverage of the comparison/classification logic
itself.

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
