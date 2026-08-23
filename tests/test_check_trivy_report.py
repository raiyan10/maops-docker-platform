"""Docker-free unit tests for scripts/security/check_trivy_report.py's
report validation and vulnerability-policy engine, using synthetic
fixture JSON (no real Trivy invocation - see .claude/CLAUDE.md: a real
CVE in the application image is never required merely to test policy
failure-path logic)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_trivy_report() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "check_trivy_report.py"
    spec = importlib.util.spec_from_file_location("check_trivy_report_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses.dataclass() looks the defining module up in sys.modules
    # (needed to resolve string type annotations) - must be registered
    # before exec_module() runs the class bodies, or it raises AttributeError.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _vuln(vuln_id: str, severity: str, fixed_version: str | None = None) -> dict:
    return {
        "VulnerabilityID": vuln_id,
        "PkgName": "examplepkg",
        "InstalledVersion": "1.0.0",
        "FixedVersion": fixed_version or "",
        "Severity": severity,
    }


def _report(vulns: list[dict], image: str = "maops-docker-platform:0.4.0") -> dict:
    return {
        "SchemaVersion": 2,
        "ArtifactName": "/input.tar",
        "Metadata": {"RepoTags": [image]},
        "Results": [{"Target": "example-target", "Vulnerabilities": vulns}],
    }


class ValidateReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_trivy_report()

    def test_valid_report_passes_validation(self) -> None:
        data, findings = self.module.validate_report(json.dumps(_report([])), "maops-docker-platform:0.4.0")
        self.assertEqual(findings, [])
        self.assertIsNotNone(data)

    def test_malformed_json_is_rejected(self) -> None:
        data, findings = self.module.validate_report("not json")
        self.assertIsNone(data)
        self.assertTrue(findings)

    def test_missing_schema_version_is_rejected(self) -> None:
        report = _report([])
        del report["SchemaVersion"]
        _, findings = self.module.validate_report(json.dumps(report))
        self.assertTrue(any("SchemaVersion" in f for f in findings))

    def test_missing_results_is_rejected(self) -> None:
        report = _report([])
        del report["Results"]
        _, findings = self.module.validate_report(json.dumps(report))
        self.assertTrue(any("Results" in f for f in findings))

    def test_image_mismatch_is_rejected(self) -> None:
        _, findings = self.module.validate_report(
            json.dumps(_report([], image="maops-docker-platform:0.3.0")), "maops-docker-platform:0.4.0"
        )
        self.assertTrue(any("RepoTags" in f for f in findings))


class EvaluatePolicyTests(unittest.TestCase):
    """Discriminating-power tests: a synthetic report engineered to trip
    each policy branch must actually trip it, and a clean report must not."""

    def setUp(self) -> None:
        self.module = load_check_trivy_report()

    def test_clean_report_passes(self) -> None:
        report = _report([_vuln("CVE-1", "LOW"), _vuln("CVE-2", "MEDIUM")])
        policy = self.module.evaluate_policy(report)
        self.assertTrue(policy.passed)
        self.assertEqual(policy.critical, [])
        self.assertEqual(policy.fixable_high, [])

    def test_any_critical_finding_fails_policy(self) -> None:
        report = _report([_vuln("CVE-CRIT-1", "CRITICAL")])
        policy = self.module.evaluate_policy(report)
        self.assertFalse(policy.passed)
        self.assertEqual(len(policy.critical), 1)

    def test_fixable_high_finding_fails_policy(self) -> None:
        report = _report([_vuln("CVE-HIGH-1", "HIGH", fixed_version="1.0.1")])
        policy = self.module.evaluate_policy(report)
        self.assertFalse(policy.passed)
        self.assertEqual(len(policy.fixable_high), 1)

    def test_unfixed_high_finding_is_reported_but_non_blocking(self) -> None:
        report = _report([_vuln("CVE-HIGH-2", "HIGH", fixed_version=None)])
        policy = self.module.evaluate_policy(report)
        self.assertTrue(policy.passed)
        self.assertEqual(len(policy.unfixed_high), 1)
        self.assertEqual(policy.fixable_high, [])

    def test_mixed_report_discriminates_each_bucket_correctly(self) -> None:
        report = _report(
            [
                _vuln("CVE-CRIT", "CRITICAL"),
                _vuln("CVE-HIGH-FIXABLE", "HIGH", fixed_version="2.0.0"),
                _vuln("CVE-HIGH-UNFIXED", "HIGH", fixed_version=None),
                _vuln("CVE-MED", "MEDIUM"),
                _vuln("CVE-LOW", "LOW"),
            ]
        )
        policy = self.module.evaluate_policy(report)
        self.assertFalse(policy.passed)
        self.assertEqual([f.vulnerability_id for f in policy.critical], ["CVE-CRIT"])
        self.assertEqual([f.vulnerability_id for f in policy.fixable_high], ["CVE-HIGH-FIXABLE"])
        self.assertEqual([f.vulnerability_id for f in policy.unfixed_high], ["CVE-HIGH-UNFIXED"])
        self.assertEqual(policy.other_counts.get("MEDIUM"), 1)
        self.assertEqual(policy.other_counts.get("LOW"), 1)

    def test_empty_results_list_passes(self) -> None:
        report = _report([])
        report["Results"] = []
        policy = self.module.evaluate_policy(report)
        self.assertTrue(policy.passed)

    def test_result_with_no_vulnerabilities_key_is_handled(self) -> None:
        report = {"SchemaVersion": 2, "Results": [{"Target": "t"}]}
        policy = self.module.evaluate_policy(report)
        self.assertTrue(policy.passed)


class MainCliTests(unittest.TestCase):
    """Proves the policy exits non-zero on a synthetic failing fixture and
    zero on a clean one, end to end through main()."""

    def setUp(self) -> None:
        self.module = load_check_trivy_report()

    def _write_report(self, tmp_path: Path, vulns: list[dict]) -> Path:
        path = tmp_path / "trivy-report.json"
        path.write_text(json.dumps(_report(vulns)), encoding="utf-8")
        return path

    def test_main_exits_nonzero_on_critical_fixture(self) -> None:
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write_report(Path(tmp_dir), [_vuln("CVE-X", "CRITICAL")])
            old_argv = sys.argv
            sys.argv = ["check_trivy_report.py", str(path)]
            try:
                exit_code = self.module.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(exit_code, 1)

    def test_main_exits_zero_on_clean_fixture(self) -> None:
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write_report(Path(tmp_dir), [_vuln("CVE-X", "LOW")])
            old_argv = sys.argv
            sys.argv = ["check_trivy_report.py", str(path)]
            try:
                exit_code = self.module.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
