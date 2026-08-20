import tempfile
import unittest
from pathlib import Path

from state.config import DEFAULT_NAME, DEFAULT_STATE_HOST, DEFAULT_STATE_PORT, load_config, parse_port


class ParsePortTests(unittest.TestCase):
    def test_valid_port(self) -> None:
        self.assertEqual(parse_port("8080", "STATE_PORT"), 8080)

    def test_valid_port_boundaries(self) -> None:
        self.assertEqual(parse_port("1", "STATE_PORT"), 1)
        self.assertEqual(parse_port("65535", "STATE_PORT"), 65535)

    def test_whitespace_is_stripped(self) -> None:
        self.assertEqual(parse_port("  8080  ", "STATE_PORT"), 8080)

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("", "STATE_PORT")

    def test_malformed_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("not-a-port", "STATE_PORT")

    def test_zero_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("0", "STATE_PORT")

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("-1", "STATE_PORT")

    def test_above_max_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("65536", "STATE_PORT")

    def test_error_message_names_the_variable(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_port("bogus", "STATE_PORT")
        self.assertIn("STATE_PORT", str(ctx.exception))


class LoadConfigTests(unittest.TestCase):
    def test_defaults_when_env_empty(self) -> None:
        config = load_config(env={}, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.host, DEFAULT_STATE_HOST)
        self.assertEqual(config.port, DEFAULT_STATE_PORT)
        self.assertEqual(config.name, DEFAULT_NAME)
        self.assertEqual(config.data_dir, Path("/data"))
        self.assertEqual(config.state_filename, "state.json")

    def test_state_host_override(self) -> None:
        config = load_config(env={"STATE_HOST": "127.0.0.1"}, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.host, "127.0.0.1")

    def test_state_name_override(self) -> None:
        config = load_config(env={"STATE_NAME": "custom-state"}, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.name, "custom-state")

    def test_state_port_override(self) -> None:
        config = load_config(env={"STATE_PORT": "9090"}, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.port, 9090)

    def test_invalid_state_port_propagates(self) -> None:
        with self.assertRaises(ValueError):
            load_config(env={"STATE_PORT": "not-a-port"}, platform_config_path=Path("/nonexistent/platform.json"))

    def test_arbitrary_environment_is_not_consulted(self) -> None:
        env = {
            "STATE_PORT": "8080",
            "SECRET_TOKEN": "should-never-be-read",
            "AWS_SECRET_ACCESS_KEY": "should-never-be-read",
            "PATH": "/usr/bin",
        }
        config = load_config(env=env, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(
            vars(config).keys(), {"host", "port", "name", "data_dir", "state_filename"}
        )

    def test_blank_host_falls_back_to_default(self) -> None:
        config = load_config(env={"STATE_HOST": "   "}, platform_config_path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.host, DEFAULT_STATE_HOST)

    def test_data_dir_is_always_fixed_data(self) -> None:
        """Not env-configurable by design (see storage.py/docs/persistence.md)."""
        config = load_config(
            env={"STATE_HOST": "x", "PLATFORM_CONFIG_PATH": "/nonexistent"},
            platform_config_path=Path("/nonexistent/platform.json"),
        )
        self.assertEqual(config.data_dir, Path("/data"))

    def test_state_filename_comes_from_platform_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "platform.json"
            config_path.write_text(
                '{"schema_version": 1, "state_filename": "custom-counter.json"}', encoding="utf-8"
            )
            config = load_config(env={}, platform_config_path=config_path)
            self.assertEqual(config.state_filename, "custom-counter.json")


if __name__ == "__main__":
    unittest.main()
