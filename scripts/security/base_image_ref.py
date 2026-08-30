"""Derives docker/app/Dockerfile's own final-stage base image reference
(repository + digest) directly from the Dockerfile's real FROM text
(Day 7).

SCOPE: this exists so callers that need to independently corroborate the
pinned Distroless final base against REAL evidence (a real `docker pull`
+ `docker image inspect` of that exact digest - see
scripts/build/image_audit.py's `check_final_base_is_approved_distroless`
and scripts/security/patch_lifecycle_check.py) read the single real
source of truth (the Dockerfile itself, parsed the same way
scripts/lint/check_dockerfile.py already parses it) rather than trusting
a second, independently hand-copied digest constant - which would make
any comparison against it tautological (a constant can never disagree
with itself). scripts/lint/check_dockerfile.py's own `EXPECTED_FINAL_DIGEST`/
`EXPECTED_FINAL_REPO` remain the separate, legitimate check that the
Dockerfile's own text matches this project's actually-approved pin; this
module answers a different question ("what does the Dockerfile's final
stage currently say?"), not "is that the right thing to say?".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE_PATH = REPO_ROOT / "docker" / "app" / "Dockerfile"


class BaseImageRefError(ValueError):
    pass


def _load_check_dockerfile_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_dockerfile.py"
    spec = importlib.util.spec_from_file_location("check_dockerfile_for_base_image_ref", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_final_stage_base_ref(dockerfile_path: Path | None = None) -> tuple[str, str]:
    """Returns (repository, digest) - e.g.
    ("gcr.io/distroless/python3-debian13", "sha256:...") - for the LAST
    `FROM` instruction in docker/app/Dockerfile, using the exact same
    line-continuation-aware/comment-aware instruction parser
    scripts/lint/check_dockerfile.py itself uses (`parse_instructions`),
    so a change to Dockerfile comment/formatting style can never
    desynchronize this module from what check_dockerfile.py actually
    validates. Raises BaseImageRefError if the Dockerfile is missing, has
    no FROM instructions, or the final FROM isn't digest-pinned
    (`image@sha256:...`) - never guesses or falls back to a stale value."""
    path = dockerfile_path or DOCKERFILE_PATH
    if not path.is_file():
        raise BaseImageRefError(f"Dockerfile not found at {path}")

    check_dockerfile = _load_check_dockerfile_module()
    text = path.read_text(encoding="utf-8")
    instructions = check_dockerfile.parse_instructions(text)
    from_lines = [rest for _, instr, rest in instructions if instr == "FROM"]
    if not from_lines:
        raise BaseImageRefError(f"no FROM instructions found in {path}")

    final_image_ref = from_lines[-1].split()[0]
    if "@" not in final_image_ref:
        raise BaseImageRefError(f"final stage FROM is not digest-pinned: {final_image_ref!r}")

    repo, _, digest = final_image_ref.partition("@")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        raise BaseImageRefError(f"final stage FROM digest is not a well-formed sha256 digest: {digest!r}")

    return repo, digest


if __name__ == "__main__":
    try:
        repo, digest = get_final_stage_base_ref()
    except BaseImageRefError as exc:
        print(f"base_image_ref: FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"base_image_ref: final stage base = {repo}@{digest}")
