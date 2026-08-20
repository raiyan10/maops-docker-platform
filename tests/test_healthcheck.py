"""Direct unit test for app/healthcheck.py::check() (closes Day 1 finding M-1).

Prior to this test, app.healthcheck.check() had zero direct unit-test
coverage - a regression to the broken bare-script invocation form was only
ever caught by the much slower make security-check (~30s, via real Docker
HEALTHCHECK polling). This test exercises check() in-process, against a
real loopback server, the same ServerTestCase pattern tests/test_server.py
already uses, with no container needed.
"""

import threading
import unittest
from unittest.mock import patch

from app import healthcheck
from app.config import AppConfig
from app.server import build_server


class HealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        config = AppConfig(
            host="127.0.0.1",
            port=0,
            name="healthcheck-test",
            state_host="127.0.0.1",
            state_port=1,
            state_timeout_seconds=3.0,
        )
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_check_returns_true_against_reachable_healthy_server(self) -> None:
        with patch.dict("os.environ", {"APP_PORT": str(self.port)}, clear=False):
            self.assertTrue(healthcheck.check())

    def test_check_returns_false_against_unreachable_port(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with patch.dict("os.environ", {"APP_PORT": str(self.port)}, clear=False):
            self.assertFalse(healthcheck.check())


if __name__ == "__main__":
    unittest.main()
