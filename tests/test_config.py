import tempfile
import unittest
from pathlib import Path

from app.config import (
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_STATE_HOST,
    DEFAULT_STATE_PORT,
    load_config,
    parse_port,
)

NO_PLATFORM_CONFIG = Path("/nonexistent/platform.json")


class ParsePortTests(unittest.TestCase):
    def test_valid_port(self) -> None:
        self.assertEqual(parse_port("8080"), 8080)

    def test_valid_port_boundaries(self) -> None:
        self.assertEqual(parse_port("1"), 1)
        self.assertEqual(parse_port("65535"), 65535)

    def test_whitespace_is_stripped(self) -> None:
        self.assertEqual(parse_port("  8080  "), 8080)
        self.assertEqual(parse_port("\t8080\n"), 8080)

    def test_whitespace_only_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("   ")

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("")

    def test_malformed_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("not-a-port")

    def test_float_like_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("80.5")

    def test_zero_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("0")

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("-1")

    def test_above_max_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("65536")

    def test_far_above_max_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("99999999")


class LoadConfigTests(unittest.TestCase):
    def test_defaults_when_env_empty(self) -> None:
        config = load_config(env={}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.host, DEFAULT_HOST)
        self.assertEqual(config.port, DEFAULT_PORT)
        self.assertEqual(config.name, DEFAULT_NAME)
        self.assertEqual(config.state_host, DEFAULT_STATE_HOST)
        self.assertEqual(config.state_port, DEFAULT_STATE_PORT)
        self.assertEqual(config.state_timeout_seconds, 3.0)

    def test_app_host_override(self) -> None:
        config = load_config(env={"APP_HOST": "127.0.0.1"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.host, "127.0.0.1")

    def test_app_name_override(self) -> None:
        config = load_config(env={"APP_NAME": "custom-name"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.name, "custom-name")

    def test_app_port_override(self) -> None:
        config = load_config(env={"APP_PORT": "9090"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.port, 9090)

    def test_invalid_app_port_propagates(self) -> None:
        with self.assertRaises(ValueError):
            load_config(env={"APP_PORT": "not-a-port"}, platform_config_path=NO_PLATFORM_CONFIG)

    def test_state_host_override(self) -> None:
        config = load_config(env={"STATE_HOST": "custom-state"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.state_host, "custom-state")

    def test_state_port_override(self) -> None:
        config = load_config(env={"STATE_PORT": "9091"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.state_port, 9091)

    def test_invalid_state_port_propagates(self) -> None:
        with self.assertRaises(ValueError):
            load_config(env={"STATE_PORT": "not-a-port"}, platform_config_path=NO_PLATFORM_CONFIG)

    def test_arbitrary_environment_is_not_consulted(self) -> None:
        env = {
            "APP_PORT": "8080",
            "SECRET_TOKEN": "should-never-be-read",
            "AWS_SECRET_ACCESS_KEY": "should-never-be-read",
            "PATH": "/usr/bin",
        }
        config = load_config(env=env, platform_config_path=NO_PLATFORM_CONFIG)
        # AppConfig only ever has these fields - there is no mechanism by
        # which unrelated environment variables could surface.
        self.assertEqual(
            vars(config).keys(),
            {"host", "port", "name", "state_host", "state_port", "state_timeout_seconds"},
        )

    def test_blank_host_and_name_fall_back_to_defaults(self) -> None:
        config = load_config(
            env={"APP_HOST": "   ", "APP_NAME": ""}, platform_config_path=NO_PLATFORM_CONFIG
        )
        self.assertEqual(config.host, DEFAULT_HOST)
        self.assertEqual(config.name, DEFAULT_NAME)

    def test_blank_state_host_falls_back_to_default(self) -> None:
        config = load_config(env={"STATE_HOST": "   "}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.state_host, DEFAULT_STATE_HOST)

    def test_state_timeout_comes_from_platform_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "platform.json"
            config_path.write_text(
                '{"schema_version": 1, "dependency_timeout_seconds": 1.5}', encoding="utf-8"
            )
            config = load_config(env={}, platform_config_path=config_path)
            self.assertEqual(config.state_timeout_seconds, 1.5)

    def test_invalid_platform_config_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "platform.json"
            config_path.write_text(
                '{"schema_version": 1, "dependency_timeout_seconds": -1}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_config(env={}, platform_config_path=config_path)


if __name__ == "__main__":
    unittest.main()
