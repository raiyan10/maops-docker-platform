"""Direct unit test for gateway/healthcheck.py::check().

Mirrors tests/test_healthcheck.py's pattern for the app role: exercises the
real probe against a real loopback gateway server, in-process, no
container needed. The probe only ever calls the gateway's own /healthz
(liveness), never the upstream - so an unreachable/nonexistent upstream
must not affect the result.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from gateway import healthcheck
from gateway.config import GatewayConfig
from gateway.server import build_server


class _StubHealthzServer:
    """Minimal loopback stub serving a caller-chosen /healthz body.

    Used to prove gateway.healthcheck.check() itself rejects a
    well-formed but wrong-role (or role-less) response, rather than
    merely asserting on the EXPECTED_ROLE constant (Day 4 finding H-1) -
    the real gateway server always answers with its own true role, so it
    cannot exercise the rejection path on its own.
    """

    def __init__(self, body: bytes, status: int = 200) -> None:
        handler_body = body
        handler_status = status

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(handler_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(handler_body)))
                self.end_headers()
                self.wfile.write(handler_body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class GatewayHealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        # upstream_host/port deliberately point nowhere - /healthz must
        # succeed regardless, since it never contacts the upstream.
        config = GatewayConfig(
            host="127.0.0.1",
            port=0,
            upstream_host="127.0.0.1",
            upstream_port=1,
            upstream_timeout_seconds=3.0,
            name="healthcheck-test-gateway",
        )
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_check_returns_true_against_reachable_gateway_regardless_of_upstream(self) -> None:
        with patch.dict("os.environ", {"GATEWAY_PORT": str(self.port)}, clear=False):
            self.assertTrue(healthcheck.check())

    def test_check_returns_false_against_unreachable_port(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with patch.dict("os.environ", {"GATEWAY_PORT": str(self.port)}, clear=False):
            self.assertFalse(healthcheck.check())


class GatewayHealthcheckRoleDiscriminationTests(unittest.TestCase):
    """Regression protection for Day 4 finding H-1: gateway.healthcheck must
    reject a live, correctly-shaped {"status": "ok"} response that carries
    the wrong role (or no role at all) - the exact scenario that let
    `python -m gateway.healthcheck` exit 0 against an app/state container."""

    def _check_against(self, body: dict) -> bool:
        stub = _StubHealthzServer(json.dumps(body).encode("utf-8"))
        try:
            with patch.dict("os.environ", {"GATEWAY_PORT": str(stub.port)}, clear=False):
                return healthcheck.check()
        finally:
            stub.close()

    def test_accepts_correct_role(self) -> None:
        self.assertTrue(self._check_against({"status": "ok", "role": "gateway"}))

    def test_rejects_wrong_role_app(self) -> None:
        self.assertFalse(self._check_against({"status": "ok", "role": "app"}))

    def test_rejects_wrong_role_state(self) -> None:
        self.assertFalse(self._check_against({"status": "ok", "role": "state"}))

    def test_rejects_missing_role(self) -> None:
        self.assertFalse(self._check_against({"status": "ok"}))


if __name__ == "__main__":
    unittest.main()
