"""Small stdlib-only loader for the Compose-mounted, non-secret platform config.

Reads the same `config/platform.json` file every service in this platform
mounts read-only (see compose.yaml's top-level `configs:` object).

Day 5 (closes Day 3 finding A-6, "cross-hop timeout stacking"): the
`gateway -> app -> state` chain used to apply the *same* generic
`dependency_timeout_seconds` value independently at each hop, with no
awareness of how much of that budget the hop below had already spent -
during a `state` outage, the outermost caller's effective worst-case
failure-detection latency could be up to ~2x the advertised single-hop
value (see `docs/reliability.md` and the historical
`day-03-networking-review.md` M-1 finding). This module now owns the
platform's explicit two-hop timeout *hierarchy*, not just its own hop:

- `gateway_upstream_timeout_seconds` - the *outer* budget, gateway's own
  bounded call to `app`'s `/readyz`/`/state`/`/state/increment` (the field
  this module actually returns and `gateway/config.py` wires into
  `GatewayConfig.upstream_timeout_seconds`).
- `state_dependency_timeout_seconds` - the *inner* budget, app's own
  bounded call to `state` (app's own concern operationally -
  `app/platform_config.py` is what `app` itself loads - but validated here
  too, read-only, purely so the cross-hop invariant below can be checked
  against the *whole* shared config file, not just gateway's own field).
- `timeout_safety_margin_seconds` - the minimum required headroom between
  the two hops.

The required invariant, checked at load time (not merely documented):

    gateway_upstream_timeout_seconds > state_dependency_timeout_seconds
                                        + timeout_safety_margin_seconds

This is what makes the fix real rather than aspirational: if `state`
becomes unresponsive, `app`'s own inner timeout fires first and `app`
returns a controlled failure well before `gateway`'s outer timeout would
have expired on its own - the external caller's worst-case wait is bounded
by the single, larger `gateway_upstream_timeout_seconds` value, not by the
sum of two independently-expiring budgets. A `config/platform.json` that
violates this invariant fails to load (`ValueError`) rather than silently
shipping a still-broken timeout hierarchy - `gateway` is the service most
exposed to the consequence of a broken budget (it is what an external
caller's own timeout races against), so it is the one that enforces this,
matching this project's existing per-package validation convention rather
than introducing a shared cross-package validation module for three
fields.

If the file is absent (e.g. a bare `docker run` outside Compose, or a unit
test that never mounts anything), sensible defaults are used silently -
this is not a secret or a required input, just an optional override
(the shipped defaults - 5.0s outer, 2.0s inner, 1.0s margin - already
satisfy the invariant). If the file *is* present but malformed, loading
fails loudly (a `ValueError`) rather than silently falling back.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_CONFIG_PATH = Path("/etc/maops/platform.json")

DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS = 5.0
DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS = 2.0
DEFAULT_TIMEOUT_SAFETY_MARGIN_SECONDS = 1.0

MAX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS = 60.0
MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SAFETY_MARGIN_SECONDS = 30.0

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PlatformConfig:
    schema_version: int
    gateway_upstream_timeout_seconds: float
    state_dependency_timeout_seconds: float
    timeout_safety_margin_seconds: float


def _resolve_path(path: Path | None, env: Mapping[str, str]) -> Path:
    if path is not None:
        return path
    override = env.get("PLATFORM_CONFIG_PATH", "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _validate_timeout(value: object, field_name: str, max_value: float) -> float:
    """Strict numeric validation shared shape with app/platform_config.py's
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


def _validate_timeout_hierarchy(
    gateway_upstream_timeout_seconds: float,
    state_dependency_timeout_seconds: float,
    timeout_safety_margin_seconds: float,
) -> None:
    """The Day 5 A-6 closure invariant itself - see module docstring."""
    required_minimum = state_dependency_timeout_seconds + timeout_safety_margin_seconds
    if gateway_upstream_timeout_seconds <= required_minimum:
        raise ValueError(
            "platform config timeout hierarchy invariant violated: "
            f"gateway_upstream_timeout_seconds ({gateway_upstream_timeout_seconds}) "
            f"must be greater than state_dependency_timeout_seconds "
            f"({state_dependency_timeout_seconds}) + timeout_safety_margin_seconds "
            f"({timeout_safety_margin_seconds}) = {required_minimum}"
        )


def load_platform_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> PlatformConfig:
    """Load and validate the platform config, or return defaults if absent."""
    source_env = os.environ if env is None else env
    config_path = _resolve_path(path, source_env)

    if not config_path.is_file():
        return PlatformConfig(
            schema_version=SCHEMA_VERSION,
            gateway_upstream_timeout_seconds=DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
            state_dependency_timeout_seconds=DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS,
            timeout_safety_margin_seconds=DEFAULT_TIMEOUT_SAFETY_MARGIN_SECONDS,
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

    gateway_upstream_timeout_seconds = _validate_timeout(
        data.get("gateway_upstream_timeout_seconds", DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS),
        "gateway_upstream_timeout_seconds",
        MAX_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
    )
    state_dependency_timeout_seconds = _validate_timeout(
        data.get("state_dependency_timeout_seconds", DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS),
        "state_dependency_timeout_seconds",
        MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS,
    )
    timeout_safety_margin_seconds = _validate_timeout(
        data.get("timeout_safety_margin_seconds", DEFAULT_TIMEOUT_SAFETY_MARGIN_SECONDS),
        "timeout_safety_margin_seconds",
        MAX_TIMEOUT_SAFETY_MARGIN_SECONDS,
    )

    _validate_timeout_hierarchy(
        gateway_upstream_timeout_seconds, state_dependency_timeout_seconds, timeout_safety_margin_seconds
    )

    return PlatformConfig(
        schema_version=SCHEMA_VERSION,
        gateway_upstream_timeout_seconds=gateway_upstream_timeout_seconds,
        state_dependency_timeout_seconds=state_dependency_timeout_seconds,
        timeout_safety_margin_seconds=timeout_safety_margin_seconds,
    )
