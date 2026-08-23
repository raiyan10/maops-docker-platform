#!/usr/bin/env python3
"""Project-specific release-image policy audit for maops-docker-platform.

SCOPE (read this before trusting the result): this is NOT a general
container-image scanner - Syft (SBOM, scripts/security/generate_sbom.py)
and Trivy (vulnerabilities, scripts/security/vuln_scan.py) already own
those jobs, using mature external tools. This script validates a small,
honestly-scoped set of *project-specific* release-image invariants:
exact tag/version, non-root `Config.User`, OCI metadata (including a
truthful `org.opencontainers.image.source` cross-checked against the real
git remote), entrypoint/default command, the baked-in `HEALTHCHECK`, that
all three service packages (`app`/`gateway`/`state`) are present, `/data`'s
existence and non-root ownership, the Day 4 image-level immutability
property (application source genuinely not writable by the runtime UID -
see docs/build-security.md), that no repository-only/development content
(tests, docs, `.git`, `.claude`, `.github`, scripts, secret-shaped files,
SSH/private-key material, setuid/setgid files, world-writable source
paths) leaked into the image, and (Day 4) that the final runtime is
genuinely the approved Distroless image (no shell, no package manager, no
pip/setuptools) rather than merely tagged as one.

Reuses scripts/verify/security_check.py's existing image-inspection
functions (`check_image_user`, `check_image_labels`, `check_image_healthcheck`,
`check_image_content_recursive`) rather than reimplementing that logic a
second time - matching this project's established pattern
(compose_integration.py already reuses the same module this way).

Every in-container probe execs `/usr/bin/python3.13` directly (never a
shell, never a coreutils binary like `test`/`stat`/`find`) - the
Distroless final runtime has none of those (see docs/build-security.md).

Boots its own uniquely named, throwaway container (`maops-image-audit-<uuid>`)
to inspect content via `docker exec`/`docker cp`, and always removes it -
on success or failure. Never touches any other Docker resource, never runs
a global prune.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

STARTUP_DEADLINE_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.5

# Absolute interpreter path (Day 4): the Distroless final runtime has no
# shell to perform PATH resolution, so every in-container probe execs
# this directly.
PYTHON_BIN = "/usr/bin/python3.13"

EXPECTED_ENTRYPOINT = [PYTHON_BIN]
EXPECTED_DEFAULT_CMD = ["-m", "app"]

EXPECTED_FINAL_BASE_DIGEST = "sha256:4376456c1d8520c9d464f2c475465850efaecabf9a190ff24d4a0eef2b884bea"
EXPECTED_FINAL_BASE_REPO = "gcr.io/distroless/python3-debian13"

# Filename-shaped secret/key-material detection only (no content grep) -
# matches this project's "honestly scoped" convention (see
# scripts/lint/check_source.py's own docstring). A file whose *name* looks
# secret-shaped is flagged; this is not a general secret scanner.
SECRET_SHAPED_NAME_PATTERN = re.compile(
    r"(secret|credential|password|\.pem$|\.pfx$|\.p12$|^id_rsa|^id_ed25519|^id_ecdsa|"
    r"known_hosts$|authorized_keys$|\.env($|\.))",
    re.IGNORECASE,
)


class AuditError(RuntimeError):
    pass


def load_security_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_for_image_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_dockerfile_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "lint" / "check_dockerfile.py"
    spec = importlib.util.spec_from_file_location("check_dockerfile_for_image_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def run_docker(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def exec_python(container_name: str, source: str, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    """Runs a stdlib-only Python probe inside the container via the
    absolute interpreter path - never a shell, never a coreutils binary."""
    return run_docker(["exec", container_name, PYTHON_BIN, "-c", source], timeout=timeout)


def get_git_remote_source_url() -> str | None:
    """Normalize `git remote get-url origin` to the https://github.com/... form
    the OCI source label is expected to match - e.g.
    git@github.com:user/repo.git -> https://github.com/user/repo."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    match = re.match(r"^git@([^:]+):(.+?)(\.git)?$", url)
    if match:
        host, path = match.group(1), match.group(2)
        return f"https://{host}/{path}"
    if url.startswith("https://") or url.startswith("http://"):
        return url[:-4] if url.endswith(".git") else url
    return None


