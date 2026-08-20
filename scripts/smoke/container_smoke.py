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
app's own liveness/metadata surface and non-root runtime in isolation; the
gateway role and the full dependency-ready/persistence path are proven by
scripts/compose/compose_integration.py instead (this mirrors the existing,
already-documented Day 2 scope note that this script never exercises the
gateway role either).
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
    result = run_docker(["exec", container_name, "id", "-u"])
    if result.returncode != 0:
        raise SmokeTestError(f"docker exec id -u failed: {result.stderr.strip()}")
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


def main() -> int:
    version = read_version()
    image = f"maops-docker-platform:{version}"
    container_name = f"maops-smoke-{uuid.uuid4().hex[:12]}"

    print(f"smoke: image={image} container={container_name}")

    try:
        start_container(image, container_name)
        port = get_host_port(container_name)
        print(f"smoke: mapped to 127.0.0.1:{port}")

        wait_until_ready(port, STARTUP_DEADLINE_SECONDS)

        status, payload = http_get_json(port, "/healthz")
        if status != 200 or payload != {"status": "ok"}:
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

        print("smoke: PASS")
        return 0

    except SmokeTestError as exc:
        print(f"smoke: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        cleanup(container_name)


if __name__ == "__main__":
    sys.exit(main())
