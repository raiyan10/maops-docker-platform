"""Minimal stdlib-only JSON HTTP state service.

Runs directly as PID 1 inside the container: no daemonization, no process
manager, no application log files. All request/error logging goes to
stdout/stderr only. SIGTERM/SIGINT trigger a graceful HTTPServer shutdown -
mirrors app/server.py's and gateway/server.py's process model exactly, so
all three roles share the same lifecycle guarantees.

The only mutable domain here is a single monotonically increasing counter,
durably persisted under the fixed /data mount (see storage.py). No
filename, path, or destination is ever accepted from an incoming request -
the persisted file's name comes only from the Compose-mounted platform
config, validated once at startup (see config.py/platform_config.py).
"""

from __future__ import annotations

import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Callable
from urllib.parse import urlsplit

from state.config import StateConfig, load_config
from state.storage import CorruptedStateError, StateStore

JSON_CONTENT_TYPE = "application/json"

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def get_version() -> str:
    """Return the platform version, read fresh from VERSION on every call."""
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


RouteHandler = Callable[["JSONRequestHandler"], tuple[int, dict[str, object]]]


def _route_root(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    config = handler.state_config
    return 200, {
        "service": config.name,
        "version": get_version(),
        "status": "ok",
    }


def _route_healthz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    # Liveness only: proves the state process's own event loop is
    # responsive. Never touches the persisted store - that is /readyz's job.
    return 200, {"status": "ok"}


def _route_readyz(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    # Readiness includes storage readiness: a real (read-only, non-mutating)
    # attempt to load the persisted record. A corrupted store makes the
    # service not-ready rather than silently serving/accepting bad data.
    try:
        handler.state_store.read()
    except CorruptedStateError as exc:
        return 503, {"status": "not-ready", "error": str(exc)}
    return 200, {"status": "ready"}


def _route_state_get(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    try:
        record = handler.state_store.read()
    except CorruptedStateError:
        return 500, {"error": "state store corrupted"}
    return 200, {"value": record.value}


def _route_state_increment(handler: "JSONRequestHandler") -> tuple[int, dict[str, object]]:
    try:
        record = handler.state_store.increment()
    except CorruptedStateError:
        return 500, {"error": "state store corrupted"}
    return 200, {"value": record.value}


ROUTES: dict[str, dict[str, RouteHandler]] = {
    "/": {"GET": _route_root},
    "/healthz": {"GET": _route_healthz},
    "/readyz": {"GET": _route_readyz},
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

    server_version = "maops-docker-platform-state"
    state_config: StateConfig
    state_store: StateStore

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

    def do_GET(self) -> None:  # noqa: N802
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
        body = json.dumps({"error": message or "error"}, sort_keys=True).encode("utf-8")
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


def build_server(config: StateConfig) -> ThreadingHTTPServer:
    handler_class = type("BoundJSONRequestHandler", (JSONRequestHandler,), {})
    handler_class.state_config = config
    handler_class.state_store = StateStore(config.data_dir, config.state_filename)
    return ThreadingHTTPServer((config.host, config.port), handler_class)


def serve_forever(config: StateConfig | None = None) -> None:
    """Run the HTTP state service until SIGTERM/SIGINT requests a graceful shutdown."""
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
        f"data={config.data_dir / config.state_filename} (version {get_version()})\n"
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
