#!/usr/bin/env python3
"""Real Docker reliability validation for maops-docker-platform (Day 5).

SCOPE: this is the dedicated runtime home for Day 5's new failure/resource/
restart/timeout behavior - `scripts/compose/compose_integration.py` keeps
owning everything it already proved (topology, DNS, network segmentation,
persistence, config mounting, runtime hardening, the H-1 3x3 healthcheck
matrix, startup ordering, and the existing state-stop/degrade/recover
scenario). This script does not duplicate any of that; it brings up its
own uniquely named Compose stack and proves the properties that are new
this day:

- CPU/memory/PID resource limits are genuinely applied to the real
  containers Compose creates (not merely declared in YAML) - Docker
  `HostConfig` values ([C]), cross-checked where available against the
  containers' own cgroup v2 files read from inside the container ([D]).
- The bounded `on-failure:3` restart policy is genuinely applied
  (`HostConfig.RestartPolicy`), and genuinely *behaves* as bounded: an
  unexpected crash triggers automatic restarts with no manual
  `docker start` anywhere in this script, up to (and correctly no further
  than) the configured maximum, while an intentional `docker stop` does
  not trigger it at all.

  The crash is a real, kernel-initiated `SIGKILL` (a cgroup memory-limit
  OOM-kill via `docker update --memory`, confirmed via `State.OOMKilled`)
  - deliberately **not** `docker kill`/`docker stop`. Empirically verified
  against this project's own Docker Desktop install (see
  `docs/reliability.md`): `docker kill`/`docker stop` mark a container as
  *manually* terminated, and Docker's restart-policy engine does not
  restart a manually terminated container regardless of exit code -
  `RestartCount` stays `0` forever, confirmed by direct experiment (a bare
  `docker run --restart on-failure:3 ...` container, killed with
  `docker kill`, never restarted even after 40s; the identical container
  OOM-killed by the kernel restarted correctly, `RestartCount` advancing
  automatically). A kernel-initiated OOM-kill is not only the *only*
  mechanism that actually exercises the restart-policy engine here - it
  is also a more honest simulation of "unexpected" than `docker kill`
  ever was: sending a kill signal from the host is definitionally an
  *intentional* action, not something a service does to itself.
- `stop_grace_period` is genuinely applied (`Config.StopTimeout`) and a
  `docker stop` against a real container completes cleanly (exit 0) well
  inside it.
- The Day 5 timeout-hierarchy config (`gateway/platform_config.py`,
  `app/platform_config.py`) genuinely closes Day 3 finding A-6: pausing
  `state` (a real stalled dependency, not a mock) proves `app`'s own inner
  timeout fires before `gateway`'s outer timeout would have, so the
  external caller's request completes in bounded time with a controlled
  failure - never a hang, never a raw traceback - and un-pausing recovers
  automatically.
- Liveness/readiness stay correctly separated under all of the above:
  `/healthz` never degrades because of a downstream failure; `/readyz`
  always does.

Like `compose_integration.py`, this script uses a uniquely named Compose
project (`maops-reliability-<uuid>`), a dynamic loopback host port, real
`time.monotonic()`-measured deadlines (no fixed sleeps used as a
correctness assertion - only short, explicitly bounded settle-polls), no
`shell=True`/`os.system`/`os.popen`, and tears its own stack down (`down
-t 10 -v`) in a `finally` block on every exit path, including a real
mid-run SIGTERM (converted to a catchable exception the same way
`compose_integration.py` does, so cleanup still runs). It never touches
another Compose project or Docker resource, and never runs a global prune.

Reuses `scripts/verify/security_check.py`'s own `CheckResult`/`run_docker`/
`docker_json`/evidence-tier constants and
`scripts/compose/compose_integration.py`'s general shape, rather than
reimplementing container-inspection plumbing a third time.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "compose.yaml"

# app/ and gateway/ are real importable packages at the repository root
# (not scripts/ tooling loaded via importlib) - used only to independently
# re-validate the *actual shipped* config/platform.json file's Day 5
# timeout-hierarchy invariant (see check_timeout_hierarchy_config() below),
# a pure, Docker-free config-coherence proof distinct from the synthetic-
# fixture unit tests in tests/test_gateway_platform_config.py.
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


class _TerminatedError(RuntimeError):
    """Mirrors compose_integration.py's own SIGTERM-safe-cleanup handler."""


def _handle_sigterm(signum: int, frame: object) -> None:
    raise _TerminatedError(f"received signal {signum} (SIGTERM)")


def _install_sigterm_handler() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)


PYTHON_BIN = "/usr/bin/python3.13"

UP_TIMEOUT_SECONDS = 150.0
LIFECYCLE_TIMEOUT_SECONDS = 30.0
DOWN_TIMEOUT_SECONDS = 60.0
STARTUP_HEALTHY_DEADLINE_SECONDS = 30.0
CRASH_RECOVERY_DEADLINE_SECONDS = 60.0
READYZ_DEADLINE_SECONDS = 30.0
STOP_SETTLE_WINDOW_SECONDS = 3.0
POLL_INTERVAL_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 5.0

# Day 6 (closes Day 5 finding L-1, day-05-health-timeout-review.md): a tight
# band around the configured inner timeout, not a loose ">= half of it"
# lower bound - see the inner_governed check below for the full rationale.
INNER_GOVERNED_LOWER_RATIO = 0.75
INNER_GOVERNED_UPPER_RATIO = 1.25

EXPECTED_CPUS = 0.50
EXPECTED_MEM_LIMIT_BYTES = 128 * 1024 * 1024
EXPECTED_PIDS_LIMIT = 64
EXPECTED_RESTART_POLICY_NAME = "on-failure"
EXPECTED_RESTART_MAX_ATTEMPTS = 3
EXPECTED_STOP_GRACE_PERIOD_SECONDS = 10

ALL_SERVICES = ("state", "app", "gateway")


class ReliabilityError(RuntimeError):
    pass


def load_security_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_for_reliability", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def compose(project: str, env: dict, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def get_actual_gateway_host_port(sc: ModuleType, container_name: str) -> int:
    result = sc.run_docker(["port", container_name, "8080/tcp"])
    if result.returncode != 0 or not result.stdout.strip():
        raise ReliabilityError(f"could not determine mapped port for {container_name}: {result.stderr.strip()}")
    line = result.stdout.strip().splitlines()[0]
    return int(line.rpartition(":")[2])


