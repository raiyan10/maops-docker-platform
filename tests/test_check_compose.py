"""Docker-free unit tests for scripts/compose/check_compose.py's Day 5
structural/parsing logic (Day 6, closes Day 5 finding M-C,
day-05-test-adversarial-review.md).

Prior to this file, `check_compose.py` had never had persisted unit-test
coverage for any day - it was only ever exercised against the real,
rendered `compose.yaml` via `make compose-check`. That still passes and is
the authoritative structural gate; these tests exist so a regression in the
newest, most parsing-heavy Day 5 logic (`_parse_cpus`, `_parse_bytes`,
`_parse_duration_seconds`, `check_resource_limits`, `check_restart_policy`,
`check_stop_grace_period`) is caught in milliseconds by `make test`, not
only by re-running `make compose-check` against the one shipped good
config. Adversarial cases below are deliberately not limited to the
shipped-good compose.yaml - each covers a class of bad input the day-05
resource-restart review manually, ad hoc verified was correctly rejected
(19 cases, not persisted anywhere until now) plus the specific gaps that
review and the test-adversarial review flagged (a lower-bound floor on
resource limits, and the Go-duration-string/malformed-retry-count parsing
paths).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_compose() -> ModuleType:
    path = REPO_ROOT / "scripts" / "compose" / "check_compose.py"
    spec = importlib.util.spec_from_file_location("check_compose_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(services: dict) -> dict:
    return {"services": services}


class ParseCpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def test_float_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_cpus(0.5), 0.5)

    def test_int_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_cpus(1), 1.0)

    def test_numeric_string_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_cpus("0.5"), 0.5)

    def test_none_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_cpus(None))

    def test_missing_key_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_cpus({}.get("cpus")))

    def test_bool_true_is_rejected(self) -> None:
        """bool is an int subclass in Python - True must not silently parse as 1.0 cpus."""
        self.assertIsNone(self.module._parse_cpus(True))

    def test_bool_false_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_cpus(False))

    def test_non_numeric_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_cpus("not-a-number"))

    def test_empty_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_cpus(""))


class ParseBytesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def test_int_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_bytes(134217728), 134217728)

    def test_numeric_string_is_accepted(self) -> None:
        """mem_limit renders as a numeric string in `docker compose config
        --format json` on this project's own Compose install."""
        self.assertEqual(self.module._parse_bytes("134217728"), 134217728)

    def test_negative_int_is_accepted_by_the_parser_itself(self) -> None:
        """The unlimited pids_limit sentinel (-1) must still PARSE cleanly -
        rejection of a negative/unlimited value is check_resource_limits()'s
        job (the <= 0 branch), not the parser's."""
        self.assertEqual(self.module._parse_bytes(-1), -1)

    def test_none_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_bytes(None))

    def test_bool_true_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_bytes(True))

    def test_bool_false_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_bytes(False))

    def test_non_numeric_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_bytes("unlimited"))


class ParseDurationSecondsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def test_seconds_string_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_duration_seconds("10s"), 10.0)

    def test_zero_seconds_string_is_accepted_by_the_parser_itself(self) -> None:
        """"0s" is a well-formed Go duration string - rejection of a zero
        grace period is check_stop_grace_period()'s job, not the parser's."""
        self.assertEqual(self.module._parse_duration_seconds("0s"), 0.0)

    def test_hours_minutes_seconds_string_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_duration_seconds("1h30m10s"), 3600 + 1800 + 10)

    def test_minutes_only_string_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_duration_seconds("2m"), 120.0)

    def test_fractional_seconds_string_is_accepted(self) -> None:
        self.assertEqual(self.module._parse_duration_seconds("1.5s"), 1.5)

    def test_nanosecond_integer_is_converted_to_seconds(self) -> None:
        """Above the 3600 disambiguation boundary: treated as nanoseconds."""
        self.assertEqual(self.module._parse_duration_seconds(10_000_000_000), 10.0)

    def test_small_integer_is_treated_as_whole_seconds(self) -> None:
        """At/below the 3600 disambiguation boundary: treated as seconds."""
        self.assertEqual(self.module._parse_duration_seconds(10), 10.0)

    def test_boundary_integer_at_3600_is_treated_as_whole_seconds(self) -> None:
        """Exactly at the boundary (not `> 3600`) - the inclusive edge."""
        self.assertEqual(self.module._parse_duration_seconds(3600), 3600.0)

    def test_boundary_integer_just_above_3600_is_treated_as_nanoseconds(self) -> None:
        self.assertEqual(self.module._parse_duration_seconds(3601), 3601 / 1_000_000_000)

    def test_empty_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds(""))

    def test_whitespace_only_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds("   "))

    def test_malformed_string_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds("not-a-duration"))

    def test_none_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds(None))

    def test_bool_true_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds(True))

    def test_bool_false_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds(False))

    def test_unit_suffix_missing_digits_is_rejected(self) -> None:
        self.assertIsNone(self.module._parse_duration_seconds("h"))


class CheckResourceLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def _good_service(self) -> dict:
        return {"cpus": 0.5, "mem_limit": 134217728, "pids_limit": 64}

    def test_good_config_passes(self) -> None:
        config = _config({"state": self._good_service()})
        self.assertEqual(self.module.check_resource_limits(config), [])

    def test_missing_cpus_fails(self) -> None:
        service = self._good_service()
        del service["cpus"]
        config = _config({"state": service})
        self.assertTrue(self.module.check_resource_limits(config))

    def test_none_cpus_fails(self) -> None:
        service = self._good_service()
        service["cpus"] = None
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_zero_cpus_fails(self) -> None:
        service = self._good_service()
        service["cpus"] = 0
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_negative_cpus_fails(self) -> None:
        service = self._good_service()
        service["cpus"] = -0.5
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_bool_cpus_fails(self) -> None:
        """bool is an int subclass - True must not silently pass as a valid cpus value."""
        service = self._good_service()
        service["cpus"] = True
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_cpus_above_target_fails(self) -> None:
        service = self._good_service()
        service["cpus"] = 2.0
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_cpus_below_target_fails(self) -> None:
        """Day 6 (closes Day 5 finding L-1, day-05-resource-restart-review.md):
        a valid-but-too-restrictive value must now be rejected too, not just
        an above-target one."""
        service = self._good_service()
        service["cpus"] = 0.1
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_cpus_within_float_tolerance_passes(self) -> None:
        service = self._good_service()
        service["cpus"] = 0.505
        self.assertEqual(self.module.check_resource_limits(_config({"state": service})), [])

    def test_missing_mem_limit_fails(self) -> None:
        service = self._good_service()
        del service["mem_limit"]
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_zero_mem_limit_fails(self) -> None:
        service = self._good_service()
        service["mem_limit"] = 0
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_negative_mem_limit_fails(self) -> None:
        service = self._good_service()
        service["mem_limit"] = -1
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_mem_limit_as_numeric_string_passes(self) -> None:
        service = self._good_service()
        service["mem_limit"] = "134217728"
        self.assertEqual(self.module.check_resource_limits(_config({"state": service})), [])

    def test_mem_limit_above_target_fails(self) -> None:
        service = self._good_service()
        service["mem_limit"] = 512 * 1024 * 1024
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_mem_limit_below_target_fails(self) -> None:
        """Day 6 L-1 closure: below-target is now also rejected."""
        service = self._good_service()
        service["mem_limit"] = 1
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_missing_pids_limit_fails(self) -> None:
        service = self._good_service()
        del service["pids_limit"]
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_zero_pids_limit_fails(self) -> None:
        service = self._good_service()
        service["pids_limit"] = 0
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_unlimited_sentinel_pids_limit_fails(self) -> None:
        """-1 is Docker's own "unlimited" sentinel for pids_limit - must be rejected."""
        service = self._good_service()
        service["pids_limit"] = -1
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_pids_limit_above_target_fails(self) -> None:
        service = self._good_service()
        service["pids_limit"] = 4096
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_pids_limit_below_target_fails(self) -> None:
        """Day 6 L-1 closure: an absurdly restrictive value (would almost
        certainly prevent the service from ever starting) is now rejected."""
        service = self._good_service()
        service["pids_limit"] = 1
        self.assertTrue(self.module.check_resource_limits(_config({"state": service})))

    def test_multiple_services_each_evaluated_independently(self) -> None:
        config = _config({"state": self._good_service(), "app": {"cpus": 0, "mem_limit": 0, "pids_limit": 0}})
        findings = self.module.check_resource_limits(config)
        self.assertTrue(findings)
        self.assertTrue(any("'app'" in str(f) for f in findings))
        self.assertFalse(any("'state'" in str(f) for f in findings))


class CheckRestartPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def test_on_failure_3_passes(self) -> None:
        config = _config({"state": {"restart": "on-failure:3"}})
        self.assertEqual(self.module.check_restart_policy(config), [])

    def test_missing_restart_fails(self) -> None:
        config = _config({"state": {}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_none_restart_fails(self) -> None:
        config = _config({"state": {"restart": None}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_always_policy_fails(self) -> None:
        config = _config({"state": {"restart": "always"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_unless_stopped_policy_fails(self) -> None:
        config = _config({"state": {"restart": "unless-stopped"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_no_policy_at_all_fails(self) -> None:
        config = _config({"state": {"restart": "no"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_on_failure_without_count_fails(self) -> None:
        """"on-failure" with no colon/count at all - a real Compose-accepted
        shorthand (unbounded retries) that must still be rejected here."""
        config = _config({"state": {"restart": "on-failure"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_on_failure_non_numeric_count_fails(self) -> None:
        config = _config({"state": {"restart": "on-failure:abc"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_on_failure_zero_count_fails(self) -> None:
        config = _config({"state": {"restart": "on-failure:0"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_on_failure_wrong_count_fails(self) -> None:
        config = _config({"state": {"restart": "on-failure:5"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_on_failure_negative_count_fails(self) -> None:
        config = _config({"state": {"restart": "on-failure:-1"}})
        self.assertTrue(self.module.check_restart_policy(config))

    def test_bool_restart_fails(self) -> None:
        config = _config({"state": {"restart": True}})
        self.assertTrue(self.module.check_restart_policy(config))


class CheckStopGracePeriodTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_compose()

    def test_10s_duration_string_passes(self) -> None:
        config = _config({"state": {"stop_grace_period": "10s"}})
        self.assertEqual(self.module.check_stop_grace_period(config), [])

    def test_10s_as_nanosecond_integer_passes(self) -> None:
        config = _config({"state": {"stop_grace_period": 10_000_000_000}})
        self.assertEqual(self.module.check_stop_grace_period(config), [])

    def test_missing_grace_period_fails(self) -> None:
        config = _config({"state": {}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_none_grace_period_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": None}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_zero_duration_string_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": "0s"}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_empty_duration_string_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": ""}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_zero_integer_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": 0}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_wrong_value_duration_string_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": "30s"}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_wrong_value_nanosecond_integer_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": 5_000_000_000}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_bool_grace_period_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": True}})
        self.assertTrue(self.module.check_stop_grace_period(config))

    def test_malformed_duration_string_fails(self) -> None:
        config = _config({"state": {"stop_grace_period": "ten seconds"}})
        self.assertTrue(self.module.check_stop_grace_period(config))


if __name__ == "__main__":
    unittest.main()