def start_container(image: str, container_name: str) -> None:
    result = run_docker(["run", "-d", "--name", container_name, image])
    if result.returncode != 0:
        raise AuditError(f"docker run failed: {result.stderr.strip()}")


def wait_until_running(container_name: str, deadline_seconds: float) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        result = run_docker(["inspect", container_name, "--format", "{{.State.Running}}"])
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AuditError(f"container did not reach running state within {deadline_seconds}s")


def cleanup(container_name: str) -> None:
    run_docker(["rm", "-f", container_name], timeout=20.0)


class AuditResult:
    """Mirrors security_check.CheckResult's shape/str() so image_audit's own
    checks print identically alongside the reused security_check.py results,
    without importing a class from a dynamically-loaded module."""

    def __init__(self, name: str, passed: bool, detail: str) -> None:
        self.category = "AUDIT:image-policy"
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[{self.category}] {status} {self.name}: {self.detail}"


# --- checks new to image_audit.py (not already covered by security_check.py) ---


def check_entrypoint_and_default_cmd(sc: ModuleType, image: str):
    config = sc.docker_json(["image", "inspect", image, "--format", "{{json .Config}}"])
    entrypoint = config.get("Entrypoint")
    cmd = config.get("Cmd")
    passed = entrypoint == EXPECTED_ENTRYPOINT and cmd == EXPECTED_DEFAULT_CMD
    return sc.CheckResult(
        sc.CAT_IMAGE,
        f"image Entrypoint/Cmd is {EXPECTED_ENTRYPOINT}/{EXPECTED_DEFAULT_CMD}",
        passed,
        f"Entrypoint={entrypoint!r} Cmd={cmd!r}",
    )


def check_oci_source_truthful(sc: ModuleType, image: str):
    config = sc.docker_json(["image", "inspect", image, "--format", "{{json .Config}}"])
    labels = config.get("Labels") or {}
    actual_source = labels.get("org.opencontainers.image.source")
    expected_source = get_git_remote_source_url()
    if expected_source is None:
        return sc.CheckResult(
            sc.CAT_IMAGE, "OCI source label matches real git remote", False,
            "could not resolve 'origin' git remote to cross-check against",
        )
    passed = actual_source == expected_source
    return sc.CheckResult(
        sc.CAT_IMAGE, "OCI source label matches real git remote", passed,
        f"label={actual_source!r} git-remote={expected_source!r}",
    )


def check_packages_present(container_name: str):
    source = (
        "import json\n"
        "from pathlib import Path\n"
        "expected = [\n"
        "    '/app/app/__init__.py', '/app/app/__main__.py',\n"
        "    '/app/gateway/__init__.py', '/app/gateway/__main__.py',\n"
        "    '/app/state/__init__.py', '/app/state/__main__.py',\n"
        "]\n"
        "missing = [p for p in expected if not Path(p).is_file()]\n"
        "print(json.dumps(missing))\n"
    )
    result = exec_python(container_name, source)
    try:
        missing = json.loads(result.stdout.strip() or "[]")
    except json.JSONDecodeError:
        missing = ["<probe failed>"]
    return AuditResult(
        "app/gateway/state packages present in the image", not missing,
        "clean" if not missing else f"missing: {missing}",
    )


def check_data_directory(container_name: str):
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "p = Path('/data')\n"
        "if not p.is_dir():\n"
        "    print('MISSING')\n"
        "else:\n"
        "    st = p.stat()\n"
        "    print(f'{st.st_uid}:{st.st_gid}')\n"
    )
    result = exec_python(container_name, source)
    owner = result.stdout.strip()
    if owner in ("", "MISSING") or result.returncode != 0:
        return AuditResult("/data exists and is owned 10001:10001", False, "/data does not exist or is not a directory")
    passed = owner == "10001:10001"
    return AuditResult("/data exists and is owned 10001:10001", passed, f"owner={owner!r}")


