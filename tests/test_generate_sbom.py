"""Docker-free unit test for scripts/security/generate_sbom.py: proves the
constructed `docker run` command for the Syft scanner never mounts the
Docker daemon socket and disables networking - the supply-chain isolation
property .claude/CLAUDE.md requires - by mocking subprocess.run and
inspecting the actual argv it would have executed."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_generate_sbom() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "generate_sbom.py"
    spec = importlib.util.spec_from_file_location("generate_sbom_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunSyftDockerSocketIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_generate_sbom()

    def test_no_docker_socket_is_mounted_and_networking_is_disabled(self) -> None:
        recorded_argv: list[list[str]] = []

        def fake_run(args, **kwargs):
            recorded_argv.append(args)
            result = __import__("subprocess").CompletedProcess(args, 0, stdout="", stderr="")
            return result

        with tempfile.TemporaryDirectory() as tmp_dir:
            archive = Path(tmp_dir) / "image.tar"
            archive.write_bytes(b"fake")
            scratch = Path(tmp_dir) / "scratch"
            output = Path(tmp_dir) / "output"
            output.mkdir()

            with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                self.module.run_syft("anchore/syft:v1.0.0@sha256:" + "a" * 64, archive, scratch, output, "out.json")

        self.assertEqual(len(recorded_argv), 1)
        argv_text = " ".join(recorded_argv[0])
        self.assertNotIn("docker.sock", argv_text)
        self.assertIn("--network", recorded_argv[0])
        self.assertIn("none", recorded_argv[0])

    def test_archive_is_mounted_read_only(self) -> None:
        recorded_argv: list[list[str]] = []

        def fake_run(args, **kwargs):
            recorded_argv.append(args)
            return __import__("subprocess").CompletedProcess(args, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            archive = Path(tmp_dir) / "image.tar"
            archive.write_bytes(b"fake")
            scratch = Path(tmp_dir) / "scratch"
            output = Path(tmp_dir) / "output"
            output.mkdir()

            with patch.object(self.module.subprocess, "run", side_effect=fake_run):
                self.module.run_syft("anchore/syft:v1.0.0@sha256:" + "a" * 64, archive, scratch, output, "out.json")

        mount_args = [a for a in recorded_argv[0] if str(archive) in a]
        self.assertTrue(mount_args and mount_args[0].endswith(":ro"))


if __name__ == "__main__":
    unittest.main()
