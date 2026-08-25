"""Deliberately small, explicit application configuration.

Five environment variables are read directly: APP_HOST, APP_PORT,
APP_NAME, STATE_HOST, STATE_PORT. A sixth, PLATFORM_CONFIG_PATH, is read
only indirectly by platform_config.py to locate the mounted, non-secret
platform config file (default /etc/maops/platform.json) - it never
carries configuration *values* itself, only an optional path override. No
other environment variable is ever inspected or exposed by this module or
by anything that consumes it.

STATE_HOST/STATE_PORT are the *only* destination app will ever connect to
for its state dependency (see server.py) - fixed at process startup from
this narrow, validated configuration, never derived from an incoming
request. This mirrors gateway's own UPSTREAM_HOST/UPSTREAM_PORT design and
is what prevents app from becoming an SSRF-style proxy toward state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.platform_config import load_platform_config

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_NAME = "maops-docker-platform"
DEFAULT_STATE_HOST = "state"
DEFAULT_STATE_PORT = 8080

MIN_PORT = 1
MAX_PORT = 65535


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    name: str
    state_host: str
    state_port: int
    state_timeout_seconds: float


def parse_port(raw: str, var_name: str = "APP_PORT") -> int:
    """Parse and validate a *_PORT value (defaults to APP_PORT's own name).

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
        raise ValueError(
            f"{var_name} must be between {MIN_PORT} and {MAX_PORT}, got {port}"
        )
    return port


def load_config(
    env: Mapping[str, str] | None = None, platform_config_path: Path | None = None
) -> AppConfig:
    """Build an AppConfig from an environment mapping (defaults to os.environ)."""
    source = os.environ if env is None else env

    host = source.get("APP_HOST", "").strip() or DEFAULT_HOST
    name = source.get("APP_NAME", "").strip() or DEFAULT_NAME
    state_host = source.get("STATE_HOST", "").strip() or DEFAULT_STATE_HOST

    raw_port = source.get("APP_PORT")
    port = parse_port(raw_port) if raw_port is not None else DEFAULT_PORT

    raw_state_port = source.get("STATE_PORT")
    state_port = (
        parse_port(raw_state_port, "STATE_PORT") if raw_state_port is not None else DEFAULT_STATE_PORT
    )

    platform_cfg = load_platform_config(path=platform_config_path, env=source)

    return AppConfig(
        host=host,
        port=port,
        name=name,
        state_host=state_host,
        state_port=state_port,
        state_timeout_seconds=platform_cfg.state_dependency_timeout_seconds,
    )
