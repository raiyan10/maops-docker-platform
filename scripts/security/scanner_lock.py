"""Parses and validates security/scanners.lock - this project's small,
public, version-controlled record of exact, digest-pinned scanner
container image references (Syft, Trivy).

SCOPE: this is a narrow, project-specific parser/validator for one small
KEY=value file, not a general lockfile format. It exists so every script
that launches a scanner container (scripts/security/generate_sbom.py,
scripts/security/vuln_scan.py) and every test that validates the pins
themselves reads from a single source of truth, rather than duplicating
the digest strings.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCANNERS_LOCK_PATH = REPO_ROOT / "security" / "scanners.lock"

# tag@sha256:<64 hex> - never a bare tag, never `latest`.
_PINNED_REFERENCE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class ScannerLockError(ValueError):
    pass


def parse_scanner_lock(text: str) -> dict[str, str]:
    """Parse KEY=value lines (comments and blank lines ignored) and
    validate every value is an exact, well-formed `ref@sha256:<64 hex>`
    pin. Raises ScannerLockError on any malformed or non-digest-pinned
    entry - a malformed lock file is a real configuration error, not
    something to skip silently."""
    entries: dict[str, str] = {}
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ScannerLockError(f"scanners.lock line {line_no}: not a KEY=value line: {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ScannerLockError(f"scanners.lock line {line_no}: empty key")
        if "@" not in value:
            raise ScannerLockError(
                f"scanners.lock line {line_no}: {key} is not digest-pinned (must be tag@sha256:<64 hex>): {value!r}"
            )
        tag_part = value.split("@", 1)[0]
        if tag_part.endswith(":latest") or ":" not in tag_part:
            raise ScannerLockError(
                f"scanners.lock line {line_no}: {key} must use an explicit non-latest tag alongside its digest pin: {value!r}"
            )
        if not _PINNED_REFERENCE_PATTERN.match(value):
            raise ScannerLockError(
                f"scanners.lock line {line_no}: {key} is not a well-formed digest pin: {value!r}"
            )
        if key in entries:
            raise ScannerLockError(f"scanners.lock line {line_no}: duplicate key {key!r}")
        entries[key] = value
    return entries


def load_scanner_lock(path: Path | None = None) -> dict[str, str]:
    lock_path = path or SCANNERS_LOCK_PATH
    if not lock_path.is_file():
        raise ScannerLockError(f"scanners lock file not found: {lock_path}")
    return parse_scanner_lock(lock_path.read_text(encoding="utf-8"))


def get_syft_image(path: Path | None = None) -> str:
    entries = load_scanner_lock(path)
    try:
        return entries["SYFT_IMAGE"]
    except KeyError as exc:
        raise ScannerLockError("scanners.lock is missing SYFT_IMAGE") from exc


def get_trivy_image(path: Path | None = None) -> str:
    entries = load_scanner_lock(path)
    try:
        return entries["TRIVY_IMAGE"]
    except KeyError as exc:
        raise ScannerLockError("scanners.lock is missing TRIVY_IMAGE") from exc
