#!/usr/bin/env python3
"""Release-consumer bundle preparation for maops-docker-platform (Day 7,
closes DAY6-POST-M1 - see docs/engineering-reviews/day-06-post-release-
verification.md §7.1).

WHY THIS EXISTS: `v0.6.0`'s real published `SHA256SUMS` recorded CI
workspace-relative paths (`release-evidence/sbom/...`,
`release-evidence/security/...`), while GitHub Releases serve attached
assets FLAT (no subdirectory structure) - so a consumer who downloaded
all three assets into one directory and ran the standard, unmodified

    sha256sum -c SHA256SUMS

got a hard failure, even though every asset's bytes and hash were
genuinely correct (see the post-release record above for the full,
non-destructive verification proving this was a filename/path
representation defect only, never a checksum-integrity one).

WHAT THIS SCRIPT DOES: takes the CI-internal, nested evidence tree
(`<source-dir>/sbom/*.spdx.json`, `<source-dir>/security/trivy-*.json` -
exactly what scripts/security/generate_sbom.py / vuln_scan.py already
produce under artifacts/, and what release.yml's `actions/download-
artifact` step reconstructs under `release-evidence/` in CI) and STAGES
a flat, consumer-shaped bundle directory containing ONLY the release
assets themselves, each under its own basename, plus a `SHA256SUMS`
manifest that references those exact basenames - i.e. it locally
reconstructs the REAL GitHub Release asset layout before publication, so
`sha256sum -c SHA256SUMS` is proven to succeed unmodified in that
directory before `gh release create` ever runs (release.yml's `publish`
job runs this script, then attaches `release-bundle/*` verbatim - the
Makefile/this script remain the one authoritative release-bundle
contract; release.yml's own YAML never re-implements this policy).

SECURITY NOTE: `write_sha256sums`/`stage_release_bundle` only ever
accept/emit BARE basenames (no `/`, no directory-traversal token) -
`validate_manifest_entries_are_bare_basenames` additionally re-parses
whatever `SHA256SUMS` a caller hands to `verify_release_bundle` and
rejects any entry that isn't a bare basename, so a hand-tampered
manifest can never smuggle a path-traversal/nested-CI-path reference
past this project's own verification step even if this script's own
writer never produces one.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SHA256SUMS_FILENAME = "SHA256SUMS"
_UNSAFE_NAME_PATTERN = re.compile(r"[\\/]")
_SHA256SUMS_LINE_PATTERN = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>.+)$")


class ReleaseBundleError(RuntimeError):
    pass


def _validate_asset_basename(name: str) -> None:
    if not name:
        raise ReleaseBundleError("empty release asset name")
    if name in (".", ".."):
        raise ReleaseBundleError(f"release asset name is a directory-traversal token: {name!r}")
    if _UNSAFE_NAME_PATTERN.search(name):
        raise ReleaseBundleError(
            f"release asset name contains a path separator (must be a bare basename, "
            f"never a nested/internal CI path): {name!r}"
        )


def stage_release_bundle(sources: list[Path], staging_dir: Path) -> list[str]:
    """Copies each file in `sources` into `staging_dir` under its own
    basename ONLY. Raises ReleaseBundleError (never silently skips) on:
      - a source that does not exist or is not a regular file (a missing
        asset must fail, not publish a short bundle)
      - a basename that is empty, contains a path separator, or is a
        directory-traversal token
      - two different source paths that would collide on the same
        basename (a duplicate manifest name)
    Returns the sorted list of staged basenames."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, Path] = {}
    for src in sources:
        if not src.is_file():
            raise ReleaseBundleError(f"release asset source is missing or not a regular file: {src}")
        name = src.name
        _validate_asset_basename(name)
        if name in seen:
            raise ReleaseBundleError(
                f"duplicate release asset basename {name!r}: {seen[name]} and {src} both map to it"
            )
        seen[name] = src
        shutil.copy2(src, staging_dir / name)
    return sorted(seen.keys())


