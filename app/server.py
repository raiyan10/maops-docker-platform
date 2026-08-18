"""Minimal stdlib-only JSON HTTP server.

Runs directly as PID 1 inside the container: no daemonization, no process
manager, no application log files. All request/error logging goes to
stdout/stderr only. SIGTERM/SIGINT trigger a graceful HTTPServer shutdown
suitable for `docker stop`.
"""

from __future__ import annotations

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


def _route_root(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.app_config
    return 200, {
        "service": config.name,
        "version": get_version(),
        "status": "ok",
    }


def _route_healthz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    return 200, {"status": "ok"}


def _route_readyz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
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


ROUTES: dict[str, RouteHandler] = {
    "/": _route_root,
    "/healthz": _route_healthz,
    "/readyz": _route_readyz,
    "/info": _route_info,
}

ALLOWED_METHODS = "GET, HEAD"


class JSONRequestHandler(BaseHTTPRequestHandler):
    """Serves a fixed, deterministic set of JSON GET/HEAD endpoints."""

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

    def _dispatch(self, write_body: bool) -> None:
        path = urlsplit(self.path).path
        route = ROUTES.get(path)
        if route is None:
            self._send_json(404, {"error": "not found"}, write_body)
            return
        try:
            status, payload = route(self)
        except Exception:  # noqa: BLE001 - never leak internals to the client
            self.log_error("unhandled error serving %s", path)
            self._send_json(500, {"error": "internal server error"}, write_body)
            return
        self._send_json(status, payload, write_body)

    def do_GET(self) -> None:  # noqa: N802 - required BaseHTTPRequestHandler name
        self._dispatch(write_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(write_body=False)

    def _unsupported_method(self) -> None:
        path = urlsplit(self.path).path
        if path not in ROUTES:
            self._send_json(404, {"error": "not found"}, write_body=True)
            return
        body = json.dumps({"error": "method not allowed"}, sort_keys=True).encode(
            "utf-8"
        )
        self.send_response(405)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Allow", ALLOWED_METHODS)
        self.end_headers()
        self.wfile.write(body)

    do_POST = _unsupported_method
    do_PUT = _unsupported_method
    do_DELETE = _unsupported_method
    do_PATCH = _unsupported_method

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
