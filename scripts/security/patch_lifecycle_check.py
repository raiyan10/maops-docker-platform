#!/usr/bin/env python3
"""Runtime security-patch lifecycle validator for maops-docker-platform
(Day 7).

WHY THIS EXISTS: docker/app/Dockerfile's `security-patch` stage overlays
one checksum-pinned Debian-security `libssl3t64` package on top of the
pinned Distroless final base image, because that base digest currently
ships a fixable HIGH vulnerability the base itself hasn't picked up yet
(see security/runtime-patches.lock and docs/build-security.md). That
overlay is a deliberate, temporary exception, not a permanent fixture -
its EXIT CONDITION is: the pinned Distroless base itself eventually ships
a libssl3t64 build at least as new as the overlay's own patched version,
at which point the overlay becomes redundant (and, worse, a future base
refresh could ship something NEWER than the overlay, in which case this
project's own overlay would silently downgrade the runtime). Nothing
before this script ever checked for that condition automatically - a
human would have had to remember to look.

WHAT THIS SCRIPT PROVES (real evidence, not duplicated constants): it
derives the pinned final base's (repository, digest) from
docker/app/Dockerfile's own FROM text (scripts/security/base_image_ref.py -
never a second hand-copied digest literal), independently `docker pull`s
that EXACT digest, and inspects the REAL libssl3t64 package metadata that
base image ships (`docker create` + `docker cp` of
/var/lib/dpkg/status.d/<package> - Distroless has no shell, so this
project's established create/cp/no-shell pattern from
scripts/build/image_audit.py is reused rather than requiring one). The
overlay's own recorded LIBSSL_VULNERABLE_VERSION/LIBSSL_VERSION
(security/runtime-patches.lock) are then compared against that REAL,
freshly observed base version using genuine Debian version-comparison
semantics (scripts/security/debian_version.py - not string/tuple
comparison, which gets `~deb13uN` revisions wrong).

CLASSIFICATION (see `classify_patch_lifecycle` - the pure, Docker-free,
independently unit-tested decision function):

  A. base version < overlay's patched version, AND base version matches
     the lock file's own recorded LIBSSL_VULNERABLE_VERSION (the lock's
     assumption is still accurate) -> overlay still REQUIRED -> PASS.
  B. base version >= overlay's patched version -> the base has caught up
     (or overtaken) the overlay -> overlay is now REDUNDANT and must be
     explicitly reviewed/removed -> FAIL clearly (never silently PASS).
  C. the real base package version/metadata could not be established at
     all (pull failed, package missing, unparseable version, malformed
     Debian version string) -> FAIL clearly - NEVER silently assumed
     still-required just because that's the "safe-sounding" default;
     absence of evidence is its own failure here.
  D. the base version IS still older than the patched version (overlay
     is still technically required) BUT does not match the lock file's
     own recorded LIBSSL_VULNERABLE_VERSION -> the lock's documented
     rationale has drifted from reality (a partial base refresh, a
     different point release, etc.) -> FAIL clearly, prompting a lock
     update, rather than silently continuing to trust stale metadata.

Never given the Docker socket to any *other* container - this script
only ever runs plain `docker pull`/`create`/`cp`/`rm` against the public,
digest-pinned base image itself, matching this project's existing
scripts/build/image_audit.py pattern.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PULL_TIMEOUT_SECONDS = 180.0
CREATE_TIMEOUT_SECONDS = 30.0
CP_TIMEOUT_SECONDS = 20.0
RM_TIMEOUT_SECONDS = 20.0

_VERSION_LINE_PATTERN = re.compile(r"^Version:\s*(.+?)\s*$", re.MULTILINE)
_PACKAGE_LINE_PATTERN = re.compile(r"^Package:\s*(.+?)\s*$", re.MULTILINE)

# The four classification outcomes this validator distinguishes - see the
# module docstring above for the full rationale of each.
CLASS_REQUIRED = "A-REQUIRED"
CLASS_REDUNDANT = "B-REDUNDANT"
CLASS_INDETERMINATE = "C-INDETERMINATE"
CLASS_METADATA_DRIFT = "D-METADATA-DRIFT"


class PatchLifecycleError(RuntimeError):
    pass


def _load_module(relative_path: str, name: str) -> ModuleType:
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchLifecycleResult:
    """Mirrors security_check.CheckResult's shape/str() (see
    scripts/build/image_audit.py's own AuditResult for the same pattern),
    without importing a class from a dynamically-loaded module."""

    def __init__(self, name: str, passed: bool, detail: str) -> None:
        self.category = "LIFECYCLE:security-patch"
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{self.category}] {status} {self.name}: {self.detail}"


# --- pure classification logic (Docker-free, independently unit-tested) ----


def classify_patch_lifecycle(
    debian_version_module: ModuleType,
    *,
    base_version: str | None,
    base_package_name: str | None,
    patched_version: str,
    vulnerable_version_recorded: str,
    expected_package_name: str,
) -> tuple[str, bool, str]:
    """Returns (classification_code, passed, detail). Pure function - takes
    the already-extracted base package name/version as plain strings (or
    None if extraction failed) so this decision logic is fully testable
    without Docker. See the module docstring for what each classification
    code (A/B/C/D) means."""
    if base_version is None:
        return (
            CLASS_INDETERMINATE,
            False,
            "could not determine the real libssl3t64 version shipped by the pinned Distroless "
            "final base (extraction failed) - refusing to assume the overlay is still required",
        )

    if base_package_name is not None and base_package_name != expected_package_name:
        return (
            CLASS_INDETERMINATE,
            False,
            f"unexpected package name in base image dpkg status.d metadata: "
            f"got {base_package_name!r}, expected {expected_package_name!r}",
        )

    try:
        base_is_older = debian_version_module.is_older(base_version, patched_version)
    except debian_version_module.DebianVersionError as exc:
        return (
            CLASS_INDETERMINATE,
            False,
            f"could not compare Debian versions ({base_version!r} vs patched {patched_version!r}): {exc}",
        )

    if not base_is_older:
        return (
            CLASS_REDUNDANT,
            False,
            f"pinned Distroless base now ships {expected_package_name} {base_version!r}, which is "
            f">= the overlay's own patched version {patched_version!r} - the security-patch overlay "
            f"is REDUNDANT and must be explicitly reviewed/removed from docker/app/Dockerfile and "
            f"security/runtime-patches.lock, not silently left in place",
        )

    if base_version != vulnerable_version_recorded:
        return (
            CLASS_METADATA_DRIFT,
            False,
            f"pinned Distroless base ships {expected_package_name} {base_version!r}, which is still "
            f"older than the overlay's patched version {patched_version!r} (overlay still required), "
            f"but does NOT match security/runtime-patches.lock's own recorded "
            f"LIBSSL_VULNERABLE_VERSION={vulnerable_version_recorded!r} - the lock file's documented "
            f"rationale has drifted from the real base and must be updated",
        )

    return (
        CLASS_REQUIRED,
        True,
        f"pinned Distroless base ships {expected_package_name} {base_version!r} (matches "
        f"security/runtime-patches.lock's recorded LIBSSL_VULNERABLE_VERSION), still older than the "
        f"overlay's patched version {patched_version!r} - the security-patch overlay remains required",
    )


# --- Docker-integration evidence gathering -----------------------------


def run_docker(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def extract_base_package_metadata(base_ref: str, package: str, container_name: str) -> tuple[str | None, str | None, str]:
    """Real [B]/[D]-tier evidence gathering: `docker pull`s the exact
    pinned base digest, `docker create`s (never starts/runs - Distroless
    has no shell to exec into anyway) a throwaway container from it, and
    `docker cp`s out /var/lib/dpkg/status.d/<package> - the same dpkg
    status.d layout this project's own security-patch stage writes to
    (docker/app/Dockerfile) - to read the REAL currently-shipped
    Package/Version fields. Returns (package_name, version, detail) -
    both None on any failure, with `detail` explaining what happened; the
    caller (main()) is responsible for always cleaning up
    `container_name` via a try/finally, matching
    scripts/build/image_audit.py's own established pattern."""
    pull_result = run_docker(["pull", base_ref], timeout=PULL_TIMEOUT_SECONDS)
    if pull_result.returncode != 0:
        return None, None, f"docker pull {base_ref} failed: {pull_result.stderr.strip()}"

    create_result = run_docker(["create", "--name", container_name, base_ref], timeout=CREATE_TIMEOUT_SECONDS)
    if create_result.returncode != 0:
        return None, None, f"docker create from {base_ref} failed: {create_result.stderr.strip()}"

    with tempfile.TemporaryDirectory(prefix="maops-patch-lifecycle-") as tmp_dir:
        dest_dir = Path(tmp_dir)
        cp_result = run_docker(
            ["cp", f"{container_name}:/var/lib/dpkg/status.d/{package}", str(dest_dir / package)],
            timeout=CP_TIMEOUT_SECONDS,
        )
        if cp_result.returncode != 0:
            return None, None, (
                f"docker cp {container_name}:/var/lib/dpkg/status.d/{package} failed "
                f"(package metadata not present at the expected dpkg status.d path in the pinned "
                f"base image): {cp_result.stderr.strip()}"
            )

        status_path = dest_dir / package
        if not status_path.is_file():
            return None, None, f"docker cp reported success but {status_path} was not created"

        text = status_path.read_text(encoding="utf-8", errors="replace")

    version_match = _VERSION_LINE_PATTERN.search(text)
    package_match = _PACKAGE_LINE_PATTERN.search(text)
    if version_match is None:
        return None, None, f"no 'Version:' line found in base image's {package} dpkg status.d metadata"

    version = version_match.group(1).strip()
    package_name = package_match.group(1).strip() if package_match else None
    return package_name, version, f"extracted Package={package_name!r} Version={version!r} from base image"


