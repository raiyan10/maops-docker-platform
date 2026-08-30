"""Docker-free unit tests for scripts/reliability/reliability_check.py's own
pure logic: the bounded-deadline poll helper, and the resource-limit/
restart-policy/stop-grace-period/timeout-hierarchy check functions' own
pass/fail evaluation against a fake, docker-free `sc` (mirroring
tests/test_compose_integration.py's own `_fake_sc()` pattern for the same
reason - these functions take `sc` as a parameter specifically so their
evaluation logic can be exercised without a real Docker daemon). The real
Docker-integration proof - that the values these functions read really are
what Compose applied to real containers - is `make reliability-check`'s
job, not unittest (see .claude/CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exact error GitHub Actions run 32960673438 hit, ~0.17s after Scenario
# 1's real transient-crash automatic-recovery proof had already completed -
# a `docker update --memory 6m --memory-swap 6m` against the just-restarted
# `state` container on a GitHub-hosted Linux runner. Reused verbatim by
# multiple test classes below as the real-world regression fixture.
GITHUB_RUN_32960673438_TRANSIENT_STDERR = (
    "Error response from daemon: Cannot update container "
    "3f8e2b1a9c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f: "
    "runc did not terminate successfully: exit status 1: "
    "openat2 /sys/fs/cgroup/system.slice/"
    "docker-3f8e2b1a9c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f.scope/cgroup.controllers: "
    "no such file or directory"
)

# Day 7 (DAY6-POST-M2): the real GitHub run 33059581018 (attempt 1, a
# post-release evidence-commit CI run, immediately after a genuine
# Scenario-1 OOM crash and automatic restart) hit a closely related but
# distinct variant of the same underlying post-restart runc/cgroup-v2
# synchronization race - `memory.max`, not `cgroup.controllers`.
GITHUB_RUN_33059581018_TRANSIENT_STDERR = (
    "Error response from daemon: Cannot update container "
    "7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b: "
    "runc did not terminate successfully: exit status 1: "
    "openat2 /sys/fs/cgroup/system.slice/"
    "docker-7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b.scope/memory.max: "
    "no such file or directory"
)


def load_reliability_check() -> ModuleType:
    path = REPO_ROOT / "scripts" / "reliability" / "reliability_check.py"
    spec = importlib.util.spec_from_file_location("reliability_check_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_security_checker() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_for_reliability_tests", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_sc() -> SimpleNamespace:
    sc = SimpleNamespace()
    sc.CAT_SOURCE = "A:source/config"
    sc.CAT_RUNTIME = "C:docker-runtime"
    sc.CAT_KERNEL = "D:kernel/process"
    sc.CheckResult = lambda category, name, passed, detail: SimpleNamespace(
        category=category, name=name, passed=passed, detail=detail
    )
    return sc


class PollUntilTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def test_returns_predicate_value_once_true(self) -> None:
        calls = {"count": 0}

        def predicate():
            calls["count"] += 1
            return calls["count"] >= 2, calls["count"]

        result = self.module.poll_until(predicate, deadline_seconds=5.0, description="test predicate")
        self.assertEqual(result, 2)

    def test_raises_reliability_error_when_deadline_exceeded(self) -> None:
        started = time.monotonic()
        with self.assertRaises(self.module.ReliabilityError):
            self.module.poll_until(lambda: (False, "never"), deadline_seconds=0.3, description="never-true predicate")
        elapsed = time.monotonic() - started
        # Bounded, not an indefinite hang.
        self.assertLess(elapsed, 5.0)

    def test_error_message_names_the_description(self) -> None:
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.poll_until(lambda: (False, "x"), deadline_seconds=0.2, description="my-predicate")
        self.assertIn("my-predicate", str(ctx.exception))


class CheckResourceLimitsAppliedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _sc(self, host_configs: dict[str, dict]) -> SimpleNamespace:
        sc = _fake_sc()
        sc.docker_json = lambda args: host_configs[args[1]]
        return sc

    def test_all_within_target_passes(self) -> None:
        host_config = {"NanoCpus": 500000000, "Memory": 128 * 1024 * 1024, "PidsLimit": 64}
        sc = self._sc({"state-c": host_config, "app-c": host_config, "gateway-c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "state-c", "app": "app-c", "gateway": "gateway-c"})
        self.assertTrue(result.passed)

    def test_cpu_quota_period_fallback_representation_passes(self) -> None:
        """Some Docker/cgroup-driver combinations report CpuQuota/CpuPeriod
        instead of NanoCpus - both must be accepted."""
        host_config = {"NanoCpus": 0, "CpuQuota": 50000, "CpuPeriod": 100000, "Memory": 128 * 1024 * 1024, "PidsLimit": 64}
        sc = self._sc({"c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "c"})
        self.assertTrue(result.passed)

    def test_missing_cpu_limit_fails(self) -> None:
        host_config = {"NanoCpus": 0, "Memory": 128 * 1024 * 1024, "PidsLimit": 64}
        sc = self._sc({"c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)
        self.assertIn("cpus", result.detail)

    def test_missing_memory_limit_fails(self) -> None:
        host_config = {"NanoCpus": 500000000, "Memory": 0, "PidsLimit": 64}
        sc = self._sc({"c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_missing_pids_limit_fails(self) -> None:
        host_config = {"NanoCpus": 500000000, "Memory": 128 * 1024 * 1024, "PidsLimit": 0}
        sc = self._sc({"c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_permissive_drift_beyond_target_fails(self) -> None:
        host_config = {"NanoCpus": 2_000_000_000, "Memory": 512 * 1024 * 1024, "PidsLimit": 4096}
        sc = self._sc({"c": host_config})
        result = self.module.check_resource_limits_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)


class CheckRestartPolicyAppliedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _sc(self, restart_policy: dict) -> SimpleNamespace:
        sc = _fake_sc()
        sc.docker_json = lambda args: {"RestartPolicy": restart_policy}
        return sc

    def test_on_failure_3_passes(self) -> None:
        sc = self._sc({"Name": "on-failure", "MaximumRetryCount": 3})
        result = self.module.check_restart_policy_applied(sc, {"state": "c"})
        self.assertTrue(result.passed)

    def test_always_policy_fails(self) -> None:
        sc = self._sc({"Name": "always", "MaximumRetryCount": 0})
        result = self.module.check_restart_policy_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_unless_stopped_policy_fails(self) -> None:
        sc = self._sc({"Name": "unless-stopped", "MaximumRetryCount": 0})
        result = self.module.check_restart_policy_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_unbounded_on_failure_retry_count_fails(self) -> None:
        sc = self._sc({"Name": "on-failure", "MaximumRetryCount": 0})
        result = self.module.check_restart_policy_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_missing_restart_policy_fails(self) -> None:
        sc = self._sc({})
        result = self.module.check_restart_policy_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)


class CheckStopGracePeriodAppliedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _sc(self, stop_timeout) -> SimpleNamespace:
        sc = _fake_sc()
        sc.docker_json = lambda args: {"StopTimeout": stop_timeout}
        return sc

    def test_expected_grace_period_passes(self) -> None:
        sc = self._sc(10)
        result = self.module.check_stop_grace_period_applied(sc, {"state": "c"})
        self.assertTrue(result.passed)

    def test_missing_grace_period_fails(self) -> None:
        sc = self._sc(None)
        result = self.module.check_stop_grace_period_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_wrong_grace_period_fails(self) -> None:
        sc = self._sc(0)
        result = self.module.check_stop_grace_period_applied(sc, {"state": "c"})
        self.assertFalse(result.passed)


class CheckCgroupV2ResourceLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _sc(self, stdout_by_container: dict[str, str]) -> SimpleNamespace:
        sc = _fake_sc()

        def fake_run_docker(args, timeout=20.0):
            container = args[1]
            return SimpleNamespace(returncode=0, stdout=stdout_by_container[container], stderr="")

        sc.run_docker = fake_run_docker
        return sc

    def test_matching_cgroup_values_pass(self) -> None:
        stdout = '{"memory_max": "134217728", "pids_max": "64", "cpu_max": "50000 100000"}'
        sc = self._sc({"c": stdout})
        result = self.module.check_cgroup_v2_resource_limits(sc, {"state": "c"})
        self.assertTrue(result.passed)

    def test_unavailable_paths_do_not_fail(self) -> None:
        """A host/backend where cgroup v2 files aren't visible from inside
        the container must not be reported as a resource-limit regression -
        CLAUDE.md is explicit that this probe is best-effort/environment-
        dependent, never assumed."""
        stdout = '{"memory_max": null, "pids_max": null, "cpu_max": null}'
        sc = self._sc({"c": stdout})
        result = self.module.check_cgroup_v2_resource_limits(sc, {"state": "c"})
        self.assertTrue(result.passed)

    def test_wrong_memory_max_fails(self) -> None:
        stdout = '{"memory_max": "999999999", "pids_max": "64", "cpu_max": "50000 100000"}'
        sc = self._sc({"c": stdout})
        result = self.module.check_cgroup_v2_resource_limits(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_wrong_pids_max_fails(self) -> None:
        stdout = '{"memory_max": "134217728", "pids_max": "4096", "cpu_max": "50000 100000"}'
        sc = self._sc({"c": stdout})
        result = self.module.check_cgroup_v2_resource_limits(sc, {"state": "c"})
        self.assertFalse(result.passed)

    def test_wrong_cpu_ratio_fails(self) -> None:
        stdout = '{"memory_max": "134217728", "pids_max": "64", "cpu_max": "100000 100000"}'
        sc = self._sc({"c": stdout})
        result = self.module.check_cgroup_v2_resource_limits(sc, {"state": "c"})
        self.assertFalse(result.passed)


class TransientCgroupUpdateRaceClassifierTests(unittest.TestCase):
    """Day 6 (GitHub run 32960673438): proves the narrow retryable-error
    classifier matches the REAL observed GitHub error, and does NOT match
    generic near-miss errors or genuinely unrelated `docker update`
    failures - the "no such file or directory" fragment alone is common to
    many non-retryable errors and must never by itself be treated as
    retryable."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    def test_real_github_run_32960673438_error_is_classified_as_transient(self) -> None:
        self.assertTrue(self.module._is_transient_cgroup_update_race(GITHUB_RUN_32960673438_TRANSIENT_STDERR))

    def test_real_github_run_33059581018_memory_max_error_is_classified_as_transient(self) -> None:
        """Day 7 (DAY6-POST-M2): the newly evidenced `memory.max`
        disappearance variant must now be accepted, alongside the
        original `cgroup.controllers` variant - not instead of it."""
        self.assertTrue(self.module._is_transient_cgroup_update_race(GITHUB_RUN_33059581018_TRANSIENT_STDERR))

    def test_unrelated_cgroup_controller_filename_is_deliberately_not_transient(self) -> None:
        """The accepted filename set is DELIBERATELY narrow - `pids.max`
        has never been observed to disappear transiently in this
        project's real CI evidence, and accepting it on spec (rather than
        on evidence) is exactly the "any cgroup-shaped filename" wildcard
        the Day 7 hardening requirement forbids, even though the rest of
        the error shape (runc phrase, openat2, real cgroup path context)
        is otherwise identical to an accepted variant."""
        stderr = GITHUB_RUN_32960673438_TRANSIENT_STDERR.replace("cgroup.controllers", "pids.max")
        self.assertFalse(self.module._is_transient_cgroup_update_race(stderr))

    def test_memory_max_outside_a_real_cgroup_path_is_not_transient(self) -> None:
        """An accepted FILENAME outside a real cgroup hierarchy path is
        not evidence of this race - the path context is required, not
        just the basename."""
        stderr = (
            "Error response from daemon: runc did not terminate successfully: exit status 1: "
            "openat2 /var/lib/docker/containers/abc123/memory.max: no such file or directory"
        )
        self.assertFalse(self.module._is_transient_cgroup_update_race(stderr))

    def test_openat2_without_enoent_wording_is_not_transient(self) -> None:
        """A real openat2 failure against an accepted cgroup filename that
        is NOT "no such file or directory" (e.g. a permissions problem)
        must never be treated as this transient race."""
        stderr = (
            "Error response from daemon: runc did not terminate successfully: exit status 1: "
            "openat2 /sys/fs/cgroup/system.slice/docker-abc.scope/memory.max: permission denied"
        )
        self.assertFalse(self.module._is_transient_cgroup_update_race(stderr))

    def test_generic_no_such_file_or_directory_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Error response from daemon: OCI runtime exec failed: exec failed: "
            'unable to start container process: exec: "/bad/path": stat /bad/path: no such file or directory'
        ))

    def test_runc_phrase_without_cgroup_controllers_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Error response from daemon: runc did not terminate successfully: exit status 1: "
            "some unrelated runc failure - file missing: no such file or directory"
        ))

    def test_cgroup_controllers_without_runc_phrase_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "a log line mentions cgroup.controllers and no such file or directory, "
            "but never the runc termination phrase"
        ))

    def test_permission_denied_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Error response from daemon: permission denied"
        ))

    def test_invalid_memory_limit_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Error response from daemon: Cannot update container: invalid memory limit, memory limit "
            "should be smaller than already set memoryswap limit"
        ))

    def test_invalid_argument_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Error response from daemon: invalid argument"
        ))

    def test_container_not_found_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race("Error: No such container: abc123"))

    def test_daemon_unavailable_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        ))

    def test_unknown_flag_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race("unknown flag: --bogus-flag"))

    def test_empty_stderr_is_not_transient(self) -> None:
        self.assertFalse(self.module._is_transient_cgroup_update_race(""))


