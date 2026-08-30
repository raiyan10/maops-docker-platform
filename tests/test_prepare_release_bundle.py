"""Proves scripts/release/prepare_release_bundle.py's real CONSUMER
behavior (Day 7, closes DAY6-POST-M1) - not merely that its functions
return the right Python values, but that the actual, unmodified

    sha256sum -c SHA256SUMS

command a release consumer is told to run genuinely succeeds against a
flat staged bundle, and genuinely fails (with no special-casing) against
a missing, renamed, or tampered asset, and that a hand-tampered manifest
referencing a path-traversal/nested-CI-path entry is rejected before
`sha256sum` is even invoked. Uses the REAL `sha256sum` binary via
subprocess (present on every Linux dev machine and GitHub-hosted
runner this project targets) - never a Python-side hash
reimplementation standing in for that external-tool proof."""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "release" / "prepare_release_bundle.py"
    spec = importlib.util.spec_from_file_location("prepare_release_bundle_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseBundleConsumerVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self._tmp = tempfile.TemporaryDirectory(prefix="maops-release-bundle-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def _make_source_tree(self) -> list[Path]:
        """Mirrors the real nested CI/local layout
        (<root>/sbom/maops-docker-platform-9.9.9.spdx.json,
        <root>/security/trivy-9.9.9.json) this script stages from."""
        source_root = self.tmp_path / "source"
        (source_root / "sbom").mkdir(parents=True)
        (source_root / "security").mkdir(parents=True)
        sbom = source_root / "sbom" / "maops-docker-platform-9.9.9.spdx.json"
        trivy = source_root / "security" / "trivy-9.9.9.json"
        sbom.write_text('{"spdxVersion": "SPDX-2.3", "name": "fake-sbom"}', encoding="utf-8")
        trivy.write_text('{"SchemaVersion": 2, "Results": []}', encoding="utf-8")
        return [sbom, trivy]

    # --- the golden path: a real flat bundle genuinely passes sha256sum -c ---

    def test_staged_bundle_passes_real_sha256sum_dash_c(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        stdout = self.module.verify_release_bundle(staging_dir)

        self.assertEqual(sorted(basenames), ["maops-docker-platform-9.9.9.spdx.json", "trivy-9.9.9.json"])
        self.assertIn("maops-docker-platform-9.9.9.spdx.json", stdout)
        self.assertIn("trivy-9.9.9.json", stdout)
        # No nested/internal-CI path anywhere in what was verified.
        self.assertNotIn("source/sbom", stdout)
        self.assertNotIn("release-evidence", stdout)

    def test_bundle_is_genuinely_flat_no_subdirectories(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        self.module.stage_release_bundle(sources, staging_dir)
        entries = list(staging_dir.iterdir())
        self.assertTrue(all(entry.is_file() for entry in entries))

    # --- missing asset must fail ---

    def test_missing_source_asset_fails_to_even_stage(self) -> None:
        sbom, trivy = self._make_source_tree()
        trivy.unlink()
        staging_dir = self.tmp_path / "bundle"
        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.stage_release_bundle([sbom, trivy], staging_dir)
        self.assertIn("missing", str(ctx.exception))

    def test_asset_deleted_after_manifest_written_fails_verification(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        (staging_dir / "trivy-9.9.9.json").unlink()

        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("sha256sum -c", str(ctx.exception))

    # --- renamed/mismatched asset must fail ---

    def test_renamed_asset_fails_verification(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        shutil.move(staging_dir / "trivy-9.9.9.json", staging_dir / "trivy-9.9.9-renamed.json")

        with self.assertRaises(self.module.ReleaseBundleError):
            self.module.verify_release_bundle(staging_dir)

    # --- modified/tampered asset must fail ---

    def test_tampered_asset_content_fails_verification(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        (staging_dir / "maops-docker-platform-9.9.9.spdx.json").write_text(
            '{"spdxVersion": "SPDX-2.3", "name": "TAMPERED"}', encoding="utf-8"
        )

        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("sha256sum -c", str(ctx.exception))

    # --- duplicate manifest names must be rejected ---

    def test_duplicate_basename_from_different_sources_is_rejected(self) -> None:
        source_root = self.tmp_path / "dupsource"
        (source_root / "a").mkdir(parents=True)
        (source_root / "b").mkdir(parents=True)
        first = source_root / "a" / "same-name.json"
        second = source_root / "b" / "same-name.json"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")

        staging_dir = self.tmp_path / "bundle"
        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.stage_release_bundle([first, second], staging_dir)
        self.assertIn("duplicate", str(ctx.exception))

    # --- path traversal / nested internal CI path leakage must be rejected ---

    def test_hand_tampered_manifest_with_traversal_entry_is_rejected(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        # Simulate a hand-tampered/malicious manifest referencing a path
        # outside the flat bundle directory.
        manifest_path = staging_dir / "SHA256SUMS"
        digest = "a" * 64
        manifest_path.write_text(f"{digest}  ../../etc/passwd\n", encoding="utf-8")

        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("path separator", str(ctx.exception))

    def test_hand_tampered_manifest_with_nested_ci_path_is_rejected(self) -> None:
        """The exact real-world regression this closes: a manifest entry
        shaped like the old CI-workspace-relative form
        (`release-evidence/sbom/...`) must be rejected outright, not
        merely "fail to match" - it should never reach `sha256sum` at
        all once this validator runs."""
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        basenames = self.module.stage_release_bundle(sources, staging_dir)
        self.module.write_sha256sums(staging_dir, basenames)

        digest = "b" * 64
        manifest_path = staging_dir / "SHA256SUMS"
        manifest_path.write_text(
            f"{digest}  release-evidence/sbom/maops-docker-platform-9.9.9.spdx.json\n", encoding="utf-8"
        )

        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("path separator", str(ctx.exception))

    def test_stage_rejects_a_source_whose_basename_is_a_traversal_token(self) -> None:
        # Can't literally name a file ".." on a real filesystem, so prove
        # the validator itself rejects it directly.
        with self.assertRaises(self.module.ReleaseBundleError):
            self.module._validate_asset_basename("..")

    def test_malformed_manifest_line_is_rejected(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        self.module.stage_release_bundle(sources, staging_dir)
        (staging_dir / "SHA256SUMS").write_text("not a valid sha256sums line\n", encoding="utf-8")

        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("not a well-formed", str(ctx.exception))

    # --- missing SHA256SUMS entirely ---

    def test_missing_manifest_fails(self) -> None:
        sources = self._make_source_tree()
        staging_dir = self.tmp_path / "bundle"
        self.module.stage_release_bundle(sources, staging_dir)
        with self.assertRaises(self.module.ReleaseBundleError) as ctx:
            self.module.verify_release_bundle(staging_dir)
        self.assertIn("SHA256SUMS not found", str(ctx.exception))

    # --- real project naming convention end-to-end ---

    def test_real_asset_source_naming_matches_generate_sbom_and_vuln_scan(self) -> None:
        sources = self.module.real_release_asset_sources(Path("/tmp/x"), "1.0.0")
        names = [p.name for p in sources]
        self.assertIn("maops-docker-platform-1.0.0.spdx.json", names)
        self.assertIn("trivy-1.0.0.json", names)


if __name__ == "__main__":
    unittest.main()