def get_container_image(sc: ModuleType, container_name: str) -> str:
    result = sc.run_docker(["inspect", container_name, "--format", "{{.Config.Image}}"])
    if result.returncode != 0:
        raise ReliabilityError(f"docker inspect {container_name} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_running(sc: ModuleType, container_name: str) -> bool:
    result = sc.run_docker(["inspect", container_name, "--format", "{{.State.Running}}"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def get_restart_count(sc: ModuleType, container_name: str) -> int:
    return int(sc.docker_json(["inspect", container_name, "--format", "{{json .RestartCount}}"]))


def http_get_json(port: int, path: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> tuple[int, dict, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReliabilityError(f"{path}: response body is not valid JSON: {body!r}") from exc
    return response.status, payload, text


def http_post_json(port: int, path: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> tuple[int, dict, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", path)
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReliabilityError(f"{path}: response body is not valid JSON: {body!r}") from exc
    return response.status, payload, text


def poll_until(predicate, deadline_seconds: float, description: str):
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        ok, last = predicate()
        if ok:
            return last
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ReliabilityError(f"{description} did not converge within {deadline_seconds}s: last={last}")


def poll_gateway_readyz(port: int, expect_ready: bool, deadline_seconds: float, timeout: float = REQUEST_TIMEOUT_SECONDS):
    def predicate():
        try:
            status, payload, _ = http_get_json(port, "/readyz", timeout=timeout)
            ready = status == 200 and payload.get("status") == "ready"
            return ready == expect_ready, (status, payload)
        except OSError as exc:
            if not expect_ready:
                return True, (0, {"status": "not-ready", "error": str(exc)})
            return False, (None, str(exc))

    return poll_until(predicate, deadline_seconds, "gateway /readyz reaching expected state")


def exec_healthcheck(sc: ModuleType, container_name: str, role: str) -> bool:
    module = sc.healthcheck_module_for_role(role)
    result = sc.run_docker(["exec", container_name, PYTHON_BIN, "-m", module])
    return result.returncode == 0


_LOCAL_READYZ_SOURCE = (
    "import http.client, json, sys\n"
    "conn = http.client.HTTPConnection('127.0.0.1', 8080, timeout={timeout})\n"
    "try:\n"
    "    conn.request('GET', '/readyz')\n"
    "    r = conn.getresponse()\n"
    "    body = r.read()\n"
    "finally:\n"
    "    conn.close()\n"
    "print(json.dumps({{'status_code': r.status, 'body': json.loads(body)}}))\n"
)


def exec_local_readyz(sc: ModuleType, container_name: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> tuple[int, dict]:
    """Calls a container's own /readyz from *inside* that same container -
    proof of that service's own readiness independent of whatever host
    port happens to be published (app has none)."""
    result = sc.run_docker(
        ["exec", container_name, PYTHON_BIN, "-c", _LOCAL_READYZ_SOURCE.format(timeout=timeout)],
        timeout=timeout + 5.0,
    )
    if result.returncode != 0:
        raise ReliabilityError(f"in-container /readyz probe failed in {container_name}: {result.stderr.strip()}")
    parsed = json.loads(result.stdout.strip())
    return parsed["status_code"], parsed["body"]


def print_result(result) -> None:
    print(f"  {result}")


# --- resource limits, restart policy, stop grace period [C]/[D] ----------


def check_resource_limits_applied(sc: ModuleType, containers: dict[str, str]):
    lines: list[str] = []
    mismatches: list[str] = []
    for name, container in containers.items():
        host_config = sc.docker_json(["inspect", container, "--format", "{{json .HostConfig}}"])
        nano_cpus = host_config.get("NanoCpus") or 0
        cpu_quota = host_config.get("CpuQuota") or 0
        cpu_period = host_config.get("CpuPeriod") or 0
        if nano_cpus:
            actual_cpus = nano_cpus / 1_000_000_000
        elif cpu_quota and cpu_period:
            actual_cpus = cpu_quota / cpu_period
        else:
            actual_cpus = 0.0

        memory = host_config.get("Memory") or 0
        pids_limit = host_config.get("PidsLimit") or 0

        lines.append(f"{name}: cpus={actual_cpus} memory={memory} pids_limit={pids_limit}")

        if abs(actual_cpus - EXPECTED_CPUS) > 0.01:
            mismatches.append(f"{name}: cpus={actual_cpus}, expected {EXPECTED_CPUS}")
        if memory != EXPECTED_MEM_LIMIT_BYTES:
            mismatches.append(f"{name}: memory={memory}, expected {EXPECTED_MEM_LIMIT_BYTES}")
        if pids_limit != EXPECTED_PIDS_LIMIT:
            mismatches.append(f"{name}: pids_limit={pids_limit}, expected {EXPECTED_PIDS_LIMIT}")

    passed = not mismatches
    detail = "; ".join(lines) if passed else "; ".join(lines) + " | MISMATCHES: " + "; ".join(mismatches)
    return sc.CheckResult(
        sc.CAT_RUNTIME,
        f"CPU/memory/PID limits applied to all three real containers "
        f"(cpus<={EXPECTED_CPUS}, mem<={EXPECTED_MEM_LIMIT_BYTES}B, pids<={EXPECTED_PIDS_LIMIT})",
        passed,
        detail,
    )


_CGROUP_PROBE_SOURCE = (
    "import json\n"
    "paths = {'memory_max': '/sys/fs/cgroup/memory.max', 'pids_max': '/sys/fs/cgroup/pids.max', "
    "'cpu_max': '/sys/fs/cgroup/cpu.max'}\n"
    "result = {}\n"
    "for key, path in paths.items():\n"
    "    try:\n"
    "        with open(path) as f:\n"
    "            result[key] = f.read().strip()\n"
    "    except OSError as exc:\n"
    "        result[key] = None\n"
    "print(json.dumps(result))\n"
)


def check_cgroup_v2_resource_limits(sc: ModuleType, containers: dict[str, str]):
    """Best-effort [D] proof, in addition to the mandatory [C] HostConfig
    check above: where cgroup v2 files are actually readable from inside
    the container (not guaranteed - depends on the host/Docker Desktop
    backend's cgroup configuration, per CLAUDE.md's explicit "do not
    assume cgroup representation" instruction), the kernel-enforced limits
    must independently agree with what Docker was asked to configure. A
    container where the path is genuinely unavailable is reported, not
    silently treated as passing, but also does not by itself fail this
    check - only an actually-wrong value does."""
    lines: list[str] = []
    mismatches: list[str] = []
    any_available = False

    for name, container in containers.items():
        result = sc.run_docker(["exec", container, PYTHON_BIN, "-c", _CGROUP_PROBE_SOURCE])
        if result.returncode != 0:
            lines.append(f"{name}: probe failed ({result.stderr.strip()})")
            continue
        try:
            probe = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            lines.append(f"{name}: probe returned non-JSON output")
            continue

        memory_max, pids_max, cpu_max = probe.get("memory_max"), probe.get("pids_max"), probe.get("cpu_max")
        lines.append(f"{name}: memory.max={memory_max!r} pids.max={pids_max!r} cpu.max={cpu_max!r}")

        if memory_max is not None:
            any_available = True
            if memory_max != str(EXPECTED_MEM_LIMIT_BYTES):
                mismatches.append(f"{name}: cgroup memory.max={memory_max!r}, expected {EXPECTED_MEM_LIMIT_BYTES!r}")
        if pids_max is not None:
            any_available = True
            if pids_max != str(EXPECTED_PIDS_LIMIT):
                mismatches.append(f"{name}: cgroup pids.max={pids_max!r}, expected {EXPECTED_PIDS_LIMIT!r}")
        if cpu_max is not None and cpu_max != "max":
            any_available = True
            parts = cpu_max.split()
            if len(parts) == 2:
                try:
                    quota, period = float(parts[0]), float(parts[1])
                    ratio = quota / period if period else 0.0
                except ValueError:
                    mismatches.append(f"{name}: cgroup cpu.max={cpu_max!r} not parseable")
                else:
                    if abs(ratio - EXPECTED_CPUS) > 0.02:
                        mismatches.append(f"{name}: cgroup cpu.max ratio={ratio}, expected ~{EXPECTED_CPUS}")
            else:
                mismatches.append(f"{name}: cgroup cpu.max={cpu_max!r} not parseable")

    passed = not mismatches
    availability_note = "cgroup v2 paths available" if any_available else "cgroup v2 paths NOT available in this environment (documented best-effort probe, not counted as a failure)"
    detail = f"{'; '.join(lines)} | {availability_note}"
    if mismatches:
        detail += " | MISMATCHES: " + "; ".join(mismatches)
    return sc.CheckResult(
        sc.CAT_KERNEL,
        "cgroup v2 files independently corroborate the configured resource limits (best-effort [D])",
        passed,
        detail,
    )


def check_restart_policy_applied(sc: ModuleType, containers: dict[str, str]):
    lines: list[str] = []
    mismatches: list[str] = []
    for name, container in containers.items():
        host_config = sc.docker_json(["inspect", container, "--format", "{{json .HostConfig}}"])
        policy = host_config.get("RestartPolicy") or {}
        actual_name = policy.get("Name")
        actual_max = policy.get("MaximumRetryCount")
        lines.append(f"{name}: Name={actual_name!r} MaximumRetryCount={actual_max!r}")
        if actual_name != EXPECTED_RESTART_POLICY_NAME or actual_max != EXPECTED_RESTART_MAX_ATTEMPTS:
            mismatches.append(f"{name}: {policy!r}")

    passed = not mismatches
    detail = "; ".join(lines) if passed else "; ".join(lines) + " | MISMATCHES: " + "; ".join(mismatches)
    return sc.CheckResult(
        sc.CAT_RUNTIME,
        f"bounded restart policy ({EXPECTED_RESTART_POLICY_NAME}:{EXPECTED_RESTART_MAX_ATTEMPTS}) applied to all three real containers",
        passed,
        detail,
    )


def check_stop_grace_period_applied(sc: ModuleType, containers: dict[str, str]):
    lines: list[str] = []
    mismatches: list[str] = []
    for name, container in containers.items():
        config = sc.docker_json(["inspect", container, "--format", "{{json .Config}}"])
        stop_timeout = config.get("StopTimeout")
        lines.append(f"{name}: StopTimeout={stop_timeout!r}")
        if stop_timeout != EXPECTED_STOP_GRACE_PERIOD_SECONDS:
            mismatches.append(f"{name}: StopTimeout={stop_timeout!r}, expected {EXPECTED_STOP_GRACE_PERIOD_SECONDS}")

    passed = not mismatches
    detail = "; ".join(lines) if passed else "; ".join(lines) + " | MISMATCHES: " + "; ".join(mismatches)
    return sc.CheckResult(
        sc.CAT_RUNTIME,
        f"stop_grace_period={EXPECTED_STOP_GRACE_PERIOD_SECONDS}s applied to all three real containers (Config.StopTimeout)",
        passed,
        detail,
    )


def check_timeout_hierarchy_config(sc: ModuleType):
    """Docker-free config-coherence proof against the *real, shipped*
    config/platform.json (not a synthetic unittest fixture) - closes Day 3
    finding A-6. Loading either module's config already raises ValueError
    if the invariant is violated (see gateway/platform_config.py), so a
    successful load here is itself part of the proof; the explicit
    re-check below makes the actual numbers visible in this report."""
    import app.platform_config as app_platform_config
    import gateway.platform_config as gateway_platform_config

    app_cfg = app_platform_config.load_platform_config(path=REPO_ROOT / "config" / "platform.json")
    gateway_cfg = gateway_platform_config.load_platform_config(path=REPO_ROOT / "config" / "platform.json")

    inner = app_cfg.state_dependency_timeout_seconds
    outer = gateway_cfg.gateway_upstream_timeout_seconds
    margin = gateway_cfg.timeout_safety_margin_seconds
    passed = outer > inner + margin
    detail = (
        f"state_dependency_timeout_seconds(inner)={inner} "
        f"gateway_upstream_timeout_seconds(outer)={outer} "
        f"timeout_safety_margin_seconds={margin} "
        f"(outer > inner + margin: {outer} > {inner + margin} == {passed})"
    )
    return sc.CheckResult(
        sc.CAT_SOURCE,
        "Day 5 timeout-hierarchy invariant holds against the real shipped config/platform.json",
        passed,
        detail,
    )


# --- Day 6/7 GitHub finding: bounded, monotonic, VERIFIED retry for
# `docker update` resource mutations -------------------------------------
#
# GitHub run 32960673438 proved the docker-container Buildx portability fix
# (docs/ci-cd.md) and then exercised the ENTIRE reliability harness
# correctly through Scenario 1's real transient PID 1 OOM crash and full
# automatic recovery - only to fail ~0.17s later, inside Scenario 2, on the
# very first `docker update --memory 6m --memory-swap 6m` issued against the
# just-restarted `state` container:
#
#   Error response from daemon: Cannot update container <id>:
#   runc did not terminate successfully: exit status 1:
#   openat2 /sys/fs/cgroup/system.slice/docker-<id>.scope/cgroup.controllers:
#   no such file or directory
#
# Day 7 (DAY6-POST-M2, docs/engineering-reviews/day-06-post-release-
# verification.md §7.2): a post-release evidence-commit CI run (run
# 33059581018, attempt 1) hit a CLOSELY RELATED but distinct variant of the
# exact same underlying race, this time against a different cgroup v2
# resource-controller file:
#
#   Error response from daemon: Cannot update container <id>:
#   runc did not terminate successfully: exit status 1:
#   openat2 /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.max:
#   no such file or directory
#
# Both are treated as GitHub-hosted-runner/runc/cgroup-v2 post-restart
# synchronization races until proven otherwise (a container that was just
# automatically restarted by the restart-policy engine can, on some Linux
# runner cgroup v2 hierarchies, have a brief window where runc's own
# per-controller-file bookkeeping for the new cgroup instance is not yet
# fully settled when a `docker update` lands) - NOT evidence that arbitrary
# `docker update` failures, or arbitrary missing-file errors, should ever be
# retried. The classifier below stays deliberately conservative: it
# requires the runc-termination wrapper phrase, a real `openat2 <path>: no
# such file or directory` failure (real ENOENT semantics, not merely the
# words "no such file or directory" appearing anywhere in the message), AND
# that the missing path both (a) lives under a real cgroup hierarchy
# directory and (b) names one of a small, deliberately restricted,
# explicitly enumerated set of cgroup v2 resource/controller files this
# project has ACTUALLY observed disappear transiently - `cgroup.controllers`
# and `memory.max` - never a broad "any cgroup-shaped filename" wildcard.
# `pids.max`/`cpu.max`/`memory.swap.max`/anything else is deliberately NOT
# accepted: it has never been observed, and accepting it on spec would widen
# this from "hardened against two known real GitHub Actions failures" to
# "retries most things that look vaguely like a cgroup file", which the Day
# 7 hardening requirement explicitly forbids. Every eventual "success" is
# independently re-verified via `docker inspect`, never inferred from exit
# code alone.

RESOURCE_UPDATE_RETRY_DEADLINE_SECONDS = 10.0
RESOURCE_UPDATE_RETRY_INTERVAL_SECONDS = 0.5

_MEMORY_STRING_SUFFIXES = {"b": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}


def _docker_memory_string_to_bytes(value: str) -> int:
    """Parses a `docker update --memory`/`--memory-swap` CLI value (a plain
    byte count, optionally suffixed `b`/`k`/`m`/`g`, or `-1` for unlimited)
    into the integer byte count `HostConfig.Memory`/`HostConfig.MemorySwap`
    reports - so callers that only have the CLI string (e.g. `"6m"`) don't
    have to separately compute the expected verification value by hand."""
    text = value.strip()
    suffix = text[-1].lower() if text else ""
    if suffix in _MEMORY_STRING_SUFFIXES:
        return int(text[:-1]) * _MEMORY_STRING_SUFFIXES[suffix]
    return int(text)


# The ONLY cgroup v2 resource/controller filenames this classifier accepts
# as evidence of the known transient post-restart race - deliberately
# narrow (see the block comment above). Extending this set requires a new,
# independently observed real GitHub Actions failure, not speculation.
_TRANSIENT_CGROUP_RACE_ACCEPTED_FILENAMES = frozenset({"cgroup.controllers", "memory.max"})

# Matches the real `openat2 <path>: no such file or directory` fragment
# Docker/runc emit for this class of error - requires the literal ENOENT
# wording, not merely its rough presence anywhere in the message.
_OPENAT2_ENOENT_PATTERN = re.compile(r"openat2\s+(?P<path>\S+):\s*no such file or directory", re.IGNORECASE)


def _is_transient_cgroup_update_race(stderr: str) -> bool:
    """Conservative classifier covering exactly the class of error GitHub
    runs 32960673438 and 33059581018 hit. ALL of the following must hold:

      1. the runc-termination wrapper phrase "runc did not terminate
         successfully" is present (proves this is a real runc failure, not
         merely an unrelated message that happens to mention a cgroup file);
      2. a genuine `openat2 <path>: no such file or directory` fragment is
         present (real ENOENT-on-openat2 semantics - a bare "no such file
         or directory" elsewhere in the message, with no openat2 syscall
         context, is common to many unrelated, genuinely non-retryable
         Docker/runc errors and is never by itself sufficient);
      3. the missing path's own directory component contains `/cgroup/`
         (real cgroup-hierarchy context - a `memory.max`/`cgroup.controllers`
         reference OUTSIDE a cgroup path is not this race); and
      4. the missing path's basename is one of
         `_TRANSIENT_CGROUP_RACE_ACCEPTED_FILENAMES` - deliberately NOT any
         other cgroup-shaped filename.

    This intentionally does NOT match "permission denied", "invalid memory
    limit", "invalid argument", "container not found", "Cannot connect to
    the Docker daemon", an unknown-flag/CLI-syntax error, or a missing file
    that merely happens to be named `memory.max`/`cgroup.controllers`
    somewhere outside a real cgroup path - all of those fail immediately,
    exactly as a real, non-transient error should."""
    text = stderr or ""
    if "runc did not terminate successfully" not in text:
        return False

    match = _OPENAT2_ENOENT_PATTERN.search(text)
    if match is None:
        return False

    path = match.group("path")
    if "/cgroup/" not in path:
        return False

    filename = path.rsplit("/", 1)[-1]
    return filename in _TRANSIENT_CGROUP_RACE_ACCEPTED_FILENAMES


def _inspect_host_config(sc: ModuleType, container: str, context: str) -> dict:
    result = sc.run_docker(["inspect", container, "--format", "{{json .HostConfig}}"])
    if result.returncode != 0:
        raise ReliabilityError(f"{context}: docker inspect {container} failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReliabilityError(f"{context}: docker inspect {container} returned non-JSON output: {result.stdout!r}") from exc


def update_container_resources_verified(
    sc: ModuleType,
    container: str,
    memory: str,
    memory_swap: str,
    *,
    expected_memory_bytes: int | None = None,
    expected_memory_swap_bytes: int | None = None,
    deadline_seconds: float = RESOURCE_UPDATE_RETRY_DEADLINE_SECONDS,
    retry_interval_seconds: float = RESOURCE_UPDATE_RETRY_INTERVAL_SECONDS,
    now=time.monotonic,
    sleep=time.sleep,
) -> dict:
    """Issues `docker update --memory <memory> --memory-swap <memory_swap>
    <container>` and returns only once the resulting `HostConfig.Memory`/
    `HostConfig.MemorySwap` are independently re-inspected and confirmed to
    hold the EXACT expected values - never inferring success from exit code
    alone (`expected_memory_bytes`/`expected_memory_swap_bytes` default to
    parsing `memory`/`memory_swap` themselves via
    `_docker_memory_string_to_bytes`, but a caller restoring to a value it
    already has as an int - e.g. the original `HostConfig.Memory` - should
    pass the exact expected ints directly rather than round-tripping through
    a string).

    Retry semantics, bounded by a real `time.monotonic()`-measured deadline
    (`now`/`sleep` are injectable for Docker-free unit testing - see
    tests/test_reliability_check.py):

    - `docker update` exits 0: the update's OWN `HostConfig` is inspected.
      Exact match -> return immediately. A mismatch is NOT retried (a
      "successful" update that produced the wrong values is a real
      verification failure, not the narrow transient race this helper
      exists for) - raises `ReliabilityError` immediately.
    - `docker update` exits non-zero with the EXACT narrow transient
      cgroup/runc race signature (`_is_transient_cgroup_update_race`):
      `HostConfig` is inspected before any retry, in case the mutation
      landed despite the non-zero exit (Docker/runc can genuinely return
      non-zero after a partial operation) - an exact match here returns
      immediately without reissuing `docker update`; a real mismatch
      re-checks the bounded deadline and either sleeps and retries, or
      raises `ReliabilityError` if the deadline has passed. A container
      that disappears mid-retry (the verification `docker inspect` itself
      fails) raises `ReliabilityError` immediately - it never keeps
      retrying against a container that may no longer exist.
    - `docker update` exits non-zero with ANY other error: raises
      `ReliabilityError` immediately - no retry, no retry storm.
    """
    if expected_memory_bytes is None:
        expected_memory_bytes = _docker_memory_string_to_bytes(memory)
    if expected_memory_swap_bytes is None:
        expected_memory_swap_bytes = _docker_memory_string_to_bytes(memory_swap)

    def _verified(host_config: dict) -> bool:
        return (
            host_config.get("Memory") == expected_memory_bytes
            and host_config.get("MemorySwap") == expected_memory_swap_bytes
        )

    action_desc = f"docker update {container} --memory {memory} --memory-swap {memory_swap}"
    deadline = now() + deadline_seconds
    attempt = 0

    while True:
        attempt += 1
        update_result = sc.run_docker(["update", "--memory", memory, "--memory-swap", memory_swap, container])

        if update_result.returncode == 0:
            host_config = _inspect_host_config(sc, container, f"{action_desc}: post-update verification (attempt {attempt})")
            if _verified(host_config):
                return {"attempts": attempt, "memory": host_config.get("Memory"), "memory_swap": host_config.get("MemorySwap")}
            raise ReliabilityError(
                f"{action_desc} reported success but HostConfig did not verify (attempt {attempt}): "
                f"Memory={host_config.get('Memory')!r} MemorySwap={host_config.get('MemorySwap')!r}, "
                f"expected Memory={expected_memory_bytes!r} MemorySwap={expected_memory_swap_bytes!r}"
            )

        stderr = (update_result.stderr or "").strip()
        if not _is_transient_cgroup_update_race(stderr):
            raise ReliabilityError(f"{action_desc} failed (attempt {attempt}, non-retryable): {stderr}")

        # Recognized narrow transient race: before retrying (or giving up),
        # check whether the mutation actually landed despite the non-zero
        # exit - avoids both a redundant blind retry and a false FAIL.
        host_config = _inspect_host_config(
            sc, container,
            f"{action_desc}: transient cgroup/runc race retry check (attempt {attempt}): {stderr}",
        )
        if _verified(host_config):
            return {
                "attempts": attempt,
                "memory": host_config.get("Memory"),
                "memory_swap": host_config.get("MemorySwap"),
                "note": "verified already applied despite non-zero docker update exit (transient cgroup/runc race)",
            }

        remaining = deadline - now()
        if remaining <= 0:
            raise ReliabilityError(
                f"{action_desc} did not succeed and verify within the {deadline_seconds}s bounded retry "
                f"deadline ({attempt} attempt(s)); last recognized transient error: {stderr}"
            )
        sleep(min(retry_interval_seconds, remaining))


def with_memory_shrink_restored(
    sc: ModuleType,
    container: str,
    memory: str,
    memory_swap: str,
    action,
    *,
    now=time.monotonic,
    sleep=time.sleep,
):
    """Shrinks `container`'s memory limit, captures its ORIGINAL values via a
    real `docker inspect`, invokes `action()`, and ALWAYS attempts to restore
    the original `--memory`/`--memory-swap` afterward - even if `action`
    raises. This is what SCENARIO 2's persistent-failure proof (below)
    depends on: a real container must never be left permanently
    resource-starved just because the assertion inside `action` failed or a
    `docker` subprocess call itself raised.

    Day 6 (closes Day 5 finding M-A, day-05-resource-restart-review.md, and
    the real GitHub run 32960673438 cgroup/runc finding above): BOTH the
    shrink and the restore now go through `update_container_resources_
    verified()` - a bounded, monotonic, independently-re-inspected retry,
    narrowly scoped to the exact transient cgroup/runc race GitHub's runner
    hit. Restoration remains a first-class VERIFIED invariant, never a
    warning-only best-effort: if it cannot be applied AND verified inside
    the bounded retry deadline, `update_container_resources_verified` raises
    `ReliabilityError` - `reliability-check` FAILS rather than silently
    reporting PASS while a container stays incorrectly constrained.

    Precedence when BOTH the wrapped action raised AND the restore ulimately
    failed/was not verified: the restore failure is raised (a permanently
    misconfigured container is the more urgent operational fact), with the
    action's own exception attached as its `__cause__` (`raise ... from
    action_exc`) so neither failure's diagnostics are lost - `str()` on the
    raised exception still names the restore failure, and the action's
    traceback remains visible via Python's own exception-chaining
    ("The above exception was the direct cause of the following
    exception:"). If the restore succeeds and verifies but the action
    raised, the action's own exception is re-raised unchanged. No exception
    is ever swallowed.

    A pure, Docker-mockable unit of the shape this project's other reusable
    check functions already take (`sc` first, real Docker calls only
    through `sc.docker_json`/`sc.run_docker`) - see
    tests/test_reliability_check.py for the injected-failure cleanup proof.
    `now`/`sleep` default to real `time.monotonic`/`time.sleep` (production
    behavior, unchanged) and are forwarded verbatim to both the shrink and
    restore calls into `update_container_resources_verified` - overridable
    purely so tests can exercise a bounded multi-retry sequence without any
    real fixed-time delay.
    """
    host_config = sc.docker_json(["inspect", container, "--format", "{{json .HostConfig}}"])
    original_memory = host_config.get("Memory")
    original_memory_swap = host_config.get("MemorySwap")

    shrink_info = update_container_resources_verified(sc, container, memory, memory_swap, now=now, sleep=sleep)
    print(
        f"reliability_check: shrank AND VERIFIED {container}'s memory limit to {memory} "
        f"(HostConfig Memory={shrink_info['memory']} MemorySwap={shrink_info['memory_swap']}, "
        f"{shrink_info['attempts']} attempt(s)) - the kernel will OOM-kill under this persistent condition"
    )

    action_exc: BaseException | None = None
    action_result = None
    try:
        action_result = action()
    except BaseException as exc:  # noqa: BLE001 - re-raised (or chained) below, never swallowed
        action_exc = exc

    try:
        restore_info = update_container_resources_verified(
            sc, container, str(original_memory), str(original_memory_swap),
            expected_memory_bytes=original_memory,
            expected_memory_swap_bytes=original_memory_swap,
            now=now, sleep=sleep,
        )
    except ReliabilityError as restore_exc:
        if action_exc is not None:
            raise restore_exc from action_exc
        raise

    print(
        f"reliability_check: restored AND VERIFIED {container}'s memory limit to {original_memory} bytes "
        f"(HostConfig Memory={restore_info['memory']} MemorySwap={restore_info['memory_swap']}, "
        f"{restore_info['attempts']} attempt(s))"
    )

    if action_exc is not None:
        raise action_exc

    return action_result


# Day 7 (DAY7-REL-M1, day-07-reliability-adversarial-review.md): a real
# `docker unpause` can transiently fail (daemon load, a Docker Desktop
# network blip, etc.). Previously, `state_is_paused` was cleared
# unconditionally right after the attempt regardless of its outcome - which
# meant the OUTER teardown `finally` (which only re-attempts an unpause
# `if state_is_paused:`) would never get a second try, and `compose down
# -t 10 -v` could run against a container that is still genuinely paused.
# Extracted as its own function (rather than left inline in `main()`) so
# this VERIFIED-success-only transition is Docker-free unit-testable -
# `main()` itself is not (it drives a real Compose stack end to end).
def _unpause_state_container(sc: ModuleType, container: str) -> bool:
    """Attempts a real `docker unpause` and returns whether it VERIFIABLY
    succeeded (`returncode == 0`) - never inferred any other way. A caller
    must only clear its own `state_is_paused` flag when this returns
    `True`; on `False`, the flag must be left `True` so a later teardown
    step gets a chance to retry before `compose down -v` runs. Never
    swallows the failure - prints the same WARNING to stderr as before this
    fix on any failed attempt, and never raises on its own."""
    unpause_result = sc.run_docker(["unpause", container])
    if unpause_result.returncode == 0:
        return True
    print(f"reliability_check: WARNING: docker unpause {container} failed: {unpause_result.stderr.strip()}", file=sys.stderr)
    return False


def main() -> int:
    _install_sigterm_handler()

    if shutil.which("docker") is None:
        print("reliability_check: docker CLI not found on PATH", file=sys.stderr)
        return 1

    sc = load_security_checker()
    version = read_version()
    image = f"maops-docker-platform:{version}"
    project = f"maops-reliability-{uuid.uuid4().hex[:12]}"

    env = dict(os.environ)
    env["VERSION"] = version
    env["GATEWAY_HOST_PORT"] = "0"

    state_container = f"{project}-state-1"
    app_container = f"{project}-app-1"
    gateway_container = f"{project}-gateway-1"
    containers = {"state": state_container, "app": app_container, "gateway": gateway_container}

    print(f"reliability_check: project={project} image={image}")

    results = []
    state_is_paused = False

    try:
        up_result = compose(project, env, ["up", "-d"], UP_TIMEOUT_SECONDS)
        if up_result.returncode != 0:
            raise ReliabilityError(f"docker compose up failed: {up_result.stderr.strip()}")

        for name, container in containers.items():
            actual_image = get_container_image(sc, container)
            if actual_image != image:
                raise ReliabilityError(f"{name}: container image is {actual_image!r}, expected {image!r}")

        for name, container in containers.items():
            health_result = sc.check_runtime_healthy(container)
            results.append(health_result)
            if not health_result.passed:
                raise ReliabilityError(f"{name} did not become healthy")
        print("reliability_check: state, app, gateway all reached Docker healthy state")

        host_port = get_actual_gateway_host_port(sc, gateway_container)
        print(f"reliability_check: gateway mapped to 127.0.0.1:{host_port}")

        # --- resource controls, restart policy, stop_grace_period, A-6 config ---

        results.append(check_resource_limits_applied(sc, containers))
        results.append(check_cgroup_v2_resource_limits(sc, containers))
        results.append(check_restart_policy_applied(sc, containers))
        results.append(check_stop_grace_period_applied(sc, containers))
        results.append(check_timeout_hierarchy_config(sc))
        for result in results[-5:]:
            print_result(result)

        # --- A-6 real adversarial proof: pause state, prove bounded controlled failure ---

        import gateway.platform_config as gateway_platform_config

        gateway_cfg = gateway_platform_config.load_platform_config(path=REPO_ROOT / "config" / "platform.json")
        outer_timeout = gateway_cfg.gateway_upstream_timeout_seconds
        inner_timeout = gateway_cfg.state_dependency_timeout_seconds
        client_timeout = outer_timeout + 5.0

        status, payload, _ = http_get_json(host_port, "/state", timeout=REQUEST_TIMEOUT_SECONDS)
        if status != 200:
            raise ReliabilityError(f"baseline GET /state failed before pause test: {status} {payload}")
        pre_pause_value = payload["value"]
        print(f"reliability_check: baseline persisted value before pause = {pre_pause_value}")

        pause_result = sc.run_docker(["pause", state_container])
        if pause_result.returncode != 0:
            raise ReliabilityError(f"docker pause {state_container} failed: {pause_result.stderr.strip()}")
        state_is_paused = True
        print(f"reliability_check: {state_container} paused (real stalled dependency)")

        try:
            app_live = exec_healthcheck(sc, app_container, "app")
            results.append(sc.CheckResult(sc.CAT_KERNEL, "app stays locally live while state is paused", app_live, f"app.healthcheck exit-0={app_live}"))

            gateway_live = exec_healthcheck(sc, gateway_container, "gateway")
            results.append(sc.CheckResult(sc.CAT_KERNEL, "gateway stays locally live while state is paused", gateway_live, f"gateway.healthcheck exit-0={gateway_live}"))

            app_readyz_status, app_readyz_body = exec_local_readyz(sc, app_container, timeout=client_timeout)
            app_unready = app_readyz_status == 503
            results.append(sc.CheckResult(sc.CAT_KERNEL, "app's own /readyz becomes unavailable while state is paused", app_unready, f"status={app_readyz_status} body={app_readyz_body}"))

            started = time.monotonic()
            gw_status, gw_payload, gw_text = http_get_json(host_port, "/readyz", timeout=client_timeout)
            gw_readyz_elapsed = time.monotonic() - started
            gw_unready = gw_status == 503
            results.append(sc.CheckResult(sc.CAT_KERNEL, "gateway's own /readyz becomes unavailable while state is paused", gw_unready, f"status={gw_status} body={gw_payload} elapsed={gw_readyz_elapsed:.2f}s"))

            started = time.monotonic()
            state_status, state_payload, state_text = http_get_json(host_port, "/state", timeout=client_timeout)
            state_request_elapsed = time.monotonic() - started

            controlled_failure = state_status == 503 and "Traceback" not in state_text
            results.append(sc.CheckResult(sc.CAT_KERNEL, "state-dependent request returns a controlled failure (no hang, no raw traceback) while state is paused", controlled_failure, f"status={state_status} body={state_payload} elapsed={state_request_elapsed:.2f}s"))

            bounded_by_outer = state_request_elapsed < outer_timeout
            results.append(sc.CheckResult(sc.CAT_KERNEL, f"failure completes inside the configured OUTER budget ({outer_timeout}s), not inner+outer stacked serially", bounded_by_outer, f"elapsed={state_request_elapsed:.2f}s outer_timeout={outer_timeout}s"))

            # Day 6 (closes Day 5 finding L-1, day-05-health-timeout-review.md):
            # tightened from a loose `>= inner_timeout * 0.5` lower bound (which
            # a much-larger-than-configured effective timeout could also have
            # passed) to a tight band around the CONFIGURED inner timeout
            # itself - `[inner_timeout * INNER_GOVERNED_LOWER_RATIO,
            # inner_timeout * INNER_GOVERNED_UPPER_RATIO]`. The independent
            # health-timeout review's own 5-trial repeated-pause probe measured
            # 2.008s-2.035s against a configured 2.0s inner timeout (a spread
            # of ~1.75%), so a +/-25% band is comfortably wide enough to absorb
            # realistic CI scheduler jitter while still failing if `app`
            # applied anywhere close to a materially different effective
            # timeout (e.g. the outer budget instead of the inner one).
            inner_governed = (
                inner_timeout * INNER_GOVERNED_LOWER_RATIO
                <= state_request_elapsed
                <= inner_timeout * INNER_GOVERNED_UPPER_RATIO
            )
            results.append(sc.CheckResult(
                sc.CAT_KERNEL,
                f"the wait was tightly governed by the CONFIGURED inner timeout ({inner_timeout}s), "
                f"not an instant failure and not a much-larger effective timeout",
                inner_governed,
                f"elapsed={state_request_elapsed:.2f}s inner_timeout={inner_timeout}s "
                f"expected_band=[{inner_timeout * INNER_GOVERNED_LOWER_RATIO:.2f}s, "
                f"{inner_timeout * INNER_GOVERNED_UPPER_RATIO:.2f}s]",
            ))

            for result in results[-7:]:
                print_result(result)
            print(f"reliability_check: A-6 closed - state-dependent request through gateway->app->state completed in {state_request_elapsed:.2f}s (inner={inner_timeout}s, outer={outer_timeout}s, safety_margin={gateway_cfg.timeout_safety_margin_seconds}s)")

        finally:
            if _unpause_state_container(sc, state_container):
                state_is_paused = False

        state_healthy_again = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_again)
        if not state_healthy_again.passed:
            raise ReliabilityError("state did not become healthy again after unpause")

        _, recovered_readyz = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz recovered after unpause ({recovered_readyz})")

        status, payload, _ = http_get_json(host_port, "/state")
        if status != 200 or payload.get("value") != pre_pause_value:
            raise ReliabilityError(f"value did not survive the pause/unpause cycle: expected {pre_pause_value}, got {status} {payload}")
        print(f"reliability_check: value survived pause/unpause unchanged (value={payload['value']})")

        # --- SCENARIO 1: a real TRANSIENT process crash -> genuine AUTOMATIC
        # recovery (the authoritative automatic-crash-recovery proof) ---
        #
        # Two mechanisms were independently tested and rejected before this
        # one, both against this project's own real Docker Desktop install
        # (see docs/reliability.md for the full experiment record):
        #
        #   1. `docker kill`/`docker stop` (daemon-API-initiated): dockerd
        #      tags these as a manual/intentional stop and never applies
        #      `on-failure` to them, regardless of exit code - RestartCount
        #      stayed 0 forever after a real `docker kill`, confirmed over a
        #      40s bounded observation.
        #   2. `docker exec <container> python3 -c "os.kill(1, SIGKILL)"`
        #      (an internal signal, sent from a sibling process in the SAME
        #      PID namespace as PID 1): confirmed to have NO effect at all
        #      (exit 0, container untouched) - this is real, documented
        #      Linux kernel behavior (see `man 7 pid_namespaces`): a PID
        #      namespace's init process only receives signals for which it
        #      has installed a handler, even SIGKILL/SIGSTOP, when the
        #      signal comes from a process inside the SAME namespace. Only
        #      an ancestor-namespace sender (i.e. the host/dockerd, which is
        #      exactly what `docker kill` is) can force it through - which
        #      is precisely mechanism 1, already ruled out above. Writing
        #      `/sys/fs/cgroup/memory.max` from inside the container was
        #      also tried and confirmed blocked (`EROFS` - the cgroup
        #      controller files are genuinely read-only from inside a
        #      hardened, non-privileged container).
        #
        # What DOES work, confirmed by direct experiment: a process CAN
        # write its OWN process's `/proc/1/oom_score_adj` from inside the
        # same container (same real UID as PID 1, no special capability
        # needed) to bias which process the kernel's OOM killer selects,
        # then generate enough memory pressure - from a throwaway sibling
        # process, never touching PID 1's own code or the container's
        # actual `mem_limit` (128m stays 128m throughout) - to trip the
        # *existing*, unmodified cgroup memory limit. The kernel's OOM
        # killer, not dockerd, selects PID 1 (its badness score is now
        # maximized) and sends it a real SIGKILL directly - this delivery
        # path bypasses the same-namespace signal-immunity rule above,
        # because it is not a `kill()` syscall between processes; it is the
        # kernel's own memory-accounting subsystem acting on the cgroup.
        # When PID 1 dies, the kernel's own PID-namespace teardown rule
        # (again `man 7 pid_namespaces`) SIGKILLs every other process left
        # in the namespace - including the throwaway sibling - which is why
        # the `docker exec` below is *expected* to itself exit non-zero.
        # Because the real `mem_limit` was never touched, the fresh
        # restarted process starts under completely normal conditions and
        # stays up - a genuinely TRANSIENT crash, confirmed reproducible
        # (`RestartCount` advanced by exactly 1 and the container reached
        # Docker-`healthy` again, with a real `docker events` "oom" -> "die"
        # -> "start" sequence occurring exactly once, no loop) - unlike
        # SCENARIO 2 below, which deliberately keeps the failure condition
        # present across every restart attempt.

        restart_count_before_transient_crash = get_restart_count(sc, state_container)
        baseline_value = payload["value"]

        transient_crash_source = (
            "import os\n"
            "with open('/proc/1/oom_score_adj', 'w') as f:\n"
            "    f.write('1000')\n"
            "data = bytearray()\n"
            "for _ in range(4000):\n"
            "    data += bytearray(1024 * 1024)\n"
        )
        # The exec'd process is expected to be killed alongside PID 1 (the
        # kernel's PID-namespace teardown rule, see comment above) - a
        # non-zero/negative returncode here is the SUCCESS signature, not a
        # script failure, and is deliberately never treated as one. Never
        # `docker kill`/`docker stop`/`docker start` anywhere in this block.
        transient_crash_result = sc.run_docker(
            ["exec", state_container, PYTHON_BIN, "-c", transient_crash_source], timeout=30.0
        )
        print(
            f"reliability_check: transient-crash exec on {state_container} returned "
            f"{transient_crash_result.returncode} (non-zero/killed is the expected success signature - "
            "PID 1 died to a real kernel OOM-kill, tearing down the throwaway sibling exec too)"
        )

        def _transient_restart_observed():
            count = get_restart_count(sc, state_container)
            return count > restart_count_before_transient_crash, count

        poll_until(
            _transient_restart_observed, CRASH_RECOVERY_DEADLINE_SECONDS,
            "state automatically restarting after a real transient PID 1 crash",
        )

        state_healthy_after_transient_crash = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_after_transient_crash)
        if not state_healthy_after_transient_crash.passed:
            raise ReliabilityError("state did not become healthy automatically after the transient crash")

        restart_count_after_transient_crash = get_restart_count(sc, state_container)
        transient_crash_recovered = restart_count_after_transient_crash == restart_count_before_transient_crash + 1
        results.append(sc.CheckResult(
            sc.CAT_KERNEL,
            "a real TRANSIENT PID 1 crash (kernel OOM-kill, PID 1's own oom_score_adj "
            "maxed from inside, mem_limit never touched, never docker kill/stop/start) "
            "triggers exactly one automatic restart",
            transient_crash_recovered,
            f"RestartCount before={restart_count_before_transient_crash} after={restart_count_after_transient_crash}",
        ))
        print_result(results[-1])
        if not transient_crash_recovered:
            raise ReliabilityError(f"transient-crash restart-count proof failed: {results[-1].detail}")
        print("reliability_check: state automatically restarted and became healthy again - no manual docker start anywhere in this path")

        app_readyz_status, app_readyz_body = exec_local_readyz(sc, app_container, timeout=REQUEST_TIMEOUT_SECONDS)
        app_readyz_recovered = app_readyz_status == 200 and app_readyz_body.get("status") == "ready"
        results.append(sc.CheckResult(sc.CAT_KERNEL, "app readiness recovers automatically after state's transient crash", app_readyz_recovered, f"status={app_readyz_status} body={app_readyz_body}"))
        print_result(results[-1])
        if not app_readyz_recovered:
            raise ReliabilityError(f"app /readyz did not recover automatically: {results[-1].detail}")

        _, recovered_readyz = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz recovered automatically after state's transient crash ({recovered_readyz})")

        status, payload, _ = http_get_json(host_port, "/state")
        value_preserved = status == 200 and payload.get("value") == baseline_value
        results.append(sc.CheckResult(sc.CAT_KERNEL, "persisted state value is unchanged across a real transient crash + automatic recovery", value_preserved, f"before_crash={baseline_value} after_recovery={payload.get('value')}"))
        print_result(results[-1])
        if not value_preserved:
            raise ReliabilityError(f"persisted value did not survive the transient crash: expected {baseline_value}, got {status} {payload}")

        status, payload, _ = http_post_json(host_port, "/state/increment")
        chain_works_again = status == 200 and payload.get("value") == baseline_value + 1
        results.append(sc.CheckResult(sc.CAT_KERNEL, "full gateway->app->state path works again after automatic transient-crash recovery", chain_works_again, f"status={status} body={payload}"))
        print_result(results[-1])
        if not chain_works_again:
            raise ReliabilityError(f"gateway->app->state chain did not work after transient-crash recovery: {status} {payload}")
        baseline_value = payload["value"]
        print(f"reliability_check: SCENARIO 1 (transient crash) complete - genuine automatic recovery proven end to end (value={baseline_value})")

        # --- SCENARIO 2: a PERSISTENT failure condition -> bounded retry
        # exhaustion, NOT automatic service recovery ---
        #
        # Deliberately different from Scenario 1: the memory limit itself is
        # lowered (`docker update --memory 6m`) and stays lowered across
        # every restart attempt, so this proves the *bound* on-failure:3
        # actually enforces (retries automatically up to exactly the
        # configured maximum, then correctly stops - never an infinite
        # crash loop) - it does NOT prove the service comes back on its
        # own, because under a genuinely persistent failure condition it
        # cannot: real operator intervention (restoring capacity, then an
        # explicit restart) is required once retries are exhausted, exactly
        # as a real bounded restart policy is supposed to work. Any
        # `docker update` resource mutation here is restored under
        # try/finally (`_with_memory_shrink_restored`) even if the
        # assertion below fails or a subprocess call raises.

        restart_count_before_persistent_failure = get_restart_count(sc, state_container)

        def _retries_exhausted():
            count = get_restart_count(sc, state_container)
            running = is_running(sc, state_container)
            return (count >= EXPECTED_RESTART_MAX_ATTEMPTS and not running), (count, running)

        def _wait_for_bounded_exhaustion():
            poll_until(
                _retries_exhausted, CRASH_RECOVERY_DEADLINE_SECONDS,
                "state's on-failure restart policy automatically retrying, then correctly "
                "exhausting its bounded attempts under a persistent failure condition",
            )
            oom_state = sc.docker_json(["inspect", state_container, "--format", "{{json .State}}"])
            restart_count_after = get_restart_count(sc, state_container)
            # `RestartCount` is a CUMULATIVE, lifetime counter for this one
            # container instance, not reset per crash episode - confirmed
            # empirically: Scenario 1's single transient crash above already
            # spent 1 of the 3 lifetime attempts `on-failure:3` allows, so
            # this persistent condition only gets 2 MORE retries before
            # hitting the same absolute cap (`before=1, after=3`, never
            # `before + 3`). The correct bound assertion is therefore against
            # the absolute configured maximum, not a delta from whatever
            # RestartCount happened to already be.
            bounded = (
                restart_count_after == EXPECTED_RESTART_MAX_ATTEMPTS
                and restart_count_after >= restart_count_before_persistent_failure
                and oom_state.get("OOMKilled") is True
                and not oom_state.get("Running")
            )
            results.append(sc.CheckResult(
                sc.CAT_KERNEL,
                f"a PERSISTENT kernel OOM condition triggers automatic restarts up to the "
                f"configured maximum ({EXPECTED_RESTART_MAX_ATTEMPTS}, a cumulative lifetime cap "
                "for this container, not per-episode) and never more - bounded, not an infinite "
                "crash loop; this is NOT an automatic-recovery proof (see Scenario 1) since the "
                "container remains stopped, requiring operator intervention",
                bounded,
                f"RestartCount before={restart_count_before_persistent_failure} after={restart_count_after} "
                f"(cap={EXPECTED_RESTART_MAX_ATTEMPTS}) OOMKilled={oom_state.get('OOMKilled')} Running={oom_state.get('Running')}",
            ))
            print_result(results[-1])
            if not bounded:
                raise ReliabilityError(f"persistent-failure bound proof failed: {results[-1].detail}")

        with_memory_shrink_restored(sc, state_container, "6m", "6m", _wait_for_bounded_exhaustion)
        print(
            f"reliability_check: SCENARIO 2 (persistent failure) complete - on-failure:"
            f"{EXPECTED_RESTART_MAX_ATTEMPTS} retried automatically {EXPECTED_RESTART_MAX_ATTEMPTS} times then "
            "correctly stopped (bounded, not infinite); memory limit restored; operator recovery next"
        )

        # Explicit OPERATOR recovery - only performed AFTER the bound is
        # already proven above (memory already restored to normal by
        # `with_memory_shrink_restored`'s own finally block). This is not
        # "manually starting the container during the automatic-recovery
        # proof": that proof is Scenario 1, above, and is already complete.
        crash_recovery_start = compose(project, env, ["start", "state"], LIFECYCLE_TIMEOUT_SECONDS)
        if crash_recovery_start.returncode != 0:
            raise ReliabilityError(f"docker compose start state (post-persistent-failure operator recovery) failed: {crash_recovery_start.stderr.strip()}")

        state_healthy_after_operator_recovery = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_after_operator_recovery)
        if not state_healthy_after_operator_recovery.passed:
            raise ReliabilityError("state did not become healthy after post-persistent-failure operator recovery")
        print("reliability_check: state healthy again after restored capacity + explicit operator restart")

        _, recovered_readyz = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz recovered after operator recovery ({recovered_readyz})")

        status, payload, _ = http_get_json(host_port, "/state")
        value_preserved = status == 200 and payload.get("value") == baseline_value
        results.append(sc.CheckResult(sc.CAT_KERNEL, "persisted state value is unchanged across the persistent-failure bound proof + operator recovery", value_preserved, f"before={baseline_value} after_recovery={payload.get('value')}"))
        print_result(results[-1])
        if not value_preserved:
            raise ReliabilityError(f"persisted value did not survive the persistent-failure cycle: expected {baseline_value}, got {status} {payload}")

        status, payload, _ = http_post_json(host_port, "/state/increment")
        chain_works_again = status == 200 and payload.get("value") == baseline_value + 1
        results.append(sc.CheckResult(sc.CAT_KERNEL, "full gateway->app->state path works again after operator recovery", chain_works_again, f"status={status} body={payload}"))
        print_result(results[-1])
        if not chain_works_again:
            raise ReliabilityError(f"gateway->app->state chain did not work after operator recovery: {status} {payload}")
        baseline_value = payload["value"]

        # --- intentional stop: must NOT auto-restart under on-failure ---

        restart_count_before_stop = get_restart_count(sc, state_container)

        stop_started = time.monotonic()
        stop_result = sc.run_docker(["stop", state_container], timeout=EXPECTED_STOP_GRACE_PERIOD_SECONDS + 15.0)
        stop_elapsed = time.monotonic() - stop_started
        if stop_result.returncode != 0:
            raise ReliabilityError(f"docker stop {state_container} failed: {stop_result.stderr.strip()}")

        state_after_stop = sc.docker_json(["inspect", state_container, "--format", "{{json .State}}"])
        graceful_exit = state_after_stop.get("ExitCode") == 0 and stop_elapsed < EXPECTED_STOP_GRACE_PERIOD_SECONDS
        results.append(sc.CheckResult(sc.CAT_KERNEL, f"docker stop exits cleanly (code 0) within the {EXPECTED_STOP_GRACE_PERIOD_SECONDS}s grace period (SIGTERM handled, no forced SIGKILL)", graceful_exit, f"ExitCode={state_after_stop.get('ExitCode')} elapsed={stop_elapsed:.2f}s"))
        print_result(results[-1])

        settle_deadline = time.monotonic() + STOP_SETTLE_WINDOW_SECONDS
        stayed_stopped = True
        while time.monotonic() < settle_deadline:
            if is_running(sc, state_container):
                stayed_stopped = False
                break
            time.sleep(POLL_INTERVAL_SECONDS)
        restart_count_after_stop_window = get_restart_count(sc, state_container)
        no_auto_restart = stayed_stopped and restart_count_after_stop_window == restart_count_before_stop
        results.append(sc.CheckResult(sc.CAT_RUNTIME, "an intentional docker stop does NOT trigger the on-failure restart policy", no_auto_restart, f"stayed_stopped={stayed_stopped} restart_count before={restart_count_before_stop} after={restart_count_after_stop_window}"))
        print_result(results[-1])
        if not no_auto_restart:
            raise ReliabilityError("state auto-restarted after an intentional stop - restart policy is not correctly bounded to on-failure")

        start_result = compose(project, env, ["start", "state"], LIFECYCLE_TIMEOUT_SECONDS)
        if start_result.returncode != 0:
            raise ReliabilityError(f"docker compose start state failed: {start_result.stderr.strip()}")

        state_healthy_after_explicit_start = sc.check_runtime_healthy(state_container)
        results.append(state_healthy_after_explicit_start)
        if not state_healthy_after_explicit_start.passed:
            raise ReliabilityError("state did not become healthy after explicit restart")

        _, recovered_readyz = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz recovered after explicit restart ({recovered_readyz})")

        status, payload, _ = http_get_json(host_port, "/state")
        if status != 200 or payload.get("value") != baseline_value:
            raise ReliabilityError(f"value did not survive intentional stop/start: expected {baseline_value}, got {status} {payload}")
        print(f"reliability_check: value survived intentional stop/explicit start unchanged (value={payload['value']})")

        # --- APP DOWN: gateway healthz stays 200, gateway readyz degrades ---

        app_stop_result = compose(project, env, ["stop", "app"], LIFECYCLE_TIMEOUT_SECONDS)
        if app_stop_result.returncode != 0:
            raise ReliabilityError(f"docker compose stop app failed: {app_stop_result.stderr.strip()}")

        gateway_still_live = exec_healthcheck(sc, gateway_container, "gateway")
        results.append(sc.CheckResult(sc.CAT_KERNEL, "gateway /healthz stays 200 while app is down", gateway_still_live, f"gateway.healthcheck exit-0={gateway_still_live}"))
        print_result(results[-1])

        _, degraded_readyz = poll_gateway_readyz(host_port, expect_ready=False, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz correctly degraded while app is down ({degraded_readyz})")

        app_start_result = compose(project, env, ["start", "app"], LIFECYCLE_TIMEOUT_SECONDS)
        if app_start_result.returncode != 0:
            raise ReliabilityError(f"docker compose start app failed: {app_start_result.stderr.strip()}")
        app_healthy_again = sc.check_runtime_healthy(app_container)
        results.append(app_healthy_again)
        if not app_healthy_again.passed:
            raise ReliabilityError("app did not become healthy after restart")
        _, recovered_readyz = poll_gateway_readyz(host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: gateway /readyz recovered after app restart ({recovered_readyz})")

        # --- GATEWAY DOWN: internal app/state remain unaffected ---

        gateway_stop_result = compose(project, env, ["stop", "gateway"], LIFECYCLE_TIMEOUT_SECONDS)
        if gateway_stop_result.returncode != 0:
            raise ReliabilityError(f"docker compose stop gateway failed: {gateway_stop_result.stderr.strip()}")

        app_unaffected = exec_healthcheck(sc, app_container, "app") and is_running(sc, state_container) and exec_healthcheck(sc, state_container, "state")
        results.append(sc.CheckResult(sc.CAT_KERNEL, "app and state remain unaffected while gateway is down", app_unaffected, f"app.healthcheck+state.healthcheck both PASS={app_unaffected}"))
        print_result(results[-1])

        gateway_start_result = compose(project, env, ["start", "gateway"], LIFECYCLE_TIMEOUT_SECONDS)
        if gateway_start_result.returncode != 0:
            raise ReliabilityError(f"docker compose start gateway failed: {gateway_start_result.stderr.strip()}")
        gateway_healthy_again = sc.check_runtime_healthy(gateway_container)
        results.append(gateway_healthy_again)
        if not gateway_healthy_again.passed:
            raise ReliabilityError("gateway did not become healthy after restart")

        new_host_port = get_actual_gateway_host_port(sc, gateway_container)
        _, recovered_readyz = poll_gateway_readyz(new_host_port, expect_ready=True, deadline_seconds=READYZ_DEADLINE_SECONDS)
        print(f"reliability_check: full chain recovered after gateway restart ({recovered_readyz})")

    except ReliabilityError as exc:
        print(f"reliability_check: FAIL: {exc}", file=sys.stderr)
        print()
        for result in results:
            print_result(result)
        return 1

    except _TerminatedError as exc:
        print(f"reliability_check: TERMINATED: {exc}", file=sys.stderr)
        print()
        for result in results:
            print_result(result)
        return 143

    finally:
        if state_is_paused:
            sc.run_docker(["unpause", state_container])
        down_result = compose(project, env, ["down", "-t", "10", "-v"], DOWN_TIMEOUT_SECONDS)
        if down_result.returncode != 0:
            print(f"reliability_check: WARNING: docker compose down -v failed for project {project}: {down_result.stderr.strip()}", file=sys.stderr)
        sys.stdout.flush()

    print()
    for result in results:
        print_result(result)

    failures = [r for r in results if not r.passed]
    print()
    if failures:
        print(f"reliability_check: FAIL ({len(failures)}/{len(results)} reliability checks failed)")
        return 1
    print(f"reliability_check: PASS ({len(results)}/{len(results)} reliability checks passed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
