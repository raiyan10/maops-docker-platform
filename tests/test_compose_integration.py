"""Docker-free unit tests for scripts/compose/compose_integration.py's pure
logic: the SIGTERM-handling regression (Day 3 finding A-5) and the network
Internal-flag check's own parsing/comparison logic (Day 3 finding A-3).

scripts/ is not an importable package (matching this project's existing
convention), so this module is loaded via importlib.util.spec_from_file_location
against the real file, mirroring compose_integration.py's own
load_security_checker(). Real Compose-stack behavior (bringing up
containers, real docker network inspect output) is Docker-integration
scope, covered by `make compose-test`, not unittest (see .claude/CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import os
import signal
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_compose_integration() -> ModuleType:
    path = REPO_ROOT / "scripts" / "compose" / "compose_integration.py"
    spec = importlib.util.spec_from_file_location("compose_integration_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SigtermHandlerTests(unittest.TestCase):
    """Adversarial regression for Day 3 finding A-5: sends a real SIGTERM to
    this test process (not merely checking that signal.signal() was called)
    and proves the handler converts it into a catchable exception - the
    exact discriminating property a broken/missing handler would fail."""

    def setUp(self) -> None:
        self.module = load_compose_integration()
        self.addCleanup(signal.signal, signal.SIGTERM, signal.SIG_DFL)

    def test_sigterm_is_converted_to_a_catchable_terminated_error(self) -> None:
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
        """Proves the property compose_integration.py's main() actually
        relies on: a SIGTERM raised mid-try still runs the finally block -
        the exact guarantee that was missing before this fix (a bare
        SIGTERM's default disposition skips `finally` entirely)."""
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


class CheckNetworkInternalFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_compose_integration()

    def _fake_sc(self, stdout: str, returncode: int = 0):
        sc = SimpleNamespace()
        sc.CAT_RUNTIME = "C:docker-runtime"
        sc.run_docker = lambda args, timeout=20.0: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr="" if returncode == 0 else "no such network"
        )
        sc.CheckResult = lambda category, name, passed, detail: SimpleNamespace(
            category=category, name=name, passed=passed, detail=detail
        )
        return sc

    def test_backend_internal_true_matches_expected(self) -> None:
        sc = self._fake_sc("true\n")
        result = self.module.check_network_internal_flag(sc, "maops-compose-abc123", "backend", True)
        self.assertTrue(result.passed)

    def test_edge_internal_false_matches_expected(self) -> None:
        sc = self._fake_sc("false\n")
        result = self.module.check_network_internal_flag(sc, "maops-compose-abc123", "edge", False)
        self.assertTrue(result.passed)

    def test_mismatched_internal_flag_fails(self) -> None:
        """Discriminating-power guard: if the real network is not actually
        internal, this must fail, not silently pass."""
        sc = self._fake_sc("false\n")
        result = self.module.check_network_internal_flag(sc, "maops-compose-abc123", "backend", True)
        self.assertFalse(result.passed)

    def test_docker_command_failure_is_reported_as_failed_not_raised(self) -> None:
        sc = self._fake_sc("", returncode=1)
        result = self.module.check_network_internal_flag(sc, "maops-compose-abc123", "backend", True)
        self.assertFalse(result.passed)

    def test_uses_project_prefixed_full_network_name(self) -> None:
        recorded_args: list[list[str]] = []
        sc = SimpleNamespace()
        sc.CAT_RUNTIME = "C:docker-runtime"

        def fake_run_docker(args, timeout=20.0):
            recorded_args.append(args)
            return SimpleNamespace(returncode=0, stdout="true\n", stderr="")

        sc.run_docker = fake_run_docker
        sc.CheckResult = lambda category, name, passed, detail: SimpleNamespace(
            category=category, name=name, passed=passed, detail=detail
        )
        self.module.check_network_internal_flag(sc, "maops-compose-deadbeef0000", "backend", True)
        self.assertIn("maops-compose-deadbeef0000_backend", recorded_args[0])


class CheckRoleDiscriminationMatrixTests(unittest.TestCase):
    """Docker-free unit test for check_role_discrimination_matrix()'s own
    pure pass/fail logic (Day 4 finding H-1's core regression proof). The
    real Docker-integration proof - that app/gateway/state.healthcheck
    genuinely reject each other's live /healthz responses - is exercised
    by `make compose-test` against real containers, not here; this only
    proves the matrix-evaluation logic itself correctly turns a set of
    per-cell exit codes into pass/fail."""

    def setUp(self) -> None:
        self.module = load_compose_integration()

    def _fake_sc(self, container_to_role: dict[str, str], exit_codes: dict[tuple[str, str], int]):
        """exit_codes maps (target_role, probe_role) -> returncode."""
        sc = SimpleNamespace()
        sc.CAT_KERNEL = "D:kernel/process"
        sc.healthcheck_module_for_role = lambda role: f"{role}.healthcheck"
        sc.CheckResult = lambda category, name, passed, detail: SimpleNamespace(
            category=category, name=name, passed=passed, detail=detail
        )

        def fake_run_docker(args, timeout=20.0):
            container = args[1]
            module = args[-1]
            probe_role = module.split(".", 1)[0]
            target_role = container_to_role[container]
            return SimpleNamespace(returncode=exit_codes[(target_role, probe_role)], stdout="", stderr="")

        sc.run_docker = fake_run_docker
        return sc

    def test_correct_discrimination_passes(self) -> None:
        container_to_role = {"app-c": "app", "gateway-c": "gateway", "state-c": "state"}
        containers = {"app": "app-c", "gateway": "gateway-c", "state": "state-c"}
        exit_codes = {}
        for target in ("app", "gateway", "state"):
            for probe in ("app", "gateway", "state"):
                exit_codes[(target, probe)] = 0 if probe == target else 1
        sc = self._fake_sc(container_to_role, exit_codes)
        result = self.module.check_role_discrimination_matrix(sc, containers)
        self.assertTrue(result.passed)

    def test_wrong_role_exiting_zero_fails(self) -> None:
        """The exact H-1 regression: state's container also accepts
        app.healthcheck (exits 0) - must be reported as a failure."""
        container_to_role = {"app-c": "app", "gateway-c": "gateway", "state-c": "state"}
        containers = {"app": "app-c", "gateway": "gateway-c", "state": "state-c"}
        exit_codes = {}
        for target in ("app", "gateway", "state"):
            for probe in ("app", "gateway", "state"):
                exit_codes[(target, probe)] = 0  # every probe "succeeds" everywhere (the bug)
        sc = self._fake_sc(container_to_role, exit_codes)
        result = self.module.check_role_discrimination_matrix(sc, containers)
        self.assertFalse(result.passed)
        self.assertIn("MISMATCHES", result.detail)

    def test_own_role_failing_is_also_reported_as_mismatch(self) -> None:
        container_to_role = {"app-c": "app", "gateway-c": "gateway", "state-c": "state"}
        containers = {"app": "app-c", "gateway": "gateway-c", "state": "state-c"}
        exit_codes = {}
        for target in ("app", "gateway", "state"):
            for probe in ("app", "gateway", "state"):
                exit_codes[(target, probe)] = 1  # nothing ever succeeds, including own role
        sc = self._fake_sc(container_to_role, exit_codes)
        result = self.module.check_role_discrimination_matrix(sc, containers)
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
