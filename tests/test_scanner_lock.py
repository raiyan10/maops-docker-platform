"""Docker-free unit tests for scripts/security/scanner_lock.py's parsing
and digest-pin validation logic."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_scanner_lock_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "scanner_lock.py"
    spec = importlib.util.spec_from_file_location("scanner_lock_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_DIGEST = "a" * 64


class ParseScannerLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_scanner_lock_module()

    def test_valid_entries_are_parsed(self) -> None:
        text = f"SYFT_IMAGE=anchore/syft:v1.0.0@sha256:{VALID_DIGEST}\n"
        entries = self.module.parse_scanner_lock(text)
        self.assertEqual(entries["SYFT_IMAGE"], f"anchore/syft:v1.0.0@sha256:{VALID_DIGEST}")

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        text = f"# a comment\n\nSYFT_IMAGE=anchore/syft:v1.0.0@sha256:{VALID_DIGEST}\n"
        entries = self.module.parse_scanner_lock(text)
        self.assertEqual(len(entries), 1)

    def test_bare_tag_without_digest_is_rejected(self) -> None:
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock("SYFT_IMAGE=anchore/syft:v1.0.0\n")

    def test_latest_tag_is_rejected(self) -> None:
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock(f"SYFT_IMAGE=anchore/syft:latest@sha256:{VALID_DIGEST}\n")

    def test_short_digest_is_rejected(self) -> None:
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock("SYFT_IMAGE=anchore/syft:v1.0.0@sha256:deadbeef\n")

    def test_non_hex_digest_is_rejected(self) -> None:
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock("SYFT_IMAGE=anchore/syft:v1.0.0@sha256:" + ("z" * 64) + "\n")

    def test_malformed_line_is_rejected(self) -> None:
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock("not a key value line\n")

    def test_duplicate_key_is_rejected(self) -> None:
        text = f"SYFT_IMAGE=anchore/syft:v1.0.0@sha256:{VALID_DIGEST}\nSYFT_IMAGE=anchore/syft:v2.0.0@sha256:{VALID_DIGEST}\n"
        with self.assertRaises(self.module.ScannerLockError):
            self.module.parse_scanner_lock(text)

    def test_real_repository_lock_file_parses_and_pins_both_scanners(self) -> None:
        """The actual security/scanners.lock this repository ships must
        itself parse cleanly and provide both required entries."""
        entries = self.module.load_scanner_lock()
        self.assertIn("SYFT_IMAGE", entries)
        self.assertIn("TRIVY_IMAGE", entries)
        self.assertRegex(entries["SYFT_IMAGE"], r"@sha256:[0-9a-f]{64}$")
        self.assertRegex(entries["TRIVY_IMAGE"], r"@sha256:[0-9a-f]{64}$")

    def test_get_syft_image_and_get_trivy_image_helpers(self) -> None:
        self.assertTrue(self.module.get_syft_image().startswith("anchore/syft"))
        self.assertTrue(self.module.get_trivy_image().startswith("aquasec/trivy"))


if __name__ == "__main__":
    unittest.main()
