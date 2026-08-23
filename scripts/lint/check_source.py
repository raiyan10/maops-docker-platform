#!/usr/bin/env python3
"""Project-specific source validator for the app/, gateway/, state/, and
(Day 4) scripts/build/, scripts/security/ packages.

SCOPE (read this before trusting the result): this is a small, deliberately
narrow AST-based check for a short, explicit list of constructs that would
be inappropriate in this project's source. It is **not** a general-purpose
static security scanner, does not understand data flow, and does not
replace a real tool (e.g. Bandit, Semgrep) for a larger codebase.

Two different rule sets apply, because the *workload* and the *tooling*
have genuinely different legitimate needs — narrowing one to fit the
other would either be pointlessly strict (tooling has no legitimate need
for `subprocess`... except that it does, since it drives Docker) or
dangerously loose (the application workload has no legitimate need for
process/shell execution at all):

  * **Workload** (`app/`, `gateway/`, `state/` — the actual runtime HTTP
    services): `subprocess`/`pickle`/`ctypes` imports are forbidden
    outright. Legitimate stdlib HTTP networking (`http.client`, `socket`,
    `urllib.parse`) is never flagged — the gateway's/app's whole job is
    making real, bounded outbound HTTP calls to a fixed configured
    upstream. `scripts/compose/`, `scripts/lint/`, `scripts/smoke/`,
    `scripts/verify/`, and `tests/` are never scanned by either rule set
    below — they are Docker-driving/test infrastructure, not workload or
    the two Day 4 directories this scan was deliberately widened to.
  * **Tooling** (`scripts/build/`, `scripts/security/` — Day 4's Docker/
    scanner-driving infrastructure): `subprocess` is explicitly permitted
    (both directories' whole job is invoking `docker build`/`docker run`/
    the pinned Syft/Trivy scanner containers) — but `pickle`/`ctypes` are
    still forbidden (neither directory has any legitimate need for
    either), and every check below that isn't import-based (eval/exec/
    os.system/os.popen/shell=True) applies identically to both rule sets.
    Widening this scan to the tooling directories does not weaken the
    workload's own restrictions in any way — the two rule sets are kept
    genuinely separate, not merged into one relaxed policy.

Checks performed, each via the `ast` module (never naive substring
matching, so e.g. a string literal or a comment mentioning "eval" never
trips a finding):

  * no `eval`/`exec`/`compile`/`__import__` calls (both rule sets)
  * no `import subprocess` / `import pickle` / `import ctypes` (workload);
    no `import pickle` / `import ctypes` (tooling — `subprocess` allowed)
  * no `os.system(...)` / `os.popen(...)` calls — including through a
    single-hop import alias (`import os as x; x.system(...)`) or a
    `from os import system as x; x(...)` rebinding, both tracked via each
    file's own module-level `Import`/`ImportFrom` statements (closes the
    Day 1/2 carried-forward finding L-1: a prior version only matched a
    literal `os.system(...)` call, so a one-line aliasing rename bypassed
    it entirely) — both rule sets, since even `subprocess`-using tooling
    has no legitimate need for a shell-string `os.system`/`os.popen` call
  * no call anywhere passing `shell=True` (both rule sets — tooling's
    `subprocess.run([...])` calls always use an argv list, never a shell
    string)
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

WORKLOAD_FORBIDDEN_MODULES = {"subprocess", "pickle", "ctypes"}
TOOLING_FORBIDDEN_MODULES = {"pickle", "ctypes"}
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


def check_file(path: Path, forbidden_modules: set[str]) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    findings: list[Finding] = []
    os_aliases, forbidden_bare_names = _collect_os_aliases(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in forbidden_modules:
                    findings.append(
                        Finding(path, node.lineno, f"forbidden import: {alias.name}")
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in forbidden_modules:
                findings.append(
                    Finding(path, node.lineno, f"forbidden import: {node.module}")
                )
        elif isinstance(node, ast.Call):
            findings.extend(_check_call(node, path, os_aliases, forbidden_bare_names))

    return findings


def _collect_files(scan_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            raise FileNotFoundError(str(scan_dir))
        files.extend(sorted(scan_dir.rglob("*.py")))
    return files


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    workload_dirs = [repo_root / "app", repo_root / "gateway", repo_root / "state"]
    tooling_dirs = [repo_root / "scripts" / "build", repo_root / "scripts" / "security"]

    try:
        workload_files = _collect_files(workload_dirs)
        tooling_files = _collect_files(tooling_dirs)
    except FileNotFoundError as exc:
        print(f"no such directory: {exc}", file=sys.stderr)
        return 1

    if not workload_files:
        print(f"no Python files found under {workload_dirs}", file=sys.stderr)
        return 1
    if not tooling_files:
        print(f"no Python files found under {tooling_dirs}", file=sys.stderr)
        return 1

    all_findings: list[Finding] = []
    for path in workload_files:
        all_findings.extend(check_file(path, WORKLOAD_FORBIDDEN_MODULES))
    for path in tooling_files:
        all_findings.extend(check_file(path, TOOLING_FORBIDDEN_MODULES))

    if all_findings:
        print(f"check_source.py: {len(all_findings)} finding(s):")
        for finding in all_findings:
            print(f"  {finding}")
        return 1

    workload_names = "/, ".join(d.name for d in workload_dirs) + "/"
    tooling_names = ", ".join(f"scripts/{d.name}/" for d in tooling_dirs)
    print(
        f"check_source.py: OK ({len(workload_files)} workload file(s) scanned under "
        f"{workload_names}; {len(tooling_files)} tooling file(s) scanned under {tooling_names})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
