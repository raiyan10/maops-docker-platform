"""Real-server tests for gateway/server.py.

Every test starts the real gateway ThreadingHTTPServer on an OS-assigned
loopback port, and every "upstream" it talks to is either a real fake
loopback HTTP server (started per-test, torn down per-test) or a closed
port chosen from the OS and never bound (a genuine connection-refused
condition, not a mock). Nothing here calls the public internet, uses a
fixed port, or mocks gateway.server's own request/response handling.
"""

import http.client
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from gateway import server as gateway_server
from gateway.config import GatewayConfig


def _closed_port() -> int:
    """Return a port number that is free right now and nothing is listening on.

    Binds then immediately closes a loopback socket to obtain an OS-assigned
    free port; the tiny window between close() and use is an accepted,
    extremely-low-risk pattern for a genuine "connection refused" fixture.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
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


def start_fake_upstream(responses: dict, delay_seconds: float = 0.0) -> tuple[HTTPServer, threading.Thread]:
    handler_class = type(
        "BoundFakeUpstreamHandler",
        (_FakeUpstreamHandler,),
        {"responses": responses, "delay_seconds": delay_seconds},
    )
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def json_response(status: int, payload: dict) -> tuple[int, bytes, str]:
    return status, json.dumps(payload).encode("utf-8"), "application/json"


class GatewayTestCase(unittest.TestCase):
    """Starts a real gateway pointed at an upstream_host/upstream_port the
    subclass controls via `upstream()`; both are always torn down."""

    upstream_responses: dict = {}
    upstream_delay_seconds: float = 0.0
    use_fake_upstream: bool = True
    upstream_timeout_seconds: float = 3.0

    def setUp(self) -> None:
        if self.use_fake_upstream:
            self.upstream_server, self.upstream_thread = start_fake_upstream(
                self.upstream_responses, self.upstream_delay_seconds
            )
            upstream_host = "127.0.0.1"
            upstream_port = self.upstream_server.server_address[1]
        else:
            self.upstream_server = None
            self.upstream_thread = None
            upstream_host = "127.0.0.1"
            upstream_port = _closed_port()

        config = GatewayConfig(
            host="127.0.0.1",
            port=0,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            upstream_timeout_seconds=self.upstream_timeout_seconds,
            name="test-gateway",
        )
        self.gateway_server = gateway_server.build_server(config)
        self.gateway_port = self.gateway_server.server_address[1]
        self.gateway_thread = threading.Thread(
            target=self.gateway_server.serve_forever, daemon=True
        )
        self.gateway_thread.start()

    def tearDown(self) -> None:
        self.gateway_server.shutdown()
        self.gateway_server.server_close()
        self.gateway_thread.join(timeout=5)
        if self.upstream_server is not None:
            self.upstream_server.shutdown()
            self.upstream_server.server_close()
            self.upstream_thread.join(timeout=5)

    def _request(self, method: str, path: str) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            response.read_body = response.read()  # type: ignore[attr-defined]
            return response
        finally:
            conn.close()


class RootEndpointTests(GatewayTestCase):
    def test_root_returns_expected_schema(self) -> None:
        response = self._request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(set(payload.keys()), {"service", "version", "status"})
        self.assertEqual(payload["service"], "test-gateway")
        self.assertEqual(payload["status"], "ok")


class HealthzEndpointTests(GatewayTestCase):
    """/healthz must never contact the upstream - use_fake_upstream=False
    (a closed port) and it must still succeed, proving liveness-only scope."""

    use_fake_upstream = False

    def test_healthz_returns_ok_without_contacting_upstream(self) -> None:
        response = self._request("GET", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ok", "role": "gateway"})

    def test_healthz_head_has_no_body(self) -> None:
        response = self._request("HEAD", "/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read_body, b"")  # type: ignore[attr-defined]
        self.assertIsNotNone(response.getheader("Content-Length"))


class ReadyzSuccessTests(GatewayTestCase):
    upstream_responses = {"/readyz": json_response(200, {"status": "ready"})}

    def test_readyz_success(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ready"})


class ReadyzUpstreamNotReadyTests(GatewayTestCase):
    upstream_responses = {"/readyz": json_response(200, {"status": "not-ready"})}

    def test_readyz_reports_upstream_not_ready(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        self.assertIn("error", payload)


class ReadyzUpstreamUnavailableTests(GatewayTestCase):
    use_fake_upstream = False

    def test_readyz_reports_unavailable_when_upstream_unreachable(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        self.assertIn("error", payload)
        # No raw network exception detail (e.g. errno text) is disclosed.
        self.assertNotIn("Errno", json.dumps(payload))


class UpstreamInfoSuccessTests(GatewayTestCase):
    upstream_responses = {
        "/info": json_response(
            200,
            {"name": "maops-docker-platform", "version": "0.2.0", "python_version": "3.13.0", "host": "0.0.0.0", "port": 8080},
        )
    }

    def test_upstream_info_success(self) -> None:
        response = self._request("GET", "/upstream/info")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("upstream", payload)
        self.assertEqual(payload["upstream"]["name"], "maops-docker-platform")
        self.assertEqual(payload["upstream"]["version"], "0.2.0")


class UpstreamInfoMalformedResponseTests(GatewayTestCase):
    upstream_responses = {"/info": (200, b"not-json-at-all", "application/json")}

    def test_upstream_info_malformed_response_returns_controlled_502(self) -> None:
        response = self._request("GET", "/upstream/info")
        self.assertEqual(response.status, 502)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)


class UpstreamInfoNonDictResponseTests(GatewayTestCase):
    upstream_responses = {"/info": (200, b"[1, 2, 3]", "application/json")}

    def test_upstream_info_non_dict_json_returns_controlled_502(self) -> None:
        response = self._request("GET", "/upstream/info")
        self.assertEqual(response.status, 502)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)


class UpstreamInfoUnreachableTests(GatewayTestCase):
    use_fake_upstream = False

    def test_upstream_info_unreachable_returns_controlled_502(self) -> None:
        response = self._request("GET", "/upstream/info")
        self.assertEqual(response.status, 502)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)
        self.assertNotIn(".py", body_text)


class UpstreamInfoNonStatus200Tests(GatewayTestCase):
    upstream_responses = {"/info": json_response(500, {"error": "boom"})}

    def test_upstream_info_non_200_upstream_status_returns_controlled_502(self) -> None:
        response = self._request("GET", "/upstream/info")
        self.assertEqual(response.status, 502)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)


class UpstreamTimeoutTests(GatewayTestCase):
    """Upstream deliberately never responds in time; the gateway's own
    configured upstream_timeout_seconds is set small (via GatewayConfig,
    not a module-level constant - the timeout is a per-config value as of
    Day 3, sourced from the mounted platform config) so this test stays
    fast rather than waiting out the real 3s production default."""

    upstream_responses = {"/readyz": json_response(200, {"status": "ready"})}
    upstream_delay_seconds = 0.5
    upstream_timeout_seconds = 0.1

    def test_upstream_timeout_converts_to_controlled_503(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)


class StateGetForwardingTests(GatewayTestCase):
    upstream_responses = {"/state": json_response(200, {"value": 3})}

    def test_state_get_forwards_upstream_payload_unchanged(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 3})


class StateGetUnavailableTests(GatewayTestCase):
    use_fake_upstream = False

    def test_state_get_returns_controlled_503_when_upstream_unreachable(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)


class StateIncrementForwardingTests(GatewayTestCase):
    upstream_responses = {"/state/increment": json_response(200, {"value": 4})}

    def test_state_increment_forwards_as_a_real_post(self) -> None:
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 4})

    def test_state_increment_get_is_405(self) -> None:
        response = self._request("GET", "/state/increment")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "POST")


class StateIncrementUnavailableTests(GatewayTestCase):
    use_fake_upstream = False

    def test_state_increment_returns_controlled_503_when_upstream_unreachable(self) -> None:
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)


class NotFoundTests(GatewayTestCase):
    use_fake_upstream = False

    def test_unknown_path_returns_404_json(self) -> None:
        response = self._request("GET", "/does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_unknown_path_head_returns_404(self) -> None:
        response = self._request("HEAD", "/does-not-exist")
        self.assertEqual(response.status, 404)


class UnsupportedMethodTests(GatewayTestCase):
    use_fake_upstream = False

    def test_post_to_known_path_returns_405_with_allow_header(self) -> None:
        response = self._request("POST", "/healthz")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_put_to_known_path_returns_405(self) -> None:
        response = self._request("PUT", "/")
        self.assertEqual(response.status, 405)

    def test_patch_to_known_path_returns_405(self) -> None:
        response = self._request("PATCH", "/healthz")
        self.assertEqual(response.status, 405)

    def test_delete_to_known_path_returns_405(self) -> None:
        response = self._request("DELETE", "/upstream/info")
        self.assertEqual(response.status, 405)

    def test_post_to_unknown_path_returns_404_not_405(self) -> None:
        response = self._request("POST", "/does-not-exist")
        self.assertEqual(response.status, 404)

    def test_post_to_state_returns_405(self) -> None:
        response = self._request("POST", "/state")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")


class NoTracebackDisclosureTests(GatewayTestCase):
    use_fake_upstream = False

    def test_error_responses_never_contain_traceback_markers(self) -> None:
        for method, path in (("GET", "/does-not-exist"), ("POST", "/healthz"), ("GET", "/readyz")):
            response = self._request(method, path)
            body = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
            self.assertNotIn("Traceback", body)
            self.assertNotIn(".py", body)


if __name__ == "__main__":
    unittest.main()
