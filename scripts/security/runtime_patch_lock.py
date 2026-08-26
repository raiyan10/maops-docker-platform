"""Parses and validates security/runtime-patches.lock - this project's
small, public, version-controlled record of exact, checksum-pinned
Debian-security package overlays applied on top of the pinned Distroless
final runtime (see the `security-patch` stage in docker/app/Dockerfile).

SCOPE: this is a narrow, project-specific parser for one small KEY=value
file, not a general lockfile format - mirrors
scripts/security/scanner_lock.py's own approach for the same reason: a
single source of truth read by the Dockerfile checker
(scripts/lint/check_dockerfile.py), the image-level audit
(scripts/build/image_audit.py), and this module's own tests, rather than
duplicating the pinned strings across all three.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME_PATCHES_LOCK_PATH = REPO_ROOT / "security" / "runtime-patches.lock"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_LIBSSL_KEYS = (
    "LIBSSL_CVE",
    "LIBSSL_PACKAGE",
    "LIBSSL_SOURCE_PACKAGE",
    "LIBSSL_VULNERABLE_VERSION",
    "LIBSSL_VERSION",
    "LIBSSL_ARCH",
    "LIBSSL_SUITE",
    "LIBSSL_ARCHIVE",
    "LIBSSL_SNAPSHOT_TIMESTAMP",
    "LIBSSL_URL",
    "LIBSSL_DEB_SHA256",
    "LIBSSL_DEB_SIZE_BYTES",
    "LIBSSL_SO_SHA256",
    "LIBCRYPTO_SO_SHA256",
)
_SHA256_KEYS = ("LIBSSL_DEB_SHA256", "LIBSSL_SO_SHA256", "LIBCRYPTO_SO_SHA256")


class RuntimePatchLockError(ValueError):
    pass


def parse_runtime_patch_lock(text: str) -> dict[str, str]:
    """Parse KEY=value lines (comments and blank lines ignored). Raises
    RuntimePatchLockError on a malformed line, a duplicate key, a missing
    required key, a *_SHA256 value that isn't a well-formed 64-hex-char
    digest, or a URL that isn't HTTPS - a malformed lock file is a real
    configuration error, not something to skip silently."""
    entries: dict[str, str] = {}
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimePatchLockError(f"runtime-patches.lock line {line_no}: not a KEY=value line: {raw_line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimePatchLockError(f"runtime-patches.lock line {line_no}: empty key")
        if key in entries:
            raise RuntimePatchLockError(f"runtime-patches.lock line {line_no}: duplicate key {key!r}")
        entries[key] = value

    missing = [key for key in _REQUIRED_LIBSSL_KEYS if key not in entries]
    if missing:
        raise RuntimePatchLockError(f"runtime-patches.lock is missing required key(s): {missing}")

    for key in _SHA256_KEYS:
        if not _SHA256_PATTERN.match(entries[key]):
            raise RuntimePatchLockError(
                f"runtime-patches.lock: {key} is not a well-formed sha256 digest (64 hex chars): {entries[key]!r}"
            )

    if not entries["LIBSSL_URL"].startswith("https://"):
        raise RuntimePatchLockError(
            f"runtime-patches.lock: LIBSSL_URL must be an https:// URL: {entries['LIBSSL_URL']!r}"
        )

    return entries


def load_runtime_patch_lock(path: Path | None = None) -> dict[str, str]:
    lock_path = path or RUNTIME_PATCHES_LOCK_PATH
    if not lock_path.is_file():
        raise RuntimePatchLockError(f"runtime patches lock file not found: {lock_path}")
    return parse_runtime_patch_lock(lock_path.read_text(encoding="utf-8"))
