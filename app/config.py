"""Deliberately small, explicit application configuration.

Only three environment variables are read: APP_HOST, APP_PORT, APP_NAME.
No other environment variable is ever inspected or exposed by this module
or by anything that consumes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
DEFAULT_NAME = "maops-docker-platform"

MIN_PORT = 1
MAX_PORT = 65535


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    name: str


def parse_port(raw: str) -> int:
    """Parse and validate an APP_PORT value.

    Accepts optional surrounding whitespace (int() strips it). Rejects
    non-integer values, and values outside 1-65535 inclusive.
    """
    if raw is None or not raw.strip():
        raise ValueError("APP_PORT must not be empty")
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"APP_PORT must be an integer, got {raw!r}") from exc
    if port < MIN_PORT or port > MAX_PORT:
        raise ValueError(
            f"APP_PORT must be between {MIN_PORT} and {MAX_PORT}, got {port}"
        )
    return port


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    """Build an AppConfig from an environment mapping (defaults to os.environ)."""
    source = os.environ if env is None else env

    host = source.get("APP_HOST", "").strip() or DEFAULT_HOST
    name = source.get("APP_NAME", "").strip() or DEFAULT_NAME

    raw_port = source.get("APP_PORT")
    port = parse_port(raw_port) if raw_port is not None else DEFAULT_PORT

    return AppConfig(host=host, port=port, name=name)
