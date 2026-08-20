"""Deliberately small, explicit state-service configuration.

Three environment variables are read directly: STATE_HOST, STATE_PORT,
STATE_NAME. A fourth, PLATFORM_CONFIG_PATH, is read only indirectly by
platform_config.py to locate the mounted, non-secret platform config file
(default /etc/maops/platform.json) - it never carries configuration
*values* itself, only an optional path override. No other environment
variable is ever inspected or exposed by this module or by anything that
consumes it.

The persisted-file *name* (not its fixed /data directory - see
storage.py) is a separate, narrower concern read from the Compose-mounted
platform config (see platform_config.py), not from the environment - it
is validated there as a bare filename, never an arbitrary path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from state.platform_config import load_platform_config

DEFAULT_STATE_HOST = "0.0.0.0"
DEFAULT_STATE_PORT = 8080
DEFAULT_NAME = "maops-docker-platform-state"

MIN_PORT = 1
MAX_PORT = 65535

# Fixed by design (see docs/persistence.md) - the platform's named volume is
# always mounted here; this is not env-configurable, unlike the filename
# within it, so no request or environment value can redirect state storage
# to an arbitrary host path.
DATA_DIR = Path("/data")


@dataclass(frozen=True)
class StateConfig:
    host: str
    port: int
    name: str
    data_dir: Path
    state_filename: str


def parse_port(raw: str, var_name: str) -> int:
    """Parse and validate a *_PORT value.

    Accepts optional surrounding whitespace (int() strips it). Rejects
    non-integer values, and values outside 1-65535 inclusive.
    """
    if raw is None or not raw.strip():
        raise ValueError(f"{var_name} must not be empty")
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{var_name} must be an integer, got {raw!r}") from exc
    if port < MIN_PORT or port > MAX_PORT:
        raise ValueError(f"{var_name} must be between {MIN_PORT} and {MAX_PORT}, got {port}")
    return port


def load_config(
    env: Mapping[str, str] | None = None, platform_config_path: Path | None = None
) -> StateConfig:
    """Build a StateConfig from an environment mapping (defaults to os.environ)."""
    source = os.environ if env is None else env

    host = source.get("STATE_HOST", "").strip() or DEFAULT_STATE_HOST
    name = source.get("STATE_NAME", "").strip() or DEFAULT_NAME

    raw_port = source.get("STATE_PORT")
    port = parse_port(raw_port, "STATE_PORT") if raw_port is not None else DEFAULT_STATE_PORT

    platform_cfg = load_platform_config(path=platform_config_path, env=source)

    return StateConfig(
        host=host,
        port=port,
        name=name,
        data_dir=DATA_DIR,
        state_filename=platform_cfg.state_filename,
    )
