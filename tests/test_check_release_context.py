"""Docker-free, git-free unit tests for
scripts/release/check_release_context.py's pure release-context validation
logic (Day 6).

Every test here exercises the pure validation functions directly - the one
function that genuinely shells out to `git` (`default_git_is_ancestor`) is
never invoked; `validate_main_history`/`build_tag_context` always take an
injected fake `is_ancestor` callable instead, matching this project's
existing `sc`-injection pattern in `tests/test_reliability_check.py`/
`tests/test_compose_integration.py`. The real end-to-end proof (that a real
tag's commit really is/isn't in `main`'s real history) is
`.github/workflows/release.yml` running for real, not `unittest`.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_release_context() -> ModuleType:
    path = REPO_ROOT / "scripts" / "release" / "check_release_context.py"
    spec = importlib.util.spec_from_file_location("check_release_context_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # The module defines a frozen @dataclass - CPython's dataclasses
    # implementation looks the defining module up in sys.modules (for
    # forward-reference type resolution), which importlib.util.module_from_spec
    # alone does not register. Registering it before exec_module mirrors
    # what a normal `import`/`python3 scripts/...` invocation does for free.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ValidateVersionFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_valid_semver_is_accepted(self) -> None:
        self.module.validate_version_format("0.6.0")  # must not raise

    def test_missing_patch_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_version_format("0.6")

    def test_prerelease_suffix_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_version_format("0.6.0-rc1")

    def test_v_prefix_is_rejected(self) -> None:
        """VERSION itself must never carry the tag's 'v' prefix."""
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_version_format("v0.6.0")

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_version_format("")

    def test_non_numeric_component_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_version_format("0.x.0")


class ValidateTagFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_valid_tag_is_accepted(self) -> None:
        self.module.validate_tag_format("v0.6.0")  # must not raise

    def test_missing_v_prefix_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_tag_format("0.6.0")

    def test_missing_patch_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_tag_format("v0.6")

    def test_prerelease_suffix_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_tag_format("v0.6.0-beta")

    def test_uppercase_v_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_tag_format("V0.6.0")


class TagMatchesVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_matching_pair_is_accepted(self) -> None:
        self.module.tag_matches_version("v0.6.0", "0.6.0")  # must not raise

    def test_version_mismatch_is_rejected(self) -> None:
        """VERSION=0.6.0, tag=v0.5.0 -> FAIL, per the Day 6 spec's own example."""
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.tag_matches_version("v0.5.0", "0.6.0")

    def test_extra_whitespace_is_tolerated(self) -> None:
        self.module.tag_matches_version(" v0.6.0 ", " 0.6.0 ")  # must not raise


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_existing_notes_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            (repo_root / "docs" / "releases").mkdir(parents=True)
            (repo_root / "docs" / "releases" / "v0.6.0.md").write_text("notes", encoding="utf-8")
            path = self.module.validate_release_notes_exist("v0.6.0", repo_root)
            self.assertTrue(path.is_file())

    def test_missing_notes_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.validate_release_notes_exist("v0.6.0", repo_root)

    def test_real_shipped_v0_6_0_notes_exist(self) -> None:
        """Cross-check against this repository's own real release-notes
        file for the version this Day 6 branch actually ships."""
        path = self.module.validate_release_notes_exist("v0.6.0", REPO_ROOT)
        self.assertTrue(path.is_file())


class ValidateMainHistoryTests(unittest.TestCase):
    """Exercises validate_main_history purely against an injected fake
    is_ancestor callable - never real git."""

    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_ancestor_commit_is_accepted(self) -> None:
        self.module.validate_main_history("abc123", "origin/main", is_ancestor=lambda c, r: True)

    def test_non_ancestor_commit_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_main_history("abc123", "origin/main", is_ancestor=lambda c, r: False)

    def test_empty_commit_is_rejected_without_calling_is_ancestor(self) -> None:
        calls: list[tuple[str, str]] = []

        def spy(commit: str, ref: str) -> bool:
            calls.append((commit, ref))
            return True

        with self.assertRaises(self.module.ReleaseContextError):
            self.module.validate_main_history("", "origin/main", is_ancestor=spy)
        self.assertEqual(calls, [])

    def test_is_ancestor_receives_the_exact_commit_and_ref(self) -> None:
        received = {}

        def spy(commit: str, ref: str) -> bool:
            received["commit"] = commit
            received["ref"] = ref
            return True

        self.module.validate_main_history("deadbeef", "origin/main", is_ancestor=spy)
        self.assertEqual(received, {"commit": "deadbeef", "ref": "origin/main"})


class BuildDryRunContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def test_derives_proposed_tag_from_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            (repo_root / "docs" / "releases").mkdir(parents=True)
            (repo_root / "docs" / "releases" / "v0.6.0.md").write_text("notes", encoding="utf-8")
            ctx = self.module.build_dry_run_context("0.6.0", repo_root=repo_root)
            self.assertEqual(ctx.mode, "dry-run")
            self.assertEqual(ctx.tag, "v0.6.0")
            self.assertEqual(ctx.version, "0.6.0")

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaises(self.module.ReleaseContextError):
            self.module.build_dry_run_context("not-a-version")

    def test_missing_release_notes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.build_dry_run_context("0.6.0", repo_root=Path(tmp_dir))

    def test_real_repository_dry_run_context_succeeds(self) -> None:
        """Cross-checks against this repository's own real VERSION/notes."""
        version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        ctx = self.module.build_dry_run_context(version, repo_root=REPO_ROOT)
        self.assertEqual(ctx.tag, f"v{version}")


class BuildTagContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_release_context()

    def _repo_with_notes(self, tmp_dir: str, tag: str) -> Path:
        repo_root = Path(tmp_dir)
        (repo_root / "docs" / "releases").mkdir(parents=True)
        (repo_root / "docs" / "releases" / f"{tag}.md").write_text("notes", encoding="utf-8")
        return repo_root

    def test_full_valid_tag_context_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = self._repo_with_notes(tmp_dir, "v0.6.0")
            ctx = self.module.build_tag_context(
                "0.6.0", "v0.6.0", "deadbeef", is_ancestor=lambda c, r: True, repo_root=repo_root
            )
            self.assertEqual(ctx.mode, "tag")
            self.assertIn("main_history", ctx.checks)

    def test_version_tag_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = self._repo_with_notes(tmp_dir, "v0.5.0")
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.build_tag_context(
                    "0.6.0", "v0.5.0", "deadbeef", is_ancestor=lambda c, r: True, repo_root=repo_root
                )

    def test_invalid_semver_tag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.build_tag_context(
                    "0.6.0", "release-0.6.0", "deadbeef", is_ancestor=lambda c, r: True, repo_root=repo_root
                )

    def test_commit_not_in_main_history_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = self._repo_with_notes(tmp_dir, "v0.6.0")
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.build_tag_context(
                    "0.6.0", "v0.6.0", "deadbeef", is_ancestor=lambda c, r: False, repo_root=repo_root
                )

    def test_missing_release_notes_is_rejected_even_with_valid_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            with self.assertRaises(self.module.ReleaseContextError):
                self.module.build_tag_context(
                    "0.6.0", "v0.6.0", "deadbeef", is_ancestor=lambda c, r: True, repo_root=repo_root
                )


if __name__ == "__main__":
    unittest.main()
