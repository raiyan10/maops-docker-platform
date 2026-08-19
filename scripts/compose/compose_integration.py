#!/usr/bin/env python3
"""Real Compose stack integration test.

SCOPE: this is the *runtime* counterpart to scripts/compose/check_compose.py
(which only validates the *rendered* `docker compose config`, never a real
container). It brings up the actual two-service Day 2 stack (`app` +
`gateway`) under a uniquely named Compose project, on a dynamic
loopback-only host port, and proves real behavior: both services reach
Docker-health `healthy`, `app` publishes no host port, `gateway` publishes
only the expected loopback port, the gateway genuinely reaches `app` over
Compose service-name discovery (`/readyz`, `/upstream/info`), stopping
`app` degrades gateway readiness while the gateway process itself stays
alive, restarting `app` recovers readiness, and both Compose-managed
containers carry the same hardening this project's direct-`docker run`
checks already prove (read-only rootfs, `cap_drop: [ALL]`,
`no-new-privileges`, non-root UID/GID, no host PID/network, no Docker
socket) plus each role's expected PID 1 identity.

This closes Day 1 finding M-3: no automated check previously inspected
Compose-*created* containers, only Compose's own rendered configuration.

Deliberately reuses scripts/verify/security_check.py's existing [C]/[D]
container-inspection check functions (they already operate generically on
any container name, Compose-managed or not) rather than re-implementing
the same `docker inspect`/`docker exec` logic a second time.

Uses its own uniquely named Compose project (`maops-compose-<uuid>`) and
tears it down - on success or failure - via `docker compose ... down`.
Never touches any other Compose project or Docker resource, and never
runs a global prune.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "compose.yaml"

UP_TIMEOUT_SECONDS = 120.0
LIFECYCLE_TIMEOUT_SECONDS = 30.0
DOWN_TIMEOUT_SECONDS = 60.0
STARTUP_HEALTHY_DEADLINE_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.5
DEGRADE_DEADLINE_SECONDS = 30.0
RECOVER_DEADLINE_SECONDS = 60.0


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
    """Resolve the real OS-assigned host port via `docker port`.

    `HostConfig.PortBindings` only ever reflects what was *requested*
    (`HostPort: "0"`, since GATEWAY_HOST_PORT=0 asks Docker to pick one) -
    the actual ephemeral port Docker assigned is only visible via `docker
    port` (or `.NetworkSettings.Ports`), the same resolution
    scripts/smoke/container_smoke.py already relies on for its own
    OS-assigned host port.
    """
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


def poll_gateway_readyz(port: int, expect_ready: bool, deadline_seconds: float) -> tuple[int, dict]:
    deadline = time.monotonic() + deadline_seconds
    last: tuple[object, object] | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = http_get_json(port, "/readyz")
            ready = status == 200 and payload.get("status") == "ready"
            if ready == expect_ready:
                return status, payload
            last = (status, payload)
        except OSError as exc:
            last = (None, str(exc))
            if not expect_ready:
                # Connection-level failure also counts as "not ready".
                return 0, {"status": "not-ready", "error": str(exc)}
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ComposeIntegrationError(
        f"gateway /readyz did not reach expected state (expect_ready={expect_ready}) "
        f"within {deadline_seconds}s: last={last}"
    )


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
    # Docker itself picks a free loopback host port when the host-port
    # component of a publish spec is 0 - the same OS-assigned-port
    # pattern scripts/smoke/container_smoke.py uses via `-p 127.0.0.1::8080`.
    env["GATEWAY_HOST_PORT"] = "0"

    app_container = f"{project}-app-1"
    gateway_container = f"{project}-gateway-1"

    print(f"compose_integration: project={project} image={image}")

    results = []

    try:
        up_result = compose(project, env, ["up", "-d"], UP_TIMEOUT_SECONDS)
        if up_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose up failed: {up_result.stderr.strip()}")

        for name, container in (("app", app_container), ("gateway", gateway_container)):
            actual_image = get_container_image(sc, container)
            if actual_image != image:
                raise ComposeIntegrationError(
                    f"{name}: Compose-created container image is {actual_image!r}, expected {image!r}"
                )
        print(f"compose_integration: both services created from exact image {image}")

        app_healthy = sc.check_runtime_healthy(app_container)
        results.append(app_healthy)
        if not app_healthy.passed:
            raise ComposeIntegrationError("app did not become healthy")
        print("compose_integration: app reached Docker healthy state")

        gateway_healthy = sc.check_runtime_healthy(gateway_container)
        results.append(gateway_healthy)
        if not gateway_healthy.passed:
            raise ComposeIntegrationError("gateway did not become healthy")
        print("compose_integration: gateway reached Docker healthy state")

        app_bindings = get_port_bindings(sc, app_container)
        if app_bindings:
            raise ComposeIntegrationError(f"app must not publish a host port, found: {app_bindings}")
        print("compose_integration: app has no published host port (proven via docker inspect)")

        gateway_bindings = get_port_bindings(sc, gateway_container)
        binding = (gateway_bindings or {}).get("8080/tcp")
        if not binding or binding[0].get("HostIp") != "127.0.0.1":
            raise ComposeIntegrationError(
                f"gateway port publication is not loopback-only: {gateway_bindings}"
            )
        host_ip, host_port = get_actual_gateway_host_port(sc, gateway_container)
        if host_ip != "127.0.0.1":
            raise ComposeIntegrationError(
                f"gateway's actual published address is not loopback-only: {host_ip}:{host_port}"
            )
        print(f"compose_integration: gateway is the sole host-published service, on 127.0.0.1:{host_port}")

        status, payload = http_get_json(host_port, "/readyz")
        if status != 200 or payload.get("status") != "ready":
            raise ComposeIntegrationError(f"/readyz unexpected: {status} {payload}")
        print("compose_integration: gateway /readyz succeeds (app reachable via service-name discovery)")

        status, payload = http_get_json(host_port, "/upstream/info")
        if status != 200:
            raise ComposeIntegrationError(f"/upstream/info unexpected status: {status} {payload}")
        upstream = payload.get("upstream", {})
        if upstream.get("version") != version or upstream.get("name") != "maops-docker-platform":
            raise ComposeIntegrationError(f"/upstream/info did not prove real app communication: {payload}")
        print(
            f"compose_integration: /upstream/info proves real gateway->app HTTP communication "
            f"(name={upstream.get('name')!r}, version={upstream.get('version')!r})"
        )

        stop_result = compose(project, env, ["stop", "app"], LIFECYCLE_TIMEOUT_SECONDS)
        if stop_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose stop app failed: {stop_result.stderr.strip()}")

        if not is_running(sc, gateway_container):
            raise ComposeIntegrationError("gateway process did not remain alive after app was stopped")
        print("compose_integration: gateway process remained alive after app was stopped")

        status, payload = poll_gateway_readyz(host_port, expect_ready=False, deadline_seconds=DEGRADE_DEADLINE_SECONDS)
        print(f"compose_integration: gateway /readyz correctly degraded to not-ready ({status} {payload})")

        start_result = compose(project, env, ["start", "app"], LIFECYCLE_TIMEOUT_SECONDS)
        if start_result.returncode != 0:
            raise ComposeIntegrationError(f"docker compose start app failed: {start_result.stderr.strip()}")

        app_healthy_again = sc.check_runtime_healthy(app_container)
        results.append(app_healthy_again)
        if not app_healthy_again.passed:
            raise ComposeIntegrationError("app did not become healthy again after restart")
        print("compose_integration: app became healthy again after restart")

        status, payload = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=RECOVER_DEADLINE_SECONDS)
        print(f"compose_integration: gateway /readyz recovered to ready ({status} {payload})")

        for name, container in (("app", app_container), ("gateway", gateway_container)):
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

    except ComposeIntegrationError as exc:
        print(f"compose_integration: FAIL: {exc}", file=sys.stderr)
        print()
        for result in results:
            print_result(result)
        return 1

    finally:
        # Best-effort teardown regardless of how far `up` got - Compose's
        # own `down` is a safe no-op against a project with nothing
        # running, and this project name is unique to this run, so it
        # never touches another Compose project or Docker resource.
        down_result = compose(project, env, ["down", "-t", "10"], DOWN_TIMEOUT_SECONDS)
        if down_result.returncode != 0:
            print(
                f"compose_integration: WARNING: docker compose down failed for project "
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
