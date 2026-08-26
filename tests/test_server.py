import http.client
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import AppConfig, load_config
from app.server import build_server
from app.version import get_version

DEFAULT_STATE_TIMEOUT_SECONDS = 3.0


def _closed_port() -> int:
    """Return a port number that is free right now and nothing is listening on.

    Binds then immediately closes a loopback socket to obtain an OS-assigned
    free port; the tiny window between close() and use is an accepted,
    extremely-low-risk pattern for a genuine "connection refused" fixture
    (same pattern already used by tests/test_gateway_server.py).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _FakeStateHandler(BaseHTTPRequestHandler):
    responses: dict = {}
    delay_seconds: float = 0.0

    def _serve(self, write_body: bool) -> None:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        status, body, content_type = self.responses.get(
            self.path, (404, b'{"error": "not found"}', "application/json")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        self._serve(write_body=True)

    def do_POST(self) -> None:
        self._serve(write_body=True)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def start_fake_state(responses: dict, delay_seconds: float = 0.0) -> tuple[HTTPServer, threading.Thread]:
    handler_class = type(
        "BoundFakeStateHandler", (_FakeStateHandler,), {"responses": responses, "delay_seconds": delay_seconds}
    )
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def json_response(status: int, payload: dict) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json"


DEFAULT_STATE_RESPONSES = {"/readyz": json_response(200, {"status": "ready"})}


class ServerTestCase(unittest.TestCase):
    """Runs the real app server on an OS-assigned loopback port, pointed at
    an OS-assigned loopback fake `state` service the subclass controls via
    `state_responses`/`use_fake_state`; both are always torn down."""

    state_responses: dict = DEFAULT_STATE_RESPONSES
    state_delay_seconds: float = 0.0
    use_fake_state: bool = True
    state_timeout_seconds: float = DEFAULT_STATE_TIMEOUT_SECONDS

    def setUp(self) -> None:
        if self.use_fake_state:
            self.state_server, self.state_thread = start_fake_state(self.state_responses, self.state_delay_seconds)
            state_host = "127.0.0.1"
            state_port = self.state_server.server_address[1]
        else:
            self.state_server = None
            self.state_thread = None
            state_host = "127.0.0.1"
            state_port = _closed_port()

        config = AppConfig(
            host="127.0.0.1",
            port=0,
            name="test-app",
            state_host=state_host,
            state_port=state_port,
            state_timeout_seconds=self.state_timeout_seconds,
        )
        self.server = build_server(config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.state_server is not None:
            self.state_server.shutdown()
            self.state_server.server_close()
            self.state_thread.join(timeout=5)

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
    """/healthz must never contact state - use_fake_state=False (a closed
    port) and it must still succeed, proving liveness-only scope."""

    use_fake_state = False

    def test_healthz_returns_ok(self) -> None:
        response = self._request("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ok", "role": "app"})

    def test_healthz_head_has_no_body(self) -> None:
        response = self._request("HEAD", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read_body, b"")  # type: ignore[attr-defined]
        self.assertIsNotNone(response.getheader("Content-Length"))


class ReadyzSuccessTests(ServerTestCase):
    state_responses = {"/readyz": json_response(200, {"status": "ready"})}

    def test_readyz_returns_ready_when_state_is_ready(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ready"})


class ReadyzStateNotReadyTests(ServerTestCase):
    state_responses = {"/readyz": json_response(200, {"status": "not-ready"})}

    def test_readyz_reports_state_not_ready(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        self.assertIn("error", payload)


class ReadyzStateUnavailableTests(ServerTestCase):
    use_fake_state = False

    def test_readyz_reports_unavailable_when_state_unreachable(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        self.assertIn("error", payload)
        # No raw network exception detail (e.g. errno text) is disclosed.
        self.assertNotIn("Errno", json.dumps(payload))


class StateTimeoutTests(ServerTestCase):
    """Day 6 (closes Day 5 finding M-B, day-05-health-timeout-review.md):
    a fast, Docker-free regression test proving app's own INNER hop
    (state_dependency_timeout_seconds) actually bounds a slow `state`
    response and converts it to a controlled 503 - mirroring
    tests/test_gateway_server.py::UpstreamTimeoutTests for gateway's outer
    hop. The real-Docker A-6 proof (scripts/reliability/reliability_check.py's
    `docker pause state` scenario) remains the authoritative runtime
    evidence that this timeout is honored end-to-end through the real
    gateway->app->state chain; this test exists so a regression in app's
    own timeout wiring is caught by `make test` in a fraction of a second,
    not only by the multi-minute `make reliability-check`.

    `state_delay_seconds`/`state_timeout_seconds` were already exposed as
    fixture hooks on ServerTestCase before this test existed - no subclass
    had ever set state_delay_seconds to a nonzero value."""

    state_responses = {"/state": json_response(200, {"value": 1})}
    state_delay_seconds = 0.5
    state_timeout_seconds = 0.1

    def test_state_inner_timeout_converts_to_controlled_503(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)


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


class StateGetForwardingTests(ServerTestCase):
    state_responses = {"/state": json_response(200, {"value": 5})}

    def test_state_get_forwards_state_payload_unchanged(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 5})


class StateGetUnavailableTests(ServerTestCase):
    use_fake_state = False

    def test_state_get_returns_controlled_503_when_state_unreachable(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)
        self.assertNotIn(".py", body_text)


class StateIncrementForwardingTests(ServerTestCase):
    state_responses = {"/state/increment": json_response(200, {"value": 6})}

    def test_state_increment_forwards_as_a_real_post(self) -> None:
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 6})

    def test_state_increment_get_is_405(self) -> None:
        response = self._request("GET", "/state/increment")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "POST")


class StateIncrementUnavailableTests(ServerTestCase):
    use_fake_state = False

    def test_state_increment_returns_controlled_503_when_state_unreachable(self) -> None:
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)


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

    def test_post_to_state_returns_405(self) -> None:
        response = self._request("POST", "/state")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")


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
