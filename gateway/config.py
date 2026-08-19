"""Deliberately small, explicit gateway configuration.

Only four environment variables are read: GATEWAY_HOST, GATEWAY_PORT,
UPSTREAM_HOST, UPSTREAM_PORT. No other environment variable is ever
inspected or exposed by this module or by anything that consumes it.

UPSTREAM_HOST/UPSTREAM_PORT are the *only* destination the gateway will
ever connect to (see server.py) - they are fixed at process startup from
this narrow, validated configuration, never derived from an incoming
request. This is what prevents the gateway from being an open/SSRF-style
proxy to an arbitrary destination.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_GATEWAY_HOST = "0.0.0.0"
DEFAULT_GATEWAY_PORT = 8080
DEFAULT_UPSTREAM_HOST = "app"
DEFAULT_UPSTREAM_PORT = 8080
DEFAULT_NAME = "maops-docker-platform-gateway"

MIN_PORT = 1
MAX_PORT = 65535

# Bounded so the gateway can never hang indefinitely waiting on the
# backend. http.client.HTTPConnection applies a single socket-level
# timeout to both the connect and the subsequent read/recv, so one value
# covers the whole request.
UPSTREAM_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    upstream_host: str
    upstream_port: int
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


def load_config(env: Mapping[str, str] | None = None) -> GatewayConfig:
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

    return GatewayConfig(
        host=host,
        port=port,
        upstream_host=upstream_host,
        upstream_port=upstream_port,
        name=DEFAULT_NAME,
    )
