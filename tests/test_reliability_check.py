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
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


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


class WithMemoryShrinkRestoredTests(unittest.TestCase):
    """Docker-free proof of the Day 5 crash-remediation cleanup guarantee:
    scripts/reliability/reliability_check.py's SCENARIO 2 (persistent
    failure) shrinks a real container's memory limit and MUST restore it
    under try/finally even if the wrapped action raises - a real container
    must never be left permanently resource-starved just because an
    assertion inside SCENARIO 2 failed. This is exercised here purely
    against a fake `sc` (a spy recording every `docker` argv it was asked
    to run) - the real Docker-integration proof (an actual `docker update`
    round-trip) is `make reliability-check`'s job, not unittest."""

    def setUp(self) -> None:
        self.module = load_reliability_check()

    def _spy_sc(self, original_memory: int = 134217728, original_memory_swap: int = -1) -> tuple[SimpleNamespace, list]:
        sc = _fake_sc()
        calls: list[list[str]] = []

        def fake_docker_json(args):
            calls.append(list(args))
            return {"Memory": original_memory, "MemorySwap": original_memory_swap}

        def fake_run_docker(args, timeout=20.0):
            calls.append(list(args))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        sc.docker_json = fake_docker_json
        sc.run_docker = fake_run_docker
        return sc, calls

    def _restore_calls(self, calls: list[list[str]]) -> list[list[str]]:
        return [c for c in calls if c[:2] == ["update", "--memory"] and c[-1] == "c" and "6m" not in c]

    def test_restores_original_values_on_success(self) -> None:
        sc, calls = self._spy_sc()
        result = self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "ok")
        self.assertEqual(result, "ok")
        restores = self._restore_calls(calls)
        self.assertEqual(len(restores), 1)
        self.assertIn("134217728", restores[0])
        self.assertIn("-1", restores[0])

    def test_restores_original_values_even_when_action_raises(self) -> None:
        """The core injected-failure cleanup proof the remediation review
        asked for: a real assertion failure inside the wrapped action must
        not skip the memory restore."""
        sc, calls = self._spy_sc(original_memory=99999999, original_memory_swap=200000000)

        def _boom():
            raise self.module.ReliabilityError("simulated assertion failure inside SCENARIO 2")

        with self.assertRaises(self.module.ReliabilityError):
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom)

        restores = self._restore_calls(calls)
        self.assertEqual(len(restores), 1, "restore must run exactly once even though the action raised")
        self.assertIn("99999999", restores[0])
        self.assertIn("200000000", restores[0])

    def test_restores_original_values_even_when_action_raises_unexpected_exception(self) -> None:
        """Not just ReliabilityError - ANY exception from the wrapped
        action must still trigger the restore (a bare `finally`, not a
        narrow `except`)."""
        sc, calls = self._spy_sc()

        def _boom():
            raise RuntimeError("some other unexpected failure")

        with self.assertRaises(RuntimeError):
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", _boom)

        self.assertEqual(len(self._restore_calls(calls)), 1)

    def test_shrink_failure_raises_reliability_error(self) -> None:
        sc, _ = self._spy_sc()

        def fake_run_docker(args, timeout=20.0):
            return SimpleNamespace(returncode=1, stdout="", stderr="docker update failed")

        sc.run_docker = fake_run_docker
        with self.assertRaises(self.module.ReliabilityError):
            self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "unreachable")

    def test_restore_failure_is_a_warning_not_a_raise(self) -> None:
        """If the restore command itself fails (e.g. the container already
        exited), the wrapped action's own success/failure must still be
        what determines the overall outcome - a failed restore is reported
        (stderr), not silently swallowed, but does not mask the action's
        result."""
        sc, calls = self._spy_sc()
        call_count = {"n": 0}

        def fake_run_docker(args, timeout=20.0):
            call_count["n"] += 1
            calls.append(list(args))
            if call_count["n"] == 1:
                return SimpleNamespace(returncode=0, stdout="", stderr="")  # shrink succeeds
            return SimpleNamespace(returncode=1, stdout="", stderr="container not found")  # restore fails

        sc.run_docker = fake_run_docker
        result = self.module.with_memory_shrink_restored(sc, "c", "6m", "6m", lambda: "action-result")
        self.assertEqual(result, "action-result")


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


if __name__ == "__main__":
    unittest.main()
