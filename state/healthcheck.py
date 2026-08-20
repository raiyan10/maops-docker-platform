"""Stdlib-only Docker HEALTHCHECK probe for the state service. No curl/wget.

Connects to the state service's own /healthz endpoint over loopback -
liveness only, never a storage read/write - and exits 0 only on an HTTP
200 with the expected JSON body. Uses http.client (not urllib.request) to
avoid any proxy-environment-variable interference.
"""

from __future__ import annotations

import http.client
import json
import sys

from state.config import DEFAULT_STATE_PORT, load_config

TIMEOUT_SECONDS = 2.0


def check() -> bool:
    config = load_config()
    port = config.port if config.port else DEFAULT_STATE_PORT
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

    return payload.get("status") == "ok"


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