class UpdateContainerResourcesVerifiedTests(unittest.TestCase):
    """Docker-free proof of the Day 6 GitHub-finding remediation
    (`update_container_resources_verified`): a bounded, monotonic retry for
    `docker update` resource mutations, narrowly scoped to the exact
    transient cgroup/runc race GitHub run 32960673438 hit. `now`/`sleep`
    are injected fakes throughout - no real fixed-time delay anywhere in
    this class."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _scripted_sc(self, responses: list[SimpleNamespace]) -> tuple[SimpleNamespace, list]:
        """`responses` is consumed in order for every `sc.run_docker` call
        - both the `update` and the `inspect` calls go through
        `run_docker`, matching the real interface these functions use."""
        sc = _fake_sc()
        calls: list[list[str]] = []
        queue = list(responses)

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            if not queue:
                raise AssertionError(f"unexpected extra run_docker call: {args}")
            return queue.pop(0)

        sc.run_docker = fake_run_docker
        return sc, calls

    @staticmethod
    def _ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    @classmethod
    def _inspect_ok(cls, memory: int, memory_swap: int) -> SimpleNamespace:
        return cls._ok(stdout=json.dumps({"Memory": memory, "MemorySwap": memory_swap}))

    @staticmethod
    def _fake_clock(start: float = 0.0):
        state = {"t": start}

        def now() -> float:
            return state["t"]

        def sleep(seconds: float) -> None:
            state["t"] += seconds

        return now, sleep

    # A: first update succeeds + exact verification -> PASS
    def test_first_update_succeeds_and_verifies(self) -> None:
        sc, calls = self._scripted_sc([self._ok(returncode=0), self._inspect_ok(6291456, 6291456)])
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(calls), 2)

    # B: exact GitHub transient error once, then success + verify -> PASS
    def test_github_transient_error_then_success_retries_and_verifies(self) -> None:
        sc, calls = self._scripted_sc([
            self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR),
            self._inspect_ok(134217728, -1),  # retry check: still old values
            self._ok(returncode=0),
            self._inspect_ok(6291456, 6291456),
        ])
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(calls), 4)

    # B2: the newly evidenced memory.max transient variant retries and
    # verifies exactly like the original cgroup.controllers variant.
    def test_memory_max_transient_error_then_success_retries_and_verifies(self) -> None:
        sc, calls = self._scripted_sc([
            self._ok(returncode=1, stderr=GITHUB_RUN_33059581018_TRANSIENT_STDERR),
            self._inspect_ok(134217728, -1),  # retry check: still old values
            self._ok(returncode=0),
            self._inspect_ok(6291456, 6291456),
        ])
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(calls), 4)

    # C: several transient failures then success before the deadline -> PASS
    def test_several_transient_failures_then_success_before_deadline(self) -> None:
        responses: list[SimpleNamespace] = []
        for _ in range(3):
            responses.append(self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR))
            responses.append(self._inspect_ok(134217728, -1))
        responses.append(self._ok(returncode=0))
        responses.append(self._inspect_ok(6291456, 6291456))
        sc, calls = self._scripted_sc(responses)
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(
            sc, "c", "6m", "6m", now=now, sleep=sleep, deadline_seconds=10.0, retry_interval_seconds=0.5,
        )
        self.assertEqual(result["attempts"], 4)
        self.assertLessEqual(now(), 10.0)

    # D: transient failures continue until the bounded deadline -> FAIL
    def test_transient_failures_continue_until_deadline_fails(self) -> None:
        sc = _fake_sc()
        calls: list[list[str]] = []

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            if args[0] == "update":
                return self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR)
            return self._inspect_ok(134217728, -1)  # never matches the 6m target

        sc.run_docker = fake_run_docker
        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError):
            self.module.update_container_resources_verified(
                sc, "c", "6m", "6m", now=now, sleep=sleep, deadline_seconds=2.0, retry_interval_seconds=0.5,
            )
        self.assertGreaterEqual(now(), 2.0)
        # Bounded retries (deadline / interval + a couple), not a runaway loop.
        self.assertLess(len(calls), 40)

    # E: unrelated docker update error -> immediate FAIL, no retry storm
    def test_unrelated_error_fails_immediately_with_no_retry(self) -> None:
        sc, calls = self._scripted_sc([self._ok(returncode=1, stderr="Error response from daemon: invalid argument")])
        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertIn("invalid argument", str(ctx.exception))
        self.assertEqual(len(calls), 1, "an unrelated error must never trigger a retry")

    # F: docker command reports success but HostConfig values mismatch -> FAIL
    def test_success_but_hostconfig_mismatch_fails(self) -> None:
        sc, calls = self._scripted_sc([self._ok(returncode=0), self._inspect_ok(999999, 999999)])
        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertIn("999999", str(ctx.exception))
        self.assertEqual(len(calls), 2, "a reported-success mismatch must not be retried")

    # G: retryable command error but inspect shows the desired values were
    # already applied -> handle safely and deterministically, no extra update
    def test_transient_error_but_already_applied_returns_without_extra_update(self) -> None:
        sc, calls = self._scripted_sc([
            self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR),
            self._inspect_ok(6291456, 6291456),  # already applied despite the non-zero exit
        ])
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertEqual(len(calls), 2, "must not blindly issue a second update once already verified")
        self.assertIn("note", result)

    # H: container disappears during retry -> FAIL
    def test_container_disappears_during_retry_fails(self) -> None:
        sc, calls = self._scripted_sc([
            self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR),
            self._ok(returncode=1, stdout="", stderr="Error: No such container: c"),
        ])
        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.update_container_resources_verified(sc, "c", "6m", "6m", now=now, sleep=sleep)
        self.assertIn("No such container", str(ctx.exception))
        self.assertEqual(len(calls), 2)

    def test_explicit_expected_bytes_used_over_parsed_string(self) -> None:
        """A caller restoring to a known int (e.g. the original
        HostConfig.Memory) passes it directly rather than round-tripping
        through a parsed string - proves the explicit kwargs win."""
        sc, calls = self._scripted_sc([self._ok(returncode=0), self._inspect_ok(134217728, -1)])
        now, sleep = self._fake_clock()
        result = self.module.update_container_resources_verified(
            sc, "c", "134217728", "-1",
            expected_memory_bytes=134217728, expected_memory_swap_bytes=-1,
            now=now, sleep=sleep,
        )
        self.assertEqual(result["attempts"], 1)


class DockerMemoryStringToBytesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_reliability_check()

    def test_megabyte_suffix(self) -> None:
        self.assertEqual(self.module._docker_memory_string_to_bytes("6m"), 6 * 1024 * 1024)

    def test_gigabyte_suffix(self) -> None:
        self.assertEqual(self.module._docker_memory_string_to_bytes("1g"), 1024 * 1024 * 1024)

    def test_plain_byte_count(self) -> None:
        self.assertEqual(self.module._docker_memory_string_to_bytes("134217728"), 134217728)

    def test_unlimited_sentinel(self) -> None:
        self.assertEqual(self.module._docker_memory_string_to_bytes("-1"), -1)


class WithMemoryShrinkRestoredTests(unittest.TestCase):
    """Docker-free proof of the Day 5/6 crash-remediation cleanup guarantee:
    scripts/reliability/reliability_check.py's SCENARIO 2 (persistent
    failure) shrinks a real container's memory limit and MUST restore it
    under try/finally even if the wrapped action raises - a real container
    must never be left permanently resource-starved just because an
    assertion inside SCENARIO 2 failed. This is exercised here purely
    against a fake `sc` (a spy recording every `docker` argv it was asked
    to run) - the real Docker-integration proof (an actual `docker update`
    round-trip) is `make reliability-check`'s job, not unittest.

    Day 6 (closes Day 5 finding M-A, day-05-resource-restart-review.md, and
    the real GitHub run 32960673438 cgroup/runc finding): BOTH the shrink
    and the restore now go through `update_container_resources_verified` -
    a failed restore command, a restore that "succeeds" but the container's
    re-inspected HostConfig doesn't actually match the original values, or
    a restore that exhausts its bounded transient-race retry deadline, all
    still raise ReliabilityError (never a warning-only stderr print)."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _scripted_sc(self, initial_host_config: dict, run_docker_responses: list[SimpleNamespace]) -> tuple[SimpleNamespace, list]:
        sc = _fake_sc()
        calls: list[list[str]] = []

        def fake_docker_json(args):
            calls.append(list(args))
            return initial_host_config

        queue = list(run_docker_responses)

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            if not queue:
                raise AssertionError(f"unexpected extra run_docker call: {args}")
            return queue.pop(0)

        sc.docker_json = fake_docker_json
        sc.run_docker = fake_run_docker
        return sc, calls

    @staticmethod
    def _ok(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    @classmethod
    def _inspect_ok(cls, memory: int, memory_swap: int) -> SimpleNamespace:
        return cls._ok(stdout=json.dumps({"Memory": memory, "MemorySwap": memory_swap}))

    @staticmethod
    def _fake_clock(start: float = 0.0):
        state = {"t": start}

        def now() -> float:
            return state["t"]

        def sleep(seconds: float) -> None:
            state["t"] += seconds

        return now, sleep

    def test_successful_shrink_and_restore_returns_action_result(self) -> None:
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),   # shrink
                self._ok(returncode=0), self._inspect_ok(134217728, -1),      # restore
            ],
        )
        result = self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "ok")
        self.assertEqual(result, "ok")

    def test_action_failure_and_successful_restore_reraises_action_exception(self) -> None:
        """The core injected-failure cleanup proof: a real assertion
        failure inside the wrapped action must not skip the memory
        restore, and (since the restore succeeds and verifies) the
        action's own exception is what propagates."""
        sc, calls = self._scripted_sc(
            {"Memory": 99999999, "MemorySwap": 200000000},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),
                self._ok(returncode=0), self._inspect_ok(99999999, 200000000),
            ],
        )

        def _boom():
            raise self.module.ReliabilityError("simulated assertion failure inside SCENARIO 2")

        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom)
        self.assertIn("simulated assertion failure", str(ctx.exception))

    def test_restores_original_values_even_when_action_raises_unexpected_exception(self) -> None:
        """Not just ReliabilityError - ANY exception from the wrapped
        action must still trigger the restore (a bare `finally`-equivalent,
        no narrow `except`)."""
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),
                self._ok(returncode=0), self._inspect_ok(134217728, -1),
            ],
        )

        def _boom():
            raise RuntimeError("some other unexpected failure")

        with self.assertRaises(RuntimeError):
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom)

    def test_shrink_failure_raises_reliability_error(self) -> None:
        sc, _ = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [self._ok(returncode=1, stderr="invalid argument")],
        )
        with self.assertRaises(self.module.ReliabilityError):
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "unreachable")

    def test_restore_command_failure_raises_reliability_error(self) -> None:
        """Day 6 M-A: a non-retryable failed restore `docker update` call
        must FAIL the check, not merely warn to stderr - the action's own
        success must not mask a permanently misconfigured container."""
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),
                self._ok(returncode=1, stderr="Error: No such container: c"),
            ],
        )
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "action-result")
        self.assertIn("No such container", str(ctx.exception))

    def test_restore_verification_mismatch_raises_reliability_error(self) -> None:
        """Day 6 M-A: the restore `docker update` call can report exit 0
        while the container's real HostConfig still doesn't match the
        original values - this must be independently re-verified via a
        follow-up `docker inspect`, and a mismatch must FAIL the check."""
        sc, _ = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),  # still shrunk despite "success"
            ],
        )
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "action-result")
        message = str(ctx.exception)
        self.assertIn("134217728", message)
        self.assertIn("6291456", message)

    def test_action_failure_and_restore_failure_precedence_and_diagnostics(self) -> None:
        """Day 6 M-A: when BOTH the action raises AND the restore fails,
        the restore failure is raised (the more urgent operational fact -
        a permanently misconfigured container), with the action's own
        exception preserved as the chained cause so neither failure's
        diagnostics are lost."""
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),
                self._ok(returncode=1, stderr="docker daemon unreachable"),
            ],
        )

        def _boom():
            raise self.module.ReliabilityError("action-side assertion failure")

        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom)

        raised = ctx.exception
        self.assertIn("docker daemon unreachable", str(raised))
        self.assertIsNotNone(raised.__cause__)
        self.assertIsInstance(raised.__cause__, self.module.ReliabilityError)
        self.assertIn("action-side assertion failure", str(raised.__cause__))

    # I: restoration uses the same verified retry mechanism - a transient
    # GitHub-class cgroup/runc race during RESTORE recovers via retry.
    def test_restore_recovers_from_transient_cgroup_race_via_bounded_retry(self) -> None:
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),  # shrink
                self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR),  # restore: transient
                self._inspect_ok(6291456, 6291456),                                       # retry check: not yet
                self._ok(returncode=0), self._inspect_ok(134217728, -1),                  # restore: succeeds
            ],
        )
        now, sleep = self._fake_clock()
        result = self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "ok", now=now, sleep=sleep)
        self.assertEqual(result, "ok")

    # J: action failure + restore retry success still re-raises the
    # original action failure correctly.
    def test_action_failure_with_restore_retry_success_still_reraises_action_failure(self) -> None:
        sc, calls = self._scripted_sc(
            {"Memory": 134217728, "MemorySwap": -1},
            [
                self._ok(returncode=0), self._inspect_ok(6291456, 6291456),  # shrink
                self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR),  # restore: transient
                self._inspect_ok(6291456, 6291456),                                       # retry check: not yet
                self._ok(returncode=0), self._inspect_ok(134217728, -1),                  # restore: succeeds
            ],
        )

        def _boom():
            raise self.module.ReliabilityError("action-side assertion failure during persistent-failure bound proof")

        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom, now=now, sleep=sleep)
        self.assertIn("action-side assertion failure", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__, "a successful restore must not chain a cause onto the action's own exception")

    # K: action failure + restore ultimately fails (bounded retry deadline
    # exhausted) preserves useful diagnostic precedence.
    def test_action_failure_with_restore_retry_exhaustion_preserves_precedence(self) -> None:
        sc = _fake_sc()
        calls: list[list[str]] = []
        inspect_calls = {"n": 0}

        def fake_docker_json(args):
            calls.append(list(args))
            return {"Memory": 134217728, "MemorySwap": -1}

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            if args[0] == "update" and args[2] == "6m":
                return self._ok(returncode=0)
            if args[0] == "inspect" and inspect_calls["n"] == 0:
                inspect_calls["n"] += 1
                return self._inspect_ok(6291456, 6291456)  # post-shrink verify
            if args[0] == "update":  # every restore attempt hits the transient race
                return self._ok(returncode=1, stderr=GITHUB_RUN_32960673438_TRANSIENT_STDERR)
            return self._inspect_ok(6291456, 6291456)  # retry checks: restore never actually lands

        sc.docker_json = fake_docker_json
        sc.run_docker = fake_run_docker

        def _boom():
            raise self.module.ReliabilityError("action-side assertion failure")

        now, sleep = self._fake_clock()
        with self.assertRaises(self.module.ReliabilityError) as ctx:
            self.module.with_memory_shrink_restored(
                sc, "c", "6m", "6m", _boom, now=now, sleep=sleep,
            )

        raised = ctx.exception
        self.assertIn("bounded retry", str(raised))
        self.assertIsNotNone(raised.__cause__)
        self.assertIsInstance(raised.__cause__, self.module.ReliabilityError)
        self.assertIn("action-side assertion failure", str(raised.__cause__))


