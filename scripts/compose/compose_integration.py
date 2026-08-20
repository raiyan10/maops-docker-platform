#!/usr/bin/env python3
"""Real Compose stack integration test.

SCOPE: this is the *runtime* counterpart to scripts/compose/check_compose.py
(which only validates the *rendered* `docker compose config`, never a real
container). It brings up the actual three-service Day 3 stack
(`state` -> `app` -> `gateway`) under a uniquely named Compose project, on
a dynamic loopback-only host port, and proves real behavior:

- all three services reach Docker-health `healthy`, in the right order
  (state before app starts, app before gateway starts - not just
  eventually-all-healthy; this is a genuine timestamp-based ordering
  proof, closing Day 2 finding M-1, day-02-compose-review.md)
- `app`/`state` publish no host port; `gateway` publishes only the
  expected loopback port
- real network segmentation: gateway<->app and app<->state communicate;
  gateway and state cannot resolve or reach each other at all
- a genuine gateway -> app -> state persistence path (increment via the
  public gateway port, observe the value survive container recreation and
  a full `compose down`/`up` cycle with the volume retained)
- stopping `state` degrades `app`'s and then `gateway`'s readiness while
  both processes stay alive, and restarting `state` recovers both
- every Compose-managed container carries the same hardening this
  project's direct-`docker run` checks already prove (read-only rootfs
  *and*, closing Day 2 finding M-1 from day-02-security-review.md /
  day-02-compose-review.md L-2, a real [D] rejected-write proof - not
  merely the [C] "Docker was asked" check Day 2 shipped), cap_drop ALL,
  no-new-privileges, non-root UID/GID, no host PID/network, no Docker
  socket, plus each role's expected PID 1 identity
- the mounted platform config is genuinely read-only inside every
  container (a real rejected write, not just `docker inspect`)
- `state`'s /data mount is genuinely writable despite the read-only rootfs

Deliberately reuses scripts/verify/security_check.py's existing [C]/[D]
container-inspection check functions (they already operate generically on
any container name, Compose-managed or not) rather than reimplementing the
same `docker inspect`/`docker exec` logic a second time.

Uses its own uniquely named Compose project (`maops-compose-<uuid>`) and
tears it down - on success or failure - via `docker compose ... down -v`,
removing only this run's own named volume. Never touches any other
Compose project or Docker resource, and never runs a global prune.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "compose.yaml"

UP_TIMEOUT_SECONDS = 150.0
LIFECYCLE_TIMEOUT_SECONDS = 30.0
DOWN_TIMEOUT_SECONDS = 60.0
STARTUP_HEALTHY_DEADLINE_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.5
DEGRADE_DEADLINE_SECONDS = 30.0
RECOVER_DEADLINE_SECONDS = 60.0

CONFIG_TARGET_PATH = "/etc/maops/platform.json"
PROTECTED_ROOTFS_PROBE_PATH = "/etc/maops-readonly-probe"

EXPECTED_NETWORKS = {
    "gateway": {"edge"},
    "app": {"edge", "backend"},
    "state": {"backend"},
}


class ComposeIntegrationError(RuntimeError):
    pass


def load_security_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def compose(project: str, env: dict, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_port_bindings(sc: ModuleType, container_name: str) -> dict:
    return sc.docker_json(["inspect", container_name, "--format", "{{json .HostConfig.PortBindings}}"]) or {}


def get_actual_gateway_host_port(sc: ModuleType, container_name: str) -> tuple[str, int]:
    """Resolve the real OS-assigned host port via `docker port`."""
    result = sc.run_docker(["port", container_name, "8080/tcp"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ComposeIntegrationError(
            f"could not determine mapped port for {container_name}: {result.stderr.strip()}"
        )
    line = result.stdout.strip().splitlines()[0]
    host_ip, _, host_port = line.rpartition(":")
    return host_ip, int(host_port)


def get_container_image(sc: ModuleType, container_name: str) -> str:
    result = sc.run_docker(["inspect", container_name, "--format", "{{.Config.Image}}"])
    if result.returncode != 0:
        raise ComposeIntegrationError(f"docker inspect {container_name} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def get_pid1_cmdline(sc: ModuleType, container_name: str) -> list[str]:
    result = sc.run_docker(["exec", container_name, "cat", "/proc/1/cmdline"])
    if result.returncode != 0:
        raise ComposeIntegrationError(f"docker exec {container_name} cat /proc/1/cmdline failed: {result.stderr.strip()}")
    return [part for part in result.stdout.split("\x00") if part]


def is_running(sc: ModuleType, container_name: str) -> bool:
    result = sc.run_docker(["inspect", container_name, "--format", "{{.State.Running}}"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def get_network_names(sc: ModuleType, container_name: str) -> set[str]:
    networks = sc.docker_json(["inspect", container_name, "--format", "{{json .NetworkSettings.Networks}}"]) or {}
    # Compose-managed network keys are the short names ("edge"), not the
    # project-prefixed real network name.
    return {name.split("_", 1)[-1] if "_" in name else name for name in networks.keys()}


_TIMESTAMP_FRACTION_PATTERN = re.compile(r"^(.*\.\d{6})\d*(Z|[+-]\d{2}:\d{2})$")


def parse_docker_timestamp(value: str) -> datetime:
    """Parse a Docker RFC3339Nano timestamp (up to 9 fractional digits).

    Python's datetime.fromisoformat only reliably handles up to
    microsecond precision, so any additional nanosecond digits are
    truncated (never rounded up, so a truncated-vs-actual ordering
    comparison stays conservative rather than misleadingly precise).
    """
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    match = _TIMESTAMP_FRACTION_PATTERN.match(normalized.replace("Z", "+00:00"))
    if match:
        normalized = match.group(1) + match.group(2)
    return datetime.fromisoformat(normalized)


def get_started_at(sc: ModuleType, container_name: str) -> datetime:
    raw = sc.docker_json(["inspect", container_name, "--format", "{{json .State.StartedAt}}"])
    return parse_docker_timestamp(raw)


def get_first_healthy_at(sc: ModuleType, container_name: str) -> datetime | None:
    log = sc.docker_json(["inspect", container_name, "--format", "{{json .State.Health.Log}}"]) or []
    successes = [entry for entry in log if entry.get("ExitCode") == 0]
    if not successes:
        return None
    return parse_docker_timestamp(successes[0]["End"])


def check_startup_ordering(
    sc: ModuleType, dependency_name: str, dependency_container: str, dependent_name: str, dependent_container: str
):
    """Proves `dependent` genuinely did not START until `dependency` was
    already Docker-healthy - not merely that both eventually became
    healthy (Day 2 finding M-1, day-02-compose-review.md: the prior
    runtime script only polled to eventually-healthy for each container
    independently, so it could not distinguish a real `condition:
    service_healthy` gate from a silently-weakened `service_started` one,
    or from pure timing luck)."""
    dependency_healthy_at = get_first_healthy_at(sc, dependency_container)
    dependent_started_at = get_started_at(sc, dependent_container)
    passed = dependency_healthy_at is not None and dependency_healthy_at <= dependent_started_at
    detail = (
        f"{dependency_name} first healthy at {dependency_healthy_at}, "
        f"{dependent_name} started at {dependent_started_at}"
    )
    return sc.CheckResult(
        sc.CAT_RUNTIME,
        f"{dependent_name} did not start before {dependency_name} was Docker-healthy",
        passed,
        detail,
    )


def check_config_mount_readonly(sc: ModuleType, container_name: str):
    """[C] the config bind mount is configured RW=false, AND [D] a real
    write to it is rejected while the service keeps functioning - the
    same [C]/[D] distinction this project applies to the rootfs."""
    mounts = sc.docker_json(["inspect", container_name, "--format", "{{json .Mounts}}"]) or []
    config_mount = next((m for m in mounts if m.get("Destination") == CONFIG_TARGET_PATH), None)
    if config_mount is None:
        return sc.CheckResult(
            sc.CAT_RUNTIME, "platform config is mounted read-only", False, "no mount found at target"
        )
    configured_readonly = config_mount.get("RW") is False

    write_result = sc.run_docker(["exec", container_name, "sh", "-c", f"echo probe > {CONFIG_TARGET_PATH}"])
    write_rejected = write_result.returncode != 0

    passed = configured_readonly and write_rejected
    detail = f"Mounts.RW={config_mount.get('RW')!r} write_exit={write_result.returncode}"
    return sc.CheckResult(sc.CAT_KERNEL, "platform config mount rejects a real write ([C]+[D])", passed, detail)


def check_state_data_write_succeeds(sc: ModuleType, container_name: str):
    """[D] proves /data is genuinely writable despite the container's own
    read-only rootfs - the counterpart to check_kernel_readonly_write_fails
    (which proves the rootfs itself rejects writes)."""
    probe_path = "/data/.maops-write-probe"
    write_result = sc.run_docker(["exec", container_name, "sh", "-c", f"echo probe > {probe_path} && rm -f {probe_path}"])
    passed = write_result.returncode == 0
    detail = f"write+cleanup exit={write_result.returncode} stderr={write_result.stderr.strip()!r}"
    return sc.CheckResult(sc.CAT_KERNEL, "state's /data mount accepts a real write despite read-only rootfs", passed, detail)


def dns_resolves(sc: ModuleType, container_name: str, hostname: str) -> bool:
    result = sc.run_docker(
        ["exec", container_name, "python3", "-c", f"import socket; socket.gethostbyname({hostname!r})"]
    )
    return result.returncode == 0


def http_get_json(port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ComposeIntegrationError(f"{path}: response body is not valid JSON: {body!r}") from exc
    return response.status, payload


def http_post_json(port: int, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        conn.request("POST", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ComposeIntegrationError(f"{path}: response body is not valid JSON: {body!r}") from exc
    return response.status, payload


def poll_until(predicate, deadline_seconds: float, description: str):
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        ok, last = predicate()
        if ok:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ComposeIntegrationError(f"{description} did not converge within {deadline_seconds}s: last={last}")


def poll_gateway_readyz(port: int, expect_ready: bool, deadline_seconds: float) -> tuple[int, dict]:
    def predicate():
        try:
            status, payload = http_get_json(port, "/readyz")
            ready = status == 200 and payload.get("status") == "ready"
            return ready == expect_ready, (status, payload)
        except OSError as exc:
            if not expect_ready:
                return True, (0, {"status": "not-ready", "error": str(exc)})
            return False, (None, str(exc))

    return poll_until(predicate, deadline_seconds, "gateway /readyz reaching expected state")


def print_result(result) -> None:
    print(f"  {result}")


def main() -> int:
    if shutil.which("docker") is None:
        print("compose_integration: docker CLI not found on PATH", file=sys.stderr)
        return 1

    sc = load_security_checker()
    version = read_version()
    image = f"maops-docker-platform:{version}"
    project = f"maops-compose-{uuid.uuid4().hex[:12]}"

    env = dict(os.environ)
    env["VERSION"] = version
    env["GATEWAY_HOST_PORT"] = "0"

    state_container = f"{project}-state-1"
    app_container = f"{project}-app-1"
    gateway_container = f"{project}-gateway-1"
    containers = {"state": state_container, "app": app_container, "gateway": gateway_container}

    print(f"compose_integration: project={project} image={image}")

    results = []

    try:
        up_result = compose(project, env, ["up", "-d"], UP_TIMEOUT_SECONDS)
        if up_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose up failed: {up_result.stderr.strip()}")

        for name, container in containers.items():
            actual_image = get_container_image(sc, container)
            if actual_image != image:
                raise ComposeIntegrationError(
                    f"{name}: Compose-created container image is {actual_image!r}, expected {image!r}"
                )
        print(f"compose_integration: all three services created from exact image {image}")

        for name, container in containers.items():
            health_result = sc.check_runtime_healthy(container)
            results.append(health_result)
            if not health_result.passed:
                raise ComposeIntegrationError(f"{name} did not become healthy")
        print("compose_integration: state, app, gateway all reached Docker healthy state")

        ordering_state_app = check_startup_ordering(sc, "state", state_container, "app", app_container)
        results.append(ordering_state_app)
        ordering_app_gateway = check_startup_ordering(sc, "app", app_container, "gateway", gateway_container)
        results.append(ordering_app_gateway)
        if not (ordering_state_app.passed and ordering_app_gateway.passed):
            raise ComposeIntegrationError("health-gated startup ordering was not honored")
        print("compose_integration: health-gated startup ordering proven (state -> app -> gateway)")

        for name, expected_networks in EXPECTED_NETWORKS.items():
            actual_networks = get_network_names(sc, containers[name])
            passed = actual_networks == expected_networks
            results.append(
                sc.CheckResult(
                    sc.CAT_RUNTIME,
                    f"{name} container network membership is {sorted(expected_networks)}",
                    passed,
                    repr(sorted(actual_networks)),
                )
            )
            if not passed:
                raise ComposeIntegrationError(f"{name} network membership mismatch: {actual_networks}")
        print("compose_integration: real network membership matches the declared edge/backend topology")

        for name in ("app", "state"):
            bindings = get_port_bindings(sc, containers[name])
            if bindings:
                raise ComposeIntegrationError(f"{name} must not publish a host port, found: {bindings}")
        print("compose_integration: app and state have no published host port (proven via docker inspect)")

        gateway_bindings = get_port_bindings(sc, gateway_container)
        binding = (gateway_bindings or {}).get("8080/tcp")
        if not binding or binding[0].get("HostIp") != "127.0.0.1":
            raise ComposeIntegrationError(f"gateway port publication is not loopback-only: {gateway_bindings}")
        host_ip, host_port = get_actual_gateway_host_port(sc, gateway_container)
        if host_ip != "127.0.0.1":
            raise ComposeIntegrationError(f"gateway's actual published address is not loopback-only: {host_ip}:{host_port}")
        print(f"compose_integration: gateway is the sole host-published service, on 127.0.0.1:{host_port}")

        # --- network segmentation: real reachability, not just membership ---

        if not dns_resolves(sc, gateway_container, "app"):
            raise ComposeIntegrationError("gateway cannot resolve 'app' over the edge network")
        if not dns_resolves(sc, app_container, "state"):
            raise ComposeIntegrationError("app cannot resolve 'state' over the backend network")
        print("compose_integration: gateway->app and app->state DNS resolution succeed (real edge/backend reachability)")

        gateway_to_state_blocked = not dns_resolves(sc, gateway_container, "state")
        results.append(
            sc.CheckResult(
                sc.CAT_KERNEL, "gateway cannot resolve/reach state (no shared network)", gateway_to_state_blocked,
                "DNS resolution failed as expected" if gateway_to_state_blocked else "UNEXPECTEDLY RESOLVED",
            )
        )
        state_to_gateway_blocked = not dns_resolves(sc, state_container, "gateway")
        results.append(
            sc.CheckResult(
                sc.CAT_KERNEL, "state cannot resolve/reach gateway (no shared network)", state_to_gateway_blocked,
                "DNS resolution failed as expected" if state_to_gateway_blocked else "UNEXPECTEDLY RESOLVED",
            )
        )
        if not (gateway_to_state_blocked and state_to_gateway_blocked):
            raise ComposeIntegrationError("gateway<->state network isolation is not real")
        print("compose_integration: gateway<->state isolation proven (DNS resolution fails both directions)")

        # --- gateway -> app -> state persistence path ---

        status, payload = http_get_json(host_port, "/state")
        if status != 200 or "value" not in payload:
            raise ComposeIntegrationError(f"GET /state unexpected: {status} {payload}")
        initial_value = payload["value"]
        print(f"compose_integration: gateway -> app -> state GET /state succeeds (initial value={initial_value})")

        status, payload = http_post_json(host_port, "/state/increment")
        if status != 200 or payload.get("value") != initial_value + 1:
            raise ComposeIntegrationError(f"POST /state/increment unexpected: {status} {payload}")
        incremented_value = payload["value"]
        print(f"compose_integration: gateway -> app -> state POST /state/increment succeeds (value={incremented_value})")

        # --- health-gated dependency failure: stop state, observe degrade ---

        stop_result = compose(project, env, ["stop", "state"], LIFECYCLE_TIMEOUT_SECONDS)
        if stop_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose stop state failed: {stop_result.stderr.strip()}")

        if not is_running(sc, app_container):
            raise ComposeIntegrationError("app process did not remain alive after state was stopped")
        if not is_running(sc, gateway_container):
            raise ComposeIntegrationError("gateway process did not remain alive after state was stopped")
        print("compose_integration: app and gateway processes remained alive after state was stopped")

        app_liveness = sc.run_docker(["exec", app_container, "python3", "-m", "app.healthcheck"])
        if app_liveness.returncode != 0:
            raise ComposeIntegrationError("app local liveness (/healthz) failed while state was down")
        print("compose_integration: app's own /healthz liveness stayed healthy while state was down")

        status, payload = poll_gateway_readyz(host_port, expect_ready=False, deadline_seconds=DEGRADE_DEADLINE_SECONDS)
        print(f"compose_integration: gateway /readyz correctly degraded to not-ready ({status} {payload})")

        status, payload = http_get_json(host_port, "/state")
        if status != 503:
            raise ComposeIntegrationError(f"GET /state while state is down should be a controlled 503, got: {status} {payload}")
        print(f"compose_integration: gateway GET /state returns a controlled error while state is down ({status} {payload})")

        # --- recovery ---

        start_result = compose(project, env, ["start", "state"], LIFECYCLE_TIMEOUT_SECONDS)
        if start_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose start state failed: {start_result.stderr.strip()}")

        state_healthy_again = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_again)
        if not state_healthy_again.passed:
            raise ComposeIntegrationError("state did not become healthy again after restart")
        print("compose_integration: state became healthy again after restart")

        status, payload = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=RECOVER_DEADLINE_SECONDS)
        print(f"compose_integration: gateway /readyz recovered to ready ({status} {payload})")

        status, payload = http_get_json(host_port, "/state")
        if status != 200 or payload.get("value") != incremented_value:
            raise ComposeIntegrationError(
                f"value did not survive a graceful state stop/start: expected {incremented_value}, got {status} {payload}"
            )
        print(f"compose_integration: value survived graceful state stop/start (value={payload['value']})")

        # --- persistence across real container recreation (not stop/start) ---

        recreate_result = compose(
            project, env, ["up", "-d", "--force-recreate", "--no-deps", "state"], LIFECYCLE_TIMEOUT_SECONDS
        )
        if recreate_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose up --force-recreate state failed: {recreate_result.stderr.strip()}")

        state_healthy_after_recreate = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_after_recreate)
        if not state_healthy_after_recreate.passed:
            raise ComposeIntegrationError("state did not become healthy after recreation")

        status, payload = http_get_json(host_port, "/state")
        if status != 200 or payload.get("value") != incremented_value:
            raise ComposeIntegrationError(
                f"value did not survive state container recreation: expected {incremented_value}, got {status} {payload}"
            )
        print(f"compose_integration: value survived state container recreation (value={payload['value']})")

        status, payload = http_post_json(host_port, "/state/increment")
        if status != 200 or payload.get("value") != incremented_value + 1:
            raise ComposeIntegrationError(f"increment after recreation unexpected: {status} {payload}")
        post_recreate_value = payload["value"]
        print(f"compose_integration: increment after recreation succeeds (value={post_recreate_value})")

        # --- persistence across a full compose down/up with the volume retained ---

        down_result = compose(project, env, ["down", "-t", "10"], DOWN_TIMEOUT_SECONDS)
        if down_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose down (without -v) failed: {down_result.stderr.strip()}")

        up_again_result = compose(project, env, ["up", "-d"], UP_TIMEOUT_SECONDS)
        if up_again_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose up (second run) failed: {up_again_result.stderr.strip()}")

        for name, container in containers.items():
            health_result = sc.check_runtime_healthy(container)
            results.append(health_result)
            if not health_result.passed:
                raise ComposeIntegrationError(f"{name} did not become healthy after compose down/up")

        host_ip, host_port = get_actual_gateway_host_port(sc, gateway_container)
        status, payload = http_get_json(host_port, "/state")
        if status != 200 or payload.get("value") != post_recreate_value:
            raise ComposeIntegrationError(
                f"value did not survive compose down/up with the volume retained: "
                f"expected {post_recreate_value}, got {status} {payload}"
            )
        print(f"compose_integration: value survived a full compose down/up with the volume retained (value={payload['value']})")

        # --- runtime hardening on all three Compose-managed containers ---

        for name, container in containers.items():
            results.append(sc.check_runtime_readonly_rootfs(container))
            results.append(sc.check_runtime_cap_drop_all(container))
            results.append(sc.check_runtime_no_new_privileges(container))
            results.append(sc.check_runtime_not_privileged(container))
            results.append(sc.check_runtime_no_host_pid(container))
            results.append(sc.check_runtime_no_host_network(container))
            results.append(sc.check_runtime_no_docker_socket(container))
            results.append(sc.check_kernel_effective_uid_gid(container))
            results.append(sc.check_kernel_capabilities_effective(container))
            results.append(sc.check_kernel_no_new_privs(container))
            # Closes Day 2 finding M-1 (day-02-security-review.md) / L-2
            # (day-02-compose-review.md): a real [D] rejected-write proof
            # for Compose-managed containers, not merely the [C]
            # "Docker was asked" check Day 2 shipped.
            results.append(sc.check_kernel_readonly_write_fails(container, 0))
            results.append(check_config_mount_readonly(sc, container))

            cmdline = get_pid1_cmdline(sc, container)
            expected_cmdline = ["python3", "-m", name]
            results.append(
                sc.CheckResult(
                    sc.CAT_KERNEL,
                    f"{name} PID 1 process identity is {expected_cmdline}",
                    cmdline == expected_cmdline,
                    repr(cmdline),
                )
            )

        results.append(check_state_data_write_succeeds(sc, state_container))

    except ComposeIntegrationError as exc:
        print(f"compose_integration: FAIL: {exc}", file=sys.stderr)
        print()
        for result in results:
            print_result(result)
        return 1

    finally:
        # Best-effort teardown regardless of how far `up` got - Compose's
        # own `down -v` is a safe no-op against a project with nothing
        # running, and this project name (and therefore its named volume,
        # `<project>_state_data`) is unique to this run, so it never
        # touches another Compose project or Docker resource.
        down_result = compose(project, env, ["down", "-t", "10", "-v"], DOWN_TIMEOUT_SECONDS)
        if down_result.returncode != 0:
            print(
                f"compose_integration: WARNING: docker compose down -v failed for project "
                f"{project}: {down_result.stderr.strip()}",
                file=sys.stderr,
            )

    print()
    for result in results:
        print_result(result)

    failures = [r for r in results if not r.passed]
    print()
    if failures:
        print(f"compose_integration: FAIL ({len(failures)}/{len(results)} inspection checks failed)")
        return 1
    print(f"compose_integration: PASS ({len(results)}/{len(results)} inspection checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
