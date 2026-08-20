"""Small stdlib-only loader for the Compose-mounted, non-secret platform config.

Reads the same `config/platform.json` file every service in this platform
mounts read-only (see compose.yaml's top-level `configs:` object). This
module validates only the fields the *state* service actually uses
(`state_filename`) - it deliberately does not care about
`dependency_timeout_seconds` (that's app's/gateway's concern), matching
this project's existing convention of small, independently maintained,
narrowly-scoped per-package modules rather than a shared library.

If the file is absent (e.g. a bare `docker run` outside Compose, or a unit
test that never mounts anything), sensible defaults are used silently -
this is not a secret or a required input, just an optional override. If
the file *is* present but malformed, loading fails loudly (a ValueError)
rather than silently falling back - a mounted, intentionally-provided
config that doesn't parse is a real operator error, not something to
paper over.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("/etc/maops/platform.json")
DEFAULT_PLATFORM_NAME = "maops-docker-platform"
DEFAULT_STATE_FILENAME = "state.json"

SCHEMA_VERSION = 1

# A bare filename only - no path separators, and never a directory-traversal
# token - since this value is joined onto the fixed /data mount point
# without any further sanitization.
_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_FORBIDDEN_FILENAMES = {".", ".."}


@dataclass(frozen=True)
class PlatformConfig:
    schema_version: int
    platform_name: str
    state_filename: str


def _resolve_path(path: Path | None, env: Mapping[str, str]) -> Path:
    if path is not None:
        return path
    override = env.get("PLATFORM_CONFIG_PATH", "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _validate_state_filename(value: object) -> str:
    if not isinstance(value, str) or not _FILENAME_PATTERN.match(value):
        raise ValueError(
            f"platform config 'state_filename' must be a bare filename "
            f"matching {_FILENAME_PATTERN.pattern!r}, got {value!r}"
        )
    if value in _FORBIDDEN_FILENAMES:
        raise ValueError(f"platform config 'state_filename' must not be {value!r}")
    return value


def load_platform_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> PlatformConfig:
    """Load and validate the platform config, or return defaults if absent."""
    source_env = os.environ if env is None else env
    config_path = _resolve_path(path, source_env)

    if not config_path.is_file():
        return PlatformConfig(
            schema_version=SCHEMA_VERSION,
            platform_name=DEFAULT_PLATFORM_NAME,
            state_filename=DEFAULT_STATE_FILENAME,
        )

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"platform config at {config_path} is unreadable: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"platform config at {config_path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"platform config at {config_path} must be a JSON object, got {type(data).__name__}")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"platform config 'schema_version' must be {SCHEMA_VERSION}, got {schema_version!r}"
        )

    platform_name = data.get("platform_name", DEFAULT_PLATFORM_NAME)
    if not isinstance(platform_name, str) or not platform_name.strip():
        raise ValueError(f"platform config 'platform_name' must be a non-empty string, got {platform_name!r}")

    state_filename = _validate_state_filename(data.get("state_filename", DEFAULT_STATE_FILENAME))

    return PlatformConfig(
        schema_version=SCHEMA_VERSION, platform_name=platform_name, state_filename=state_filename
    )
