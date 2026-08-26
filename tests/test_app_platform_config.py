import tempfile
import unittest
from pathlib import Path

from app.platform_config import (
    DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS,
    MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS,
    load_platform_config,
)


class LoadPlatformConfigTests(unittest.TestCase):
    def test_missing_file_returns_default_timeout(self) -> None:
        config = load_platform_config(path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.state_dependency_timeout_seconds, DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS)

    def test_valid_config_overrides_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": 2.5}', encoding="utf-8")
            config = load_platform_config(path=path)
            self.assertEqual(config.state_dependency_timeout_seconds, 2.5)

    def test_integer_timeout_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": 5}', encoding="utf-8")
            config = load_platform_config(path=path)
            self.assertEqual(config.state_dependency_timeout_seconds, 5.0)

    def test_zero_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": 0}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_negative_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": -3}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_above_max_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": 999}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_max_boundary_timeout_is_accepted(self) -> None:
        """Day 6 (closes Day 5 finding L-1, day-05-test-adversarial-review.md):
        the validator's own comparison is inclusive (`0 < value <=
        max_value`) - a value exactly AT the max must be accepted, not just
        rejected above it. An off-by-one regression that flipped `<=` to
        `<` would not be caught without this test."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                f'{{"schema_version": 1, "state_dependency_timeout_seconds": {MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS}}}',
                encoding="utf-8",
            )
            config = load_platform_config(path=path)
            self.assertEqual(config.state_dependency_timeout_seconds, MAX_STATE_DEPENDENCY_TIMEOUT_SECONDS)

    def test_boolean_timeout_is_rejected(self) -> None:
        """bool is a subclass of int in Python - must be explicitly rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_string_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": "3"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_nan_timeout_is_rejected(self) -> None:
        """Python's json module accepts NaN/Infinity as a non-standard
        extension by default - a config author could genuinely ship one."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_infinity_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": Infinity}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_negative_infinity_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": -Infinity}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_malformed_json_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_wrong_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 99}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_schema_version_true_is_rejected(self) -> None:
        """bool is a subclass of int in Python - True == 1 must not pass as schema_version 1."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": true, "state_dependency_timeout_seconds": 3}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_schema_version_false_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": false, "state_dependency_timeout_seconds": 3}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_path_override_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_dependency_timeout_seconds": 7}', encoding="utf-8")
            config = load_platform_config(env={"PLATFORM_CONFIG_PATH": str(path)})
            self.assertEqual(config.state_dependency_timeout_seconds, 7.0)


if __name__ == "__main__":
    unittest.main()