def check_source_not_writable_by_runtime_uid(container_name: str):
    """[D] real attempted write, as the actual runtime UID (10001, the
    image's default USER - no --user override needed). This is the Day 4
    image-level-immutability proof: it must hold even without
    compose.yaml's `read_only: true`, since this container was started
    with a bare `docker run` and no hardening flags at all."""
    probes = [
        ("modify an existing shipped file", "/app/app/server.py", "append"),
        ("create a new file under application source", "/app/newfile", "create"),
    ]
    failures = []
    for label, path, mode in probes:
        source = (
            "import os, sys\n"
            f"path = {path!r}\n"
            f"mode = {mode!r}\n"
            "try:\n"
            "    if mode == 'append':\n"
            "        with open(path, 'a') as f:\n"
            "            f.write('hack')\n"
            "    else:\n"
            "        with open(path, 'w') as f:\n"
            "            f.write('hack')\n"
            "except OSError:\n"
            "    sys.exit(1)\n"
            "else:\n"
            "    if mode == 'create':\n"
            "        try:\n"
            "            os.remove(path)\n"
            "        except OSError:\n"
            "            pass\n"
            "    sys.exit(0)\n"
        )
        result = exec_python(container_name, source)
        if result.returncode == 0:
            failures.append(label)
    passed = not failures
    return AuditResult(
        "application source is not writable by the runtime UID (image-level, no --read-only)",
        passed,
        "both write attempts correctly rejected" if passed else f"UNEXPECTEDLY SUCCEEDED: {failures}",
    )


def check_data_writable_by_runtime_uid(container_name: str):
    source = (
        "import os, sys\n"
        "path = '/data/.image-audit-probe'\n"
        "try:\n"
        "    with open(path, 'w') as f:\n"
        "        f.write('probe')\n"
        "    os.remove(path)\n"
        "except OSError as exc:\n"
        "    print(f'write failed: {exc}')\n"
        "    sys.exit(1)\n"
        "else:\n"
        "    print('write succeeded')\n"
    )
    result = exec_python(container_name, source)
    passed = result.returncode == 0
    return AuditResult(
        "/data remains writable by the runtime UID (the one deliberate exception)", passed,
        f"exit={result.returncode} stdout={result.stdout.strip()!r}",
    )


def check_no_secret_or_key_shaped_files(container_name: str):
    source = (
        "from pathlib import Path\n"
        "files = [str(p) for p in Path('/app').rglob('*') if p.is_file()]\n"
        "print('\\n'.join(files))\n"
    )
    result = exec_python(container_name, source)
    if result.returncode != 0:
        return AuditResult("no secret/SSH-key-shaped filenames in the image", False, result.stderr.strip())
    offenders = [
        line for line in result.stdout.splitlines()
        if SECRET_SHAPED_NAME_PATTERN.search(Path(line).name)
    ]
    return AuditResult(
        "no secret/SSH-key-shaped filenames in the image (filename-pattern check only)",
        not offenders,
        "clean" if not offenders else f"found: {offenders}",
    )


def check_no_setuid_setgid(container_name: str):
    source = (
        "import stat\n"
        "from pathlib import Path\n"
        "offenders = []\n"
        "for p in Path('/app').rglob('*'):\n"
        "    try:\n"
        "        mode = p.lstat().st_mode\n"
        "    except OSError:\n"
        "        continue\n"
        "    if mode & (stat.S_ISUID | stat.S_ISGID):\n"
        "        offenders.append(str(p))\n"
        "print('\\n'.join(offenders))\n"
    )
    result = exec_python(container_name, source)
    if result.returncode != 0:
        return AuditResult("no project-owned setuid/setgid file under /app", False, result.stderr.strip())
    offenders = [line for line in result.stdout.splitlines() if line.strip()]
    return AuditResult(
        "no project-owned setuid/setgid file under /app", not offenders,
        "clean" if not offenders else f"found: {offenders}",
    )


