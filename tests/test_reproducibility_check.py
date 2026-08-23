"""Docker-free unit tests for scripts/build/reproducibility_check.py's pure
logic: SOURCE_DATE_EPOCH derivation (never current-clock) and the
normalized filesystem-manifest algorithm (path/type/mode/uid/gid/
symlink-target/content-hash, deliberately excluding mtime - the one
property BuildKit's rewrite-timestamp normalizes away between two builds).

The manifest algorithm itself is exercised by running the exact same
embedded Python source (`_MANIFEST_SCRIPT`) the real script execs inside a
container - here executed locally as a subprocess against a disposable
temp directory (via MAOPS_MANIFEST_ROOT), so this is a real test of the
production code path, not a reimplementation of it. No Docker/build
involved - real build-to-build comparison is scripts/build/
reproducibility_check.py's own integration-level job (make
reproducibility-check), not unittest's (see .claude/CLAUDE.md)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_reproducibility_check() -> ModuleType:
    path = REPO_ROOT / "scripts" / "build" / "reproducibility_check.py"
    spec = importlib.util.spec_from_file_location("reproducibility_check_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ComputeSourceDateEpochTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reproducibility_check()

    def test_uses_git_commit_timestamp_when_available(self) -> None:
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="1700000000\n", stderr="")
        with patch.object(self.module, "run", return_value=fake_result):
            epoch = self.module.compute_source_date_epoch()
        self.assertEqual(epoch, 1700000000)

    def test_falls_back_to_fixed_zero_when_git_unavailable(self) -> None:
        """Never falls back to time.time() - a fixed sentinel only."""
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not a git repo")
        with patch.object(self.module, "run", return_value=fake_result):
            epoch = self.module.compute_source_date_epoch()
        self.assertEqual(epoch, 0)

    def test_never_calls_the_wall_clock(self) -> None:
        with patch("time.time") as mock_time:
            fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="42\n", stderr="")
            with patch.object(self.module, "run", return_value=fake_result):
                self.module.compute_source_date_epoch()
            mock_time.assert_not_called()


def run_manifest_script(root: Path) -> list[dict]:
    """Executes the exact production _MANIFEST_SCRIPT locally against a
    disposable directory, via the same MAOPS_MANIFEST_ROOT override the
    production script's own env-var default supports. Paths are
    normalized relative to `root` so two different temp directories
    (inevitable across two separate `tempfile.TemporaryDirectory()` calls
    in a test, unlike the real container's always-identical `/app`) don't
    themselves look like a content difference."""
    module = load_reproducibility_check()
    env = dict(os.environ)
    env["MAOPS_MANIFEST_ROOT"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", module._MANIFEST_SCRIPT], capture_output=True, text=True, timeout=30, env=env
    )
    assert result.returncode == 0, result.stderr
    entries = json.loads(result.stdout)
    for entry in entries:
        entry["path"] = str(Path(entry["path"]).relative_to(root))
    return entries


class ManifestScriptTests(unittest.TestCase):
    def test_manifest_excludes_mtime_identical_content_different_mtime_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            (Path(tmp_a) / "file.txt").write_text("hello", encoding="utf-8")
            (Path(tmp_b) / "file.txt").write_text("hello", encoding="utf-8")
            # Force a different mtime on B - the manifest must still match A.
            os.utime(Path(tmp_b) / "file.txt", (1000000000, 1000000000))

            manifest_a = run_manifest_script(Path(tmp_a))
            manifest_b = run_manifest_script(Path(tmp_b))
            self.assertEqual(manifest_a, manifest_b)

    def test_manifest_detects_differing_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            (Path(tmp_a) / "file.txt").write_text("hello", encoding="utf-8")
            (Path(tmp_b) / "file.txt").write_text("goodbye", encoding="utf-8")

            manifest_a = run_manifest_script(Path(tmp_a))
            manifest_b = run_manifest_script(Path(tmp_b))
            self.assertNotEqual(manifest_a, manifest_b)

    def test_manifest_detects_differing_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            path_a = Path(tmp_a) / "file.txt"
            path_b = Path(tmp_b) / "file.txt"
            path_a.write_text("hello", encoding="utf-8")
            path_b.write_text("hello", encoding="utf-8")
            path_a.chmod(0o644)
            path_b.chmod(0o600)

            manifest_a = run_manifest_script(Path(tmp_a))
            manifest_b = run_manifest_script(Path(tmp_b))
            self.assertNotEqual(manifest_a, manifest_b)

    def test_manifest_records_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "real.txt").write_text("hello", encoding="utf-8")
            (root / "link.txt").symlink_to("real.txt")

            manifest = run_manifest_script(root)
            symlink_entries = [e for e in manifest if e["type"] == "symlink"]
            self.assertEqual(len(symlink_entries), 1)
            self.assertEqual(symlink_entries[0]["target"], "real.txt")

    def test_manifest_is_sorted_and_deterministic_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")

            manifest_1 = run_manifest_script(root)
            manifest_2 = run_manifest_script(root)
            self.assertEqual(manifest_1, manifest_2)
            self.assertEqual([e["path"] for e in manifest_1], sorted(e["path"] for e in manifest_1))


if __name__ == "__main__":
    unittest.main()
