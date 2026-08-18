"""Single authoritative version source: the repository-root VERSION file."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def get_version() -> str:
    """Return the application version, read fresh from VERSION on every call."""
    return _VERSION_FILE.read_text(encoding="utf-8").strip()
