#!/usr/bin/env python3
"""Project-specific Compose *structural* validator.

SCOPE (read this before trusting the result): this validates Day 3
invariants specific to *this* project's `compose.yaml` (exactly three
services, `app`/`gateway`/`state` naming, hardening flags, health/
dependency wiring, network topology and isolation, the named persistence
volume, the mounted platform config, upstream-target-vs-real-service
cross-checks, image-version consistency with `VERSION`), plus (Day 5, see
docs/reliability.md) explicit CPU/memory/PID resource limits, a bounded
`on-failure:3` restart policy, and a `stop_grace_period`, against the
*rendered* configuration (`docker compose config --format json`), parsed
with Python stdlib `json` rather than adding a YAML dependency. It is
deliberately not a general-purpose Compose linter and does not replace
`docker compose config`'s own YAML-syntax validation (which this script
also exercises, implicitly, by shelling out to it and failing loudly on a
nonzero exit).

Day 5's `_parse_duration_seconds()` is deliberately tolerant of more than
one JSON shape for `stop_grace_period` - Compose's `Duration` type has
rendered as either a nanosecond integer or a Go-duration string
(`"10s"`) across different `docker compose` versions, and this project's
own pre-existing `check_healthchecks()` above already avoided depending on
a single fixed shape for the structurally identical `interval`/`timeout`/
`start_period` fields for the same reason - it checks only `test`, never
their numeric values.

This is a *static/structural* check only: it proves what Compose was
*asked* to run, not what the resulting containers actually do at runtime
(a valid, harmless-looking config could still describe a container that,
once started, behaves differently). scripts/compose/compose_integration.py
is the runtime counterpart -- it inspects real Compose-managed containers,
not merely this rendered config.

Closes two Day 2 review findings by widening this script's scope, not by
adding unrelated checks: (L-1, day-02-compose-review.md) `UPSTREAM_HOST`
now IS cross-checked against the real service set and the real shared
network, extended here to app's own `STATE_HOST` target too; and this
script's own network/volume/config checks are new Day 3 invariants, not a
Day 2 carryover.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_SERVICES = {"app", "gateway", "state"}
# Absolute interpreter path (Day 4): the release image's final runtime is
# Distroless (no shell, so no PATH resolution for a bare "python3" name).
PYTHON_BIN = "/usr/bin/python3.13"
EXPECTED_APP_HEALTHCHECK = ["CMD", PYTHON_BIN, "-m", "app.healthcheck"]
EXPECTED_GATEWAY_HEALTHCHECK = ["CMD", PYTHON_BIN, "-m", "gateway.healthcheck"]
EXPECTED_STATE_HEALTHCHECK = ["CMD", PYTHON_BIN, "-m", "state.healthcheck"]

EXPECTED_EDGE_MEMBERS = {"gateway", "app"}
EXPECTED_BACKEND_MEMBERS = {"app", "state"}
EXPECTED_NETWORK_NAMES = {"edge", "backend"}

EXPECTED_VOLUME_NAMES = {"state_data"}
EXPECTED_CONFIG_NAMES = {"platform"}
CONFIG_TARGET = "/etc/maops/platform.json"

# Day 5 reliability targets (see docs/reliability.md) - explicit,
# reviewable resource/restart/shutdown bounds applied identically to all
# three services, unless real evidence requires a documented adjustment.
EXPECTED_CPUS = 0.50
EXPECTED_MEM_LIMIT_BYTES = 128 * 1024 * 1024
EXPECTED_PIDS_LIMIT = 64
EXPECTED_RESTART_POLICY_NAME = "on-failure"
EXPECTED_RESTART_MAX_ATTEMPTS = 3
EXPECTED_STOP_GRACE_PERIOD_SECONDS = 10


class Finding:
    def __init__(self, message: str) -> None:
        self.message = message

    def __str__(self) -> str:
        return self.message


VERSION_FALLBACK_PATTERN = re.compile(r"\$\{VERSION:-([^}]+)\}")


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def check_version_fallback_defaults(version: str) -> list[Finding]:
    """Cross-checks every `${VERSION:-<literal>}` fallback in the raw
    compose.yaml text against VERSION exactly.

    `docker compose config` always resolves interpolation before this
    script ever sees it, so a stale hardcoded fallback default (e.g. a
    forgotten update after bumping VERSION) would otherwise never be
    caught while VERSION happens to be exported in the environment (as
    `make` always does) - the rendered value would look correct even
    though the raw fallback literal has silently drifted. This reads the
    raw source text instead, specifically so that drift can't pass
    release-check silently.
    """
    text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    mismatched = sorted({m for m in VERSION_FALLBACK_PATTERN.findall(text) if m != version})
    if mismatched:
        return [
            Finding(
                f"compose.yaml declares ${{VERSION:-<default>}} fallback literal(s) "
                f"that do not match VERSION ({version!r}): {mismatched}"
            )
        ]
    return []


def render_config() -> dict:
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose config failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def check_service_set(config: dict) -> list[Finding]:
    services = config.get("services", {})
    names = set(services.keys())
    if names != EXPECTED_SERVICES:
        return [
            Finding(
                f"expected exactly services {sorted(EXPECTED_SERVICES)}, got {sorted(names)}"
            )
        ]
    return []


def check_image_version(config: dict, version: str) -> list[Finding]:
    findings: list[Finding] = []
    expected_image = f"maops-docker-platform:{version}"
    for name, service in config.get("services", {}).items():
        image = service.get("image")
        if image != expected_image:
            findings.append(
                Finding(
                    f"service {name!r}: image is {image!r}, expected {expected_image!r} "
                    f"(matching VERSION file)"
                )
            )
    return findings


def check_app_state_not_published(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    for name in ("app", "state"):
        ports = config.get("services", {}).get(name, {}).get("ports") or []
        if ports:
            findings.append(Finding(f"service {name!r} must not publish a host port, found: {ports}"))
    return findings


def check_gateway_sole_publisher_loopback(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    services = config.get("services", {})
    for name, service in services.items():
        if name == "gateway":
            continue
        if service.get("ports"):
            findings.append(
                Finding(f"service {name!r} publishes a host port; only 'gateway' may")
            )

    gateway_ports = services.get("gateway", {}).get("ports") or []
    if not gateway_ports:
        findings.append(Finding("service 'gateway' does not publish any host port"))
    for port in gateway_ports:
        host_ip = port.get("host_ip")
        if host_ip != "127.0.0.1":
            findings.append(
                Finding(
                    f"service 'gateway' port publication is not loopback-only "
                    f"(host_ip={host_ip!r}, expected '127.0.0.1'): {port}"
                )
            )
        if port.get("target") != 8080:
            findings.append(
                Finding(f"service 'gateway' published port target is not 8080: {port}")
            )
    return findings


def check_hardening_flags(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    for name, service in config.get("services", {}).items():
        if service.get("read_only") is not True:
            findings.append(Finding(f"service {name!r}: read_only is not true"))

        cap_drop = [c.upper() for c in (service.get("cap_drop") or [])]
        if "ALL" not in cap_drop:
            findings.append(Finding(f"service {name!r}: cap_drop does not include ALL"))

        security_opt = service.get("security_opt") or []
        if not any(
            "no-new-privileges" in opt and "true" in opt for opt in security_opt
        ):
            findings.append(
                Finding(f"service {name!r}: security_opt missing no-new-privileges:true")
            )

        if service.get("privileged", False) is not False:
            findings.append(Finding(f"service {name!r}: privileged must be false/absent"))

        if service.get("pid") == "host":
            findings.append(Finding(f"service {name!r}: pid must not be 'host'"))

        if service.get("network_mode") == "host":
            findings.append(Finding(f"service {name!r}: network_mode must not be 'host'"))

        for volume in service.get("volumes") or []:
            source = str(volume.get("source", ""))
            target = str(volume.get("target", ""))
            if "docker.sock" in source or "docker.sock" in target:
                findings.append(
                    Finding(f"service {name!r}: Docker socket mount detected: {volume}")
                )

    return findings


def check_healthchecks(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    services = config.get("services", {})
    expected = {
        "app": EXPECTED_APP_HEALTHCHECK,
        "gateway": EXPECTED_GATEWAY_HEALTHCHECK,
        "state": EXPECTED_STATE_HEALTHCHECK,
    }
    for name, expected_test in expected.items():
        actual = services.get(name, {}).get("healthcheck", {}).get("test")
        if actual != expected_test:
            findings.append(
                Finding(
                    f"service {name!r}: healthcheck.test is {actual!r}, expected {expected_test!r}"
                )
            )
    return findings


def check_dependency_conditions(config: dict) -> list[Finding]:
    """Validates the Day 3 health-gated ordering chain: state -> app -> gateway."""
    findings: list[Finding] = []
    services = config.get("services", {})

    state_depends = services.get("state", {}).get("depends_on") or {}
    if state_depends:
        findings.append(
            Finding(f"service 'state' must have no depends_on (bottom of the chain), found: {state_depends}")
        )

    app_depends = services.get("app", {}).get("depends_on") or {}
    state_dep = app_depends.get("state")
    if state_dep is None:
        findings.append(Finding("service 'app' does not declare depends_on: state"))
    elif state_dep.get("condition") != "service_healthy":
        findings.append(
            Finding(
                f"service 'app' depends_on 'state' condition is "
                f"{state_dep.get('condition')!r}, expected 'service_healthy'"
            )
        )

    gateway_depends = services.get("gateway", {}).get("depends_on") or {}
    app_dep = gateway_depends.get("app")
    if app_dep is None:
        findings.append(Finding("service 'gateway' does not declare depends_on: app"))
    elif app_dep.get("condition") != "service_healthy":
        findings.append(
            Finding(
                f"service 'gateway' depends_on 'app' condition is "
                f"{app_dep.get('condition')!r}, expected 'service_healthy'"
            )
        )
    return findings


def _service_networks(config: dict, name: str) -> set[str]:
    return set((config.get("services", {}).get(name, {}).get("networks") or {}).keys())


def check_network_membership(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    expected = {
        "gateway": {"edge"},
        "app": {"edge", "backend"},
        "state": {"backend"},
    }
    for name, expected_networks in expected.items():
        actual = _service_networks(config, name)
        if actual != expected_networks:
            findings.append(
                Finding(
                    f"service {name!r}: networks are {sorted(actual)}, expected {sorted(expected_networks)}"
                )
            )
    return findings


def check_gateway_state_isolation(config: dict) -> list[Finding]:
    """Directly asserts gateway and state share no network - not merely
    implied by check_network_membership passing (a reader shouldn't have
    to derive the isolation property from two other checks)."""
    gateway_nets = _service_networks(config, "gateway")
    state_nets = _service_networks(config, "state")
    shared = gateway_nets & state_nets
    if shared:
        return [Finding(f"service 'gateway' and service 'state' share network(s), expected none: {sorted(shared)}")]
    return []


def check_top_level_networks(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    networks = config.get("networks") or {}
    names = set(networks.keys())
    if names != EXPECTED_NETWORK_NAMES:
        findings.append(
            Finding(f"expected exactly top-level networks {sorted(EXPECTED_NETWORK_NAMES)}, got {sorted(names)}")
        )

    backend = networks.get("backend") or {}
    if backend.get("internal") is not True:
        findings.append(Finding(f"network 'backend' must be internal: true, got: {backend}"))

    edge = networks.get("edge") or {}
    if edge.get("internal", False) is not False:
        findings.append(Finding(f"network 'edge' must not be internal, got: {edge}"))
    return findings


def check_state_volume(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    volumes = config.get("volumes") or {}
    names = set(volumes.keys())
    if names != EXPECTED_VOLUME_NAMES:
        findings.append(
            Finding(f"expected exactly top-level volumes {sorted(EXPECTED_VOLUME_NAMES)}, got {sorted(names)}")
        )

    services = config.get("services", {})
    state_volumes = services.get("state", {}).get("volumes") or []
    named_volume_mounts = [v for v in state_volumes if v.get("type") == "volume"]
    if len(named_volume_mounts) != 1:
        findings.append(
            Finding(f"service 'state' must mount exactly one named volume, found: {state_volumes}")
        )
    else:
        mount = named_volume_mounts[0]
        if mount.get("source") != "state_data" or mount.get("target") != "/data":
            findings.append(
                Finding(
                    f"service 'state' named-volume mount must be state_data:/data, found: {mount}"
                )
            )

    for name in ("app", "gateway"):
        volume_mounts = [v for v in (services.get(name, {}).get("volumes") or []) if v.get("type") == "volume"]
        if volume_mounts:
            findings.append(
                Finding(f"service {name!r} must not mount any named volume, found: {volume_mounts}")
            )
    return findings


def check_config_object(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    configs = config.get("configs") or {}
    names = set(configs.keys())
    if names != EXPECTED_CONFIG_NAMES:
        findings.append(
            Finding(f"expected exactly top-level configs {sorted(EXPECTED_CONFIG_NAMES)}, got {sorted(names)}")
        )

    platform_config = configs.get("platform") or {}
    expected_file = str((REPO_ROOT / "config" / "platform.json").resolve())
    actual_file = platform_config.get("file")
    if actual_file != expected_file:
        findings.append(
            Finding(f"config 'platform' file is {actual_file!r}, expected {expected_file!r}")
        )

    services = config.get("services", {})
    for name in EXPECTED_SERVICES:
        service_configs = services.get(name, {}).get("configs") or []
        matches = [
            c for c in service_configs if c.get("source") == "platform" and c.get("target") == CONFIG_TARGET
        ]
        if len(matches) != 1:
            findings.append(
                Finding(
                    f"service {name!r} must mount config 'platform' at {CONFIG_TARGET!r} exactly once, "
                    f"found: {service_configs}"
                )
            )
    return findings


def _parse_cpus(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_bytes(value: object) -> int | None:
    """`mem_limit` renders as a numeric *string* in `docker compose config
    --format json` (empirically confirmed against this project's own
    Docker Compose install - e.g. `"134217728"`, not the bare integer
    `pids_limit` renders as) - both shapes are accepted here rather than
    assuming either one, the same defensive-parsing approach
    `_parse_duration_seconds()` below already takes for the structurally
    similar `stop_grace_period` field."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


