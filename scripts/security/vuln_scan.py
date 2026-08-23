#!/usr/bin/env python3
"""Generates a real Trivy JSON vulnerability report for the exact
maops-docker-platform release image, then enforces this project's
vulnerability policy against it (scripts/security/check_trivy_report.py).

Workflow (see docs/supply-chain.md):
  1. `docker save` the exact release image to a project-owned temporary
     archive (never a registry pull, never the live daemon socket).
  2. Run the pinned Trivy scanner container (security/scanners.lock,
     `TRIVY_IMAGE`) against that archive, mounted read-only, with the
     Docker daemon socket NOT mounted.
  3. Write JSON to artifacts/security/trivy-<VERSION>.json.
  4. Validate + enforce policy via check_trivy_report.py's evaluate_policy().

IMPORTANT (documented honestly, not silently): unlike Syft (SBOM
generation, fully offline after the pinned image + application image are
present), Trivy needs real network access to fetch/refresh its
vulnerability database. This means:
  - image build reproducibility is deterministic (see
    scripts/build/reproducibility_check.py) - the immutable image never
    changes between two scans.
  - vulnerability *results* depend on the vulnerability database
    snapshot/time this scan happened to run against - a later scan of the
    exact same immutable image may report different (typically more)
    CVEs as new ones are discovered and published upstream. This is
    expected and is not evidence the image itself changed.

This project never silently ignores a finding, never auto-generates a
.trivyignore, and never manufactures an exception. If policy enforcement
fails on a genuinely unavoidable finding (no fixed version exists yet,
and no newer base-image digest is available - see docs/build-security.md
for how that was checked for this release), this script reports it
plainly and exits non-zero, per .claude/CLAUDE.md's explicit instruction
for exactly this situation.
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
ARTIFACTS_SECURITY_DIR = REPO_ROOT / "artifacts" / "security"
SAVE_TIMEOUT_SECONDS = 120.0
SCAN_TIMEOUT_SECONDS = 600.0  # includes a first-run vulnerability-DB download


class VulnScanError(RuntimeError):
    pass


def _load_module(relative_path: str, name: str) -> ModuleType:
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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
        raise VulnScanError(f"docker save failed for {image}: {result.stderr.strip()}")


def run_trivy(trivy_image: str, archive: Path, cache_dir: Path, output_dir: Path, output_filename: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-e", "TRIVY_CACHE_DIR=/cache",
            "-v", f"{archive}:/input.tar:ro",
            "-v", f"{cache_dir}:/cache",
            "-v", f"{output_dir}:/output",
            "--user", f"{os.getuid()}:{os.getgid()}",
            trivy_image,
            "image", "--input", "/input.tar",
            "--format", "json",
            "--output", f"/output/{output_filename}",
        ],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT_SECONDS, check=False,
    )
    if result.returncode != 0:
        raise VulnScanError(f"trivy scan failed: {result.stderr.strip()[-4000:]}")


def main() -> int:
    if shutil.which("docker") is None:
        print("vuln_scan: docker CLI not found on PATH", file=sys.stderr)
        return 1

    sl = _load_module("scripts/security/scanner_lock.py", "scanner_lock_for_vuln_scan")
    ctr = _load_module("scripts/security/check_trivy_report.py", "check_trivy_report_for_vuln_scan")

    version = read_version()
    image = f"maops-docker-platform:{version}"
    trivy_image = sl.get_trivy_image()

    ARTIFACTS_SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    output_path = ARTIFACTS_SECURITY_DIR / f"trivy-{version}.json"

    print(f"vuln_scan: image={image} trivy={trivy_image}")
    print(f"vuln_scan: output={output_path}")
    print(
        "vuln_scan: NOTE - vulnerability *results* are time-varying (live Trivy DB); "
        "the image itself is deterministic (see reproducibility-check) but a later scan "
        "of this exact image may report different CVEs."
    )

    with tempfile.TemporaryDirectory(prefix="maops-vuln-") as tmp_dir:
        archive = Path(tmp_dir) / "image.tar"
        cache_dir = Path(tmp_dir) / "cache"
        try:
            docker_save(image, archive)
            print(f"vuln_scan: saved exact release image archive ({archive.stat().st_size} bytes)")

            run_trivy(trivy_image, archive, cache_dir, ARTIFACTS_SECURITY_DIR, output_path.name)
        except VulnScanError as exc:
            print(f"vuln_scan: FAIL: {exc}", file=sys.stderr)
            return 1

    if not output_path.is_file():
        print(f"vuln_scan: FAIL: expected output not found at {output_path}", file=sys.stderr)
        return 1

    raw_text = output_path.read_text(encoding="utf-8")
    data, findings = ctr.validate_report(raw_text, expected_image=image)
    if findings:
        print(f"vuln_scan: FAIL: report validation ({len(findings)} finding(s)):")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    assert data is not None
    policy = ctr.evaluate_policy(data)
    ctr.print_policy_report(policy)

    if not policy.passed:
        print("vuln_scan: FAIL - vulnerability policy violated (see above)", file=sys.stderr)
        return 1

    print(f"vuln_scan: PASS - {output_path} satisfies the vulnerability policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
