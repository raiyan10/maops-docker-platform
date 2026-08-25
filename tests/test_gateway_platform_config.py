import tempfile
import unittest
from pathlib import Path

from gateway.platform_config import (
    DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
    DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SAFETY_MARGIN_SECONDS,
    load_platform_config,
)


class LoadPlatformConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self) -> None:
        config = load_platform_config(path=Path("/nonexistent/platform.json"))
        self.assertEqual(config.gateway_upstream_timeout_seconds, DEFAULT_GATEWAY_UPSTREAM_TIMEOUT_SECONDS)
        self.assertEqual(config.state_dependency_timeout_seconds, DEFAULT_STATE_DEPENDENCY_TIMEOUT_SECONDS)
        self.assertEqual(config.timeout_safety_margin_seconds, DEFAULT_TIMEOUT_SAFETY_MARGIN_SECONDS)

    def test_valid_config_overrides_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "gateway_upstream_timeout_seconds": 6.0, '
                '"state_dependency_timeout_seconds": 2.0, "timeout_safety_margin_seconds": 1.0}',
                encoding="utf-8",
            )
            config = load_platform_config(path=path)
            self.assertEqual(config.gateway_upstream_timeout_seconds, 6.0)

    def test_zero_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": 0}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_negative_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": -3}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_above_max_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": 999}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_boolean_timeout_is_rejected(self) -> None:
        """bool is a subclass of int in Python - must be explicitly rejected."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_string_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": "6"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_nan_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": NaN}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_infinity_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": Infinity}', encoding="utf-8")
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
                '{"schema_version": true, "gateway_upstream_timeout_seconds": 6}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_schema_version_false_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": false, "gateway_upstream_timeout_seconds": 6}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_path_override_via_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text('{"schema_version": 1, "gateway_upstream_timeout_seconds": 7}', encoding="utf-8")
            config = load_platform_config(env={"PLATFORM_CONFIG_PATH": str(path)})
            self.assertEqual(config.gateway_upstream_timeout_seconds, 7.0)


class StateDependencyTimeoutFieldTests(unittest.TestCase):
    """gateway/platform_config.py reads state_dependency_timeout_seconds too
    (not operationally - app's own hop uses it - but to check the Day 5
    cross-hop timeout hierarchy invariant below against the whole shared
    config file)."""

    def test_zero_state_dependency_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "gateway_upstream_timeout_seconds": 6, '
                '"state_dependency_timeout_seconds": 0}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_boolean_state_dependency_timeout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "gateway_upstream_timeout_seconds": 6, '
                '"state_dependency_timeout_seconds": true}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)


class TimeoutSafetyMarginFieldTests(unittest.TestCase):
    def test_zero_margin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "timeout_safety_margin_seconds": 0}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_negative_margin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "timeout_safety_margin_seconds": -1}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_boolean_margin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "timeout_safety_margin_seconds": false}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_above_max_margin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "timeout_safety_margin_seconds": 999}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_valid_margin_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "platform.json"
            path.write_text(
                '{"schema_version": 1, "gateway_upstream_timeout_seconds": 10, '
                '"state_dependency_timeout_seconds": 2, "timeout_safety_margin_seconds": 2.5}',
                encoding="utf-8",
            )
            config = load_platform_config(path=path)
            self.assertEqual(config.timeout_safety_margin_seconds, 2.5)


class TimeoutHierarchyInvariantTests(unittest.TestCase):
    """Closes Day 3 finding A-6 (cross-hop timeout stacking): the outer
    gateway_upstream_timeout_seconds must genuinely exceed
    state_dependency_timeout_seconds + timeout_safety_margin_seconds, or the
    config fails to load - see gateway/platform_config.py's own docstring."""

    def _write(self, tmp_dir: str, outer: float, inner: float, margin: float) -> Path:
        path = Path(tmp_dir) / "platform.json"
        path.write_text(
            "{"
            '"schema_version": 1, '
            f'"gateway_upstream_timeout_seconds": {outer}, '
            f'"state_dependency_timeout_seconds": {inner}, '
            f'"timeout_safety_margin_seconds": {margin}'
            "}",
            encoding="utf-8",
        )
        return path

    def test_defaults_satisfy_the_invariant(self) -> None:
        """5.0 > 2.0 + 1.0 - the shipped config/platform.json values."""
        config = load_platform_config(path=Path("/nonexistent/platform.json"))
        self.assertGreater(
            config.gateway_upstream_timeout_seconds,
            config.state_dependency_timeout_seconds + config.timeout_safety_margin_seconds,
        )

    def test_outer_greater_than_inner_plus_margin_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, outer=10.0, inner=2.0, margin=1.0)
            config = load_platform_config(path=path)
            self.assertEqual(config.gateway_upstream_timeout_seconds, 10.0)

    def test_outer_equal_to_inner_plus_margin_is_rejected(self) -> None:
        """The invariant is strict (>), not (>=)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, outer=3.0, inner=2.0, margin=1.0)
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_outer_less_than_inner_plus_margin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, outer=2.0, inner=2.0, margin=1.0)
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_outer_less_than_inner_alone_is_rejected(self) -> None:
        """Even without a margin, the outer hop must never be tighter than
        the inner one it wraps."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, outer=1.0, inner=2.0, margin=0.1)
            with self.assertRaises(ValueError):
                load_platform_config(path=path)

    def test_violation_error_message_names_the_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, outer=2.0, inner=2.0, margin=1.0)
            with self.assertRaises(ValueError) as ctx:
                load_platform_config(path=path)
            message = str(ctx.exception)
            self.assertIn("gateway_upstream_timeout_seconds", message)
            self.assertIn("state_dependency_timeout_seconds", message)
            self.assertIn("timeout_safety_margin_seconds", message)


if __name__ == "__main__":
    unittest.main()
