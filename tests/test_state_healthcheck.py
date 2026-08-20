"""Direct unit test for state/healthcheck.py::check().

Mirrors tests/test_healthcheck.py's (app) and
tests/test_gateway_healthcheck.py's pattern: exercises the real probe
against a real loopback state server, in-process, no container needed.
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from state import healthcheck
from state.config import StateConfig
from state.server import build_server


class StateHealthcheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        config = StateConfig(
            host="127.0.0.1",
            port=0,
            name="healthcheck-test-state",
            data_dir=Path(self._tmp_dir.name),
            state_filename="state.json",
        )
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp_dir.cleanup()

    def test_check_returns_true_against_reachable_healthy_server(self) -> None:
        with patch.dict("os.environ", {"STATE_PORT": str(self.port)}, clear=False):
            self.assertTrue(healthcheck.check())

    def test_check_returns_false_against_unreachable_port(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with patch.dict("os.environ", {"STATE_PORT": str(self.port)}, clear=False):
            self.assertFalse(healthcheck.check())


if __name__ == "__main__":
    unittest.main()
