"""Minimal stdlib-only JSON HTTP server.

Runs directly as PID 1 inside the container: no daemonization, no process
manager, no application log files. All request/error logging goes to
stdout/stderr only. SIGTERM/SIGINT trigger a graceful HTTPServer shutdown
suitable for `docker stop`.

As of Day 3, app also makes real, bounded outbound HTTP calls to a single
fixed destination - `AppConfig.state_host`/`state_port`, resolved once at
process startup from STATE_HOST/STATE_PORT (see config.py) - to demonstrate
persistence through the `gateway -> app -> state` chain. No request path,
query string, header, or body value is ever used to choose or influence
that destination, mirroring gateway's own SSRF-prevention design toward
`app`.
"""

from __future__ import annotations

import http.client
import json
import platform
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import FrameType
from typing import Callable
from urllib.parse import urlsplit

from app.config import AppConfig, load_config
from app.version import get_version

JSON_CONTENT_TYPE = "application/json"

RouteHandler = Callable[["JSONRequestHandler"], tuple[int, dict[str, object]]]


class StateError(Exception):
    """Raised when the state service cannot be reached or answers unusably.

    Never carries the underlying exception's raw text into a client
    response - only this module's own controlled message does.
    """


def _call_state(config: AppConfig, method: str, path: str) -> dict[str, object]:
    """Make a real, bounded HTTP call to the fixed configured state service.

    Raises StateError (never the raw underlying exception) on any
    connection failure, non-200 status, or non-JSON body - callers never
    need to catch anything except StateError.
    """
    try:
        conn = http.client.HTTPConnection(
            config.state_host,
            config.state_port,
            timeout=config.state_timeout_seconds,
        )
        try:
            conn.request(method, path)
            response = conn.getresponse()
            body = response.read()
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as exc:
        raise StateError("state unavailable") from exc

    if response.status != 200:
        raise StateError(f"state returned unexpected status {response.status}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise StateError("state returned a malformed response") from exc

    if not isinstance(payload, dict):
        raise StateError("state returned a malformed response")

    return payload


def _route_root(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    return 200, {
        "service": config.name,
        "version": get_version(),
        "status": "ok",
    }


def _route_healthz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    # Liveness only: proves app's own process/event loop is responsive.
    # Never calls state - that is /readyz's job. "role" identifies which
    # MAOps workload is answering, so a healthcheck probe can reject a
    # response from the wrong service (Day 4 finding H-1).
    return 200, {"status": "ok", "role": "app"}


def _route_readyz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    try:
        state_status = _call_state(config, "GET", "/readyz")
    except StateError as exc:
        return 503, {"status": "not-ready", "error": str(exc)}

    if state_status.get("status") != "ready":
        return 503, {"status": "not-ready", "error": "state not ready"}
    return 200, {"status": "ready"}


def _route_info(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    return 200, {
        "name": config.name,
        "version": get_version(),
        "python_version": platform.python_version(),
        "host": config.host,
        "port": config.port,
    }


def _route_state_get(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    try:
        payload = _call_state(config, "GET", "/state")
    except StateError as exc:
        return 503, {"error": str(exc)}
    return 200, payload


def _route_state_increment(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    try:
        payload = _call_state(config, "POST", "/state/increment")
    except StateError as exc:
        return 503, {"error": str(exc)}
    return 200, payload


ROUTES: dict[str, dict[str, RouteHandler]] = {
    "/": {"GET": _route_root},
    "/healthz": {"GET": _route_healthz},
    "/readyz": {"GET": _route_readyz},
    "/info": {"GET": _route_info},
    "/state": {"GET": _route_state_get},
    "/state/increment": {"POST": _route_state_increment},
}


def _allowed_methods(route: dict[str, RouteHandler]) -> list[str]:
    methods = set(route.keys())
    if "GET" in methods:
        methods.add("HEAD")
    return sorted(methods)


class JSONRequestHandler(BaseHTTPRequestHandler):
    """Serves a fixed, deterministic set of JSON endpoints."""

    server_version = "maops-docker-platform"
    app_config: AppConfig

    def _send_json(
        self, status: int, payload: dict[str, object], write_body: bool
    ) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def _send_405(self, allowed_methods: list[str], write_body: bool) -> None:
        body = json.dumps({"error": "method not allowed"}, sort_keys=True).encode("utf-8")
        self.send_response(405)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Allow", ", ".join(allowed_methods))
        self.end_headers()
        if write_body:
            self.wfile.write(body)

    def _dispatch(self, method: str, write_body: bool) -> None:
        path = urlsplit(self.path).path
        route = ROUTES.get(path)
        if route is None:
            self._send_json(404, {"error": "not found"}, write_body)
            return

        route_handler = route.get(method)
        if route_handler is None and method == "HEAD":
            route_handler = route.get("GET")
        if route_handler is None:
            self._send_405(_allowed_methods(route), write_body)
            return

        try:
            status, payload = route_handler(self)
        except Exception:  # noqa: BLE001 - never leak internals to the client
            self.log_error("unhandled error serving %s", path)
            self._send_json(500, {"error": "internal server error"}, write_body)
            return
        self._send_json(status, payload, write_body)

    def do_GET(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler name
        self._dispatch("GET", write_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD", write_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST", write_body=True)

    def _method_not_implemented(self) -> None:
        self._dispatch(self.command, write_body=True)

    do_PUT = _method_not_implemented
    do_DELETE = _method_not_implemented
    do_PATCH = _method_not_implemented

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        """Override to guarantee a JSON error body instead of the default HTML page."""
        body = json.dumps({"error": message or "error"}, sort_keys=True).encode(
            "utf-8"
        )
        self.send_response(code)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stdout.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )
        sys.stdout.flush()


def build_server(config: AppConfig) -> ThreadingHTTPServer:
    handler_class = type("BoundJSONRequestHandler", (JSONRequestHandler,), {})
    handler_class.app_config = config
    return ThreadingHTTPServer((config.host, config.port), handler_class)


def serve_forever(config: AppConfig | None = None) -> None:
    """Run the HTTP server until SIGTERM/SIGINT requests a graceful shutdown."""
    config = config or load_config()
    server = build_server(config)

    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        sys.stdout.write(f"received signal {signum}, shutting down\n")
        sys.stdout.flush()
        # server.shutdown() blocks until serve_forever()'s loop exits, so it
        # must run on a different thread than the one calling serve_forever().
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    sys.stdout.write(
        f"{config.name} listening on {config.host}:{config.port} "
        f"(version {get_version()})\n"
    )
    sys.stdout.flush()

    try:
        server.serve_forever()
    finally:
        server.server_close()
        sys.stdout.write("server stopped\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_forever()