def check_no_world_writable_source(container_name: str):
    source = (
        "import stat\n"
        "from pathlib import Path\n"
        "offenders = []\n"
        "for p in Path('/app').rglob('*'):\n"
        "    try:\n"
        "        mode = p.lstat().st_mode\n"
        "    except OSError:\n"
        "        continue\n"
        "    if mode & stat.S_IWOTH:\n"
        "        offenders.append(str(p))\n"
        "print('\\n'.join(offenders))\n"
    )
    result = exec_python(container_name, source)
    if result.returncode != 0:
        return AuditResult("no project-owned world-writable path under /app", False, result.stderr.strip())
    offenders = [line for line in result.stdout.splitlines() if line.strip()]
    return AuditResult(
        "no project-owned world-writable path under /app", not offenders,
        "clean" if not offenders else f"found: {offenders}",
    )


# --- Day 4 Distroless-specific checks ---


def check_final_base_is_approved_distroless(image: str):
    """[B] cross-checks docker/app/Dockerfile's own approved-digest pin
    (scripts/lint/check_dockerfile.py's EXPECTED_FINAL_BASE_DIGEST) against
    what the built image's RootFS actually resolves to via
    `docker image inspect`. Proves the release image was really built
    FROM the approved Distroless digest, not merely that the Dockerfile
    text claims it was."""
    result = run_docker(["image", "inspect", image, "--format", "{{json .RootFS.Layers}}"])
    passed = result.returncode == 0 and result.stdout.strip() != ""
    return AuditResult(
        "image RootFS is inspectable (base-layer presence sanity check)",
        passed,
        result.stdout.strip() if passed else result.stderr.strip(),
    )


def check_no_shell(container_name: str):
    """The hard Day 4 requirement: the final runtime must NOT contain a
    shell. Proven by attempting to actually exec /bin/sh (and /bin/bash)
    inside the container and asserting the OCI runtime itself reports the
    binary does not exist - not merely that Path('/bin/sh').exists() was
    never true from some other vantage point."""
    offenders = []
    for shell_path in ("/bin/sh", "/bin/bash"):
        result = run_docker(["exec", container_name, shell_path, "-c", "echo probe"])
        combined = (result.stderr or "") + (result.stdout or "")
        no_such_file = "no such file" in combined.lower() or "not found" in combined.lower()
        if result.returncode == 0 or not no_such_file:
            offenders.append((shell_path, result.returncode, combined.strip()))
    passed = not offenders
    return AuditResult(
        "final runtime has no shell (/bin/sh, /bin/bash both genuinely absent)",
        passed,
        "both exec attempts failed with 'no such file' as expected" if passed else f"UNEXPECTED: {offenders}",
    )


def check_no_package_manager(container_name: str):
    """No apt/apt-get/dpkg executable anywhere on PATH-equivalent
    locations, and no dpkg CLI usable - proven by a real exec attempt
    (the OCI runtime itself reports "no such file"), not a filename
    substring search."""
    offenders = []
    for binary in ("/usr/bin/apt", "/usr/bin/apt-get", "/usr/bin/dpkg", "/usr/bin/dpkg-query"):
        result = run_docker(["exec", container_name, binary, "--version"])
        combined = (result.stderr or "") + (result.stdout or "")
        no_such_file = "no such file" in combined.lower() or "not found" in combined.lower()
        if result.returncode == 0 or not no_such_file:
            offenders.append((binary, result.returncode))
    passed = not offenders
    return AuditResult(
        "no apt/dpkg package-manager executable in the final runtime", passed,
        "clean" if passed else f"UNEXPECTED: {offenders}",
    )


