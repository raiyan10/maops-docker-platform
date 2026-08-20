import tempfile
import unittest
from pathlib import Path

from gateway.config import (
    DEFAULT_GATEWAY_HOST,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_NAME,
    DEFAULT_UPSTREAM_HOST,
    DEFAULT_UPSTREAM_PORT,
    load_config,
    parse_port,
)

NO_PLATFORM_CONFIG = Path("/nonexistent/platform.json")


class ParsePortTests(unittest.TestCase):
    def test_valid_port(self) -> None:
        self.assertEqual(parse_port("8080", "GATEWAY_PORT"), 8080)

    def test_valid_port_boundaries(self) -> None:
        self.assertEqual(parse_port("1", "GATEWAY_PORT"), 1)
        self.assertEqual(parse_port("65535", "GATEWAY_PORT"), 65535)

    def test_whitespace_is_stripped(self) -> None:
        self.assertEqual(parse_port("  8080  ", "GATEWAY_PORT"), 8080)

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("", "GATEWAY_PORT")

    def test_malformed_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("not-a-port", "GATEWAY_PORT")

    def test_zero_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("0", "GATEWAY_PORT")

    def test_negative_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("-1", "GATEWAY_PORT")

    def test_above_max_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_port("65536", "GATEWAY_PORT")

    def test_error_message_names_the_variable(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_port("bogus", "UPSTREAM_PORT")
        self.assertIn("UPSTREAM_PORT", str(ctx.exception))


class LoadConfigTests(unittest.TestCase):
    def test_defaults_when_env_empty(self) -> None:
        config = load_config(env={}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.host, DEFAULT_GATEWAY_HOST)
        self.assertEqual(config.port, DEFAULT_GATEWAY_PORT)
        self.assertEqual(config.upstream_host, DEFAULT_UPSTREAM_HOST)
        self.assertEqual(config.upstream_port, DEFAULT_UPSTREAM_PORT)
        self.assertEqual(config.name, DEFAULT_NAME)
        self.assertEqual(config.upstream_timeout_seconds, 3.0)

    def test_gateway_host_override(self) -> None:
        config = load_config(env={"GATEWAY_HOST": "127.0.0.1"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.host, "127.0.0.1")

    def test_gateway_port_override(self) -> None:
        config = load_config(env={"GATEWAY_PORT": "9090"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.port, 9090)

    def test_upstream_host_override(self) -> None:
        config = load_config(env={"UPSTREAM_HOST": "custom-app"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.upstream_host, "custom-app")

    def test_upstream_port_override(self) -> None:
        config = load_config(env={"UPSTREAM_PORT": "9091"}, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(config.upstream_port, 9091)

    def test_invalid_gateway_port_propagates(self) -> None:
        with self.assertRaises(ValueError):
            load_config(env={"GATEWAY_PORT": "not-a-port"}, platform_config_path=NO_PLATFORM_CONFIG)

    def test_invalid_upstream_port_propagates(self) -> None:
        with self.assertRaises(ValueError):
            load_config(env={"UPSTREAM_PORT": "99999999"}, platform_config_path=NO_PLATFORM_CONFIG)

    def test_arbitrary_environment_is_not_consulted(self) -> None:
        env = {
            "GATEWAY_PORT": "8080",
            "SECRET_TOKEN": "should-never-be-read",
            "AWS_SECRET_ACCESS_KEY": "should-never-be-read",
            "PATH": "/usr/bin",
        }
        config = load_config(env=env, platform_config_path=NO_PLATFORM_CONFIG)
        self.assertEqual(
            vars(config).keys(),
            {"host", "port", "upstream_host", "upstream_port", "upstream_timeout_seconds", "name"},
        )

    def test_blank_hosts_fall_back_to_defaults(self) -> None:
        config = load_config(
            env={"GATEWAY_HOST": "   ", "UPSTREAM_HOST": ""}, platform_config_path=NO_PLATFORM_CONFIG
        )
        self.assertEqual(config.host, DEFAULT_GATEWAY_HOST)
        self.assertEqual(config.upstream_host, DEFAULT_UPSTREAM_HOST)

    def test_upstream_timeout_comes_from_platform_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "platform.json"
            config_path.write_text(
                '{"schema_version": 1, "dependency_timeout_seconds": 0.75}', encoding="utf-8"
            )
            config = load_config(env={}, platform_config_path=config_path)
            self.assertEqual(config.upstream_timeout_seconds, 0.75)

    def test_invalid_platform_config_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "platform.json"
            config_path.write_text('{"schema_version": 1, "dependency_timeout_seconds": -1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(env={}, platform_config_path=config_path)


if __name__ == "__main__":
    unittest.main()
