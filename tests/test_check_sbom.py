"""Docker-free unit tests for scripts/security/check_sbom.py's SBOM
validation logic, using synthetic fixture SBOMs (no real Syft invocation -
see .claude/CLAUDE.md: external scanner execution belongs to make targets,
not unittest)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_check_sbom() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "check_sbom.py"
    spec = importlib.util.spec_from_file_location("check_sbom_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_sbom(version: str = "0.4.0") -> dict:
    # Distroless dpkg-status-derived package names (Day 4) - not the
    # literal "python" name python:3.13-slim's own dpkg database happened
    # to also carry. libssl3t64's versionInfo must match the real,
    # checked-in security/runtime-patches.lock's LIBSSL_VERSION (Day 6).
    return {
        "spdxVersion": "SPDX-2.3",
        "name": "/input.tar",
        "packages": [
            {"name": "python3.13-minimal", "versionInfo": "3.13.5-1"},
            {"name": "libpython3.13-stdlib", "versionInfo": "3.13.5-1"},
            {"name": "libc6", "versionInfo": "2.41-8"},
            {"name": "libssl3t64", "versionInfo": "3.5.7-1~deb13u2"},
        ],
        "creationInfo": {"creators": ["Organization: Anchore, Inc", "Tool: syft-1.51.0"]},
    }


class CheckSbomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_check_sbom()

    def _write(self, tmp_dir: str, name: str, content: str) -> Path:
        path = Path(tmp_dir) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_valid_sbom_passes_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "maops-docker-platform-0.4.0.spdx.json", json.dumps(_valid_sbom()))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertEqual(findings, [])

    def test_malformed_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", "not json")
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("not valid JSON" in f for f in findings))

    def test_missing_spdx_marker_is_rejected(self) -> None:
        payload = _valid_sbom()
        del payload["spdxVersion"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("spdxVersion" in f for f in findings))

    def test_empty_package_inventory_is_rejected(self) -> None:
        payload = _valid_sbom()
        payload["packages"] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("empty" in f for f in findings))

    def test_missing_python_package_is_rejected(self) -> None:
        payload = _valid_sbom()
        payload["packages"] = [{"name": "libc6", "versionInfo": "2.41-8"}]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("python" in f for f in findings))

    def test_distroless_dpkg_named_python_package_is_accepted(self) -> None:
        """Discriminating-power guard for the Day 4 lenient match: a
        Distroless-shaped dpkg package name ('python3.13-minimal') must
        satisfy this check, not just the literal 'python' name
        python:3.13-slim's own dpkg database happened to also carry."""
        payload = _valid_sbom()
        payload["packages"] = [
            {"name": "python3.13-minimal", "versionInfo": "3.13.5-1"},
            {"name": "libssl3t64", "versionInfo": "3.5.7-1~deb13u2"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertEqual(findings, [])

    def test_missing_libssl_package_is_rejected(self) -> None:
        """Day 6: the SBOM must reflect the emergency libssl3t64
        Debian-security overlay (security/runtime-patches.lock)."""
        payload = _valid_sbom()
        payload["packages"] = [{"name": "python3.13-minimal", "versionInfo": "3.13.5-1"}]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("libssl3t64" in f and "does not include" in f for f in findings))

    def test_stale_libssl_version_is_rejected(self) -> None:
        """If the filesystem was patched but the SBOM still reports the
        vulnerable version, supply-chain metadata and the real image
        content have diverged - this must fail, not pass silently."""
        payload = _valid_sbom()
        for pkg in payload["packages"]:
            if pkg["name"] == "libssl3t64":
                pkg["versionInfo"] = "3.5.6-1~deb13u2"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("does not include the" in f and "patched version" in f for f in findings))

    def test_version_mismatch_in_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "maops-docker-platform-0.3.0.spdx.json", json.dumps(_valid_sbom()))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("VERSION" in f for f in findings))

    def test_missing_scanner_reference_is_rejected(self) -> None:
        payload = _valid_sbom()
        payload["creationInfo"] = {"creators": ["Organization: Anchore, Inc"]}
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("Syft" in f for f in findings))

    def test_local_path_leakage_is_rejected(self) -> None:
        payload = _valid_sbom()
        payload["packages"][0]["sourceInfo"] = "found at /home/exampleuser/project/app"
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("local workstation path" in f for f in findings))

    def test_secret_shaped_string_is_rejected(self) -> None:
        payload = _valid_sbom()
        payload["packages"][0]["copyrightText"] = "password=\"hunter2super\""
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = self._write(tmp_dir, "x-0.4.0.spdx.json", json.dumps(payload))
            findings = self.module.check_sbom(path, "0.4.0")
            self.assertTrue(any("secret-shaped" in f for f in findings))


if __name__ == "__main__":
    unittest.main()
