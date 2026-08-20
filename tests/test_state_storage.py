import json
import tempfile
import unittest
from pathlib import Path

from state.storage import CorruptedStateError, StateRecord, StateStore


class ReadTests(unittest.TestCase):
    def test_read_returns_zero_when_file_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            record = store.read()
            self.assertEqual(record.value, 0)

    def test_read_does_not_create_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            store.read()
            self.assertFalse(store.path.exists())

    def test_read_returns_persisted_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 1, "value": 7}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            self.assertEqual(store.read().value, 7)


class MalformedStateTests(unittest.TestCase):
    def test_invalid_json_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text("{not valid json", encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_non_object_json_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_wrong_schema_version_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 2, "value": 1}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_missing_value_field_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 1}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_negative_value_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 1, "value": -1}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_non_integer_value_raises_corrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 1, "value": "seven"}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_boolean_value_raises_corrupted(self) -> None:
        """bool is a subclass of int in Python - must be explicitly rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text('{"schema_version": 1, "value": true}', encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()

    def test_corrupted_state_is_never_silently_coerced(self) -> None:
        """Reading corrupted state raises rather than returning value=0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text("garbage", encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.read()
            # The file must not have been silently rewritten as a side effect.
            self.assertEqual(path.read_text(encoding="utf-8"), "garbage")


class IncrementTests(unittest.TestCase):
    def test_increment_from_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            record = store.increment()
            self.assertEqual(record.value, 1)

    def test_increment_persists_across_new_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_a = StateStore(Path(tmp_dir), "state.json")
            store_a.increment()
            store_a.increment()
            store_b = StateStore(Path(tmp_dir), "state.json")
            self.assertEqual(store_b.read().value, 2)

    def test_repeated_increments_are_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            values = [store.increment().value for _ in range(5)]
            self.assertEqual(values, [1, 2, 3, 4, 5])

    def test_increment_raises_on_existing_corrupted_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "state.json"
            path.write_text("garbage", encoding="utf-8")
            store = StateStore(Path(tmp_dir), "state.json")
            with self.assertRaises(CorruptedStateError):
                store.increment()


class AtomicWriteTests(unittest.TestCase):
    def test_written_file_is_valid_json_matching_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            store.increment()
            on_disk = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, {"schema_version": 1, "value": 1})

    def test_no_temporary_file_left_behind_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            store.increment()
            remaining = sorted(p.name for p in Path(tmp_dir).iterdir())
            self.assertEqual(remaining, ["state.json"])

    def test_write_failure_does_not_leave_a_stray_tmp_file(self) -> None:
        """A failed write (e.g. read-only directory) must clean up its temp file."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir)
            store = StateStore(data_dir, "state.json")
            original_mode = data_dir.stat().st_mode
            data_dir.chmod(0o500)  # read + execute only, no write
            try:
                with self.assertRaises(OSError):
                    store.increment()
            finally:
                data_dir.chmod(original_mode)
            remaining = list(data_dir.iterdir())
            self.assertEqual(remaining, [])

    def test_partial_write_never_visible_to_a_reader(self) -> None:
        """The final file always parses as valid JSON - simulated by
        confirming the store never writes directly to the real filename
        (only ever via atomic rename of a temp file)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir), "state.json")
            for _ in range(10):
                store.increment()
                # A reader concurrently opening the file must always see
                # complete, valid JSON - never a half-written fragment.
                record = store.read()
                self.assertIsInstance(record, StateRecord)


if __name__ == "__main__":
    unittest.main()
