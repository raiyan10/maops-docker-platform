"""Deliberately small, explicit gateway configuration.

Four environment variables are read directly: GATEWAY_HOST, GATEWAY_PORT,
UPSTREAM_HOST, UPSTREAM_PORT. A fifth, PLATFORM_CONFIG_PATH, is read only
indirectly by platform_config.py to locate the mounted, non-secret
platform config file (default /etc/maops/platform.json) - it never
carries configuration *values* itself, only an optional path override. No
other environment variable is ever inspected or exposed by this module or
by anything that consumes it.

UPSTREAM_HOST/UPSTREAM_PORT are the *only* destination the gateway will
ever connect to (see server.py) - they are fixed at process startup from
this narrow, validated configuration, never derived from an incoming
request. This is what prevents the gateway from being an open/SSRF-style
proxy to an arbitrary destination.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from gateway.platform_config import load_platform_config

DEFAULT_GATEWAY_HOST = "0.0.0.0"
DEFAULT_GATEWAY_PORT = 8080
DEFAULT_UPSTREAM_HOST = "app"
DEFAULT_UPSTREAM_PORT = 8080
DEFAULT_NAME = "maops-docker-platform-gateway"

MIN_PORT = 1
MAX_PORT = 65535

# The actual default lives in
# platform_config.DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS (used when no
# mounted platform config overrides it) - bounded so the gateway can never
# hang indefinitely waiting on the backend, and - as of Day 5 - guaranteed
# by platform_config.py's own load-time invariant check to exceed app's own
# inner state_dependency_timeout_seconds by at least
# timeout_safety_margin_seconds (closes Day 3 finding A-6, cross-hop
# timeout stacking - see platform_config.py's module docstring and
# docs/reliability.md). http.client.HTTPConnection applies a single
# socket-level timeout to both the connect and the subsequent read/recv, so
# one value covers the whole request. See
# GatewayConfig.upstream_timeout_seconds.


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    upstream_host: str
    upstream_port: int
    upstream_timeout_seconds: float
    name: str


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
        raise ValueError(
            f"{var_name} must be between {MIN_PORT} and {MAX_PORT}, got {port}"
        )
    return port


def load_config(
    env: Mapping[str, str] | None = None, platform_config_path: Path | None = None
) -> GatewayConfig:
    """Build a GatewayConfig from an environment mapping (defaults to os.environ)."""
    source = os.environ if env is None else env

    host = source.get("GATEWAY_HOST", "").strip() or DEFAULT_GATEWAY_HOST
    upstream_host = source.get("UPSTREAM_HOST", "").strip() or DEFAULT_UPSTREAM_HOST

    raw_port = source.get("GATEWAY_PORT")
    port = (
        parse_port(raw_port, "GATEWAY_PORT")
        if raw_port is not None
        else DEFAULT_GATEWAY_PORT
    )

    raw_upstream_port = source.get("UPSTREAM_PORT")
    upstream_port = (
        parse_port(raw_upstream_port, "UPSTREAM_PORT")
        if raw_upstream_port is not None
        else DEFAULT_UPSTREAM_PORT
    )

    platform_cfg = load_platform_config(path=platform_config_path, env=source)

    return GatewayConfig(
        host=host,
        port=port,
        upstream_host=upstream_host,
        upstream_port=upstream_port,
        upstream_timeout_seconds=platform_cfg.gateway_upstream_timeout_seconds,
        name=DEFAULT_NAME,
    )