def write_sha256sums(staging_dir: Path, basenames: list[str]) -> Path:
    """Writes SHA256SUMS (GNU-coreutils text-mode `<hex>  <name>` format -
    the format `sha256sum -c` expects) into `staging_dir`, hashing each
    of `basenames` as it ACTUALLY exists on disk at call time - never a
    value threaded through from an earlier step - so the manifest can
    never silently drift from the real staged bytes."""
    lines = []
    for name in sorted(basenames):
        _validate_asset_basename(name)
        path = staging_dir / name
        if not path.is_file():
            raise ReleaseBundleError(f"cannot write {SHA256SUMS_FILENAME}: staged asset missing: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    manifest_path = staging_dir / SHA256SUMS_FILENAME
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def validate_manifest_entries_are_bare_basenames(manifest_path: Path) -> None:
    """Re-parses `manifest_path` and rejects (ReleaseBundleError) any
    entry whose recorded name is not a well-formed `sha256sum -c` line
    with a bare-basename target - defense in depth against a
    hand-tampered/malformed SHA256SUMS even though this script's own
    `write_sha256sums` never produces one."""
    text = manifest_path.read_text(encoding="utf-8")
    seen: set[str] = set()
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        match = _SHA256SUMS_LINE_PATTERN.match(line)
        if not match:
            raise ReleaseBundleError(f"{manifest_path}:{line_no}: not a well-formed SHA256SUMS line: {raw_line!r}")
        name = match.group("name")
        _validate_asset_basename(name)
        if name in seen:
            raise ReleaseBundleError(f"{manifest_path}:{line_no}: duplicate manifest entry for {name!r}")
        seen.add(name)


def verify_release_bundle(staging_dir: Path, sha256sum_bin: str = "sha256sum") -> str:
    """The real consumer-style proof: after validating SHA256SUMS's own
    entries are all bare basenames, runs the ACTUAL, unmodified

        sha256sum -c SHA256SUMS

    inside `staging_dir` (the exact command this project's release notes
    tell a consumer to run) and raises ReleaseBundleError with the real
    command output on any non-zero exit - a missing asset, a
    renamed/mismatched asset, or a modified/tampered asset all surface
    here as a genuine external-tool failure, never a Python-side hash
    re-implementation standing in for it. Returns the command's stdout on
    success."""
    manifest_path = staging_dir / SHA256SUMS_FILENAME
    if not manifest_path.is_file():
        raise ReleaseBundleError(f"{SHA256SUMS_FILENAME} not found in {staging_dir}")
    validate_manifest_entries_are_bare_basenames(manifest_path)

    if shutil.which(sha256sum_bin) is None:
        raise ReleaseBundleError(
            f"{sha256sum_bin!r} not found on PATH - cannot perform the real consumer-style "
            f"'sha256sum -c SHA256SUMS' proof"
        )

    result = subprocess.run(
        [sha256sum_bin, "-c", SHA256SUMS_FILENAME],
        cwd=staging_dir, capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode != 0:
        raise ReleaseBundleError(
            f"sha256sum -c {SHA256SUMS_FILENAME} failed in {staging_dir} (exit {result.returncode}):\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout


# --- CLI: builds this project's REAL release bundle (SBOM + Trivy report) --


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def real_release_asset_sources(source_dir: Path, version: str) -> list[Path]:
    """The exact two release-evidence files this project's release ships
    (see scripts/security/generate_sbom.py / vuln_scan.py for where these
    filenames/directory layout come from) - `source_dir` is either the
    local `artifacts/` tree (`make release-bundle`) or CI's downloaded
    `release-evidence/` tree (release.yml's `publish` job), both of which
    share the identical `sbom/`/`security/` subdirectory layout."""
    return [
        source_dir / "sbom" / f"maops-docker-platform-{version}.spdx.json",
        source_dir / "security" / f"trivy-{version}.json",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", default=str(REPO_ROOT / "artifacts"),
        help="directory containing sbom/ and security/ subdirectories (default: artifacts/)",
    )
    parser.add_argument(
        "--out", default=str(REPO_ROOT / "release-bundle"),
        help="flat staging directory to (re)build (default: release-bundle/, wiped and recreated each run)",
    )
    parser.add_argument("--version", default=None, help="defaults to reading the repository-root VERSION file")
    args = parser.parse_args(argv)

    version = args.version or read_version()
    source_dir = Path(args.source_dir)
    staging_dir = Path(args.out)

    sources = real_release_asset_sources(source_dir, version)
    print(f"prepare_release_bundle: version={version} source_dir={source_dir} out={staging_dir}")

    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    try:
        basenames = stage_release_bundle(sources, staging_dir)
        write_sha256sums(staging_dir, basenames)
        verify_release_bundle(staging_dir)
    except ReleaseBundleError as exc:
        print(f"prepare_release_bundle: FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"prepare_release_bundle: staged {len(basenames)} asset(s) + {SHA256SUMS_FILENAME} in {staging_dir}:")
    for name in basenames:
        print(f"  {name}")
    print(f"prepare_release_bundle: PASS - 'sha256sum -c {SHA256SUMS_FILENAME}' succeeds unmodified in {staging_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
