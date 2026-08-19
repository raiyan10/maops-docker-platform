#!/usr/bin/env python3
"""Project-specific Compose *structural* validator.

SCOPE (read this before trusting the result): this validates Day 2
invariants specific to *this* project's `compose.yaml` (exactly two
services, `app`/`gateway` naming, hardening flags, health/dependency
wiring, image-version consistency with `VERSION`) against the *rendered*
configuration (`docker compose config --format json`), parsed with Python
stdlib `json` rather than adding a YAML dependency. It is deliberately not
a general-purpose Compose linter and does not replace `docker compose
config`'s own YAML-syntax validation (which this script also exercises,
implicitly, by shelling out to it and failing loudly on a nonzero exit).

This is a *static/structural* check only: it proves what Compose was
*asked* to run, not what the resulting containers actually do at runtime
(a valid, harmless-looking config could still describe a container that,
once started, behaves differently). scripts/compose/compose_integration.py
is the runtime counterpart -- it inspects real Compose-managed containers,
not merely this rendered config -- and is what closes the Day 1
test-review gap (M-3) that no automated check exercised anything beyond
`docker compose config`'s own syntax validity.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXPECTED_SERVICES = {"app", "gateway"}
EXPECTED_APP_HEALTHCHECK = ["CMD", "python3", "-m", "app.healthcheck"]
EXPECTED_GATEWAY_HEALTHCHECK = ["CMD", "python3", "-m", "gateway.healthcheck"]


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


def check_app_not_published(config: dict) -> list[Finding]:
    app = config.get("services", {}).get("app", {})
    ports = app.get("ports") or []
    if ports:
        return [Finding(f"service 'app' must not publish a host port, found: {ports}")]
    return []


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


def check_no_named_volumes(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    top_level_volumes = config.get("volumes") or {}
    if top_level_volumes:
        findings.append(
            Finding(f"top-level 'volumes' must be absent on Day 2, found: {top_level_volumes}")
        )
    for name, service in config.get("services", {}).items():
        for volume in service.get("volumes") or []:
            if volume.get("type") == "volume":
                findings.append(
                    Finding(f"service {name!r}: named/persistent volume mount found: {volume}")
                )
    return findings


def check_healthchecks(config: dict) -> list[Finding]:
    findings: list[Finding] = []
    services = config.get("services", {})

    app_test = services.get("app", {}).get("healthcheck", {}).get("test")
    if app_test != EXPECTED_APP_HEALTHCHECK:
        findings.append(
            Finding(
                f"service 'app': healthcheck.test is {app_test!r}, expected "
                f"{EXPECTED_APP_HEALTHCHECK!r}"
            )
        )

    gateway_test = services.get("gateway", {}).get("healthcheck", {}).get("test")
    if gateway_test != EXPECTED_GATEWAY_HEALTHCHECK:
        findings.append(
            Finding(
                f"service 'gateway': healthcheck.test is {gateway_test!r}, expected "
                f"{EXPECTED_GATEWAY_HEALTHCHECK!r}"
            )
        )
    return findings


def check_gateway_depends_on_app(config: dict) -> list[Finding]:
    gateway = config.get("services", {}).get("gateway", {})
    depends_on = gateway.get("depends_on") or {}
    app_dep = depends_on.get("app")
    if app_dep is None:
        return [Finding("service 'gateway' does not declare depends_on: app")]
    if app_dep.get("condition") != "service_healthy":
        return [
            Finding(
                f"service 'gateway' depends_on 'app' condition is "
                f"{app_dep.get('condition')!r}, expected 'service_healthy'"
            )
        ]
    return []


def check_no_custom_networks(config: dict) -> list[Finding]:
    """Day 2 must use only the Compose-implicit default network.

    `docker compose config` always renders a `default` network entry even
    when none is declared in compose.yaml -- that's Compose's own default,
    not a custom network. What must be absent is any *other* network name,
    or a `default` entry carrying custom attributes (a non-empty `driver`,
    `driver_opts`, `ipam.config`, etc.) that would indicate a hand-authored
    network definition rather than the implicit one.
    """
    findings: list[Finding] = []
    networks = config.get("networks") or {}
    extra = set(networks.keys()) - {"default"}
    if extra:
        findings.append(Finding(f"custom network(s) declared, not permitted on Day 2: {sorted(extra)}"))

    default_net = networks.get("default") or {}
    suspicious_keys = {"driver", "driver_opts"}
    ipam = default_net.get("ipam") or {}
    if suspicious_keys & set(default_net.keys()):
        findings.append(
            Finding(f"default network carries custom attributes, not permitted on Day 2: {default_net}")
        )
    if ipam.get("config"):
        findings.append(
            Finding(f"default network has custom IPAM config, not permitted on Day 2: {ipam}")
        )

    for name, service in config.get("services", {}).items():
        service_networks = set((service.get("networks") or {}).keys())
        if service_networks - {"default"}:
            findings.append(
                Finding(f"service {name!r} attached to non-default network(s): {service_networks}")
            )
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
        check_app_not_published,
        check_gateway_sole_publisher_loopback,
        check_hardening_flags,
        check_no_named_volumes,
        check_healthchecks,
        check_gateway_depends_on_app,
        check_no_custom_networks,
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
