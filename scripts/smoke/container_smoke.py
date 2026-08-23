#!/usr/bin/env python3
"""Smoke test against the REAL built Docker image.

Boots the exact `maops-docker-platform:<VERSION>` image (never `latest`) in
a uniquely named, throwaway container bound to a dynamic loopback-only host
port, waits for it to answer, exercises /healthz, /readyz, and /info over
real HTTP, verifies the JSON responses and the runtime UID, and always
tears its own container down — on both success and failure. It never
touches any other Docker resource.

SCOPE (Day 3): this always runs the `app` role via a bare `docker run`,
never via Compose - so `state` (app's own dependency, see
docs/persistence.md) never exists here, and /readyz is expected to report
a controlled dependency-unavailable 503, not `ready`. This test proves
app's own liveness/metadata surface and non-root runtime in isolation.

MULTI-ROLE CHAIN (Day 4): closes the Day 3 Low finding that this script
only ever covered the `app` role. `multi_role_chain_smoke()` proves the
single release image can run all three roles - `state`, `app`, `gateway` -
*without Compose*: a throwaway Docker network, three uniquely named
containers started via bare `docker run` with service-name network
aliases (`state`/`app`/`gateway`) and the same `STATE_HOST`/`UPSTREAM_HOST`
environment wiring Compose would otherwise supply, and a real
`gateway -> app -> state` HTTP round trip through a dynamic loopback host
port. This is deliberately narrower than
scripts/compose/compose_integration.py (no named volume/persistence
proof, no network-segmentation/isolation proof, no failure/recovery
scenario - compose_integration.py already owns all of that) - it exists
only to prove the one image-level property Compose-based testing cannot,
by construction, isolate: that the *image itself*, not Compose's specific
topology, is what makes all three roles work.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STARTUP_DEADLINE_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.5
# Absolute interpreter path (Day 4): the release image's final runtime is
# Distroless (no shell, no coreutils), so every in-container probe execs
# this directly rather than a bare "python3" name.
PYTHON_BIN = "/usr/bin/python3.13"
_UID_SOURCE = "import os; print(os.getuid())"
# Comfortably exceeds the ~5-6s an unresolvable STATE_HOST DNS lookup can
# take in this isolated (non-Compose) container - see the /readyz check
# below; Python's http.client socket-level timeout does not bound the
# getaddrinfo() phase itself.
REQUEST_TIMEOUT_SECONDS = 10.0


class SmokeTestError(RuntimeError):
    pass


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run_docker(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def start_container(image: str, container_name: str) -> None:
    result = run_docker(
        [
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            "127.0.0.1::8080",
            image,
        ]
    )
    if result.returncode != 0:
        raise SmokeTestError(f"docker run failed: {result.stderr.strip()}")


def get_host_port(container_name: str) -> int:
    result = run_docker(["port", container_name, "8080/tcp"])
    if result.returncode != 0 or not result.stdout.strip():
        raise SmokeTestError(f"could not determine mapped port: {result.stderr.strip()}")
    # Output looks like "127.0.0.1:54321"
    host_port = result.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]
    return int(host_port)


def get_uid(container_name: str) -> str:
    result = run_docker(["exec", container_name, PYTHON_BIN, "-c", _UID_SOURCE])
    if result.returncode != 0:
        raise SmokeTestError(f"docker exec (read uid) failed: {result.stderr.strip()}")
    return result.stdout.strip()


def http_get_json(port: int, path: str) -> tuple[int, dict[str, object]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()

    content_type = response.getheader("Content-Type")
    if content_type != "application/json":
        raise SmokeTestError(f"{path}: expected application/json, got {content_type!r}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"{path}: response body is not valid JSON: {body!r}") from exc

    return response.status, payload


def http_post_json(port: int, path: str) -> tuple[int, dict[str, object]]:
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
        raise SmokeTestError(f"{path}: response body is not valid JSON: {body!r}") from exc

    return response.status, payload


def wait_until_ready(port: int, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = http_get_json(port, "/healthz")
            if status == 200 and payload.get("status") == "ok":
                return
            last_error = SmokeTestError(f"/healthz not ready yet: {status} {payload}")
        except OSError as exc:
            last_error = exc
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SmokeTestError(f"container did not become ready within {deadline_seconds}s: {last_error}")


def cleanup(container_name: str) -> None:
    run_docker(["rm", "-f", container_name], timeout=20.0)


def single_role_app_smoke(image: str, version: str) -> None:
    """Original Day 1-3 smoke test: the `app` role in isolation, via a bare
    `docker run`, proving its own liveness/metadata surface and non-root
    runtime without any dependency present."""
    container_name = f"maops-smoke-{uuid.uuid4().hex[:12]}"
    print(f"smoke: image={image} container={container_name}")

    try:
        start_container(image, container_name)
        port = get_host_port(container_name)
        print(f"smoke: mapped to 127.0.0.1:{port}")

        wait_until_ready(port, STARTUP_DEADLINE_SECONDS)

        status, payload = http_get_json(port, "/healthz")
        if status != 200 or payload != {"status": "ok", "role": "app"}:
            raise SmokeTestError(f"/healthz unexpected response: {status} {payload}")
        print("smoke: /healthz OK")

        # This smoke test runs the `app` role via a bare `docker run`, not
        # via Compose - so `state` (app's own STATE_HOST dependency, see
        # docs/persistence.md) never resolves here. /readyz is now
        # dependency-aware (Day 3), so a controlled 503 not-ready is the
        # *correct* isolated-container result, not a failure - this smoke
        # test only proves app's own liveness/metadata surface; the real
        # /readyz-becomes-ready proof lives in scripts/compose/
        # compose_integration.py, where `state` genuinely exists.
        status, payload = http_get_json(port, "/readyz")
        if status != 503 or payload.get("status") != "not-ready":
            raise SmokeTestError(
                f"/readyz expected a controlled dependency-unavailable 503 "
                f"(no 'state' service exists outside Compose), got: {status} {payload}"
            )
        print(f"smoke: /readyz correctly reports dependency-unavailable outside Compose ({status} {payload})")

        status, payload = http_get_json(port, "/info")
        if status != 200:
            raise SmokeTestError(f"/info unexpected status: {status}")
        expected_keys = {"name", "version", "python_version", "host", "port"}
        if set(payload.keys()) != expected_keys:
            raise SmokeTestError(f"/info unexpected keys: {payload.keys()}")
        if payload.get("version") != version:
            raise SmokeTestError(
                f"/info version mismatch: expected {version!r}, got {payload.get('version')!r}"
            )
        print(f"smoke: /info OK (version={payload['version']})")

        uid = get_uid(container_name)
        if uid == "0":
            raise SmokeTestError("container is running as root (uid 0)")
        if uid != "10001":
            raise SmokeTestError(f"unexpected runtime uid: {uid!r} (expected 10001)")
        print(f"smoke: runtime uid={uid} (non-root) OK")

        print("smoke: single-role (app) PASS")

    finally:
        cleanup(container_name)


# --- multi-role chain smoke (Day 4) ---

MULTI_ROLE_STARTUP_DEADLINE_SECONDS = 30.0


def create_network(network_name: str) -> None:
    result = run_docker(["network", "create", network_name])
    if result.returncode != 0:
        raise SmokeTestError(f"docker network create failed: {result.stderr.strip()}")


def remove_network(network_name: str) -> None:
    run_docker(["network", "rm", network_name], timeout=20.0)


def start_role_container(
    image: str, container_name: str, network_name: str, alias: str, role_args: list[str], env: dict[str, str],
    publish: bool = False,
) -> None:
    args = ["run", "-d", "--name", container_name, "--network", network_name, "--network-alias", alias]
    if publish:
        args += ["-p", "127.0.0.1::8080"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += [image, *role_args]
    result = run_docker(args)
    if result.returncode != 0:
        raise SmokeTestError(f"docker run failed for {alias}: {result.stderr.strip()}")


def wait_for_role_healthcheck(container_name: str, healthcheck_module: str, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    last_returncode: int | None = None
    while time.monotonic() < deadline:
        result = run_docker(["exec", container_name, PYTHON_BIN, "-m", healthcheck_module])
        last_returncode = result.returncode
        if result.returncode == 0:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise SmokeTestError(
        f"{container_name} ({healthcheck_module}) did not become healthy within "
        f"{deadline_seconds}s (last exit={last_returncode})"
    )


def get_container_image_tag(container_name: str) -> str:
    result = run_docker(["inspect", container_name, "--format", "{{.Config.Image}}"])
    if result.returncode != 0:
        raise SmokeTestError(f"docker inspect failed for {container_name}: {result.stderr.strip()}")
    return result.stdout.strip()


def multi_role_chain_smoke(image: str, version: str) -> None:
    """Closes the Day 3 Low finding: proves the single release image can run
    `state`, `app`, and `gateway` together - without Compose - on a
    throwaway Docker network, with a real gateway -> app -> state HTTP
    chain through a dynamic loopback host port. Deliberately narrower than
    scripts/compose/compose_integration.py (see module docstring)."""
    run_id = uuid.uuid4().hex[:12]
    network_name = f"maops-smoke-net-{run_id}"
    state_container = f"maops-smoke-state-{run_id}"
    app_container = f"maops-smoke-app-{run_id}"
    gateway_container = f"maops-smoke-gateway-{run_id}"
    containers = {"state": state_container, "app": app_container, "gateway": gateway_container}

    print(f"smoke: multi-role network={network_name} containers={list(containers.values())}")

    try:
        create_network(network_name)

        start_role_container(image, state_container, network_name, "state", ["-m", "state"], {})
        start_role_container(
            image, app_container, network_name, "app", ["-m", "app"], {"STATE_HOST": "state"}
        )
        start_role_container(
            image, gateway_container, network_name, "gateway", ["-m", "gateway"],
            {"UPSTREAM_HOST": "app"}, publish=True,
        )

        wait_for_role_healthcheck(state_container, "state.healthcheck", MULTI_ROLE_STARTUP_DEADLINE_SECONDS)
        print("smoke: multi-role state started and healthy")
        wait_for_role_healthcheck(app_container, "app.healthcheck", MULTI_ROLE_STARTUP_DEADLINE_SECONDS)
        print("smoke: multi-role app started and healthy")
        wait_for_role_healthcheck(gateway_container, "gateway.healthcheck", MULTI_ROLE_STARTUP_DEADLINE_SECONDS)
        print("smoke: multi-role gateway started and healthy")

        for name, container in containers.items():
            actual_image = get_container_image_tag(container)
            if actual_image != image:
                raise SmokeTestError(f"{name}: container image is {actual_image!r}, expected {image!r}")
            uid = get_uid(container)
            if uid != "10001":
                raise SmokeTestError(f"{name}: unexpected runtime uid {uid!r} (expected 10001)")
        print(f"smoke: multi-role all three containers run the exact image {image} as uid 10001")

        port = get_host_port(gateway_container)
        print(f"smoke: multi-role gateway mapped to 127.0.0.1:{port}")
        wait_until_ready(port, MULTI_ROLE_STARTUP_DEADLINE_SECONDS)

        # gateway -> app: a real, bounded HTTP call, proven via gateway's own
        # /upstream/info wrapping app's /info (includes app's own version).
        status, payload = http_get_json(port, "/upstream/info")
        if status != 200 or "upstream" not in payload:
            raise SmokeTestError(f"gateway -> app /upstream/info unexpected: {status} {payload}")
        upstream_version = payload["upstream"].get("version")
        if upstream_version != version:
            raise SmokeTestError(f"gateway -> app version mismatch: expected {version!r}, got {upstream_version!r}")
        print(f"smoke: multi-role gateway -> app works (upstream version={upstream_version})")

        # gateway -> app -> state: the full chain, through the public gateway port.
        status, payload = http_get_json(port, "/state")
        if status != 200 or "value" not in payload:
            raise SmokeTestError(f"gateway -> app -> state GET /state unexpected: {status} {payload}")
        initial_value = payload["value"]
        print(f"smoke: multi-role gateway -> app -> state GET /state works (value={initial_value})")

        status, payload = http_post_json(port, "/state/increment")
        if status != 200 or payload.get("value") != initial_value + 1:
            raise SmokeTestError(f"gateway -> app -> state POST /state/increment unexpected: {status} {payload}")
        print(f"smoke: multi-role gateway -> app -> state POST /state/increment works (value={payload['value']})")

        print("smoke: multi-role chain PASS")

    finally:
        for container_name in containers.values():
            cleanup(container_name)
        remove_network(network_name)


def main() -> int:
    version = read_version()
    image = f"maops-docker-platform:{version}"

    try:
        single_role_app_smoke(image, version)
        multi_role_chain_smoke(image, version)
        print("smoke: PASS")
        return 0
    except SmokeTestError as exc:
        print(f"smoke: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