_DURATION_STRING_PATTERN = re.compile(
    r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+(?:\.\d+)?)s)?$"
)


def _parse_duration_seconds(value: object) -> float | None:
    """Tolerant parser: `docker compose config --format json` renders
    Compose's Duration type as a nanosecond integer in some versions and a
    Go-duration string (e.g. "10s") in others - this project's own
    check_healthchecks() above deliberately never depended on a single
    fixed shape for a Duration field, for the same reason. Magnitude alone
    disambiguates an integer/float: this project's own expected grace
    period (10s) is nowhere near the nanosecond range a real Duration would
    render at (10s == 10_000_000_000ns), so anything above 3600 is treated
    as nanoseconds, anything else as whole seconds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1_000_000_000 if value > 3600 else float(value)
    if isinstance(value, str):
        match = _DURATION_STRING_PATTERN.match(value.strip())
        if not match or not value.strip():
            return None
        parts = match.groupdict()
        if not any(parts.values()):
            return None
        return (
            int(parts["hours"] or 0) * 3600
            + int(parts["minutes"] or 0) * 60
            + float(parts["seconds"] or 0)
        )
    return None


CPU_EQUALITY_TOLERANCE = 0.01


def check_resource_limits(config: dict) -> list[Finding]:
    """Day 5: every service must have an explicit, reviewable CPU/memory/PID
    limit - real Docker HostConfig values a plain `docker compose up`
    applies (the non-Swarm `cpus`/`mem_limit`/`pids_limit` fields), not a
    Swarm-only `deploy.resources.limits` block ordinary Compose ignores.

    Day 6 (closes Day 5 finding L-1, day-05-resource-restart-review.md):
    tightened from "missing/zero/negative/above-target" to EXACT equality
    against the approved platform resource contract (a small float
    tolerance for `cpus`, matching how `scripts/reliability/
    reliability_check.py`'s `check_resource_limits_applied()` already
    compares the real Docker HostConfig values) - this project now has one
    single approved target per field, not a range, so a value that is
    valid-but-below-target (e.g. `pids_limit: 1`, which would almost
    certainly prevent the service from ever starting) is rejected at this
    cheap, static gate instead of only being caught one stage later at real
    Docker runtime."""
    findings: list[Finding] = []
    for name, service in config.get("services", {}).items():
        cpus = _parse_cpus(service.get("cpus"))
        if cpus is None or cpus <= 0:
            findings.append(Finding(f"service {name!r}: cpus is missing/zero/invalid: {service.get('cpus')!r}"))
        elif abs(cpus - EXPECTED_CPUS) > CPU_EQUALITY_TOLERANCE:
            findings.append(
                Finding(f"service {name!r}: cpus={cpus}, expected exactly {EXPECTED_CPUS}")
            )

        mem_limit = _parse_bytes(service.get("mem_limit"))
        if mem_limit is None or mem_limit <= 0:
            findings.append(
                Finding(f"service {name!r}: mem_limit is missing/zero/invalid: {service.get('mem_limit')!r}")
            )
        elif mem_limit != EXPECTED_MEM_LIMIT_BYTES:
            findings.append(
                Finding(
                    f"service {name!r}: mem_limit={mem_limit} bytes, expected exactly "
                    f"{EXPECTED_MEM_LIMIT_BYTES} bytes"
                )
            )

        pids_limit = _parse_bytes(service.get("pids_limit"))
        if pids_limit is None or pids_limit <= 0:
            findings.append(
                Finding(f"service {name!r}: pids_limit is missing/zero/unlimited: {service.get('pids_limit')!r}")
            )
        elif pids_limit != EXPECTED_PIDS_LIMIT:
            findings.append(
                Finding(f"service {name!r}: pids_limit={pids_limit}, expected exactly {EXPECTED_PIDS_LIMIT}")
            )
    return findings


def check_restart_policy(config: dict) -> list[Finding]:
    """Day 5: every service must have a bounded `on-failure:<N>` restart
    policy - never absent, never `always`/`unless-stopped` (which would
    also restart after an intentional stop or on every host reboot
    regardless of failure), and never an unbounded/missing retry count."""
    findings: list[Finding] = []
    for name, service in config.get("services", {}).items():
        restart = service.get("restart")
        if not isinstance(restart, str) or not restart.startswith(f"{EXPECTED_RESTART_POLICY_NAME}:"):
            findings.append(
                Finding(
                    f"service {name!r}: restart is {restart!r}, expected "
                    f"'{EXPECTED_RESTART_POLICY_NAME}:{EXPECTED_RESTART_MAX_ATTEMPTS}'"
                )
            )
            continue
        _, _, raw_count = restart.partition(":")
        try:
            max_attempts = int(raw_count)
        except ValueError:
            findings.append(Finding(f"service {name!r}: restart retry count is not an integer: {restart!r}"))
            continue
        if max_attempts != EXPECTED_RESTART_MAX_ATTEMPTS:
            findings.append(
                Finding(
                    f"service {name!r}: restart max attempts is {max_attempts}, "
                    f"expected {EXPECTED_RESTART_MAX_ATTEMPTS}"
                )
            )
    return findings


def check_stop_grace_period(config: dict) -> list[Finding]:
    """Day 5: every service must declare an explicit, bounded
    stop_grace_period - giving the role's existing SIGTERM handler a real
    window to exit cleanly before Docker escalates to SIGKILL."""
    findings: list[Finding] = []
    for name, service in config.get("services", {}).items():
        raw = service.get("stop_grace_period")
        seconds = _parse_duration_seconds(raw)
        if seconds is None or seconds <= 0:
            findings.append(Finding(f"service {name!r}: stop_grace_period is missing/zero/invalid: {raw!r}"))
        elif seconds != EXPECTED_STOP_GRACE_PERIOD_SECONDS:
            findings.append(
                Finding(
                    f"service {name!r}: stop_grace_period={seconds}s, expected "
                    f"{EXPECTED_STOP_GRACE_PERIOD_SECONDS}s"
                )
            )
    return findings


def check_upstream_targets(config: dict) -> list[Finding]:
    """Cross-checks gateway's UPSTREAM_HOST and app's STATE_HOST against the
    real service set, the real target port, AND the real shared network -
    closes Day 2 finding L-1 (day-02-compose-review.md), widened to app's
    own dependency on state.

    A typo (e.g. UPSTREAM_HOST=nonexistent-service) or a service that
    forgot to join the network its declared dependency actually lives on
    both fail this check immediately, rather than only being discoverable
    via a slow `make compose-test` run.
    """
    findings: list[Finding] = []
    services = config.get("services", {})

    def check_target(consumer: str, target_env_host: str, target_env_port: str, expected_target: str) -> None:
        env = services.get(consumer, {}).get("environment") or {}
        target_host = env.get(target_env_host)
        target_port = env.get(target_env_port)

        if target_host not in EXPECTED_SERVICES:
            findings.append(
                Finding(
                    f"service {consumer!r}: {target_env_host}={target_host!r} does not name a "
                    f"real service in this compose file {sorted(EXPECTED_SERVICES)}"
                )
            )
            return
        if target_host != expected_target:
            findings.append(
                Finding(
                    f"service {consumer!r}: {target_env_host}={target_host!r}, expected {expected_target!r}"
                )
            )

        target_service_port = str(services.get(target_host, {}).get("environment", {}).get(
            {"app": "APP_PORT", "state": "STATE_PORT"}.get(target_host, ""), ""
        ))
        if target_port and target_service_port and str(target_port) != target_service_port:
            findings.append(
                Finding(
                    f"service {consumer!r}: {target_env_port}={target_port!r} does not match "
                    f"{target_host!r}'s own bound port {target_service_port!r}"
                )
            )

        consumer_nets = _service_networks(config, consumer)
        target_nets = _service_networks(config, target_host)
        if not (consumer_nets & target_nets):
            findings.append(
                Finding(
                    f"service {consumer!r} and its dependency {target_host!r} share no network "
                    f"(consumer={sorted(consumer_nets)}, target={sorted(target_nets)}) - "
                    f"{target_env_host} would be unresolvable at runtime"
                )
            )

    check_target("gateway", "UPSTREAM_HOST", "UPSTREAM_PORT", "app")
    check_target("app", "STATE_HOST", "STATE_PORT", "state")
    return findings


def main() -> int:
    version = read_version()

    try:
        config = render_config()
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"check_compose.py: FAIL: {exc}", file=sys.stderr)
        return 1

    checks = [
        check_service_set,
        lambda c: check_image_version(c, version),
        lambda c: check_version_fallback_defaults(version),
        check_app_state_not_published,
        check_gateway_sole_publisher_loopback,
        check_hardening_flags,
        check_healthchecks,
        check_dependency_conditions,
        check_network_membership,
        check_gateway_state_isolation,
        check_top_level_networks,
        check_state_volume,
        check_config_object,
        check_upstream_targets,
        check_resource_limits,
        check_restart_policy,
        check_stop_grace_period,
    ]

    all_findings: list[Finding] = []
    for check in checks:
        all_findings.extend(check(config))

    if all_findings:
        print(f"check_compose.py: {len(all_findings)} finding(s):")
        for finding in all_findings:
            print(f"  {finding}")
        return 1

    print(
        f"check_compose.py: OK ({len(checks)} structural checks passed against "
        f"the rendered compose config, version={version})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
