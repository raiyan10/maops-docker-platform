import tempfile
import unittest
from pathlib import Path

from app.platform_config import DEFAULT_DEPENDENCY_TIMEOUT_SECONDS, load_platform_config


class LoadPlatformConfigTests(unittest.TestCase):
    def test_missing_file_returns_default_timeout(self) -> None:
        config = load_platform_config(path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.dependency_timeout_seconds, DEFAULT_DEPENDENCY_TIMEOUT_SECONDS)

    def test_valid_config_overrides_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": 2.5}', encoding="utf-8")
            config = load_platform_config(path=path)
            self.assertEqual(config.dependency_timeout_seconds, 2.5)

    def test_integer_timeout_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": 5}', encoding="utf-8")
            config = load_platform_config(path=path)
            self.assertEqual(config.dependency_timeout_seconds, 5.0)

    def test_zero_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": 0}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_negative_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": -3}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_above_max_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": 999}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_boolean_timeout_is_rejected(self) -> None:
        """bool is a subclass of int in Python - must be explicitly rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_string_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": "3"}', encoding="utf-8")
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

    def test_path_override_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "dependency_timeout_seconds": 7}', encoding="utf-8")
            config = load_platform_config(env={"PLATFORM_CONFIG_PATH": str(path)})
            self.assertEqual(config.dependency_timeout_seconds, 7.0)


if __name__ == "__main__":
    unittest.main()
