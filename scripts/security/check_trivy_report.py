#!/usr/bin/env python3
"""Validates a Trivy JSON vulnerability report and enforces this project's
vulnerability policy against it.

SCOPE: this is a project-specific report validator/policy engine, not a
general Trivy output schema validator. It is deliberately usable both as
a library (`evaluate_policy()`, `validate_report()` - imported by
scripts/security/vuln_scan.py right after generation) and as a standalone
CLI against any given Trivy JSON file, so the policy logic itself has its
own direct, Docker-free unit tests using synthetic fixtures (see
tests/test_check_trivy_report.py) - .claude/CLAUDE.md is explicit that a
real CVE in the application image is never required merely to test
failure-path logic.

Policy (documented in full in docs/supply-chain.md):
  - any CRITICAL finding -> FAIL
  - any HIGH finding WITH a FixedVersion available -> FAIL
  - HIGH findings with no fix available -> reported prominently, non-blocking
  - lower severities -> reported, not a release blocker

This project never silently ignores a finding, never auto-generates a
.trivyignore, and never manufactures an exception - see
docs/supply-chain.md for what to do when a finding is genuinely
unavoidable (report it and stop, per .claude/CLAUDE.md's explicit
instruction for this exact situation).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Finding:
    vulnerability_id: str
    package_name: str
    installed_version: str
    fixed_version: str | None
    severity: str
    target: str


@dataclass
class PolicyResult:
    critical: list[Finding] = field(default_factory=list)
    fixable_high: list[Finding] = field(default_factory=list)
    unfixed_high: list[Finding] = field(default_factory=list)
    other_counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.critical and not self.fixable_high


def validate_report(raw_text: str, expected_image: str | None = None) -> tuple[dict | None, list[str]]:
    """Returns (parsed_report_or_None, findings). A non-empty findings list
    means the report failed validation (parsed_report may still be
    returned alongside best-effort findings if parsing itself succeeded)."""
    findings: list[str] = []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, [f"Trivy report is not valid JSON: {exc}"]

    if "SchemaVersion" not in data:
        findings.append("Trivy report is missing 'SchemaVersion'")

    results = data.get("Results")
    if not isinstance(results, list):
        findings.append("Trivy report is missing a 'Results' list")

    if expected_image is not None:
        repo_tags = (data.get("Metadata") or {}).get("RepoTags") or []
        if expected_image not in repo_tags:
            findings.append(
                f"Trivy report Metadata.RepoTags {repo_tags!r} does not include the expected image {expected_image!r}"
            )

    return data, findings


def evaluate_policy(data: dict) -> PolicyResult:
    result = PolicyResult()
    for scan_result in data.get("Results", []) or []:
        target = scan_result.get("Target", "")
        for vuln in scan_result.get("Vulnerabilities", []) or []:
            entry = Finding(
                vulnerability_id=vuln.get("VulnerabilityID", "?"),
                package_name=vuln.get("PkgName", "?"),
                installed_version=vuln.get("InstalledVersion", "?"),
                fixed_version=vuln.get("FixedVersion") or None,
                severity=vuln.get("Severity", "UNKNOWN"),
                target=target,
            )
            if entry.severity == "CRITICAL":
                result.critical.append(entry)
            elif entry.severity == "HIGH":
                if entry.fixed_version:
                    result.fixable_high.append(entry)
                else:
                    result.unfixed_high.append(entry)
            else:
                result.other_counts[entry.severity] = result.other_counts.get(entry.severity, 0) + 1
    return result


def _format_finding(entry: Finding) -> str:
    fix = f"fix={entry.fixed_version}" if entry.fixed_version else "no fix available"
    return f"{entry.vulnerability_id} {entry.package_name}@{entry.installed_version} ({fix}) [{entry.target}]"


def print_policy_report(policy: PolicyResult) -> None:
    print(f"vulnerability policy: CRITICAL={len(policy.critical)} (any -> FAIL)")
    for entry in policy.critical:
        print(f"  CRITICAL: {_format_finding(entry)}")

    print(f"vulnerability policy: HIGH-with-fix={len(policy.fixable_high)} (any -> FAIL)")
    for entry in policy.fixable_high:
        print(f"  HIGH (fixable): {_format_finding(entry)}")

    print(f"vulnerability policy: HIGH-without-fix={len(policy.unfixed_high)} (reported, non-blocking)")
    for entry in policy.unfixed_high:
        print(f"  HIGH (unfixed): {_format_finding(entry)}")

    if policy.other_counts:
        print(f"vulnerability policy: other severities (reported, non-blocking): {policy.other_counts}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_trivy_report.py <path-to-trivy-report.json> [expected-image]", file=sys.stderr)
        return 1

    report_path = Path(sys.argv[1])
    expected_image = sys.argv[2] if len(sys.argv) > 2 else None

    if not report_path.is_file():
        print(f"check_trivy_report: FAIL: no report found at {report_path}", file=sys.stderr)
        return 1

    raw_text = report_path.read_text(encoding="utf-8")
    data, findings = validate_report(raw_text, expected_image)

    if findings:
        print(f"check_trivy_report: FAIL ({len(findings)} finding(s)) against {report_path}:")
        for finding in findings:
            print(f"  - {finding}")
        return 1

    assert data is not None
    policy = evaluate_policy(data)
    print_policy_report(policy)

    if not policy.passed:
        print("check_trivy_report: FAIL - vulnerability policy violated (see above)", file=sys.stderr)
        return 1

    print(f"check_trivy_report: PASS - {report_path} is valid and satisfies the vulnerability policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
