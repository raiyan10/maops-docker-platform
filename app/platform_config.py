"""Small stdlib-only loader for the Compose-mounted, non-secret platform config.

Reads the same `config/platform.json` file every service in this platform
mounts read-only (see compose.yaml's top-level `configs:` object). This
module validates only the field the *app* service actually uses
(`dependency_timeout_seconds`, applied to app's own bounded call to
`state`) - it deliberately does not care about `state_filename` (that's
state's own concern), matching this project's existing convention of
small, independently maintained, narrowly-scoped per-package modules
rather than a shared library.

If the file is absent (e.g. a bare `docker run` outside Compose, or a unit
test that never mounts anything), a sensible default timeout is used
silently - this is not a secret or a required input, just an optional
override. If the file *is* present but malformed, loading fails loudly (a
ValueError) rather than silently falling back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("/etc/maops/platform.json")
DEFAULT_DEPENDENCY_TIMEOUT_SECONDS = 3.0
MAX_DEPENDENCY_TIMEOUT_SECONDS = 30.0

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlatformConfig:
    schema_version: int
    dependency_timeout_seconds: float


def _resolve_path(path: Path | None, env: Mapping[str, str]) -> Path:
    if path is not None:
        return path
    override = env.get("PLATFORM_CONFIG_PATH", "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"platform config 'dependency_timeout_seconds' must be a number, got {value!r}")
    if not (0 < value <= MAX_DEPENDENCY_TIMEOUT_SECONDS):
        raise ValueError(
            f"platform config 'dependency_timeout_seconds' must be in "
            f"(0, {MAX_DEPENDENCY_TIMEOUT_SECONDS}], got {value!r}"
        )
    return float(value)


def load_platform_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> PlatformConfig:
    """Load and validate the platform config, or return defaults if absent."""
    source_env = os.environ if env is None else env
    config_path = _resolve_path(path, source_env)

    if not config_path.is_file():
        return PlatformConfig(
            schema_version=SCHEMA_VERSION,
            dependency_timeout_seconds=DEFAULT_DEPENDENCY_TIMEOUT_SECONDS,
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
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"platform config 'schema_version' must be {SCHEMA_VERSION}, got {schema_version!r}"
        )

    timeout = _validate_timeout(data.get("dependency_timeout_seconds", DEFAULT_DEPENDENCY_TIMEOUT_SECONDS))

    return PlatformConfig(schema_version=SCHEMA_VERSION, dependency_timeout_seconds=timeout)
