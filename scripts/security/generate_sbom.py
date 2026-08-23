#!/usr/bin/env python3
"""Generates a real SPDX JSON SBOM for the exact maops-docker-platform
release image, using Syft (a mature external scanner - never a homemade
SBOM generator).

Workflow (see docs/supply-chain.md for the full rationale):
  1. `docker save` the exact release image to a project-owned temporary
     archive (never a registry pull, never the live daemon socket).
  2. Run the pinned Syft scanner container (security/scanners.lock,
     `SYFT_IMAGE`) against that archive, mounted read-only, with the
     Docker daemon socket NOT mounted and networking disabled
     (`--network none` - Syft's own package cataloging needs no network;
     the only thing it would otherwise reach for is an update-check ping,
     disabled here via SYFT_CHECK_FOR_APP_UPDATE=false).
  3. Write SPDX JSON to artifacts/sbom/ via a mounted output directory
     (host stdout redirection is not used - Syft writes the file
     directly via `-o spdx-json=<path>`).
  4. Clean up the temporary archive/cache; the SBOM output itself is kept
     as this target's documented artifact.

Runs the scanner as the invoking host UID/GID (`--user`) with
TMPDIR/XDG_CACHE_HOME/HOME redirected into the mounted output directory -
Syft's own official image has no writable /tmp or /.cache when run
non-root otherwise, and without this the SBOM file would be written
root-owned on the host (verified empirically; see docs/supply-chain.md).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_SBOM_DIR = REPO_ROOT / "artifacts" / "sbom"
SAVE_TIMEOUT_SECONDS = 120.0
SCAN_TIMEOUT_SECONDS = 300.0


class SbomGenerationError(RuntimeError):
    pass


def load_scanner_lock() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "scanner_lock.py"
    spec = importlib.util.spec_from_file_location("scanner_lock_for_sbom", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def docker_save(image: str, dest_tar: Path) -> None:
    result = subprocess.run(
        ["docker", "save", image, "-o", str(dest_tar)],
        capture_output=True, text=True, timeout=SAVE_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode != 0:
        raise SbomGenerationError(f"docker save failed for {image}: {result.stderr.strip()}")


def run_syft(syft_image: str, archive: Path, scratch_dir: Path, output_dir: Path, output_filename: str) -> None:
    """`scratch_dir` (mounted at /scratch) holds only Syft's own
    TMPDIR/cache/home - it is fully disposable. `output_dir` (mounted at
    /output) receives only the final SBOM file, so artifacts/sbom/ never
    accumulates scratch litter."""
    tmp_dir = scratch_dir / "tmp"
    cache_dir = scratch_dir / "cache"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--network", "none",
            "-e", "SYFT_CHECK_FOR_APP_UPDATE=false",
            "-e", "TMPDIR=/scratch/tmp",
            "-e", "XDG_CACHE_HOME=/scratch/cache",
            "-e", "HOME=/scratch",
            "-v", f"{archive}:/input.tar:ro",
            "-v", f"{scratch_dir}:/scratch",
            "-v", f"{output_dir}:/output",
            "--user", f"{os.getuid()}:{os.getgid()}",
            syft_image,
            "scan", "docker-archive:/input.tar",
            "-o", f"spdx-json=/output/{output_filename}",
        ],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode != 0:
        raise SbomGenerationError(f"syft scan failed: {result.stderr.strip()}")


def main() -> int:
    if shutil.which("docker") is None:
        print("generate_sbom: docker CLI not found on PATH", file=sys.stderr)
        return 1

    sl = load_scanner_lock()
    version = read_version()
    image = f"maops-docker-platform:{version}"
    syft_image = sl.get_syft_image()

    ARTIFACTS_SBOM_DIR.mkdir(parents=True, exist_ok=True)
    output_path = ARTIFACTS_SBOM_DIR / f"maops-docker-platform-{version}.spdx.json"

    print(f"generate_sbom: image={image} syft={syft_image}")
    print(f"generate_sbom: output={output_path}")

    with tempfile.TemporaryDirectory(prefix="maops-sbom-") as tmp_dir:
        archive = Path(tmp_dir) / "image.tar"
        scratch_dir = Path(tmp_dir) / "scratch"
        try:
            docker_save(image, archive)
            print(f"generate_sbom: saved exact release image archive ({archive.stat().st_size} bytes)")

            run_syft(syft_image, archive, scratch_dir, ARTIFACTS_SBOM_DIR, output_path.name)
        except SbomGenerationError as exc:
            print(f"generate_sbom: FAIL: {exc}", file=sys.stderr)
            return 1

    if not output_path.is_file():
        print(f"generate_sbom: FAIL: expected output not found at {output_path}", file=sys.stderr)
        return 1

    print(f"generate_sbom: PASS - SBOM written to {output_path} ({output_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
