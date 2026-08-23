"""Docker-free unit tests for scripts/verify/security_check.py's pure logic.

scripts/ is not an importable package (matching this project's existing
convention - see security_check.py's own load_dockerfile_checker()), so
this module is loaded the same way: importlib.util.spec_from_file_location
against the real file. Only the pure, no-Docker-call parts of
security_check.py are exercised here - the rest (starting/inspecting real
containers) is Docker-integration scope, covered by `make security-check`/
`make compose-test`, not unittest (see .claude/CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_security_check() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify" / "security_check.py"
    spec = importlib.util.spec_from_file_location("security_check_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HealthcheckModuleForRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sc = load_security_check()

    def test_app_role_maps_to_app_healthcheck(self) -> None:
        self.assertEqual(self.sc.healthcheck_module_for_role("app"), "app.healthcheck")

    def test_gateway_role_maps_to_gateway_healthcheck(self) -> None:
        self.assertEqual(self.sc.healthcheck_module_for_role("gateway"), "gateway.healthcheck")

    def test_state_role_maps_to_state_healthcheck(self) -> None:
        self.assertEqual(self.sc.healthcheck_module_for_role("state"), "state.healthcheck")

    def test_unknown_role_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.sc.healthcheck_module_for_role("nonexistent-role")

    def test_all_three_roles_map_to_distinct_modules(self) -> None:
        """Discriminating-power guard: the three roles must not collapse to
        the same probe module, or a role mismatch would be silently inert."""
        modules = {self.sc.healthcheck_module_for_role(role) for role in ("app", "gateway", "state")}
        self.assertEqual(len(modules), 3)


class CheckKernelReadonlyWriteFailsDispatchTests(unittest.TestCase):
    """Proves the fix has real discriminating power: for each role, the
    "service kept serving" probe genuinely execs that role's own healthcheck
    module - not a hardcoded app.healthcheck regardless of role (the exact
    Day 3 finding A-2 this closes)."""

    def setUp(self) -> None:
        self.sc = load_security_check()

    def _run_for_role(self, role: str) -> list[list[str]]:
        recorded_exec_argvs: list[list[str]] = []

        def fake_run_docker(args: list[str], timeout: float = 20.0):
            recorded_exec_argvs.append(args)
            result = unittest.mock.Mock()
            # Day 4: neither probe uses a shell anymore (the Distroless
            # final runtime has none) - the write attempt execs "-c
            # <probe source>", the healthcheck probe execs "-m <module>".
            # Distinguish on that rather than exact argv shape.
            result.returncode = 1 if "-c" in args else 0
            result.stdout = "write rejected: [Errno 30] Read-only file system"
            return result

        with patch.object(self.sc, "run_docker", side_effect=fake_run_docker):
            self.sc.check_kernel_readonly_write_fails("container", 0, role=role)
        return recorded_exec_argvs

    def test_state_role_probes_state_healthcheck_not_app(self) -> None:
        argvs = self._run_for_role("state")
        probe_argv = argvs[-1]
        self.assertIn("state.healthcheck", probe_argv)
        self.assertNotIn("app.healthcheck", probe_argv)

    def test_gateway_role_probes_gateway_healthcheck_not_app(self) -> None:
        argvs = self._run_for_role("gateway")
        probe_argv = argvs[-1]
        self.assertIn("gateway.healthcheck", probe_argv)
        self.assertNotIn("app.healthcheck", probe_argv)

    def test_app_role_probes_app_healthcheck(self) -> None:
        argvs = self._run_for_role("app")
        probe_argv = argvs[-1]
        self.assertIn("app.healthcheck", probe_argv)

    def test_result_label_names_the_role(self) -> None:
        with patch.object(
            self.sc,
            "run_docker",
            return_value=unittest.mock.Mock(returncode=0, stdout="", stderr=""),
        ):
            result = self.sc.check_kernel_readonly_write_fails("container", 0, role="state")
        self.assertIn("state", result.name)

    def test_probe_argv_never_invokes_a_shell(self) -> None:
        """Discriminating-power guard for the Day 4 migration: neither the
        write-rejection probe nor the healthcheck probe may ever exec a
        shell (/bin/sh, /bin/bash) - the Distroless final runtime has
        none, so any lingering shell dependency would break outright, not
        merely be undesirable."""
        argvs = self._run_for_role("app")
        for argv in argvs:
            self.assertNotIn("sh", argv)
            self.assertNotIn("bash", argv)
            self.assertIn(self.sc.PYTHON_BIN, argv)


if __name__ == "__main__":
    unittest.main()
