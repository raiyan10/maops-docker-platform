"""Atomic, fsync'd persistence for a single monotonically increasing counter.

Concurrency scope (see docs/persistence.md): this is single-process,
in-container concurrency safety only - a `threading.Lock` serializes
read-modify-write increments against concurrent requests within one
running `state` process. It does NOT coordinate writers across multiple
processes or containers sharing the same volume; this platform runs
exactly one `state` replica at a time by design, and this store makes no
attempt to be a distributed database (see `.claude/CLAUDE.md`: "No Redis,
No PostgreSQL, No SQLite dependency unless compelling").

Write safety: every write goes to a temporary file in the same directory
as the target (so the final `os.replace` is an atomic same-filesystem
rename, never a partial write visible to a reader), is flushed and
`fsync`'d before the rename, and the containing directory is `fsync`'d
afterward so the rename itself is durable. A reader never observes a
half-written file.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1


class CorruptedStateError(RuntimeError):
    """Raised when the persisted state file exists but cannot be trusted.

    Deliberately never silently coerced into a valid-looking value - a
    corrupted store is reported as an error, not guessed at.
    """


@dataclass(frozen=True)
class StateRecord:
    value: int


class StateStore:
    def __init__(self, data_dir: Path, filename: str) -> None:
        self._path = data_dir / filename
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> StateRecord:
        """Return the current persisted value, or value=0 if never initialized.

        Never mutates the filesystem - safe to call from a readiness probe.
        """
        with self._lock:
            return self._read_locked()

    def increment(self) -> StateRecord:
        """Atomically read, increment, and durably persist the new value."""
        with self._lock:
            current = self._read_locked()
            updated = StateRecord(value=current.value + 1)
            self._write_locked(updated)
            return updated

    def _read_locked(self) -> StateRecord:
        if not self._path.exists():
            return StateRecord(value=0)
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CorruptedStateError(f"state file unreadable: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CorruptedStateError(f"state file is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise CorruptedStateError("state file does not contain a JSON object")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise CorruptedStateError(
                f"state file schema_version is {data.get('schema_version')!r}, "
                f"expected {SCHEMA_VERSION}"
            )
        value = data.get("value")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CorruptedStateError(f"state file 'value' is not a non-negative integer: {value!r}")
        return StateRecord(value=value)

    def _write_locked(self, record: StateRecord) -> None:
        payload = json.dumps(
            {"schema_version": SCHEMA_VERSION, "value": record.value}, sort_keys=True
        ).encode("utf-8")
        tmp_path = self._path.with_name(f".{self._path.name}.tmp")

        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self._path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

        self._fsync_dir()

    def _fsync_dir(self) -> None:
        # Durability for the rename itself, not just the file content -
        # "practical" per this project's own scope (a single-process
        # educational platform, not a database), skipped only if the
        # platform genuinely doesn't support it (e.g. some overlay/test
        # filesystems reject O_RDONLY fsync on a directory).
        try:
            dir_fd = os.open(self._path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
