"""Docker-free unit tests for scripts/build/image_audit.py's own pure
evaluation logic. Historically image_audit.py had zero unit test
coverage of its own (all its real proof came from `make image-audit`
against a live Docker daemon); this module adds Docker-free coverage for
`check_final_base_is_approved_distroless` specifically, since Day 7
rewrote it from a tautological "is the built image's RootFS inspectable
at all" check into a real independent-pull-and-layer-prefix cross-check,
and that decision logic is now worth pinning down without a live daemon.
The full end-to-end real-Docker proof remains `make image-audit`'s job,
not unittest (see .claude/CLAUDE.md)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_image_audit() -> ModuleType:
    path = REPO_ROOT / "scripts" / "build" / "image_audit.py"
    spec = importlib.util.spec_from_file_location("image_audit_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CheckFinalBaseIsApprovedDistrolessTests(unittest.TestCase):
    BASE_REPO = "gcr.io/distroless/python3-debian13"
    BASE_DIGEST = "sha256:" + "a" * 64
    IMAGE = "maops-docker-platform:9.9.9"

    def setUp(self) -> None:
        self.module = load_image_audit()

    def _fake_run_docker(self, *, pull_returncode=0, base_layers=None, image_layers=None,
                          base_inspect_returncode=0, image_inspect_returncode=0,
                          base_inspect_stdout=None, image_inspect_stdout=None):
        base_ref = f"{self.BASE_REPO}@{self.BASE_DIGEST}"

        def fake(args, timeout=20.0):
            if args[0] == "pull":
                assert args[1] == base_ref
                return SimpleNamespace(returncode=pull_returncode, stdout="", stderr="pull failed" if pull_returncode else "")
            if args[0] == "image" and args[1] == "inspect" and args[2] == base_ref:
                stdout = base_inspect_stdout if base_inspect_stdout is not None else json.dumps(base_layers or [])
                return SimpleNamespace(returncode=base_inspect_returncode, stdout=stdout, stderr="" if base_inspect_returncode == 0 else "base inspect failed")
            if args[0] == "image" and args[1] == "inspect" and args[2] == self.IMAGE:
                stdout = image_inspect_stdout if image_inspect_stdout is not None else json.dumps(image_layers or [])
                return SimpleNamespace(returncode=image_inspect_returncode, stdout=stdout, stderr="" if image_inspect_returncode == 0 else "image inspect failed")
            raise AssertionError(f"unexpected run_docker call: {args}")

        self.module.run_docker = fake

    def _check(self):
        return self.module.check_final_base_is_approved_distroless(self.IMAGE, self.BASE_REPO, self.BASE_DIGEST)

    def test_base_layers_are_a_genuine_prefix_of_image_layers_passes(self) -> None:
        self._fake_run_docker(
            base_layers=["sha256:layer1", "sha256:layer2"],
            image_layers=["sha256:layer1", "sha256:layer2", "sha256:layer3-app-content"],
        )
        result = self._check()
        self.assertTrue(result.passed)
        self.assertIn("prefix_match=True", result.detail)

    def test_image_layers_diverge_from_base_layers_fails(self) -> None:
        """A different (even if superficially similar) base image would
        produce a real layer mismatch - this is the discriminating case
        the prior tautological check could never have caught."""
        self._fake_run_docker(
            base_layers=["sha256:layer1", "sha256:layer2"],
            image_layers=["sha256:layer1", "sha256:DIFFERENT-layer2", "sha256:layer3"],
        )
        result = self._check()
        self.assertFalse(result.passed)

    def test_image_has_fewer_layers_than_base_fails(self) -> None:
        self._fake_run_docker(
            base_layers=["sha256:layer1", "sha256:layer2", "sha256:layer3"],
            image_layers=["sha256:layer1", "sha256:layer2"],
        )
        result = self._check()
        self.assertFalse(result.passed)

    def test_empty_base_layers_fails(self) -> None:
        self._fake_run_docker(base_layers=[], image_layers=["sha256:layer1"])
        result = self._check()
        self.assertFalse(result.passed)

    def test_pull_failure_fails_clearly(self) -> None:
        self._fake_run_docker(pull_returncode=1)
        result = self._check()
        self.assertFalse(result.passed)
        self.assertIn("docker pull failed", result.detail)

    def test_base_inspect_failure_fails_clearly(self) -> None:
        self._fake_run_docker(base_inspect_returncode=1, image_layers=["sha256:layer1"])
        result = self._check()
        self.assertFalse(result.passed)

    def test_image_inspect_failure_fails_clearly(self) -> None:
        self._fake_run_docker(base_layers=["sha256:layer1"], image_inspect_returncode=1)
        result = self._check()
        self.assertFalse(result.passed)

    def test_non_json_output_fails_clearly(self) -> None:
        self._fake_run_docker(base_inspect_stdout="not json", image_layers=["sha256:layer1"])
        result = self._check()
        self.assertFalse(result.passed)

    def test_exact_equal_layer_lists_pass(self) -> None:
        layers = ["sha256:layer1", "sha256:layer2"]
        self._fake_run_docker(base_layers=layers, image_layers=list(layers))
        result = self._check()
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
