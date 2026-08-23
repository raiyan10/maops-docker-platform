"""Docker-free unit tests for scripts/lint/check_source.py's two rule sets
(Day 4: workload vs. tooling) using synthetic throwaway files - no tracked
source file is ever touched."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_source() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_source.py"
    spec = importlib.util.spec_from_file_location("check_source_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkloadVsToolingRuleSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_source()

    def _write_and_check(self, source: str, forbidden_modules: set[str]) -> list:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "probe.py"
            path.write_text(source, encoding="utf-8")
            return self.module.check_file(path, forbidden_modules)

    def test_subprocess_import_forbidden_for_workload(self) -> None:
        findings = self._write_and_check("import subprocess\n", self.module.WORKLOAD_FORBIDDEN_MODULES)
        self.assertTrue(any("subprocess" in str(f) for f in findings))

    def test_subprocess_import_allowed_for_tooling(self) -> None:
        findings = self._write_and_check("import subprocess\n", self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertEqual(findings, [])

    def test_pickle_import_still_forbidden_for_tooling(self) -> None:
        findings = self._write_and_check("import pickle\n", self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertTrue(any("pickle" in str(f) for f in findings))

    def test_ctypes_import_still_forbidden_for_tooling(self) -> None:
        findings = self._write_and_check("import ctypes\n", self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertTrue(any("ctypes" in str(f) for f in findings))

    def test_eval_forbidden_for_tooling_despite_subprocess_being_allowed(self) -> None:
        findings = self._write_and_check("eval('1+1')\n", self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertTrue(any("eval" in str(f) for f in findings))

    def test_os_system_forbidden_for_tooling_even_with_subprocess_present(self) -> None:
        source = "import subprocess\nimport os\nos.system('echo hi')\n"
        findings = self._write_and_check(source, self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertTrue(any("os.system" in str(f) for f in findings))

    def test_shell_true_forbidden_for_tooling(self) -> None:
        source = "import subprocess\nsubprocess.run(['ls'], shell=True)\n"
        findings = self._write_and_check(source, self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertTrue(any("shell=True" in str(f) for f in findings))

    def test_ordinary_subprocess_run_with_argv_list_is_clean_for_tooling(self) -> None:
        source = "import subprocess\nsubprocess.run(['docker', 'build', '.'], check=False)\n"
        findings = self._write_and_check(source, self.module.TOOLING_FORBIDDEN_MODULES)
        self.assertEqual(findings, [])

    def test_real_tooling_directories_scan_clean(self) -> None:
        """The actual scripts/build/ and scripts/security/ this repository
        ships must themselves pass the tooling rule set."""
        for scan_dir in (REPO_ROOT / "scripts" / "build", REPO_ROOT / "scripts" / "security"):
            for path in sorted(scan_dir.rglob("*.py")):
                findings = self.module.check_file(path, self.module.TOOLING_FORBIDDEN_MODULES)
                self.assertEqual(findings, [], f"{path}: {findings}")


if __name__ == "__main__":
    unittest.main()
