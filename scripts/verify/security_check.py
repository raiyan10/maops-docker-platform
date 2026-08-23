#!/usr/bin/env python3
"""Runtime security verification for the built Docker image.

Every check is explicitly labeled with which kind of evidence it is, so a
reader never mistakes "we asked Docker to configure X" for "the kernel is
actually enforcing X":

  [A] source/config   - what the Dockerfile/compose.yaml declare
  [B] image inspection - `docker image inspect` on the built image
  [C] docker runtime    - `docker inspect` on a running container
  [D] kernel/process     - facts read from inside the running container's
                            own process/kernel state (e.g. /proc/1/status),
                            or a real attempted action (e.g. a write)

A [C] finding proves what Docker was *asked* to configure. A [D] finding
proves what the kernel is *actually enforcing*. Both are required before
this script claims a runtime protection is "verified" - see docs/security.md.

This script boots its own uniquely named, hardened, throwaway container
(read-only rootfs, all capabilities dropped, no-new-privileges), runs every
check against it, and always removes it - on success or failure. It never
touches any other Docker resource.

Also closes Day 1 finding M-2: `check_lifecycle_docker_stop()` issues a
real `docker stop` (real SIGTERM, bounded grace period) against the
running container and asserts a clean, fast exit - prior to this, every
script here only ever force-removed containers (`docker rm -f`), so a
broken SIGTERM handler would have produced zero automated failures
anywhere.

Day 4: the release image's final runtime is Distroless
(`gcr.io/distroless/python3-debian13:nonroot`), which has no shell and no
coreutils (`sh`, `cat`, `id`, `find`, ... are all absent by design - see
docs/build-security.md). Every `docker exec` probe below that used to
shell out to a coreutils binary (`cat /proc/1/status`, `id -u`/`id -g`,
`sh -c 'echo ... > path'`) now instead execs
`/usr/bin/python3.13 -c '<stdlib-only probe>'` directly - no shell
involved, matching the same [D] kernel/process evidence tier as before
via a different (shell-free) mechanism, never a weaker one.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGE_LABELS = {
    "org.opencontainers.image.title": "maops-docker-platform",
    "org.opencontainers.image.licenses": "MIT",
}
STARTUP_DEADLINE_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.5
HEALTHY_DEADLINE_SECONDS = 30.0

CAT_SOURCE = "A:source/config"
CAT_IMAGE = "B:image-inspection"
CAT_RUNTIME = "C:docker-runtime"
CAT_KERNEL = "D:kernel/process"

# Absolute interpreter path (Day 4): the Distroless final runtime has no
# shell to perform PATH resolution, so every in-container probe execs
# this directly rather than a bare "python3"/"python3.13" name.
PYTHON_BIN = "/usr/bin/python3.13"


def _write_rejected_probe_source(path: str) -> str:
    """Stdlib-only probe source: attempts a real write to `path`. Exit code
    mirrors normal Unix write-command convention (0 = write succeeded,
    nonzero = write failed/rejected) so call sites can keep testing
    `returncode != 0` exactly as they did against the prior `sh -c 'echo
    ... > path'` mechanism (which the shellless Distroless runtime cannot
    run) - only the mechanism changed, not the exit-code contract."""
    return (
        "import os, sys\n"
        f"path = {path!r}\n"
        "try:\n"
        "    with open(path, 'w') as f:\n"
        "        f.write('probe')\n"
        "except OSError as exc:\n"
        "    print(f'write rejected: {exc}')\n"
        "    sys.exit(1)\n"
        "else:\n"
        "    try:\n"
        "        os.remove(path)\n"
        "    except OSError:\n"
        "        pass\n"
        "    print('write UNEXPECTEDLY SUCCEEDED')\n"
        "    sys.exit(0)\n"
    )


class CheckResult:
    def __init__(self, category: str, name: str, passed: bool, detail: str) -> None:
        self.category = category
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{self.category}] {status} {self.name}: {self.detail}"


def load_dockerfile_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_dockerfile.py"
    spec = importlib.util.spec_from_file_location("check_dockerfile", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run_docker(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def docker_json(args: list[str]) -> object:
    result = run_docker(args)
    if result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


# --- [A] source/config -------------------------------------------------


def check_source_dockerfile_user() -> CheckResult:
    checker = load_dockerfile_checker()
    text = (REPO_ROOT / "docker" / "app" / "Dockerfile").read_text(encoding="utf-8")
    instructions = checker.parse_instructions(text)
    findings = checker.check_user(instructions)
    if findings:
        return CheckResult(
            CAT_SOURCE, "dockerfile declares non-root USER 10001:10001", False, str(findings[0])
        )
    return CheckResult(
        CAT_SOURCE, "dockerfile declares non-root USER 10001:10001", True, "USER 10001:10001"
    )


def check_source_healthcheck_declared() -> CheckResult:
    checker = load_dockerfile_checker()
    text = (REPO_ROOT / "docker" / "app" / "Dockerfile").read_text(encoding="utf-8")
    instructions = checker.parse_instructions(text)
    findings = checker.check_healthcheck(instructions)
    if findings:
        return CheckResult(CAT_SOURCE, "dockerfile declares HEALTHCHECK", False, str(findings[0]))
    return CheckResult(CAT_SOURCE, "dockerfile declares HEALTHCHECK", True, "present, not NONE")


# --- [B] image inspection -----------------------------------------------


def check_image_user(image: str) -> CheckResult:
    config = docker_json(["image", "inspect", image, "--format", "{{json .Config}}"])
    user = config.get("User", "")
    if user in ("10001", "10001:10001"):
        return CheckResult(CAT_IMAGE, "image Config.User is 10001:10001", True, repr(user))
    return CheckResult(CAT_IMAGE, "image Config.User is 10001:10001", False, repr(user))


def check_image_healthcheck(image: str) -> CheckResult:
    config = docker_json(["image", "inspect", image, "--format", "{{json .Config}}"])
    healthcheck = config.get("Healthcheck")
    if not healthcheck or not healthcheck.get("Test"):
        return CheckResult(CAT_IMAGE, "image declares a HEALTHCHECK", False, "no Healthcheck in image Config")
    test = healthcheck["Test"]
    if test and test[0] == "NONE":
        return CheckResult(CAT_IMAGE, "image declares a HEALTHCHECK", False, "Healthcheck is NONE")
    return CheckResult(CAT_IMAGE, "image declares a HEALTHCHECK", True, str(test))


def check_image_labels(image: str, version: str) -> CheckResult:
    """Also cross-checks org.opencontainers.image.version against VERSION exactly.

    This is what makes a forgotten `--build-arg VERSION=...` (silently
    falling back to the Dockerfile ARG's `0.0.0-unset` default) an
    automated release-check failure rather than a label nobody re-reads.
    """
    config = docker_json(["image", "inspect", image, "--format", "{{json .Config}}"])
    labels = config.get("Labels") or {}
    missing = [k for k, v in IMAGE_LABELS.items() if labels.get(k) != v]
    if "org.opencontainers.image.description" not in labels:
        missing.append("org.opencontainers.image.description")
    actual_version_label = labels.get("org.opencontainers.image.version")
    if actual_version_label != version:
        missing.append(
            f"org.opencontainers.image.version (expected {version!r}, got {actual_version_label!r})"
        )
    if missing:
        return CheckResult(
            CAT_IMAGE, "expected OCI labels present and version matches VERSION", False,
            f"missing/mismatched: {missing}",
        )
    return CheckResult(
        CAT_IMAGE, "expected OCI labels present and version matches VERSION", True,
        f"{sorted(labels.keys())}, version={actual_version_label}",
    )


FORBIDDEN_REPO_FILES = {".git", ".claude", ".github", "tests", "docs", "README.md", "scripts", "compose.yaml", ".dockerignore"}


def check_image_content_recursive(container_name: str) -> tuple[CheckResult, CheckResult]:
    """Extract /app from the container and recursively scan it (bounded to that tree only)."""
    with tempfile.TemporaryDirectory(prefix="maops-image-content-") as tmp_dir:
        extract_dir = Path(tmp_dir) / "app"
        result = run_docker(["cp", f"{container_name}:/app", str(extract_dir)])
        if result.returncode != 0:
            error = CheckResult(CAT_IMAGE, "image content extracted for inspection", False, result.stderr.strip())
            return error, error

        leaked_bytecode = recursive_find_forbidden_bytecode(extract_dir)
        bytecode_result = CheckResult(
            CAT_IMAGE,
            "no nested __pycache__/.pyc/.pyo content in image (recursive, bounded to extracted /app)",
            not leaked_bytecode,
            "clean" if not leaked_bytecode else f"leaked: {leaked_bytecode}",
        )

        present_repo_files = [name for name in FORBIDDEN_REPO_FILES if (extract_dir / name).exists()]
        repo_files_result = CheckResult(
            CAT_IMAGE,
            "repository-only files absent from image",
            not present_repo_files,
            "clean" if not present_repo_files else f"present: {present_repo_files}",
        )

        return bytecode_result, repo_files_result


def recursive_find_forbidden_bytecode(root: Path) -> list[str]:
    """Genuinely recursive (rglob), bounded to `root` only - never /proc, /sys, /dev.

    This replaces a prior implementation's one-level os.listdir() logic, which
    missed content nested more than one directory deep.
    """
    findings: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() and path.name == "__pycache__":
            findings.append(str(path.relative_to(root)))
        elif path.is_file() and path.suffix in (".pyc", ".pyo"):
            findings.append(str(path.relative_to(root)))
    return sorted(findings)


def regression_prove_recursive_detection() -> CheckResult:
    """Self-test: prove recursive_find_forbidden_bytecode() actually catches nested content.

    Builds a synthetic fixture tree (unrelated to the real image) with a
    __pycache__ nested three levels deep, and asserts detection succeeds -
    the exact case a one-level os.listdir() scan would silently miss.
    """
    with tempfile.TemporaryDirectory(prefix="maops-bytecode-regression-") as tmp_dir:
        root = Path(tmp_dir)
        nested = root / "a" / "b" / "c" / "__pycache__"
        nested.mkdir(parents=True)
        (nested / "probe.cpython-313.pyc").write_bytes(b"probe")

        findings = recursive_find_forbidden_bytecode(root)
        if not findings:
            return CheckResult(
                CAT_IMAGE,
                "regression: recursive bytecode scan catches nested content",
                False,
                "synthetic 3-levels-deep __pycache__ fixture was NOT detected",
            )
        return CheckResult(
            CAT_IMAGE,
            "regression: recursive bytecode scan catches nested content",
            True,
            f"synthetic fixture correctly detected: {findings}",
        )


# --- [C] docker runtime inspection --------------------------------------


def check_runtime_readonly_rootfs(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    value = host_config.get("ReadonlyRootfs")
    return CheckResult(CAT_RUNTIME, "container configured with read-only rootfs", bool(value), repr(value))


def check_runtime_cap_drop_all(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    cap_drop = host_config.get("CapDrop") or []
    passed = "ALL" in [c.upper() for c in cap_drop]
    return CheckResult(CAT_RUNTIME, "container configured with --cap-drop=ALL", passed, repr(cap_drop))


def check_runtime_no_new_privileges(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    security_opt = host_config.get("SecurityOpt") or []
    passed = any("no-new-privileges" in opt and "true" in opt for opt in security_opt)
    return CheckResult(CAT_RUNTIME, "container configured with no-new-privileges:true", passed, repr(security_opt))


def check_runtime_not_privileged(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    value = host_config.get("Privileged")
    return CheckResult(CAT_RUNTIME, "container is not --privileged", value is False, repr(value))


def check_runtime_no_host_pid(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    value = host_config.get("PidMode", "")
    return CheckResult(CAT_RUNTIME, "container does not use host PID namespace", value != "host", repr(value))


def check_runtime_no_host_network(container_name: str) -> CheckResult:
    host_config = docker_json(["inspect", container_name, "--format", "{{json .HostConfig}}"])
    value = host_config.get("NetworkMode", "")
    return CheckResult(CAT_RUNTIME, "container does not use host networking", value != "host", repr(value))


def check_runtime_no_docker_socket(container_name: str) -> CheckResult:
    mounts = docker_json(["inspect", container_name, "--format", "{{json .Mounts}}"])
    offenders = [
        m for m in mounts
        if "docker.sock" in str(m.get("Source", "")) or "docker.sock" in str(m.get("Destination", ""))
    ]
    return CheckResult(CAT_RUNTIME, "no Docker socket mounted into container", not offenders, repr(offenders) if offenders else "no such mount")


def check_runtime_healthy(container_name: str) -> CheckResult:
    deadline = time.monotonic() + HEALTHY_DEADLINE_SECONDS
    last_status = "unknown"
    while time.monotonic() < deadline:
        state = docker_json(["inspect", container_name, "--format", "{{json .State.Health}}"])
        last_status = (state or {}).get("Status", "unknown")
        if last_status == "healthy":
            return CheckResult(CAT_RUNTIME, "HEALTHCHECK reaches healthy state", True, last_status)
        time.sleep(POLL_INTERVAL_SECONDS)
    return CheckResult(CAT_RUNTIME, "HEALTHCHECK reaches healthy state", False, f"still {last_status} after {HEALTHY_DEADLINE_SECONDS}s")


# --- [D] kernel/process verification ------------------------------------


_CAT_PROC_1_STATUS_SOURCE = "from pathlib import Path; print(Path('/proc/1/status').read_text(), end='')"
_UID_GID_SOURCE = "import os; print(os.getuid()); print(os.getgid())"


def read_proc_1_status(container_name: str) -> dict[str, str]:
    result = run_docker(["exec", container_name, PYTHON_BIN, "-c", _CAT_PROC_1_STATUS_SOURCE])
    if result.returncode != 0:
        raise RuntimeError(f"docker exec (read /proc/1/status) failed: {result.stderr.strip()}")
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def check_kernel_effective_uid_gid(container_name: str) -> CheckResult:
    result = run_docker(["exec", container_name, PYTHON_BIN, "-c", _UID_GID_SOURCE])
    lines = result.stdout.splitlines()
    uid = lines[0].strip() if len(lines) > 0 else "?"
    gid = lines[1].strip() if len(lines) > 1 else "?"
    passed = uid == "10001" and gid == "10001"
    return CheckResult(CAT_KERNEL, "effective process UID:GID is 10001:10001", passed, f"uid={uid} gid={gid}")


def check_kernel_capabilities_effective(container_name: str) -> CheckResult:
    status = read_proc_1_status(container_name)
    cap_eff = status.get("CapEff", "?")
    cap_bnd = status.get("CapBnd", "?")
    cap_prm = status.get("CapPrm", "?")
    zero = "0000000000000000"
    passed = cap_eff == zero and cap_bnd == zero and cap_prm == zero
    return CheckResult(
        CAT_KERNEL,
        "effective/permitted/bounding capability sets are empty",
        passed,
        f"CapEff={cap_eff} CapPrm={cap_prm} CapBnd={cap_bnd}",
    )


def check_kernel_no_new_privs(container_name: str) -> CheckResult:
    status = read_proc_1_status(container_name)
    value = status.get("NoNewPrivs", "?")
    return CheckResult(CAT_KERNEL, "kernel NoNewPrivs flag is set", value == "1", f"NoNewPrivs={value}")


ROLE_HEALTHCHECK_MODULES = {
    "app": "app.healthcheck",
    "gateway": "gateway.healthcheck",
    "state": "state.healthcheck",
}


def healthcheck_module_for_role(role: str) -> str:
    """Map a service role name to its own healthcheck probe module.

    Closes Day 3 finding A-2 (day-03-security-review.md M-1 /
    day-03-test-review.md): the "service kept serving" half of
    check_kernel_readonly_write_fails() used to unconditionally probe
    app.healthcheck regardless of which role's container it was actually
    checking. This mapping is the single source of truth for the
    dispatch-by-role behavior, and is deliberately a pure function (no
    Docker call) so it has its own direct, Docker-free unit test.
    """
    try:
        return ROLE_HEALTHCHECK_MODULES[role]
    except KeyError as exc:
        raise ValueError(
            f"unknown role {role!r}, expected one of {sorted(ROLE_HEALTHCHECK_MODULES)}"
        ) from exc


def check_kernel_readonly_write_fails(container_name: str, port: int, role: str = "app") -> CheckResult:
    """Attempt a real prohibited rootfs write, then prove *that role's own*
    service is still serving - not merely that some service somewhere is.

    `role` selects the healthcheck module via healthcheck_module_for_role()
    (`app`/`gateway`/`state`), so a `state`- or `gateway`-role container is
    genuinely probed with its own `/healthz`, not app's.
    """
    probe_path = "/etc/maops-readonly-probe"
    write_result = run_docker(
        ["exec", container_name, PYTHON_BIN, "-c", _write_rejected_probe_source(probe_path)]
    )
    write_rejected = write_result.returncode != 0

    healthcheck_module = healthcheck_module_for_role(role)

    # The service must keep functioning after the rejected write attempt.
    still_serving = False
    try:
        conn_check = run_docker(["exec", container_name, PYTHON_BIN, "-m", healthcheck_module])
        still_serving = conn_check.returncode == 0
    except Exception:
        still_serving = False

    passed = write_rejected and still_serving
    detail = (
        f"write exit={write_result.returncode} stdout={write_result.stdout.strip()!r} "
        f"service_still_healthy={still_serving} (probed via {PYTHON_BIN} -m {healthcheck_module})"
    )
    return CheckResult(
        CAT_KERNEL, f"attempted write to read-only rootfs fails, {role} service keeps serving", passed, detail
    )


_CAT_PROC_1_CMDLINE_SOURCE = "from pathlib import Path; print(Path('/proc/1/cmdline').read_text(), end='')"


def check_kernel_pid1_identity(container_name: str) -> CheckResult:
    result = run_docker(["exec", container_name, PYTHON_BIN, "-c", _CAT_PROC_1_CMDLINE_SOURCE])
    expected = [PYTHON_BIN, "-m", "app"]
    if result.returncode != 0:
        return CheckResult(
            CAT_KERNEL, f"PID 1 process identity is {' '.join(expected)}", False, result.stderr.strip()
        )
    cmdline = [part for part in result.stdout.split("\x00") if part]
    return CheckResult(
        CAT_KERNEL, f"PID 1 process identity is {' '.join(expected)}", cmdline == expected, repr(cmdline)
    )


DOCKER_STOP_GRACE_SECONDS = 10


def check_lifecycle_docker_stop(container_name: str) -> CheckResult:
    """Automated regression protection for Day 1 finding M-2.

    Prior to this check, every script in this repository only ever
    force-removed its containers (`docker rm -f`, SIGKILL-equivalent), so
    a broken `signal.signal(SIGTERM, ...)` registration in app/server.py
    would have produced zero automated test failures anywhere - the only
    symptom would have been a silent fallback to Docker's full 10s grace
    period before SIGKILL, discoverable only by a human manually timing
    `docker stop`. This issues a real `docker stop` (real SIGTERM,
    bounded grace period) against the real running container and asserts
    a clean, fast exit. Must run last among checks that need the
    container running - it stops it.
    """
    started = time.monotonic()
    stop_result = run_docker(
        ["stop", "--time", str(DOCKER_STOP_GRACE_SECONDS), container_name],
        timeout=DOCKER_STOP_GRACE_SECONDS + 5.0,
    )
    elapsed = time.monotonic() - started
    if stop_result.returncode != 0:
        return CheckResult(
            CAT_RUNTIME, "docker stop exits cleanly within the grace period (exit code 0)",
            False, stop_result.stderr.strip(),
        )

    state = docker_json(["inspect", container_name, "--format", "{{json .State}}"])
    exit_code = state.get("ExitCode")
    status = state.get("Status")
    passed = exit_code == 0 and status == "exited" and elapsed < DOCKER_STOP_GRACE_SECONDS
    detail = f"exit_code={exit_code} status={status} elapsed={elapsed:.2f}s (grace={DOCKER_STOP_GRACE_SECONDS}s)"
    return CheckResult(
        CAT_RUNTIME, "docker stop exits cleanly within the grace period (exit code 0)", passed, detail
    )


# --- orchestration --------------------------------------------------------


def start_hardened_container(image: str, container_name: str) -> None:
    result = run_docker(
        [
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            "127.0.0.1::8080",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            image,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker run (hardened) failed: {result.stderr.strip()}")


def get_host_port(container_name: str) -> int:
    result = run_docker(["port", container_name, "8080/tcp"])
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"could not determine mapped port: {result.stderr.strip()}")
    return int(result.stdout.strip().splitlines()[0].rsplit(":", 1)[-1])


def wait_until_running(container_name: str, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        result = run_docker(["exec", container_name, PYTHON_BIN, "-m", "app.healthcheck"])
        if result.returncode == 0:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"container did not become reachable within {deadline_seconds}s")


def cleanup(container_name: str) -> None:
    run_docker(["rm", "-f", container_name], timeout=20.0)


def main() -> int:
    if shutil.which("docker") is None:
        print("security_check: docker CLI not found on PATH", file=sys.stderr)
        return 1

    version = read_version()
    image = f"maops-docker-platform:{version}"
    container_name = f"maops-security-{uuid.uuid4().hex[:12]}"

    print(f"security_check: image={image} container={container_name}")

    results: list[CheckResult] = []

    # Checks that don't need a running container.
    results.append(check_source_dockerfile_user())
    results.append(check_source_healthcheck_declared())
    results.append(check_image_user(image))
    results.append(check_image_healthcheck(image))
    results.append(check_image_labels(image, version))
    results.append(regression_prove_recursive_detection())

    try:
        start_hardened_container(image, container_name)
        wait_until_running(container_name, STARTUP_DEADLINE_SECONDS)
        port = get_host_port(container_name)
        print(f"security_check: hardened container reachable on 127.0.0.1:{port}")

        bytecode_result, repo_files_result = check_image_content_recursive(container_name)
        results.append(bytecode_result)
        results.append(repo_files_result)

        results.append(check_runtime_readonly_rootfs(container_name))
        results.append(check_runtime_cap_drop_all(container_name))
        results.append(check_runtime_no_new_privileges(container_name))
        results.append(check_runtime_not_privileged(container_name))
        results.append(check_runtime_no_host_pid(container_name))
        results.append(check_runtime_no_host_network(container_name))
        results.append(check_runtime_no_docker_socket(container_name))
        results.append(check_runtime_healthy(container_name))

        results.append(check_kernel_effective_uid_gid(container_name))
        results.append(check_kernel_capabilities_effective(container_name))
        results.append(check_kernel_no_new_privs(container_name))
        results.append(check_kernel_readonly_write_fails(container_name, port))
        results.append(check_kernel_pid1_identity(container_name))

        # Must run last: stops the container, so no check needing it
        # running can come after this one.
        results.append(check_lifecycle_docker_stop(container_name))

    finally:
        cleanup(container_name)

    print()
    for result in results:
        print(result)

    failures = [r for r in results if not r.passed]
    print()
    if failures:
        print(f"security_check: FAIL ({len(failures)}/{len(results)} checks failed)")
        return 1
    print(f"security_check: PASS ({len(results)}/{len(results)} checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