class CheckTimeoutHierarchyConfigTests(unittest.TestCase):
    """Exercises the real Day 5 config-coherence check against the actual
    shipped config/platform.json (not a synthetic fixture - that coverage
    lives in tests/test_gateway_platform_config.py) using the real,
    Docker-free-to-import security_check module for CheckResult/CAT_SOURCE."""

    def setUp(self) -> None:
        self.reliability = load_reliability_check()
        self.sc = load_security_checker()

    def test_real_shipped_config_satisfies_the_invariant(self) -> None:
        result = self.reliability.check_timeout_hierarchy_config(self.sc)
        self.assertTrue(result.passed)
        self.assertIn("state_dependency_timeout_seconds", result.detail)
        self.assertIn("gateway_upstream_timeout_seconds", result.detail)


class UnpauseStateContainerTests(unittest.TestCase):
    """Day 7 (DAY7-REL-M1, day-07-reliability-adversarial-review.md): a
    failed first `docker unpause` must not be silently masked as though it
    had cleared `state_is_paused` - the outer teardown `finally` only
    re-attempts an unpause `if state_is_paused:`, so a caller must only
    clear that flag on a VERIFIED successful unpause (`_unpause_state_
    container` returning `True`), leaving it `True` on failure so the
    outer teardown gets a second attempt before `compose down -v` runs
    against a possibly still-paused container."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    @staticmethod
    def _scripted_sc(responses: list[SimpleNamespace]) -> tuple[SimpleNamespace, list]:
        sc = _fake_sc()
        calls: list[list[str]] = []
        queue = list(responses)

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            if not queue:
                raise AssertionError(f"unexpected extra run_docker call: {args}")
            return queue.pop(0)

        sc.run_docker = fake_run_docker
        return sc, calls

    @staticmethod
    def _ok(returncode: int = 0, stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    # A: successful first unpause clears state_is_paused.
    def test_successful_unpause_returns_true(self) -> None:
        sc, calls = self._scripted_sc([self._ok(returncode=0)])
        succeeded = self.module._unpause_state_container(sc, "c")
        self.assertTrue(succeeded)
        self.assertEqual(calls, [["unpause", "c"]])

        # Mirrors main()'s own inner `finally` block exactly: only a
        # verified-successful unpause clears the flag.
        state_is_paused = True
        if succeeded:
            state_is_paused = False
        self.assertFalse(state_is_paused)

    # B: failed first unpause leaves cleanup responsible for retrying -
    # the flag must NOT be cleared on a failed attempt.
    def test_failed_unpause_returns_false_and_does_not_clear_flag(self) -> None:
        sc, calls = self._scripted_sc([self._ok(returncode=1, stderr="daemon busy")])
        succeeded = self.module._unpause_state_container(sc, "c")
        self.assertFalse(succeeded)
        self.assertEqual(calls, [["unpause", "c"]])

        state_is_paused = True
        if succeeded:
            state_is_paused = False
        self.assertTrue(state_is_paused, "state_is_paused must stay True after a failed unpause")

    # C: the outer cleanup actually attempts a second unpause after the
    # first failure - composes the inner finally (this fix) and the outer
    # teardown's own `if state_is_paused: sc.run_docker(["unpause", ...])`
    # exactly as main() runs them in sequence, proving the retry the
    # review required actually happens end to end.
    def test_outer_teardown_retries_unpause_after_inner_failure(self) -> None:
        sc, calls = self._scripted_sc([
            self._ok(returncode=1, stderr="daemon busy"),  # inner (A-6 finally) attempt
            self._ok(returncode=0),  # outer teardown retry
        ])

        state_is_paused = True
        # Inner finally (main()'s A-6 pause-proof try/finally):
        if self.module._unpause_state_container(sc, "c"):
            state_is_paused = False
        self.assertTrue(state_is_paused)

        # Outer teardown finally (main()'s own top-level finally, unchanged
        # by this fix - only re-attempts when the flag is still True):
        if state_is_paused:
            sc.run_docker(["unpause", "c"])

        self.assertEqual(calls, [["unpause", "c"], ["unpause", "c"]])

    # D: original/action failure semantics preserved - the helper must
    # never raise on its own and must never swallow an exception already
    # in flight when it runs inside a `finally` block, exactly as
    # `_unpause_state_container` does inside main()'s real try/finally.
    def test_helper_does_not_swallow_an_in_flight_exception(self) -> None:
        sc, _ = self._scripted_sc([self._ok(returncode=1, stderr="daemon busy")])

        def _raise_then_cleanup() -> None:
            try:
                raise ValueError("original A-6 scenario failure")
            finally:
                # A failed unpause here must not itself raise, and must not
                # suppress the ValueError already propagating.
                self.module._unpause_state_container(sc, "c")

        with self.assertRaises(ValueError):
            _raise_then_cleanup()

    def test_no_infinite_retry_single_attempt_per_call(self) -> None:
        """A single call issues exactly one `docker unpause` - retrying is
        the OUTER teardown's job (one bounded extra attempt), never a loop
        inside this helper itself."""
        sc, calls = self._scripted_sc([self._ok(returncode=1, stderr="daemon busy")])
        self.module._unpause_state_container(sc, "c")
        self.assertEqual(len(calls), 1)


class SigtermHandlingTests(unittest.TestCase):
    """Day 6 (closes Day 5 finding L-2, day-05-failure-recovery-review.md):
    reliability_check.py copies compose_integration.py's exact
    _TerminatedError/_handle_sigterm/_install_sigterm_handler() pattern
    verbatim, but previously had no Docker-free regression test of its own
    for it (despite the identical mechanism already having one in
    tests/test_compose_integration.py::SigtermHandlingTests, which this
    class mirrors) - a real mid-run SIGTERM sent to the test process itself,
    asserting _TerminatedError is raised and that a `finally` block still
    runs (the exact guarantee reliability_check.py's own outer `finally`
    teardown - unpause-if-paused, then `compose down -v` - depends on)."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    def test_sigterm_raises_terminated_error(self) -> None:
        self.module._install_sigterm_handler()
        with self.assertRaises(self.module._TerminatedError):
            os.kill(os.getpid(), signal.SIGTERM)

    def test_terminated_error_message_names_the_signal(self) -> None:
        self.module._install_sigterm_handler()
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except self.module._TerminatedError as exc:
            self.assertIn("SIGTERM", str(exc))
        else:
            self.fail("expected _TerminatedError to be raised")

    def test_terminated_error_is_reachable_through_try_finally(self) -> None:
        """Proves the property main()'s own outer try/finally teardown
        actually relies on: a SIGTERM raised mid-try still runs the
        finally block - a bare SIGTERM's default disposition would skip
        `finally` entirely, orphaning the disposable Compose stack."""
        self.module._install_sigterm_handler()
        cleanup_ran = False

        def _raise_via_signal() -> None:
            os.kill(os.getpid(), signal.SIGTERM)

        try:
            try:
                _raise_via_signal()
            finally:
                cleanup_ran = True
        except self.module._TerminatedError:
            pass

        self.assertTrue(cleanup_ran, "finally block did not run after a SIGTERM raised mid-try")


if __name__ == "__main__":
    unittest.main()
