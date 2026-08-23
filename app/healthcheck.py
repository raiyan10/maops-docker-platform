"""Stdlib-only Docker HEALTHCHECK probe. No curl/wget dependency.

Connects to the app's own /healthz endpoint over loopback and exits 0 only
on an HTTP 200 with the expected JSON body AND role=="app" - a bare
{"status": "ok"} from some other MAOps service listening on the same port
(e.g. during a role misconfiguration) must not be accepted as this
service's own liveness (Day 4 finding H-1). Uses http.client (not
urllib.request) to avoid any proxy-environment-variable interference.
"""

from __future__ import annotations

import http.client
import json
import sys

from app.config import DEFAULT_PORT, load_config

TIMEOUT_SECONDS = 2.0
EXPECTED_ROLE = "app"


def check() -> bool:
    config = load_config()
    port = config.port if config.port else DEFAULT_PORT
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    try:
        try:
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            if response.status != 200:
                return False
            body = response.read()
        except (OSError, http.client.HTTPException):
            return False
    finally:
        conn.close()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False

    if not isinstance(payload, dict):
        return False

    return payload.get("status") == "ok" and payload.get("role") == EXPECTED_ROLE


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
