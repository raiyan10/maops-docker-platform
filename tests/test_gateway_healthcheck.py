"""Direct unit test for gateway/healthcheck.py::check().

Mirrors tests/test_healthcheck.py's pattern for the app role: exercises the
real probe against a real loopback gateway server, in-process, no
container needed. The probe only ever calls the gateway's own /healthz
(liveness), never the upstream - so an unreachable/nonexistent upstream
must not affect the result.
"""

import threading
import unittest
from unittest.mock import patch

from gateway import healthcheck
from gateway.config import GatewayConfig
from gateway.server import build_server


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


if __name__ == "__main__":
    unittest.main()
