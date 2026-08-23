#!/usr/bin/env python3
"""Project-specific Dockerfile validator.

SCOPE: this validates invariants specific to *this* project's single
Dockerfile (docker/app/Dockerfile) and is deliberately not a general
Dockerfile linter (it does not replace Hadolint and makes no attempt to).
It is line/instruction-aware (handles line continuations, comments, and
instruction case/whitespace) rather than doing naive substring matching on
the raw file, so a comment or a string value can't trivially satisfy a
check that is meant to apply to a real instruction.

Day 4 (v0.4.0): the Dockerfile is a two-stage build - a `python:3.13-slim`
builder stage (filesystem preparation only) and a Distroless
(`gcr.io/distroless/python3-debian13:nonroot`) final runtime stage (no
shell, no package manager). This checker validates BOTH stages, not just
the final `FROM` - a prior single-stage assumption (only ever looking at
the last `FROM`) would silently stop validating the builder pin entirely.

Checks:
  * There are exactly two `FROM` instructions: a `python:*-slim` builder,
    digest-pinned (`image:tag@sha256:<64 hex chars>`, validated as an
    actual well-formed sha256 digest, never `:latest`), and a Distroless
    `gcr.io/distroless/python3-debian13:nonroot` final runtime,
    digest-pinned to this project's approved index digest.
  * The final USER is non-root and matches the expected 10001:10001
    intent.
  * A HEALTHCHECK instruction exists, is not `NONE`, and its CMD is
    exactly the required `["/usr/bin/python3.13", "-m", "app.healthcheck"]`
    invocation (absolute interpreter path - the Distroless final stage has
    no shell to perform PATH resolution) - a regression to a bare `python3`
    invocation (which depends on a `python3` PATH entry this project's
    Dockerfile never relies on) fails this check rather than only failing
    at Compose-health-status time.
  * No `sudo` anywhere.
  * No remote `ADD` (a URL as the source).
  * No obviously secret-bearing ARG/ENV variable names.
  * An explicit WORKDIR is set (final stage).
  * The runtime command (ENTRYPOINT, or CMD if no ENTRYPOINT) uses exec
    form (a JSON array), never shell form, and ENTRYPOINT is the absolute
    `/usr/bin/python3.13` interpreter.
  * No `--privileged`/`setuid`/`setcap` usage.
  * No `RUN` instruction in the final (post-builder) stage - the
    Distroless runtime has no shell/coreutils to run one against, so any
    `RUN` after the final `FROM` is either a mistake or silently
    unreachable.
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
REQUIRED_HEALTHCHECK_CMD = ["/usr/bin/python3.13", "-m", "app.healthcheck"]
REQUIRED_ENTRYPOINT = ["/usr/bin/python3.13"]

EXPECTED_BUILDER_DIGEST = "sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
EXPECTED_BUILDER_REPO = "python"
EXPECTED_FINAL_DIGEST = "sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea"
EXPECTED_FINAL_REPO = "gcr.io/distroless/python3-debian13"


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


def split_stages(
    instructions: list[tuple[int, str, str]]
) -> list[list[tuple[int, str, str]]]:
    """Splits the full instruction list into per-stage instruction lists, one
    per FROM (a two-stage Dockerfile yields two lists: builder, then final)."""
    stages: list[list[tuple[int, str, str]]] = []
    current: list[tuple[int, str, str]] = []
    for entry in instructions:
        _, instr, _ = entry
        if instr == "FROM":
            if current:
                stages.append(current)
            current = [entry]
        elif current:
            current.append(entry)
    if current:
        stages.append(current)
    return stages


def _check_digest_pinned_from(
    line_no: int, image_ref: str, expected_repo: str, expected_digest: str, stage_label: str
) -> list[Finding]:
    findings: list[Finding] = []

    if "@" not in image_ref:
        findings.append(Finding(line_no, f"{stage_label} FROM is not digest-pinned: {image_ref}"))
        return findings

    repo_and_tag, _, digest_part = image_ref.partition("@")
    if not DIGEST_PATTERN.match(digest_part):
        findings.append(
            Finding(
                line_no,
                f"{stage_label} FROM digest is not a well-formed sha256 digest "
                f"(expected sha256: followed by 64 hex characters): {digest_part}",
            )
        )
    if ":latest" in repo_and_tag:
        findings.append(Finding(line_no, f"{stage_label} FROM must not use the latest tag: {image_ref}"))
    repo = repo_and_tag.split(":")[0]
    if repo != expected_repo:
        findings.append(
            Finding(
                line_no,
                f"{stage_label} FROM does not match the expected repository "
                f"{expected_repo!r}: {image_ref}",
            )
        )
    if digest_part != expected_digest:
        findings.append(
            Finding(
                line_no,
                f"{stage_label} FROM digest does not match the approved pin "
                f"{expected_digest!r}: {digest_part!r}",
            )
        )
    return findings


def check_from(instructions: list[tuple[int, str, str]]) -> list[Finding]:
    findings: list[Finding] = []
    from_lines = [(ln, rest) for ln, instr, rest in instructions if instr == "FROM"]

    if len(from_lines) != 2:
        return [
            Finding(
                0,
                f"expected exactly 2 FROM instructions (builder + Distroless final "
                f"stage), found {len(from_lines)}",
            )
        ]

    builder_line, builder_rest = from_lines[0]
    final_line, final_rest = from_lines[1]

    builder_image_ref = builder_rest.split()[0]
    final_image_ref = final_rest.split()[0]

    findings += _check_digest_pinned_from(
        builder_line, builder_image_ref, EXPECTED_BUILDER_REPO, EXPECTED_BUILDER_DIGEST, "builder stage"
    )
    findings += _check_digest_pinned_from(
        final_line, final_image_ref, EXPECTED_FINAL_REPO, EXPECTED_FINAL_DIGEST, "final stage"
    )

    builder_rest_tokens = builder_rest.split()
    if len(builder_rest_tokens) < 3 or builder_rest_tokens[1].upper() != "AS":
        findings.append(Finding(builder_line, f"builder stage FROM must declare 'AS builder': {builder_rest!r}"))
    elif builder_rest_tokens[2].lower() != "builder":
        findings.append(
            Finding(builder_line, f"builder stage name must be 'builder', got {builder_rest_tokens[2]!r}")
        )

    return findings


def check_no_run_in_final_stage(stages: list[list[tuple[int, str, str]]]) -> list[Finding]:
    """The Distroless final stage has no shell/coreutils to run a RUN
    instruction against - any RUN there is either dead weight or a mistake
    that would break the build outright. Only the builder stage (stages[0])
    may contain RUN instructions."""
    if len(stages) < 2:
        return []
    final_stage = stages[-1]
    return [
        Finding(ln, f"RUN instruction found in the shellless Distroless final stage: {rest}")
        for ln, instr, rest in final_stage
        if instr == "RUN"
    ]


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
                f"(a bare 'python3' invocation depends on shell PATH resolution, which "
                f"the shellless Distroless final stage cannot perform)",
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

    if not entrypoints:
        return [Finding(0, "no ENTRYPOINT instruction found (required: absolute /usr/bin/python3.13)")]

    line_no, rest = entrypoints[-1]
    if not rest.strip().startswith("["):
        return [Finding(line_no, f"ENTRYPOINT is not exec form (JSON array): {rest}")]
    try:
        entrypoint = json.loads(rest.strip())
    except json.JSONDecodeError:
        return [Finding(line_no, f"ENTRYPOINT is not valid JSON: {rest}")]
    findings: list[Finding] = []
    if entrypoint != REQUIRED_ENTRYPOINT:
        findings.append(
            Finding(
                line_no,
                f"ENTRYPOINT must be exactly {REQUIRED_ENTRYPOINT} (absolute interpreter path - "
                f"the Distroless final stage has no shell for PATH resolution), got {entrypoint}",
            )
        )

    if cmds:
        cmd_line, cmd_rest = cmds[-1]
        if not cmd_rest.strip().startswith("["):
            findings.append(Finding(cmd_line, f"CMD is not exec form (JSON array): {cmd_rest}"))

    return findings


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
    stages = split_stages(instructions)

    all_findings: list[Finding] = []
    all_findings += check_from(instructions)
    all_findings += check_no_run_in_final_stage(stages)
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

    print(f"check_dockerfile.py: OK (10 checks passed against {dockerfile_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
