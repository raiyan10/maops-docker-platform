import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from state.config import StateConfig
from state.server import build_server, get_version


class StateServerTestCase(unittest.TestCase):
    """Runs the real state server on an OS-assigned loopback port, backed by
    a temporary directory - never the real repository /data path."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        config = StateConfig(
            host="127.0.0.1",
            port=0,
            name="test-state",
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

    def _request(self, method: str, path: str) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            response.read_body = response.read()  # type: ignore[attr-defined]
            return response
        finally:
            conn.close()


class RootEndpointTests(StateServerTestCase):
    def test_root_returns_expected_schema(self) -> None:
        response = self._request("GET", "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"service": "test-state", "version": get_version(), "status": "ok"})


class HealthzEndpointTests(StateServerTestCase):
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


class ReadyzEndpointTests(StateServerTestCase):
    def test_readyz_returns_ready_when_store_is_empty(self) -> None:
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"status": "ready"})

    def test_readyz_does_not_mutate_storage(self) -> None:
        self._request("GET", "/readyz")
        response = self._request("GET", "/state")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 0})

    def test_readyz_reports_not_ready_on_corrupted_state(self) -> None:
        (Path(self._tmp_dir.name) / "state.json").write_text("garbage", encoding="utf-8")
        response = self._request("GET", "/readyz")
        self.assertEqual(response.status, 503)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload.get("status"), "not-ready")
        self.assertIn("error", payload)


class StateGetTests(StateServerTestCase):
    def test_state_returns_zero_initially(self) -> None:
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 0})

    def test_state_reports_corrupted_store_as_controlled_500(self) -> None:
        (Path(self._tmp_dir.name) / "state.json").write_text("garbage", encoding="utf-8")
        response = self._request("GET", "/state")
        self.assertEqual(response.status, 500)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)
        body_text = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
        self.assertNotIn("Traceback", body_text)
        self.assertNotIn(".py", body_text)


class StateIncrementTests(StateServerTestCase):
    def test_increment_returns_one_from_zero(self) -> None:
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 1})

    def test_repeated_increments_are_monotonic_and_persisted(self) -> None:
        values = []
        for _ in range(3):
            response = self._request("POST", "/state/increment")
            values.append(json.loads(response.read_body)["value"])  # type: ignore[attr-defined]
        self.assertEqual(values, [1, 2, 3])

        response = self._request("GET", "/state")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertEqual(payload, {"value": 3})

    def test_increment_get_is_405(self) -> None:
        response = self._request("GET", "/state/increment")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "POST")

    def test_increment_reports_corrupted_store_as_controlled_500(self) -> None:
        (Path(self._tmp_dir.name) / "state.json").write_text("garbage", encoding="utf-8")
        response = self._request("POST", "/state/increment")
        self.assertEqual(response.status, 500)
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)


class NotFoundTests(StateServerTestCase):
    def test_unknown_path_returns_404_json(self) -> None:
        response = self._request("GET", "/does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_unknown_path_head_returns_404(self) -> None:
        response = self._request("HEAD", "/does-not-exist")
        self.assertEqual(response.status, 404)

    def test_post_to_unknown_path_returns_404_not_405(self) -> None:
        response = self._request("POST", "/does-not-exist")
        self.assertEqual(response.status, 404)


class UnsupportedMethodTests(StateServerTestCase):
    def test_post_to_state_get_only_path_returns_405_with_allow_header(self) -> None:
        response = self._request("POST", "/state")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")
        payload = json.loads(response.read_body)  # type: ignore[attr-defined]
        self.assertIn("error", payload)

    def test_put_to_root_returns_405(self) -> None:
        response = self._request("PUT", "/")
        self.assertEqual(response.status, 405)

    def test_delete_to_healthz_returns_405(self) -> None:
        response = self._request("DELETE", "/healthz")
        self.assertEqual(response.status, 405)

    def test_patch_to_readyz_returns_405(self) -> None:
        response = self._request("PATCH", "/readyz")
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader("Allow"), "GET, HEAD")


class DeterministicJsonTests(StateServerTestCase):
    def test_state_response_is_deterministic_across_calls(self) -> None:
        first = self._request("GET", "/state").read_body  # type: ignore[attr-defined]
        second = self._request("GET", "/state").read_body  # type: ignore[attr-defined]
        self.assertEqual(first, second)


class NoTracebackDisclosureTests(StateServerTestCase):
    def test_error_responses_never_contain_traceback_markers(self) -> None:
        for method, path in (("GET", "/does-not-exist"), ("POST", "/healthz")):
            response = self._request(method, path)
            body = response.read_body.decode("utf-8")  # type: ignore[attr-defined]
            self.assertNotIn("Traceback", body)
            self.assertNotIn(".py", body)


if __name__ == "__main__":
    unittest.main()
