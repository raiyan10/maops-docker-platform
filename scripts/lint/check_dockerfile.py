#!/usr/bin/env python3
"""Project-specific Dockerfile validator.

SCOPE: this validates invariants specific to *this* project's single
Dockerfile (docker/app/Dockerfile) and is deliberately not a general
Dockerfile linter (it does not replace Hadolint and makes no attempt to).
It is line/instruction-aware (handles line continuations, comments, and
instruction case/whitespace) rather than doing naive substring matching on
the raw file, so a comment or a string value can't trivially satisfy a
check that is meant to apply to a real instruction.

Checks:
  * FROM is digest-pinned (`image:tag@sha256:<64 hex chars>` - validated as
    an actual well-formed sha256 digest, not just `@sha256:` substring
    presence), never `:latest`, and matches this project's base-image
    policy (`python:*-slim`).
  * The final USER is non-root and matches the expected 10001:10001 intent.
  * A HEALTHCHECK instruction exists, is not `NONE`, and its CMD is
    exactly the required `["python3", "-m", "app.healthcheck"]` invocation
    - a regression to the bare-script form (`python3 app/healthcheck.py`,
    which breaks because `/app` isn't on `sys.path` for a bare script)
    fails this check rather than only failing at Compose-health-status
    time.
  * No `sudo` anywhere.
  * No remote `ADD` (a URL as the source).
  * No obviously secret-bearing ARG/ENV variable names.
  * An explicit WORKDIR is set.
  * The runtime command (ENTRYPOINT, or CMD if no ENTRYPOINT) uses exec
    form (a JSON array), never shell form.
  * No `--privileged`/`setuid`/`setcap` usage.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECRET_NAME_PATTERN = re.compile(
    r"(PASSWORD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL)",
    re.IGNORECASE,
)
PRIVILEGED_PATTERN = re.compile(r"--privileged|setuid|setcap", re.IGNORECASE)
SUDO_PATTERN = re.compile(r"\bsudo\b", re.IGNORECASE)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
HEALTHCHECK_CMD_PATTERN = re.compile(r"\bCMD\s+(\[.*\])\s*$")
REQUIRED_HEALTHCHECK_CMD = ["python3", "-m", "app.healthcheck"]


class Finding:
    def __init__(self, line: int, message: str) -> None:
        self.line = line
        self.message = message

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


def parse_instructions(text: str) -> list[tuple[int, str, str]]:
    """Join line continuations, drop comments/blanks, return (line, INSTR, rest)."""
    instructions: list[tuple[int, str, str]] = []
    buffer_lines: list[str] = []
    start_line: int | None = None

    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not buffer_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            start_line = i
            buffer_lines = [line]
        else:
            buffer_lines.append(line)

        if line.endswith("\\"):
            buffer_lines[-1] = buffer_lines[-1][:-1]
            continue

        block = " ".join(part.strip() for part in buffer_lines).strip()
        buffer_lines = []
        parts = block.split(None, 1)
        if not parts:
            continue
        instr = parts[0].upper()
        rest = parts[1].strip() if len(parts) > 1 else ""
        instructions.append((start_line or i, instr, rest))

    return instructions


def check_from(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    from_lines = [(ln, rest) for ln, instr, rest in instructions if instr == "FROM"]
    if not from_lines:
        return [Finding(0, "no FROM instruction found")]

    line_no, rest = from_lines[-1]
    image_ref = rest.split()[0]

    if "@" not in image_ref:
        findings.append(Finding(line_no, f"FROM is not digest-pinned: {image_ref}"))
    else:
        digest_part = image_ref.split("@", 1)[1]
        if not DIGEST_PATTERN.match(digest_part):
            findings.append(
                Finding(
                    line_no,
                    f"FROM digest is not a well-formed sha256 digest "
                    f"(expected sha256: followed by 64 hex characters): {digest_part}",
                )
            )
    if ":latest" in image_ref or (
        "@" not in image_ref and ":" not in image_ref.split("/")[-1]
    ):
        findings.append(Finding(line_no, f"FROM must not use the latest tag: {image_ref}"))
    if not image_ref.startswith("python:"):
        findings.append(
            Finding(line_no, f"FROM does not match base-image policy python:*-slim: {image_ref}")
        )
    elif "-slim" not in image_ref.split("@")[0]:
        findings.append(
            Finding(line_no, f"FROM does not use the slim variant required by policy: {image_ref}")
        )

    return findings


def check_user(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    user_lines = [(ln, rest) for ln, instr, rest in instructions if instr == "USER"]
    if not user_lines:
        return [Finding(0, "no USER instruction found; image would run as root")]

    line_no, rest = user_lines[-1]
    value = rest.strip()
    if value in ("root", "0", "0:0"):
        return [Finding(line_no, f"final USER is root: {value}")]
    if value not in ("10001", "10001:10001"):
        return [
            Finding(
                line_no,
                f"final USER does not match expected UID:GID 10001:10001: {value}",
            )
        ]
    return []


def check_healthcheck(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    healthchecks = [(ln, rest) for ln, instr, rest in instructions if instr == "HEALTHCHECK"]
    if not healthchecks:
        return [Finding(0, "no HEALTHCHECK instruction found")]
    line_no, rest = healthchecks[-1]
    if rest.strip().upper() == "NONE":
        return [Finding(line_no, "HEALTHCHECK is explicitly disabled (NONE)")]

    match = HEALTHCHECK_CMD_PATTERN.search(rest)
    if not match:
        return [
            Finding(
                line_no,
                f"HEALTHCHECK does not use a CMD [...] exec-form array: {rest}",
            )
        ]
    try:
        cmd = json.loads(match.group(1))
    except json.JSONDecodeError:
        return [Finding(line_no, f"HEALTHCHECK CMD is not valid JSON: {match.group(1)}")]
    if cmd != REQUIRED_HEALTHCHECK_CMD:
        return [
            Finding(
                line_no,
                f"HEALTHCHECK CMD must be exactly {REQUIRED_HEALTHCHECK_CMD}, got {cmd} "
                f"(a bare-script invocation like ['python3', 'app/healthcheck.py'] breaks "
                f"because /app is not on sys.path outside package-module form)",
            )
        ]
    return []


def check_no_sudo(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    return [
        Finding(ln, f"sudo usage found: {rest}")
        for ln, instr, rest in instructions
        if SUDO_PATTERN.search(rest)
    ]


def check_no_remote_add(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for ln, instr, rest in instructions:
        if instr != "ADD":
            continue
        first_arg = rest.split()[0] if rest.split() else ""
        if first_arg.startswith("http://") or first_arg.startswith("https://"):
            findings.append(Finding(ln, f"ADD from a remote URL is forbidden: {first_arg}"))
    return findings


def check_no_secret_vars(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for ln, instr, rest in instructions:
        if instr not in ("ARG", "ENV"):
            continue
        # ENV/ARG may declare one or more NAME[=VALUE] pairs.
        for token in rest.split():
            name = token.split("=", 1)[0]
            if SECRET_NAME_PATTERN.search(name):
                findings.append(
                    Finding(ln, f"{instr} declares a secret-bearing-looking variable: {name}")
                )
    return findings


def check_workdir(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    if not any(instr == "WORKDIR" for _, instr, _ in instructions):
        return [Finding(0, "no explicit WORKDIR instruction found")]
    return []


def check_exec_form_runtime_command(
    instructions: list[tuple[int, str, str]]
) -> list[Finding]:
    entrypoints = [(ln, rest) for ln, instr, rest in instructions if instr == "ENTRYPOINT"]
    cmds = [(ln, rest) for ln, instr, rest in instructions if instr == "CMD"]

    target = entrypoints[-1] if entrypoints else (cmds[-1] if cmds else None)
    if target is None:
        return [Finding(0, "no ENTRYPOINT or CMD instruction found")]

    line_no, rest = target
    if not rest.strip().startswith("["):
        return [Finding(line_no, f"runtime command is not exec form (JSON array): {rest}")]
    return []


def check_no_privileged_concepts(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    return [
        Finding(ln, f"privileged/setuid concept found: {rest}")
        for ln, instr, rest in instructions
        if PRIVILEGED_PATTERN.search(rest)
    ]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    dockerfile_path = repo_root / "docker" / "app" / "Dockerfile"

    if not dockerfile_path.exists():
        print(f"Dockerfile not found at {dockerfile_path}", file=sys.stderr)
        return 1

    text = dockerfile_path.read_text(encoding="utf-8")
    instructions = parse_instructions(text)

    all_findings: list[Finding] = []
    all_findings += check_from(instructions)
    all_findings += check_user(instructions)
    all_findings += check_healthcheck(instructions)
    all_findings += check_no_sudo(instructions)
    all_findings += check_no_remote_add(instructions)
    all_findings += check_no_secret_vars(instructions)
    all_findings += check_workdir(instructions)
    all_findings += check_exec_form_runtime_command(instructions)
    all_findings += check_no_privileged_concepts(instructions)

    if all_findings:
        print(f"check_dockerfile.py: {len(all_findings)} finding(s):")
        for finding in all_findings:
            print(f"  {finding}")
        return 1

    print(f"check_dockerfile.py: OK (9 checks passed against {dockerfile_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
