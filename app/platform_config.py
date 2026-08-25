"""Small stdlib-only loader for the Compose-mounted, non-secret platform config.

Reads the same `config/platform.json` file every service in this platform
mounts read-only (see compose.yaml's top-level `configs:` object). This
module validates only the field the *app* service actually uses
(`state_dependency_timeout_seconds`, applied to app's own bounded call to
`state` - the *inner* hop of the `gateway -> app -> state` chain) - it
deliberately does not care about `state_filename` (that's state's own
concern) or `gateway_upstream_timeout_seconds`/`timeout_safety_margin_seconds`
(gateway's own concern, including the Day 5 cross-hop timeout-hierarchy
invariant it validates - see `gateway/platform_config.py`), matching this
project's existing convention of small, independently maintained,
narrowly-scoped per-package modules rather than a shared library.

Day 5 (closes Day 3 finding A-6, "cross-hop timeout stacking"): this field
used to be the same generic `dependency_timeout_seconds` gateway's own hop
also read, applied independently at each hop with no relative budgeting.
It is now `state_dependency_timeout_seconds` specifically - the *inner*
budget in an explicit two-hop timeout hierarchy (see
`gateway/platform_config.py`'s docstring and `docs/reliability.md` for the
full invariant). The obsolete shared name is not kept for backwards
compatibility - this is a v0.5.0 config-shape change, not a bug fix that
needs to interoperate with the old field.

If the file is absent (e.g. a bare `docker run` outside Compose, or a unit
test that never mounts anything), a sensible default timeout is used
silently - this is not a secret or a required input, just an optional
override. If the file *is* present but malformed, loading fails loudly (a
ValueError) rather than silently falling back.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("/etc/maops/platform.json")
DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS = 2.0
MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS = 30.0

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlatformConfig:
    schema_version: int
    state_dependency_timeout_seconds: float


def _resolve_path(path: Path | None, env: Mapping[str, str]) -> Path:
    if path is not None:
        return path
    override = env.get("PLATFORM_CONFIG_PATH", "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _validate_timeout(value: object, field_name: str, max_value: float) -> float:
    """Strict numeric validation shared shape with gateway/platform_config.py's
    own (independently maintained) validator: rejects bool (an int subclass
    in Python), any non-numeric type, non-finite values (NaN/+-infinity -
    Python's json module accepts these as an extension unless explicitly
    disabled, so they must be rejected here, not merely at parse time), and
    anything outside `(0, max_value]` - zero and negative values included."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"platform config {field_name!r} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"platform config {field_name!r} must be finite, got {value!r}")
    if not (0 < value <= max_value):
        raise ValueError(
            f"platform config {field_name!r} must be in (0, {max_value}], got {value!r}"
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
            state_dependency_timeout_seconds=DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS,
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

    timeout = _validate_timeout(
        data.get("state_dependency_timeout_seconds", DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS),
        "state_dependency_timeout_seconds",
        MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS,
    )

    return PlatformConfig(schema_version=SCHEMA_VERSION, state_dependency_timeout_seconds=timeout)
