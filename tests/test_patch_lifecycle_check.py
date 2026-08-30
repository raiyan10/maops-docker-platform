"""Docker-free unit tests for scripts/security/patch_lifecycle_check.py's
pure `classify_patch_lifecycle` decision function - the real Docker
integration proof (that these classifications hold against the actual
pinned Distroless base image) is `make patch-lifecycle-check`'s job, not
unittest (see .claude/CLAUDE.md)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module(relative_path: str, name: str) -> ModuleType:
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClassifyPatchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module("scripts/security/patch_lifecycle_check.py", "patch_lifecycle_check_under_test")
        self.debian_version = load_module("scripts/security/debian_version.py", "debian_version_for_lifecycle_tests")

    def _classify(self, **overrides):
        defaults = dict(
            base_version="3.5.6-1~deb13u2",
            base_package_name="libssl3t64",
            patched_version="3.5.7-1~deb13u2",
            vulnerable_version_recorded="3.5.6-1~deb13u2",
            expected_package_name="libssl3t64",
        )
        defaults.update(overrides)
        return self.module.classify_patch_lifecycle(self.debian_version, **defaults)

    # --- A: overlay still required, lock metadata accurate -> PASS ---

    def test_case_a_overlay_still_required_passes(self) -> None:
        classification, passed, detail = self._classify()
        self.assertEqual(classification, self.module.CLASS_REQUIRED)
        self.assertTrue(passed)
        self.assertIn("remains required", detail)

    def test_case_a_matches_the_real_repository_lock_file_values(self) -> None:
        """The exact pair security/runtime-patches.lock currently ships."""
        classification, passed, _ = self._classify(
            base_version="3.5.6-1~deb13u2",
            vulnerable_version_recorded="3.5.6-1~deb13u2",
            patched_version="3.5.7-1~deb13u2",
        )
        self.assertEqual(classification, self.module.CLASS_REQUIRED)
        self.assertTrue(passed)

    # --- B: base caught up (or overtook) the overlay -> overlay redundant ---

    def test_case_b_base_equals_patched_version_is_redundant(self) -> None:
        classification, passed, detail = self._classify(
            base_version="3.5.7-1~deb13u2", vulnerable_version_recorded="3.5.6-1~deb13u2",
        )
        self.assertEqual(classification, self.module.CLASS_REDUNDANT)
        self.assertFalse(passed)
        self.assertIn("REDUNDANT", detail)

    def test_case_b_base_newer_than_patched_version_is_redundant(self) -> None:
        classification, passed, _ = self._classify(
            base_version="3.5.8-1~deb13u1", vulnerable_version_recorded="3.5.6-1~deb13u2",
        )
        self.assertEqual(classification, self.module.CLASS_REDUNDANT)
        self.assertFalse(passed)

    def test_case_b_redundancy_takes_precedence_over_metadata_drift(self) -> None:
        """Even if the recorded vulnerable version is ALSO stale, a base
        that has caught up is unambiguously redundant (B), not merely
        "drifted" (D) - B is checked first because it's the more urgent
        fact (never silently keep an overlay that could now be a
        downgrade)."""
        classification, passed, _ = self._classify(
            base_version="3.5.7-1~deb13u3",
            vulnerable_version_recorded="3.5.5-1~deb13u1",
            patched_version="3.5.7-1~deb13u2",
        )
        self.assertEqual(classification, self.module.CLASS_REDUNDANT)
        self.assertFalse(passed)

    # --- C: evidence could not be established -> fail clearly, never assume ---

    def test_case_c_missing_base_version_fails(self) -> None:
        classification, passed, detail = self._classify(base_version=None)
        self.assertEqual(classification, self.module.CLASS_INDETERMINATE)
        self.assertFalse(passed)
        self.assertIn("refusing to assume", detail)

    def test_case_c_unexpected_package_name_fails(self) -> None:
        classification, passed, detail = self._classify(base_package_name="some-other-package")
        self.assertEqual(classification, self.module.CLASS_INDETERMINATE)
        self.assertFalse(passed)
        self.assertIn("unexpected package name", detail)

    def test_case_c_malformed_base_version_fails(self) -> None:
        classification, passed, detail = self._classify(base_version="not-a-debian-version")
        self.assertEqual(classification, self.module.CLASS_INDETERMINATE)
        self.assertFalse(passed)
        self.assertIn("could not compare", detail)

    def test_case_c_none_package_name_does_not_itself_fail(self) -> None:
        """A missing Package: line (package_name=None) is tolerated on its
        own - only a genuine MISMATCH against the expected name is a
        finding; the Version: line is what actually matters for A/B/D."""
        classification, passed, _ = self._classify(base_package_name=None)
        self.assertEqual(classification, self.module.CLASS_REQUIRED)
        self.assertTrue(passed)

    # --- D: overlay still required, but lock's own recorded metadata has drifted ---

    def test_case_d_recorded_vulnerable_version_stale_fails(self) -> None:
        classification, passed, detail = self._classify(
            base_version="3.5.6-1~deb13u3",  # base bumped to a *different* still-vulnerable point release
            vulnerable_version_recorded="3.5.6-1~deb13u2",  # lock still says the old one
        )
        self.assertEqual(classification, self.module.CLASS_METADATA_DRIFT)
        self.assertFalse(passed)
        self.assertIn("drifted", detail)

    def test_case_d_still_distinguishable_from_case_a(self) -> None:
        exact_match_classification, exact_match_passed, _ = self._classify(
            base_version="3.5.6-1~deb13u2", vulnerable_version_recorded="3.5.6-1~deb13u2",
        )
        drifted_classification, drifted_passed, _ = self._classify(
            base_version="3.5.6-1~deb13u1", vulnerable_version_recorded="3.5.6-1~deb13u2",
        )
        self.assertNotEqual(exact_match_classification, drifted_classification)
        self.assertTrue(exact_match_passed)
        self.assertFalse(drifted_passed)

    # --- non-tautological: this is not "compare a constant to itself" ---

    def test_check_is_not_tautological_across_all_four_branches(self) -> None:
        """The same fixed patched_version/expected_package_name/
        vulnerable_version_recorded triple produces all four different,
        real outcomes purely as a function of the (independently
        observed) base_version/base_package_name - proving the result
        genuinely depends on the extracted evidence, not on a constant
        matching itself."""
        outcomes = set()
        outcomes.add(self._classify(base_version="3.5.6-1~deb13u2")[0])  # A
        outcomes.add(self._classify(base_version="3.5.7-1~deb13u2")[0])  # B
        outcomes.add(self._classify(base_version=None)[0])  # C
        outcomes.add(self._classify(base_version="3.5.6-1~deb13u9")[0])  # D
        self.assertEqual(
            outcomes,
            {
                self.module.CLASS_REQUIRED,
                self.module.CLASS_REDUNDANT,
                self.module.CLASS_INDETERMINATE,
                self.module.CLASS_METADATA_DRIFT,
            },
        )


class RealLockFileAgainstRealDockerfileTests(unittest.TestCase):
    """Docker-free, but exercises the two REAL source-of-truth files
    together (security/runtime-patches.lock and docker/app/Dockerfile via
    base_image_ref.py) - proves the values this project actually ships
    are internally self-consistent (the lock's own recorded
    LIBSSL_VULNERABLE_VERSION really is older than LIBSSL_VERSION) even
    without a live Docker daemon to cross-check the real base image
    against."""

    def setUp(self) -> None:
        self.runtime_patch_lock = load_module(
            "scripts/security/runtime_patch_lock.py", "runtime_patch_lock_for_lifecycle_tests"
        )
        self.base_image_ref = load_module(
            "scripts/security/base_image_ref.py", "base_image_ref_for_lifecycle_tests"
        )
        self.debian_version = load_module(
            "scripts/security/debian_version.py", "debian_version_for_lock_consistency_tests"
        )

    def test_lock_vulnerable_version_is_genuinely_older_than_patched_version(self) -> None:
        lock = self.runtime_patch_lock.load_runtime_patch_lock()
        self.assertTrue(
            self.debian_version.is_older(lock["LIBSSL_VULNERABLE_VERSION"], lock["LIBSSL_VERSION"]),
            "security/runtime-patches.lock's own LIBSSL_VULNERABLE_VERSION must be strictly older "
            "than LIBSSL_VERSION using real Debian version-comparison semantics, or the overlay's "
            "own documented rationale is internally incoherent",
        )

    def test_dockerfile_final_stage_base_is_digest_pinned_and_parseable(self) -> None:
        repo, digest = self.base_image_ref.get_final_stage_base_ref()
        self.assertTrue(repo.startswith("gcr.io/distroless/"))
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
