"""Entrypoint: `python3 -m gateway`. Runs the HTTP gateway directly as PID 1."""

from gateway.server import serve_forever

if __name__ == "__main__":
    serve_forever()
