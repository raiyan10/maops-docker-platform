import http.client
import json
import socket
import threading
import unittest

from app.config import AppConfig, load_config
from app.server import build_server
from app.version import get_version


class ServerTestCase(unittest.TestCase):
    """Runs the real server on an OS-assigned loopback port for each test."""

    def setUp(self) -> None:
        config = AppConfig(host="127.0.0.1", port=0, name="test-app")
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, method: str, path: str) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            response.read_body = response.read()  # type: ignore[attr-defined]
            return response
        finally:
            conn.close()


class RootEndpointTests(ServerTestCase):
    def test_root_returns_expected_schema(self) -> None:
        response = self._request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"service": "test-app", "version": get_version(), "status": "ok"})


class HealthzEndpointTests(ServerTestCase):
    def test_healthz_returns_ok(self) -> None:
        response = self._request("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ok"})

    def test_healthz_head_has_no_body(self) -> None:
        response = self._request("HEAD", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read_body, b"")  # type: ignore[attr-defined]
        self.assertIsNotNone(response.getheader("Content-Length"))


class ReadyzEndpointTests(ServerTestCase):
    def test_readyz_returns_ready(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ready"})


class InfoEndpointTests(ServerTestCase):
    def test_info_exposes_only_safe_fields(self) -> None:
        response = self._request("GET", "/info")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(
            set(payload.keys()), {"name", "version", "python_version", "host", "port"}
        )
        self.assertEqual(payload["name"], "test-app")
        self.assertEqual(payload["version"], get_version())
        self.assertIsInstance(payload["python_version"], str)
        self.assertEqual(payload["host"], "127.0.0.1")
        self.assertIsInstance(payload["port"], int)

    def test_info_does_not_leak_arbitrary_environment(self) -> None:
        response = self._request("GET", "/info")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        serialized = json.dumps(payload)
        self.assertNotIn("SECRET", serialized.upper())
        self.assertNotIn("PATH=", serialized)


class NotFoundTests(ServerTestCase):
    def test_unknown_path_returns_404_json(self) -> None:
        response = self._request("GET", "/does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_unknown_path_head_returns_404(self) -> None:
        response = self._request("HEAD", "/does-not-exist")
        self.assertEqual(response.status, 404)


class UnsupportedMethodTests(ServerTestCase):
    def test_post_to_known_path_returns_405_with_allow_header(self) -> None:
        response = self._request("POST", "/healthz")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_put_to_known_path_returns_405(self) -> None:
        response = self._request("PUT", "/")
        self.assertEqual(response.status, 405)

    def test_delete_to_known_path_returns_405(self) -> None:
        response = self._request("DELETE", "/info")
        self.assertEqual(response.status, 405)

    def test_patch_to_known_path_returns_405(self) -> None:
        response = self._request("PATCH", "/healthz")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_post_to_unknown_path_returns_404_not_405(self) -> None:
        response = self._request("POST", "/does-not-exist")
        self.assertEqual(response.status, 404)


class EndToEndConfigurationTests(unittest.TestCase):
    """Composes load_config() output into build_server() end-to-end (closes L-4)."""

    def test_custom_host_and_port_from_env_actually_bind(self) -> None:
        # APP_PORT=0 would be rejected by load_config()'s own validation
        # (MIN_PORT=1), so a free port is obtained via a real bind/close
        # probe instead, to exercise a genuine env-driven, non-default
        # port value end-to-end.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()

        config = load_config(
            env={"APP_HOST": "127.0.0.1", "APP_PORT": str(free_port), "APP_NAME": "e2e-app"}
        )
        server = build_server(config)
        try:
            bound_host, bound_port = server.server_address
            self.assertEqual(bound_host, "127.0.0.1")
            self.assertEqual(bound_port, free_port)

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", bound_port, timeout=5)
                try:
                    conn.request("GET", "/")
                    response = conn.getresponse()
                    payload = json.loads(response.read())
                finally:
                    conn.close()
                self.assertEqual(response.status, 200)
                self.assertEqual(payload["service"], "e2e-app")
            finally:
                server.shutdown()
                thread.join(timeout=5)
        finally:
            server.server_close()


class NoTracebackDisclosureTests(ServerTestCase):
    def test_error_responses_never_contain_traceback_markers(self) -> None:
        for method, path in (("GET", "/does-not-exist"), ("POST", "/healthz")):
            response = self._request(method, path)
            body = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
            self.assertNotIn("Traceback", body)
            self.assertNotIn(".py", body)


if __name__ == "__main__":
    unittest.main()
