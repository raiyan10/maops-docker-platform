#!/usr/bin/env python3
"""Repository-owned GitHub Actions workflow policy validator (Day 6).

SCOPE: this is deliberately NOT a full YAML/GitHub-Actions-schema
implementation - it is a small, project-specific static policy check
against the two committed workflow files (`.github/workflows/ci.yml`,
`.github/workflows/release.yml`), using line/indentation-based text
analysis rather than a YAML dependency. Two small, hand-authored workflow
files do not justify a general-purpose YAML parser dependency merely to
express these specific, high-value invariants - matching this project's
existing preference (`scripts/compose/check_compose.py` parses `docker
compose config --format json`'s stdlib-friendly JSON output rather than
adding a YAML dependency for the same reason).

SELF-REFERENCE (read before changing this file): the committed `ci.yml`
runs this exact script (via `make workflow-check`, part of `make quality`)
against its own checked-out copy of these same two files. This must stay
deterministic - every check here reads only the two files from disk
(`REPO_ROOT`-relative paths); nothing here depends on a GitHub-Actions-only
runtime environment variable (`GITHUB_*`), which would make a local `make
workflow-check` run behave differently from CI's own run of the identical
script against the identical committed files.

Policy enforced (see docs/ci-cd.md for the full rationale of each):
required workflow files exist; no `pull_request_target`; `ci.yml`'s
permissions are exactly `contents: read` workflow-wide, with no broader
scope anywhere in the file; `release.yml`'s write permission is scoped to
exactly the `publish` job; every `uses:` reference is pinned to a full
40-character commit SHA; no `continue-on-error: true`; no `|| true` used to
disguise a gate; both files declare their required triggers; `release.yml`
declares the `v*.*.*` tag pattern; the `publish` job's `if:` can only ever
be satisfied by a real tag push, never `workflow_dispatch`; every job that
runs `make release-check` first creates and selects a job-scoped
`docker-container` driver Buildx builder (the GitHub-hosted runner's
default `docker` driver cannot satisfy this project's Day 4 deterministic
`type=docker,dest=...` exporter - see docs/ci-cd.md) and removes it with
`if: always()`; `release.yml`'s `validate` job runs
`scripts/release/check_release_context.py` as an unconditional step (no
`if:` gating it to one event, so a non-main `workflow_dispatch` fails
loudly rather than appearing "skipped"), invoked with explicit
`--event-name`/`--ref` GitHub context (the main-only `workflow_dispatch`
dry-run contract is enforced by that script, not merely by this YAML) and
before `make release-check` (fail fast on an invalid ref/tag/event, not
after several minutes of build work); no registry-publication command
appears; no Day 7+ tooling reference appears; the `publish` job's
`gh release create` step is guarded by an earlier existing-release check
(`gh release view "$TAG" ... ` failing the job on a hit) and never
invoked with `--clobber` anywhere in either file (Day 7,
DAY7-RELENG-L1 - see docs/engineering-reviews/day-07-release-engineering-review.md).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
REQUIRED_WORKFLOW_FILES = ("ci.yml", "release.yml")

FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
USES_PATTERN = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s@]+)@(\S+)", re.MULTILINE)
WRITE_OR_ADMIN_PERMISSION_PATTERN = re.compile(r"^\s*\S+:\s*(write|admin)\s*$", re.MULTILINE)

FORBIDDEN_REGISTRY_PATTERNS = (
    "docker login",
    "docker push",
    "ghcr.io",
    "docker.io/",
    "public.ecr.aws",
    "azurecr.io",
    "registry.hub.docker.com",
)
FORBIDDEN_DAY7_PLUS_TOOLING = (
    "cosign",
    "slsa",
    "sigstore",
    "kubectl",
    "helm ",
    "helm3",
    "argocd",
    "argo-cd",
    "terraform",
    "ansible",
    "prometheus",
    "grafana",
    "opentelemetry",
    "kubernetes",
)


class Finding:
    def __init__(self, message: str) -> None:
        self.message = message

    def __str__(self) -> str:
        return self.message


# --- small, generic indentation-based block extraction ----------------------


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _find_key_line(lines: list[str], key: str, indent: int, start: int = 0) -> int | None:
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}:\s*(#.*)?$")
    for i in range(start, len(lines)):
        if pattern.match(lines[i]):
            return i
    return None


def _extract_block(lines: list[str], key_line_index: int, key_indent: int) -> list[str]:
    """Returns the nested lines belonging to the `key:` at `lines[key_line_index]`
    - everything more deeply indented than `key_indent`, stopping at the
    first equally-or-less-indented, non-blank, non-comment line."""
    block: list[str] = []
    for line in lines[key_line_index + 1 :]:
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            block.append(line)
            continue
        if _line_indent(line) <= key_indent:
            break
        block.append(line)
    return block


def top_level_block(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    idx = _find_key_line(lines, key, indent=0)
    if idx is None:
        return []
    return _extract_block(lines, idx, key_indent=0)


def job_block(text: str, job_name: str) -> list[str]:
    lines = text.splitlines()
    jobs_idx = _find_key_line(lines, "jobs", indent=0)
    if jobs_idx is None:
        return []
    jobs_lines = _extract_block(lines, jobs_idx, key_indent=0)
    job_indent = next((_line_indent(line) for line in jobs_lines if line.strip()), None)
    if job_indent is None:
        return []
    pattern = re.compile(rf"^\s*{re.escape(job_name)}:\s*$")
    for i, line in enumerate(jobs_lines):
        if line.strip() and _line_indent(line) == job_indent and pattern.match(line):
            return _extract_block(jobs_lines, i, key_indent=job_indent)
    return []


def nested_block(block_lines: list[str], key: str) -> list[str]:
    for i, line in enumerate(block_lines):
        stripped = line.strip()
        if stripped == f"{key}:" or stripped.startswith(f"{key}:"):
            return _extract_block(block_lines, i, key_indent=_line_indent(line))
    return []


def list_items(block_lines: list[str]) -> list[list[str]]:
    """Splits a YAML list block (e.g. a `steps:` block) into per-item line
    groups. Each item starts at a `- ` marker at the list's own (shallowest
    seen) indent and includes its own more-deeply-indented continuation
    lines - generic the same way `top_level_block`/`job_block`/
    `nested_block` above are, not specific to steps."""
    items: list[list[str]] = []
    item_indent: int | None = None
    for line in block_lines:
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            if items:
                items[-1].append(line)
            continue
        indent = _line_indent(line)
        if stripped.startswith("- ") and (item_indent is None or indent == item_indent):
            item_indent = indent
            items.append([line])
        elif items:
            items[-1].append(line)
    return items


def _non_comment_values(block_lines: list[str]) -> list[str]:
    return [line.strip() for line in block_lines if line.strip() and not line.strip().startswith("#")]


# --- checks -------------------------------------------------------------


def _strip_comments(text: str) -> str:
    """Removes everything from the first '#' to end of line, on every line.

    Applied uniformly before every check below - so a docstring-style
    explanatory comment (e.g. "Deliberately NOT pull_request_target: ...",
    or "keeps workflow_dispatch ... from ever publishing") can freely
    *name* a forbidden pattern to explain its absence without itself
    triggering that pattern's check. None of this project's own workflow
    YAML ever needs a literal '#' character as data (no such value appears
    in either file), so this simple per-line cut is safe here. Comment-only
    lines become whitespace-only lines, which the block-extraction helpers
    above already treat identically to blank lines (both `continue` a
    block rather than ending it), so this has no effect on structural
    (indentation-based) parsing.
    """
    return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())


def read_workflow_files() -> dict[str, str]:
    texts: dict[str, str] = {}
    for name in REQUIRED_WORKFLOW_FILES:
        path = WORKFLOWS_DIR / name
        if path.is_file():
            texts[name] = _strip_comments(path.read_text(encoding="utf-8"))
    return texts


def check_required_files_exist() -> list[Finding]:
    findings = []
    for name in REQUIRED_WORKFLOW_FILES:
        if not (WORKFLOWS_DIR / name).is_file():
            findings.append(Finding(f"required workflow file missing: .github/workflows/{name}"))
    return findings


def check_no_pull_request_target(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        if "pull_request_target" in text:
            findings.append(Finding(f"{name}: pull_request_target must never be used"))
    return findings


def check_uses_pinned_to_full_sha(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        for action, ref in USES_PATTERN.findall(text):
            if not FULL_SHA_PATTERN.match(ref):
                findings.append(
                    Finding(
                        f"{name}: uses: {action}@{ref} is not pinned to an immutable full "
                        "40-character commit SHA (floating ref forbidden)"
                    )
                )
    return findings


def check_no_continue_on_error(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        if re.search(r"continue-on-error:\s*true", text):
            findings.append(
                Finding(f"{name}: 'continue-on-error: true' must never appear on a release-policy gate")
            )
    return findings


def check_no_manufactured_pass(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        if re.search(r"\|\|\s*true\b", text):
            findings.append(
                Finding(f"{name}: '|| true' must never be used to disguise a required gate's failure")
            )
    return findings


def check_ci_permissions_read_only(texts: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    text = texts.get("ci.yml")
    if text is None:
        return findings
    values = _non_comment_values(top_level_block(text, "permissions"))
    if values != ["contents: read"]:
        findings.append(
            Finding(f"ci.yml: workflow-level permissions must be exactly ['contents: read'], found {values}")
        )
    if WRITE_OR_ADMIN_PERMISSION_PATTERN.search(text):
        findings.append(Finding("ci.yml: no permission scope may be 'write' or 'admin' anywhere in this file"))
    return findings


def check_release_permissions_scoped(texts: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    text = texts.get("release.yml")
    if text is None:
        return findings

    workflow_values = _non_comment_values(top_level_block(text, "permissions"))
    if workflow_values != ["contents: read"]:
        findings.append(
            Finding(
                f"release.yml: workflow-level permissions must be exactly ['contents: read'], "
                f"found {workflow_values}"
            )
        )

    validate_values = _non_comment_values(nested_block(job_block(text, "validate"), "permissions"))
    if validate_values != ["contents: read"]:
        findings.append(
            Finding(
                f"release.yml: 'validate' job permissions must be exactly ['contents: read'], "
                f"found {validate_values}"
            )
        )

    publish_block = job_block(text, "publish")
    if not publish_block:
        findings.append(Finding("release.yml: no 'publish' job found"))
        return findings

    publish_values = _non_comment_values(nested_block(publish_block, "permissions"))
    if publish_values != ["contents: write"]:
        findings.append(
            Finding(
                f"release.yml: 'publish' job permissions must be exactly ['contents: write'], "
                f"found {publish_values}"
            )
        )

    all_write_or_admin = WRITE_OR_ADMIN_PERMISSION_PATTERN.findall(text)
    if len(all_write_or_admin) != 1:
        findings.append(
            Finding(
                "release.yml: expected exactly one write/admin permission scope in the whole file "
                f"(scoped to the 'publish' job), found {len(all_write_or_admin)}"
            )
        )
    return findings


def check_manual_dispatch_cannot_publish(texts: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    text = texts.get("release.yml")
    if text is None:
        return findings
    block = job_block(text, "publish")
    if not block:
        findings.append(Finding("release.yml: no 'publish' job found to verify manual-dispatch safety"))
        return findings
    publish_text = "\n".join(block)
    if "workflow_dispatch" in publish_text:
        findings.append(
            Finding(
                "release.yml: 'publish' job must never reference workflow_dispatch - a manual "
                "dispatch run must be structurally unable to reach publication"
            )
        )
    if "event_name == 'push'" not in publish_text and 'event_name == "push"' not in publish_text:
        findings.append(
            Finding("release.yml: 'publish' job's if: condition must require github.event_name == 'push'")
        )
    if not re.search(r"startsWith\(github\.ref,\s*['\"]refs/tags/['\"]\)", publish_text):
        findings.append(
            Finding("release.yml: 'publish' job's if: condition must require a tag ref (refs/tags/)")
        )
    if "success()" not in publish_text:
        findings.append(
            Finding(
                "release.yml: 'publish' job's if: condition must explicitly include success() - "
                "adding any if: to a job replaces the default 'needs succeeded' check"
            )
        )
    return findings


def check_required_triggers(texts: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []

    ci_text = texts.get("ci.yml")
    if ci_text is not None:
        on_block = "\n".join(top_level_block(ci_text, "on"))
        if "pull_request:" not in on_block:
            findings.append(Finding("ci.yml: missing required 'pull_request' trigger"))
        if "push:" not in on_block:
            findings.append(Finding("ci.yml: missing required 'push' trigger"))
        if "main" not in on_block:
            findings.append(Finding("ci.yml: triggers do not appear scoped to 'main'"))

    release_text = texts.get("release.yml")
    if release_text is not None:
        on_block = "\n".join(top_level_block(release_text, "on"))
        if "tags:" not in on_block:
            findings.append(Finding("release.yml: missing required push.tags trigger"))
        if "v*.*.*" not in on_block:
            findings.append(Finding("release.yml: push.tags does not declare the 'v*.*.*' release tag pattern"))
        if "workflow_dispatch" not in on_block:
            findings.append(
                Finding("release.yml: missing required workflow_dispatch trigger (needed for the safe dry run)")
            )
    return findings


JOBS_REQUIRING_BUILDX_CONTAINER_BUILDER = (
    ("ci.yml", "release-policy"),
    ("release.yml", "validate"),
)


def check_buildx_container_builder_before_release_check(texts: dict[str, str]) -> list[Finding]:
    """Any job that reaches `make build`/`make release-check` uses this
    project's Day 4 deterministic `type=docker,dest=...` Buildx archive
    exporter (see Makefile's `build` target). GitHub's hosted runner Docker
    Engine does not run the containerd image store, so the Buildx default
    `docker` driver builder cannot satisfy that exporter ("Docker exporter
    is not supported for the docker driver" - see docs/ci-cd.md). Each such
    job must therefore create (and select, via `--use`) a job-scoped
    `docker-container` driver builder before `make release-check`, and
    remove it afterward with `if: always()` so cleanup still runs after a
    failed gate. Checked by step order within the job block (via
    `list_items`), not a full YAML/step-graph model."""
    findings: list[Finding] = []
    for file_name, job_name in JOBS_REQUIRING_BUILDX_CONTAINER_BUILDER:
        text = texts.get(file_name)
        if text is None:
            continue
        block = job_block(text, job_name)
        if not block:
            findings.append(Finding(f"{file_name}: no '{job_name}' job found to verify Buildx builder setup"))
            continue

        steps = list_items(nested_block(block, "steps"))
        step_texts = ["\n".join(step) for step in steps]

        release_check_idx = next((i for i, s in enumerate(step_texts) if "make release-check" in s), None)
        if release_check_idx is None:
            findings.append(Finding(f"{file_name}: '{job_name}' job does not run 'make release-check'"))
            continue

        builder_create_idx = next(
            (
                i
                for i, s in enumerate(step_texts)
                if "docker buildx create" in s and "docker-container" in s and "--use" in s
            ),
            None,
        )
        if builder_create_idx is None:
            findings.append(
                Finding(
                    f"{file_name}: '{job_name}' job must create a 'docker-container' driver Buildx "
                    "builder (with --use) before 'make release-check' - the runner's default docker "
                    "driver cannot satisfy the project's type=docker,dest=... exporter"
                )
            )
        elif builder_create_idx >= release_check_idx:
            findings.append(
                Finding(
                    f"{file_name}: '{job_name}' job's Buildx builder-creation step must run before "
                    "'make release-check', not after"
                )
            )

        cleanup_idx = next((i for i, s in enumerate(step_texts) if "buildx rm" in s), None)
        if cleanup_idx is None:
            findings.append(
                Finding(f"{file_name}: '{job_name}' job must remove its job-scoped Buildx builder after use")
            )
        elif "if: always()" not in step_texts[cleanup_idx]:
            findings.append(
                Finding(
                    f"{file_name}: '{job_name}' job's Buildx builder cleanup step must run with "
                    "'if: always()' so it still runs after a failed release-check"
                )
            )
    return findings


def check_release_context_validation_is_authoritative(texts: dict[str, str]) -> list[Finding]:
    """Closes the Day 6 release-engineering-review Medium finding: the
    workflow_dispatch dry-run's "main-only" intent must be structurally
    enforced, not merely documented. This checks that `release.yml`'s
    `validate` job runs `scripts/release/check_release_context.py`:

    - unconditionally (no per-event `if:` gating it) - a `workflow_dispatch`
      run against a non-main ref must reach this step and fail loudly, never
      be silently skipped in a way that could be mistaken for a passing
      validation;
    - with explicit `--event-name`/`--ref` GitHub context, so the script
      itself - not this YAML's own `if:` expressions - is the authoritative
      distinguisher between the dry-run and tag-push paths and the
      authoritative enforcer of the main-only dry-run contract;
    - before `make release-check`, so an invalid ref/tag/event fails fast
      rather than after several minutes of build work.
    """
    findings: list[Finding] = []
    text = texts.get("release.yml")
    if text is None:
        return findings
    block = job_block(text, "validate")
    if not block:
        findings.append(Finding("release.yml: no 'validate' job found to verify release-context validation"))
        return findings

    steps = list_items(nested_block(block, "steps"))
    step_texts = ["\n".join(step) for step in steps]

    context_idx = next((i for i, s in enumerate(step_texts) if "check_release_context.py" in s), None)
    if context_idx is None:
        findings.append(
            Finding("release.yml: 'validate' job must run scripts/release/check_release_context.py")
        )
        return findings

    context_step = step_texts[context_idx]
    if re.search(r"^\s*if:", context_step, re.MULTILINE):
        findings.append(
            Finding(
                "release.yml: the check_release_context.py step must be unconditional (no "
                "per-event 'if:') so a non-main workflow_dispatch fails loudly instead of "
                "appearing skipped"
            )
        )
    if "--event-name" not in context_step:
        findings.append(
            Finding(
                "release.yml: check_release_context.py must be invoked with explicit "
                "--event-name (github.event_name) - the script, not a YAML if:, must be the "
                "authoritative distinguisher between workflow_dispatch and a tag push"
            )
        )
    if "--ref" not in context_step:
        findings.append(
            Finding(
                "release.yml: check_release_context.py must be invoked with explicit --ref "
                "(github.ref) so the main-only workflow_dispatch dry-run contract is enforced "
                "in code, not merely documented"
            )
        )

    release_check_idx = next((i for i, s in enumerate(step_texts) if "make release-check" in s), None)
    if release_check_idx is not None and context_idx > release_check_idx:
        findings.append(
            Finding(
                "release.yml: check_release_context.py must run before 'make release-check' so "
                "an invalid ref/tag/event fails fast, not after several minutes of build work"
            )
        )
    return findings


def check_no_release_clobber(texts: dict[str, str]) -> list[Finding]:
    """Closes DAY7-RELENG-L1 (day-07-release-engineering-review.md): the
    `publish` job's existing-release guard (`gh release view "$TAG" ...`,
    failing the job when a release for the tag already exists) prevents a
    re-run from silently overwriting an already-published release's
    evidence - a real safety invariant that, until now, was proven only by
    a human reading `release.yml`'s source. Scoped strictly to the
    `publish` job: a `gh release view`/guard-shaped step appearing
    anywhere else in either file (e.g. the `validate` job) does not
    satisfy this - a guard outside `publish` protects nothing, since only
    `publish` ever runs `gh release create`. Requires, in order:

    - `gh release create` is never invoked with `--clobber` anywhere in
      either workflow file (checked file-wide, since a clobber flag would
      be equally dangerous regardless of which step/job introduced it);
    - the `publish` job contains a step that both queries for an existing
      release (`gh release view`) AND fails the job when one is found (an
      `exit <nonzero>` in that same step) - merely mentioning
      `gh release view` without acting on the result does not count as a
      guard;
    - that guard step runs strictly BEFORE the `gh release create` step,
      never after (a guard placed after publication has already happened
      protects nothing).
    """
    findings: list[Finding] = []
    for name, text in texts.items():
        if "--clobber" in text:
            findings.append(Finding(f"{name}: 'gh release create' must never be invoked with '--clobber'"))

    text = texts.get("release.yml")
    if text is None:
        return findings
    block = job_block(text, "publish")
    if not block:
        findings.append(Finding("release.yml: no 'publish' job found to verify the existing-release guard"))
        return findings

    steps = list_items(nested_block(block, "steps"))
    step_texts = ["\n".join(step) for step in steps]

    create_idx = next((i for i, s in enumerate(step_texts) if "gh release create" in s), None)
    if create_idx is None:
        findings.append(Finding("release.yml: 'publish' job does not run 'gh release create'"))
        return findings

    guard_idx = next(
        (i for i, s in enumerate(step_texts) if "gh release view" in s and re.search(r"exit\s+[1-9]", s)),
        None,
    )
    if guard_idx is None:
        findings.append(
            Finding(
                "release.yml: 'publish' job must guard 'gh release create' with a pre-existing "
                "existing-release check ('gh release view \"$TAG\" ...' failing the job on a hit) "
                "before ever creating the release"
            )
        )
    elif guard_idx >= create_idx:
        findings.append(
            Finding(
                "release.yml: the existing-release guard step must run BEFORE 'gh release create', "
                "not after"
            )
        )
    return findings


def check_no_registry_publication(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        lowered = text.lower()
        for pattern in FORBIDDEN_REGISTRY_PATTERNS:
            if pattern in lowered:
                findings.append(Finding(f"{name}: forbidden registry-publication reference found: {pattern!r}"))
    return findings


def check_no_day7_plus_tooling(texts: dict[str, str]) -> list[Finding]:
    findings = []
    for name, text in texts.items():
        lowered = text.lower()
        for pattern in FORBIDDEN_DAY7_PLUS_TOOLING:
            if pattern in lowered:
                findings.append(Finding(f"{name}: forbidden Day 7+ tooling reference found: {pattern!r}"))
    return findings


CHECKS = [
    check_no_pull_request_target,
    check_uses_pinned_to_full_sha,
    check_no_continue_on_error,
    check_no_manufactured_pass,
    check_ci_permissions_read_only,
    check_release_permissions_scoped,
    check_manual_dispatch_cannot_publish,
    check_required_triggers,
    check_buildx_container_builder_before_release_check,
    check_release_context_validation_is_authoritative,
    check_no_release_clobber,
    check_no_registry_publication,
    check_no_day7_plus_tooling,
]


def main() -> int:
    all_findings: list[Finding] = list(check_required_files_exist())

    texts = read_workflow_files()
    for check in CHECKS:
        all_findings.extend(check(texts))

    if all_findings:
        print(f"check_workflows.py: {len(all_findings)} finding(s):")
        for finding in all_findings:
            print(f"  {finding}")
        return 1

    print(
        f"check_workflows.py: OK ({len(CHECKS) + 1} policy checks passed against "
        f".github/workflows/{{{','.join(REQUIRED_WORKFLOW_FILES)}}})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
