"""Entrypoint: `python3 -m state`. Runs the HTTP state service directly as PID 1."""

from state.server import serve_forever

if __name__ == "__main__":
    serve_forever()