def cleanup(container_name: str) -> None:
    run_docker(["rm", "-f", container_name], timeout=RM_TIMEOUT_SECONDS)


def main() -> int:
    if shutil.which("docker") is None:
        print("patch_lifecycle_check: docker CLI not found on PATH", file=sys.stderr)
        return 1

    debian_version = _load_module("scripts/security/debian_version.py", "debian_version_for_patch_lifecycle")
    runtime_patch_lock = _load_module("scripts/security/runtime_patch_lock.py", "runtime_patch_lock_for_patch_lifecycle")
    base_image_ref = _load_module("scripts/security/base_image_ref.py", "base_image_ref_for_patch_lifecycle")

    try:
        lock = runtime_patch_lock.load_runtime_patch_lock()
    except runtime_patch_lock.RuntimePatchLockError as exc:
        print(f"patch_lifecycle_check: FAIL: could not load security/runtime-patches.lock: {exc}", file=sys.stderr)
        return 1

    try:
        base_repo, base_digest = base_image_ref.get_final_stage_base_ref()
    except base_image_ref.BaseImageRefError as exc:
        print(f"patch_lifecycle_check: FAIL: could not derive the pinned final base from docker/app/Dockerfile: {exc}", file=sys.stderr)
        return 1

    base_ref = f"{base_repo}@{base_digest}"
    container_name = f"maops-patch-lifecycle-{uuid.uuid4().hex[:12]}"

    print(f"patch_lifecycle_check: pinned final base={base_ref} package={lock['LIBSSL_PACKAGE']} container={container_name}")

    try:
        base_package_name, base_version, extraction_detail = extract_base_package_metadata(
            base_ref, lock["LIBSSL_PACKAGE"], container_name
        )
    finally:
        cleanup(container_name)

    print(f"patch_lifecycle_check: {extraction_detail}")

    classification, passed, detail = classify_patch_lifecycle(
        debian_version,
        base_version=base_version,
        base_package_name=base_package_name,
        patched_version=lock["LIBSSL_VERSION"],
        vulnerable_version_recorded=lock["LIBSSL_VULNERABLE_VERSION"],
        expected_package_name=lock["LIBSSL_PACKAGE"],
    )

    result = PatchLifecycleResult(
        f"runtime security-patch lifecycle ({classification})", passed, detail,
    )
    print()
    print(result)
    print()

    if not passed:
        print(f"patch_lifecycle_check: FAIL ({classification})", file=sys.stderr)
        return 1

    print(f"patch_lifecycle_check: PASS ({classification})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
