"""Docker-free unit tests for scripts/security/debian_version.py's Debian
Policy §5.6.12 version-comparison algorithm."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "debian_version.py"
    spec = importlib.util.spec_from_file_location("debian_version_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompareDebianVersionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_equal_versions(self) -> None:
        self.assertEqual(self.module.compare_debian_versions("3.5.7-1~deb13u2", "3.5.7-1~deb13u2"), 0)

    def test_this_projects_real_vulnerable_vs_patched_pair(self) -> None:
        """The exact pair security/runtime-patches.lock records:
        LIBSSL_VULNERABLE_VERSION < LIBSSL_VERSION."""
        self.assertTrue(self.module.is_older("3.5.6-1~deb13u2", "3.5.7-1~deb13u2"))
        self.assertFalse(self.module.is_older("3.5.7-1~deb13u2", "3.5.6-1~deb13u2"))
        self.assertEqual(self.module.compare_debian_versions("3.5.6-1~deb13u2", "3.5.7-1~deb13u2"), -1)
        self.assertEqual(self.module.compare_debian_versions("3.5.7-1~deb13u2", "3.5.6-1~deb13u2"), 1)

    def test_tilde_sorts_before_everything_debian_policy_example(self) -> None:
        """Debian Policy Manual §5.6.12's own canonical ordering example:
        '~~' < '~~a' < '~' < the empty part < 'a'."""
        ordered = ["1~~", "1~~a", "1~", "1", "1a"]
        for i in range(len(ordered) - 1):
            with self.subTest(pair=(ordered[i], ordered[i + 1])):
                self.assertEqual(self.module.compare_debian_versions(ordered[i], ordered[i + 1]), -1)
                self.assertEqual(self.module.compare_debian_versions(ordered[i + 1], ordered[i]), 1)

    def test_debian_revision_bump_alone(self) -> None:
        self.assertTrue(self.module.is_older("1.0-1", "1.0-2"))

    def test_upstream_version_bump_outweighs_revision(self) -> None:
        self.assertTrue(self.module.is_older("1.0-99", "1.1-1"))

    def test_epoch_dominates_everything_else(self) -> None:
        self.assertTrue(self.module.is_older("1:0.1", "2:0.0"))
        self.assertFalse(self.module.is_older("2:0.1", "1:99.0"))

    def test_missing_revision_defaults_to_zero(self) -> None:
        # "1.0" has an implicit debian_revision of "0", so it's older than "1.0-1".
        self.assertTrue(self.module.is_older("1.0", "1.0-1"))

    def test_numeric_run_ignores_leading_zeros(self) -> None:
        self.assertEqual(self.module.compare_debian_versions("1.007", "1.7"), 0)

    def test_longer_digit_run_is_greater(self) -> None:
        self.assertTrue(self.module.is_older("1.9", "1.10"))

    def test_alpha_suffix_orders_before_tilde_free_numeric(self) -> None:
        # "1.0~rc1" (a pre-release marker) sorts before the final "1.0".
        self.assertTrue(self.module.is_older("1.0~rc1", "1.0"))

    def test_reflexive_and_antisymmetric(self) -> None:
        pairs = [("3.5.6-1~deb13u2", "3.5.7-1~deb13u2"), ("1.0", "1.0-1"), ("1:1.0", "2:0.0")]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertEqual(self.module.compare_debian_versions(a, a), 0)
                self.assertEqual(
                    self.module.compare_debian_versions(a, b), -self.module.compare_debian_versions(b, a)
                )

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(self.module.DebianVersionError):
            self.module.compare_debian_versions("", "1.0")

    def test_non_numeric_epoch_raises(self) -> None:
        with self.assertRaises(self.module.DebianVersionError):
            self.module.compare_debian_versions("x:1.0", "1.0")

    def test_upstream_not_starting_with_digit_raises(self) -> None:
        with self.assertRaises(self.module.DebianVersionError):
            self.module.compare_debian_versions("abc", "1.0")


if __name__ == "__main__":
    unittest.main()
