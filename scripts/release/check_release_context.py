#!/usr/bin/env python3
"""Repository-owned release-context validation for maops-docker-platform (Day 6).

Backs `.github/workflows/release.yml`'s two modes:

- `--mode dry-run` — the safe, non-publishing `workflow_dispatch` path on
  `main`: no tag exists yet. Derives the PROPOSED tag from VERSION and
  validates everything that can genuinely be validated pre-tag (VERSION
  format, the derived tag's own format, and that the release notes file the
  real tag event will require already exists — so a release-candidate dry
  run gives real, actionable feedback about readiness, not merely "VERSION
  parses").
- `--mode tag` — the real `push: tags: v*.*.*` event: validates VERSION
  format, tag format, tag-vs-VERSION exact equality, release-notes
  presence, and (via an injectable git-ancestor check, real git only at the
  CLI boundary — never inside the pure logic this module exists to make
  testable) that the tagged commit genuinely belongs to `main`'s history —
  refusing to publish a release from an arbitrary feature-only commit.

Design notes:

- All core parsing/decision logic (`validate_version_format`,
  `validate_tag_format`, `tag_matches_version`, `validate_release_notes_exist`,
  `validate_main_history`) is pure and Docker/git-free by construction — the
  one function that genuinely needs `git` (`default_git_is_ancestor`) is a
  thin, separately swappable adapter (`GitAncestorChecker`), never entangled
  with the validation logic itself. See `tests/test_check_release_context.py`
  for the Docker-free unit coverage this split enables.
- User-controlled strings (a tag ref from `GITHUB_REF_NAME`, a commit SHA
  from `GITHUB_SHA`) are NEVER interpolated into a shell command —
  `default_git_is_ancestor` passes them as separate argv elements to
  `subprocess.run([...])`, never `shell=True`, never string-formatted into
  a command line.
- Semver pattern is deliberately narrow (`MAJOR.MINOR.PATCH`, no
  prerelease/build-metadata suffix) — this project's own `VERSION` file
  never uses one.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

GitAncestorChecker = Callable[[str, str], bool]


class ReleaseContextError(ValueError):
    """Raised when release-context validation fails for any reason below."""


@dataclass(frozen=True)
class ReleaseContext:
    mode: str  # "dry-run" or "tag"
    version: str
    tag: str
    release_notes_path: Path
    checks: list[str] = field(default_factory=list)


# --- pure validation steps (Docker-free, git-free) --------------------------


def validate_version_format(version: str) -> None:
    if not VERSION_PATTERN.match(version.strip()):
        raise ReleaseContextError(
            f"VERSION {version!r} is not a valid MAJOR.MINOR.PATCH semver-like string"
        )


def validate_tag_format(tag: str) -> None:
    if not TAG_PATTERN.match(tag.strip()):
        raise ReleaseContextError(f"tag {tag!r} is not a valid vMAJOR.MINOR.PATCH string")


def tag_matches_version(tag: str, version: str) -> None:
    expected = f"v{version.strip()}"
    if tag.strip() != expected:
        raise ReleaseContextError(
            f"tag {tag!r} does not match VERSION {version!r} (expected tag {expected!r})"
        )


def release_notes_path_for(tag: str, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "docs" / "releases" / f"{tag}.md"


def validate_release_notes_exist(tag: str, repo_root: Path = REPO_ROOT) -> Path:
    path = release_notes_path_for(tag, repo_root)
    if not path.is_file():
        raise ReleaseContextError(
            f"required release notes are missing: {path} "
            f"(add docs/releases/{tag}.md before this tag can be published)"
        )
    return path


def default_git_is_ancestor(commit: str, ancestor_of: str) -> bool:
    """Real `git merge-base --is-ancestor` check — an argv list, never
    `shell=True`, so a hostile `commit`/`ancestor_of` string can never be
    interpreted as shell syntax. Not used by any unit test; the real-git
    integration proof is this script running for real inside
    `.github/workflows/release.yml`, not `unittest`."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, ancestor_of],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def validate_main_history(
    commit: str,
    main_ref: str,
    is_ancestor: GitAncestorChecker = default_git_is_ancestor,
) -> None:
    if not commit.strip():
        raise ReleaseContextError("no commit SHA was provided to validate against main history")
    if not is_ancestor(commit, main_ref):
        raise ReleaseContextError(
            f"tagged commit {commit!r} is not part of {main_ref!r} history — "
            "refusing to publish a release from an arbitrary feature-only commit"
        )


# --- context builders --------------------------------------------------------


def build_dry_run_context(version: str, repo_root: Path = REPO_ROOT) -> ReleaseContext:
    validate_version_format(version)
    proposed_tag = f"v{version.strip()}"
    validate_tag_format(proposed_tag)
    notes_path = validate_release_notes_exist(proposed_tag, repo_root)
    return ReleaseContext(
        mode="dry-run",
        version=version,
        tag=proposed_tag,
        release_notes_path=notes_path,
        checks=["version_format", "proposed_tag_format", "release_notes_present"],
    )


def build_tag_context(
    version: str,
    tag: str,
    commit: str,
    main_ref: str = "origin/main",
    is_ancestor: GitAncestorChecker = default_git_is_ancestor,
    repo_root: Path = REPO_ROOT,
) -> ReleaseContext:
    validate_version_format(version)
    validate_tag_format(tag)
    tag_matches_version(tag, version)
    notes_path = validate_release_notes_exist(tag, repo_root)
    validate_main_history(commit, main_ref, is_ancestor)
    return ReleaseContext(
        mode="tag",
        version=version,
        tag=tag,
        release_notes_path=notes_path,
        checks=[
            "version_format",
            "tag_format",
            "tag_version_equality",
            "release_notes_present",
            "main_history",
        ],
    )


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["dry-run", "tag"], required=True)
    parser.add_argument(
        "--version", default=None, help="defaults to reading the repository-root VERSION file"
    )
    parser.add_argument("--tag", default=None, help="required for --mode=tag")
    parser.add_argument(
        "--commit", default=None, help="required for --mode=tag (the tagged commit's SHA)"
    )
    parser.add_argument(
        "--main-ref", default="origin/main", help="ref to check tag-mode main-history ancestry against"
    )
    args = parser.parse_args(argv)

    version = args.version or (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    try:
        if args.mode == "dry-run":
            ctx = build_dry_run_context(version)
        else:
            if not args.tag or not args.commit:
                raise ReleaseContextError("--tag and --commit are both required for --mode=tag")
            ctx = build_tag_context(version, args.tag, args.commit, main_ref=args.main_ref)
    except ReleaseContextError as exc:
        print(f"check_release_context: FAIL: {exc}", file=sys.stderr)
        return 1

    print(
        f"check_release_context: OK mode={ctx.mode} version={ctx.version} tag={ctx.tag} "
        f"release_notes={ctx.release_notes_path} checks={ctx.checks}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
