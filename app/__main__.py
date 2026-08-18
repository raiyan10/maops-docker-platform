"""Entrypoint: `python3 -m app`. Runs the HTTP server directly as PID 1."""

from app.server import serve_forever

if __name__ == "__main__":
    serve_forever()
