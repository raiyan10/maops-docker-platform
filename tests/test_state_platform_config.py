import tempfile
import unittest
from pathlib import Path

from state.platform_config import (
    DEFAULT_PLATFORM_NAME,
    DEFAULT_STATE_FILENAME,
    load_platform_config,
)


class LoadPlatformConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        config = load_platform_config(path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.platform_name, DEFAULT_PLATFORM_NAME)
        self.assertEqual(config.state_filename, DEFAULT_STATE_FILENAME)
        self.assertEqual(config.schema_version, 1)

    def test_valid_config_is_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "platform_name": "custom-name", "state_filename": "x.json"}',
                encoding="utf-8",
            )
            config = load_platform_config(path=path)
            self.assertEqual(config.platform_name, "custom-name")
            self.assertEqual(config.state_filename, "x.json")

    def test_missing_optional_fields_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1}', encoding="utf-8")
            config = load_platform_config(path=path)
            self.assertEqual(config.platform_name, DEFAULT_PLATFORM_NAME)
            self.assertEqual(config.state_filename, DEFAULT_STATE_FILENAME)

    def test_malformed_json_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_non_object_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_wrong_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 2}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_missing_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"platform_name": "x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_empty_platform_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "platform_name": "  "}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_non_string_platform_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "platform_name": 5}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_state_filename_with_path_separator_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_filename": "sub/dir.json"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_state_filename_parent_directory_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_filename": ".."}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_state_filename_single_dot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_filename": "."}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_non_string_state_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_filename": 5}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_path_override_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "state_filename": "via-env.json"}', encoding="utf-8")
            config = load_platform_config(env={"PLATFORM_CONFIG_PATH": str(path)})
            self.assertEqual(config.state_filename, "via-env.json")

    def test_explicit_path_wins_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            explicit_path = Path(tmp_dir) / "explicit.json"
            explicit_path.write_text('{"schema_version": 1, "state_filename": "explicit.json"}', encoding="utf-8")
            config = load_platform_config(
                path=explicit_path, env={"PLATFORM_CONFIG_PATH": "/nonexistent/other.json"}
            )
            self.assertEqual(config.state_filename, "explicit.json")


if __name__ == "__main__":
    unittest.main()
