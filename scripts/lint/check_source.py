#!/usr/bin/env python3
"""Project-specific source validator for the app/, gateway/, and state/ packages.

SCOPE (read this before trusting the result): this is a small, deliberately
narrow AST-based check for a short, explicit list of constructs that would
be inappropriate in this project's tiny stdlib HTTP workload/gateway/state
service. It is **not** a general-purpose static security scanner, does not
understand data flow, and does not replace a real tool (e.g. Bandit,
Semgrep) for a larger codebase. It only scans `app/`, `gateway/`, and
`state/` — the actual runtime service source — never `scripts/` (which
legitimately uses `subprocess` to drive Docker) or `tests/`.

Legitimate stdlib HTTP networking (`http.client`, `socket`, `urllib.parse`)
is never flagged — the gateway's/app's whole job is making real, bounded
outbound HTTP calls to a fixed configured upstream. What remains forbidden
is shell/process execution and other constructs no honest HTTP
client/server needs.

Checks performed, each via the `ast` module (never naive substring
matching, so e.g. a string literal or a comment mentioning "eval" never
trips a finding):

  * no `eval`/`exec`/`compile`/`__import__` calls
  * no `import subprocess` / `import pickle` / `import ctypes`
  * no `os.system(...)` / `os.popen(...)` calls — including through a
    single-hop import alias (`import os as x; x.system(...)`) or a
    `from os import system as x; x(...)` rebinding, both tracked via each
    file's own module-level `Import`/`ImportFrom` statements (closes the
    Day 1/2 carried-forward finding L-1: a prior version only matched a
    literal `os.system(...)` call, so a one-line aliasing rename bypassed
    it entirely)
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


def _collect_os_aliases(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Returns (names bound to the `os` module itself, names bound directly
    to `os.system`/`os.popen` via `from os import system as x`).

    Only tracks module-level-reachable `Import`/`ImportFrom` statements
    (a full walk, not just top-level) - deliberately still narrow: it does
    not attempt to track re-assignment of an already-bound name to
    something else, or aliasing through indirection deeper than one hop,
    matching this script's own documented scope.
    """
    os_aliases: set[str] = set()
    forbidden_bare_names: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "os":
                for alias in node.names:
                    if alias.name in FORBIDDEN_OS_ATTRS:
                        forbidden_bare_names[alias.asname or alias.name] = alias.name

    return os_aliases, forbidden_bare_names


def _check_call(
    node: ast.Call, path: Path, os_aliases: set[str], forbidden_bare_names: dict[str, str]
) -> list[Finding]:
    findings: list[Finding] = []
    func = node.func

    if isinstance(func, ast.Name):
        if func.id in FORBIDDEN_CALL_NAMES:
            findings.append(Finding(path, node.lineno, f"forbidden call: {func.id}()"))
        elif func.id in forbidden_bare_names:
            original = forbidden_bare_names[func.id]
            findings.append(
                Finding(
                    path, node.lineno,
                    f"forbidden call: os.{original}() imported as {func.id!r} (from os import {original} as {func.id})",
                )
            )

    if (
        isinstance(func, ast.Attribute)
        and func.attr in FORBIDDEN_OS_ATTRS
        and isinstance(func.value, ast.Name)
        and func.value.id in os_aliases
    ):
        alias_note = "" if func.value.id == "os" else f" (module 'os' imported as {func.value.id!r})"
        findings.append(
            Finding(path, node.lineno, f"forbidden call: os.{func.attr}(){alias_note}")
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
    os_aliases, forbidden_bare_names = _collect_os_aliases(tree)

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
            findings.extend(_check_call(node, path, os_aliases, forbidden_bare_names))

    return findings


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    scan_dirs = [repo_root / "app", repo_root / "gateway", repo_root / "state"]

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

    scanned_names = "/, ".join(d.name for d in scan_dirs) + "/"
    print(f"check_source.py: OK ({len(files)} file(s) scanned under {scanned_names})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
