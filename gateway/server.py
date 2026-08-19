"""Minimal stdlib-only JSON HTTP gateway.

Runs directly as PID 1 inside the container: no daemonization, no process
manager, no application log files. All request/error logging goes to
stdout/stderr only. SIGTERM/SIGINT trigger a graceful HTTPServer shutdown
suitable for `docker stop` - mirrors app/server.py's process model exactly
so both roles share the same lifecycle guarantees.

The gateway makes outbound HTTP calls to exactly one fixed destination -
`GatewayConfig.upstream_host`/`upstream_port`, resolved once at process
startup from GATEWAY_HOST/GATEWAY_PORT/UPSTREAM_HOST/UPSTREAM_PORT (see
config.py). No request path, query string, header, or body value from an
incoming client request is ever used to choose or influence the upstream
destination - this is a deliberate, narrow proxy target, not an
arbitrary-URL fetcher, and is what prevents SSRF-style abuse.
"""

from __future__ import annotations

import http.client
import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Callable
from urllib.parse import urlsplit

from gateway.config import UPSTREAM_TIMEOUT_SECONDS, GatewayConfig, load_config

JSON_CONTENT_TYPE = "application/json"

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def get_version() -> str:
    """Return the platform version, read fresh from VERSION on every call."""
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


RouteHandler = Callable[["JSONRequestHandler"], tuple[int, dict[str, object]]]


class UpstreamError(Exception):
    """Raised when the upstream app cannot be reached or answers unusably.

    Never carries the underlying exception's raw text into a client
    response - only this module's own controlled message does.
    """


def _call_upstream(config: GatewayConfig, path: str) -> dict[str, object]:
    """Make a real, bounded HTTP GET to the fixed configured upstream.

    Raises UpstreamError (never the raw underlying exception) on any
    connection failure, non-200 status, or non-JSON body - callers never
    need to catch anything except UpstreamError.
    """
    try:
        conn = http.client.HTTPConnection(
            config.upstream_host,
            config.upstream_port,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
        finally:
            conn.close()
    except (OSError, http.client.HTTPException) as exc:
        raise UpstreamError("upstream unavailable") from exc

    if response.status != 200:
        raise UpstreamError(f"upstream returned unexpected status {response.status}")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UpstreamError("upstream returned a malformed response") from exc

    if not isinstance(payload, dict):
        raise UpstreamError("upstream returned a malformed response")

    return payload


def _route_root(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.gateway_config
    return 200, {
        "service": config.name,
        "version": get_version(),
        "status": "ok",
    }


def _route_healthz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    # Liveness only: proves the gateway's own process/event loop is
    # responsive. Never calls the upstream - that is /readyz's job.
    return 200, {"status": "ok"}


def _route_readyz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.gateway_config
    try:
        upstream = _call_upstream(config, "/readyz")
    except UpstreamError as exc:
        return 503, {"status": "not-ready", "error": str(exc)}

    if upstream.get("status") != "ready":
        return 503, {"status": "not-ready", "error": "upstream not ready"}
    return 200, {"status": "ready"}


def _route_upstream_info(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.gateway_config
    try:
        upstream = _call_upstream(config, "/info")
    except UpstreamError as exc:
        return 502, {"error": str(exc)}

    return 200, {"upstream": upstream}


ROUTES: dict[str, RouteHandler] = {
    "/": _route_root,
    "/healthz": _route_healthz,
    "/readyz": _route_readyz,
    "/upstream/info": _route_upstream_info,
}

ALLOWED_METHODS = "GET, HEAD"


class JSONRequestHandler(BaseHTTPRequestHandler):
    """Serves a fixed, deterministic set of JSON GET/HEAD endpoints."""

    server_version = "maops-docker-platform-gateway"
    gateway_config: GatewayConfig

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


def build_server(config: GatewayConfig) -> ThreadingHTTPServer:
    handler_class = type("BoundJSONRequestHandler", (JSONRequestHandler,), {})
    handler_class.gateway_config = config
    return ThreadingHTTPServer((config.host, config.port), handler_class)


def serve_forever(config: GatewayConfig | None = None) -> None:
    """Run the HTTP gateway until SIGTERM/SIGINT requests a graceful shutdown."""
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
        f"{config.name} listening on {config.host}:{config.port}, "
        f"upstream={config.upstream_host}:{config.upstream_port} "
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
