#!/usr/bin/env python3
"""Validates the generated SPDX JSON SBOM for maops-docker-platform.

SCOPE: this is a project-specific SBOM sanity/traceability checker, not a
general SPDX schema validator. It confirms the SBOM is what
scripts/security/generate_sbom.py claims to have produced and is honestly
usable as evidence - not that it is a perfectly schema-conformant SPDX
document in every field (that's Syft's own job to get right, and Syft is
a mature, widely used tool this project deliberately did not reimplement).

Be accurate about what this proves: presence of a Python-identified
package in the inventory is a plausible-identity signal (this project's
Day 4 release image's final runtime is Distroless's
`python3-debian13`), not a cryptographic proof the SBOM was generated
from the exact running image content - that stronger claim would require
re-deriving the image's own content-addressed digest independently, which
docs/supply-chain.md discusses honestly (Syft's SPDX `versionInfo` digest
for the scanned `docker-archive:` source is computed from the archive's
own config blob, which does not always equal `docker image inspect`'s
`.Id` on this project's Docker Desktop/containerd-image-store setup - a
real, documented four-way size/digest discrepancy this project's own Day 3
release review already surfaced for a different metric).

Day 4 (Distroless): the release image ships no `dpkg`/`apt` *executable*,
but Distroless images still carry the dpkg *status database*
(`/var/lib/dpkg/status.d/`) that records each installed OS package's
metadata - Syft's dpkg cataloger reads that database directly (it needs
no dpkg binary). This means the Python identity signal in the SBOM is a
dpkg-named package such as `python3.13-minimal` or
`libpython3.13-stdlib`, not a package literally named `python` (which
Debian's own `python:*-slim` images happened to also carry, but is not a
Distroless/dpkg naming guarantee). `check_sbom()` below matches any
package name containing "python" (case-insensitive) rather than the
exact literal "python", so this check reflects Distroless's actual dpkg
package naming instead of assuming a Debian-slim-specific package name.
Pure application `.py` files under `/app` never receive their own
individual SPDX File entries unless they ship Python package metadata
(e.g. a `dist-info`/`egg-info` directory) - this project's `app/`,
`gateway/`, `state/` are plain stdlib modules with no such metadata, so
Syft catalogs them as ordinary files, not as a Python "package". This is
expected Syft cataloger behavior for any stdlib-only Python application,
Distroless or not - not a Distroless-specific defect.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

LOCAL_PATH_PATTERN = re.compile(r"/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|[A-Za-z]:\\\\Users\\\\")
SECRET_SHAPED_PATTERN = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|password\s*=\s*\\?['\"]|"
    r"secret\s*=\s*\\?['\"])",
    re.IGNORECASE,
)


class SbomCheckError(ValueError):
    pass


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def check_sbom(path: Path, expected_version: str) -> list[str]:
    """Returns a list of finding strings; empty means the SBOM is clean."""
    findings: list[str] = []

    raw_text = path.read_text(encoding="utf-8")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return [f"SBOM is not valid JSON: {exc}"]

    spdx_version = data.get("spdxVersion", "")
    if not isinstance(spdx_version, str) or not spdx_version.startswith("SPDX-"):
        findings.append(f"missing/invalid SPDX document marker 'spdxVersion': {spdx_version!r}")

    packages = data.get("packages")
    if not isinstance(packages, list) or not packages:
        findings.append(f"SBOM package inventory is empty or missing: {type(packages).__name__}")

    package_names = {p.get("name") for p in packages if isinstance(p, dict)} if isinstance(packages, list) else set()
    has_python_package = any(isinstance(name, str) and "python" in name.lower() for name in package_names)
    if not has_python_package:
        findings.append(
            "SBOM package inventory does not include any Python-identified package "
            "(e.g. 'python3.13-minimal', 'libpython3.13-stdlib') - identity is not plausibly "
            "traceable to this project's Distroless python3-debian13-based release image"
        )

    if expected_version not in path.name:
        findings.append(f"SBOM filename {path.name!r} does not encode the expected VERSION {expected_version!r}")

    creation_info = data.get("creationInfo", {})
    creators = creation_info.get("creators", []) if isinstance(creation_info, dict) else []
    if not any(isinstance(c, str) and c.lower().startswith("tool: syft") for c in creators):
        findings.append(f"SBOM creationInfo.creators does not record a Syft tool reference: {creators!r}")

    local_path_matches = LOCAL_PATH_PATTERN.findall(raw_text)
    if local_path_matches:
        findings.append(f"SBOM appears to leak a local workstation path: {sorted(set(local_path_matches))[:5]}")

    secret_matches = SECRET_SHAPED_PATTERN.findall(raw_text)
    if secret_matches:
        findings.append("SBOM contains a secret-shaped string (private key header / AWS key / password=)")

    return findings


def main() -> int:
    version = read_version()
    if len(sys.argv) > 1:
        sbom_path = Path(sys.argv[1])
    else:
        sbom_path = REPO_ROOT / "artifacts" / "sbom" / f"maops-docker-platform-{version}.spdx.json"

    if not sbom_path.is_file():
        print(f"check_sbom: FAIL: no SBOM found at {sbom_path} (run 'make sbom' first)", file=sys.stderr)
        return 1

    findings = check_sbom(sbom_path, version)
    if findings:
        print(f"check_sbom: FAIL ({len(findings)} finding(s)) against {sbom_path}:")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    print(f"check_sbom: PASS - {sbom_path} is a valid, non-empty, traceable SPDX SBOM for VERSION={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