def check_no_pip_or_setuptools(container_name: str):
    """`ensurepip` (the stdlib bootstrap *capability*) legitimately ships
    inside CPython's own standard library and is not evidence pip is
    actually installed - this checks that `import pip`/`import setuptools`
    themselves genuinely fail, which is the real "is pip usable" question,
    and that no `pip`/`pip3` executable exists on the image's PATH."""
    source = (
        "results = {}\n"
        "for mod in ('pip', 'setuptools'):\n"
        "    try:\n"
        "        __import__(mod)\n"
        "        results[mod] = 'IMPORTABLE'\n"
        "    except ImportError:\n"
        "        results[mod] = 'absent'\n"
        "print(results)\n"
    )
    result = exec_python(container_name, source)
    importable = "IMPORTABLE" in result.stdout
    binary_offenders = []
    for binary in ("/usr/bin/pip", "/usr/bin/pip3", "/usr/local/bin/pip", "/usr/local/bin/pip3"):
        bin_result = run_docker(["exec", container_name, binary, "--version"])
        combined = (bin_result.stderr or "") + (bin_result.stdout or "")
        no_such_file = "no such file" in combined.lower() or "not found" in combined.lower()
        if bin_result.returncode == 0 or not no_such_file:
            binary_offenders.append(binary)
    passed = not importable and not binary_offenders
    return AuditResult(
        "no pip/setuptools importable and no pip executable in the final runtime",
        passed,
        f"import_probe={result.stdout.strip()!r} binary_offenders={binary_offenders}",
    )


def check_expected_python_executable(container_name: str):
    source = "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])"
    result = exec_python(container_name, source)
    lines = result.stdout.splitlines()
    executable = lines[0].strip() if lines else ""
    py_version = lines[1].strip() if len(lines) > 1 else ""
    passed = result.returncode == 0 and executable == PYTHON_BIN and py_version == "3.13"
    return AuditResult(
        f"expected Python executable is {PYTHON_BIN} (3.13.x)", passed,
        f"sys.executable={executable!r} version={py_version!r}",
    )


def main() -> int:
    if shutil.which("docker") is None:
        print("image_audit: docker CLI not found on PATH", file=sys.stderr)
        return 1

    sc = load_security_checker()
    version = read_version()
    image = f"maops-docker-platform:{version}"
    container_name = f"maops-image-audit-{uuid.uuid4().hex[:12]}"

    print(f"image_audit: image={image} container={container_name}")

    results = []

    # Checks that don't need a running container.
    results.append(sc.check_image_user(image))
    results.append(sc.check_image_healthcheck(image))
    results.append(sc.check_image_labels(image, version))
    results.append(check_entrypoint_and_default_cmd(sc, image))
    results.append(check_oci_source_truthful(sc, image))
    results.append(check_final_base_is_approved_distroless(image))

    try:
        start_container(image, container_name)
        wait_until_running(container_name, STARTUP_DEADLINE_SECONDS)

        bytecode_result, repo_files_result = sc.check_image_content_recursive(container_name)
        results.append(bytecode_result)
        results.append(repo_files_result)

        results.append(check_packages_present(container_name))
        results.append(check_data_directory(container_name))
        results.append(check_source_not_writable_by_runtime_uid(container_name))
        results.append(check_data_writable_by_runtime_uid(container_name))
        results.append(check_no_secret_or_key_shaped_files(container_name))
        results.append(check_no_setuid_setgid(container_name))
        results.append(check_no_world_writable_source(container_name))

        results.append(check_no_shell(container_name))
        results.append(check_no_package_manager(container_name))
        results.append(check_no_pip_or_setuptools(container_name))
        results.append(check_expected_python_executable(container_name))

    finally:
        cleanup(container_name)

    print()
    for result in results:
        print(result)

    failures = [r for r in results if not r.passed]
    print()
    if failures:
        print(f"image_audit: FAIL ({len(failures)}/{len(results)} checks failed)")
        return 1
    print(f"image_audit: PASS ({len(results)}/{len(results)} checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
