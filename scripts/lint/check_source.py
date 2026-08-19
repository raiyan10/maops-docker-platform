#!/usr/bin/env python3
"""Project-specific source validator for the app/ and gateway/ packages.

SCOPE (read this before trusting the result): this is a small, deliberately
narrow AST-based check for a short, explicit list of constructs that would
be inappropriate in this project's tiny stdlib HTTP workload/gateway. It is
**not** a general-purpose static security scanner, does not understand data
flow, and does not replace a real tool (e.g. Bandit, Semgrep) for a larger
codebase. It only scans `app/` and `gateway/` — the actual runtime
application and gateway source — never `scripts/` (which legitimately uses
`subprocess` to drive Docker) or `tests/`.

Legitimate stdlib HTTP networking (`http.client`, `socket`, `urllib.parse`)
is never flagged — the gateway's whole job is making real, bounded
outbound HTTP calls to a fixed configured upstream. What remains forbidden
is shell/process execution and other constructs no honest HTTP
client/server needs.

Checks performed, each via the `ast` module (never naive substring
matching, so e.g. a string literal or a comment mentioning "eval" never
trips a finding):

  * no `eval`/`exec`/`compile`/`__import__` calls
  * no `import subprocess` / `import pickle` / `import ctypes`
  * no `os.system(...)` / `os.popen(...)` calls
  * no call anywhere passing `shell=True`
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_MODULES = {"subprocess", "pickle", "ctypes"}
FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_OS_ATTRS = {"system", "popen"}


class Finding:
    def __init__(self, path: Path, line: int, message: str) -> None:
        self.path = path
        self.line = line
        self.message = message

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _check_call(node: ast.Call, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    func = node.func

    if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALL_NAMES:
        findings.append(Finding(path, node.lineno, f"forbidden call: {func.id}()"))

    if (
        isinstance(func, ast.Attribute)
        and func.attr in FORBIDDEN_OS_ATTRS
        and isinstance(func.value, ast.Name)
        and func.value.id == "os"
    ):
        findings.append(
            Finding(path, node.lineno, f"forbidden call: os.{func.attr}()")
        )

    for keyword in node.keywords:
        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
            if keyword.value.value is True:
                findings.append(
                    Finding(path, node.lineno, "forbidden argument: shell=True")
                )

    return findings


def check_file(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in FORBIDDEN_MODULES:
                    findings.append(
                        Finding(path, node.lineno, f"forbidden import: {alias.name}")
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_MODULES:
                findings.append(
                    Finding(path, node.lineno, f"forbidden import: {node.module}")
                )
        elif isinstance(node, ast.Call):
            findings.extend(_check_call(node, path))

    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    scan_dirs = [repo_root / "app", repo_root / "gateway"]

    files: list[Path] = []
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            print(f"no such directory: {scan_dir}", file=sys.stderr)
            return 1
        files.extend(sorted(scan_dir.rglob("*.py")))

    if not files:
        print(f"no Python files found under {scan_dirs}", file=sys.stderr)
        return 1

    all_findings: list[Finding] = []
    for path in files:
        all_findings.extend(check_file(path))

    if all_findings:
        print(f"check_source.py: {len(all_findings)} finding(s):")
        for finding in all_findings:
            print(f"  {finding}")
        return 1

    print(f"check_source.py: OK ({len(files)} file(s) scanned under app/, gateway/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
