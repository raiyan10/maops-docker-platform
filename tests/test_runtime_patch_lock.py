"""Docker-free unit tests for scripts/security/runtime_patch_lock.py's
parsing/validation logic."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_TEXT = """\
LIBSSL_CVE=CVE-2026-14456
LIBSSL_PACKAGE=libssl3t64
LIBSSL_SOURCE_PACKAGE=openssl
LIBSSL_VULNERABLE_VERSION=3.5.6-1~deb13u2
LIBSSL_VERSION=3.5.7-1~deb13u2
LIBSSL_ARCH=amd64
LIBSSL_SUITE=trixie-security
LIBSSL_ARCHIVE=debian-security
LIBSSL_SNAPSHOT_TIMESTAMP=20260825T185058Z
LIBSSL_URL=https://snapshot.debian.org/archive/debian-security/20260825T185058Z/pool/updates/main/o/openssl/libssl3t64_3.5.7-1~deb13u2_amd64.deb
LIBSSL_DEB_SHA256=""" + ("a" * 64) + """
LIBSSL_DEB_SIZE_BYTES=2453872
LIBSSL_SO_SHA256=""" + ("b" * 64) + """
LIBCRYPTO_SO_SHA256=""" + ("c" * 64) + "\n"


def load_module() -> ModuleType:
    path = REPO_ROOT / "scripts" / "security" / "runtime_patch_lock.py"
    spec = importlib.util.spec_from_file_location("runtime_patch_lock_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParseRuntimePatchLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_valid_lock_file_parses(self) -> None:
        entries = self.module.parse_runtime_patch_lock(VALID_TEXT)
        self.assertEqual(entries["LIBSSL_VERSION"], "3.5.7-1~deb13u2")
        self.assertEqual(entries["LIBSSL_DEB_SHA256"], "a" * 64)

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        text = "# a comment\n\n" + VALID_TEXT
        entries = self.module.parse_runtime_patch_lock(text)
        self.assertEqual(len(entries), 14)

    def test_malformed_line_is_rejected(self) -> None:
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock("not a key value line\n")

    def test_duplicate_key_is_rejected(self) -> None:
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock(VALID_TEXT + "LIBSSL_VERSION=3.5.8-1~deb13u2\n")

    def test_missing_required_key_is_rejected(self) -> None:
        text = "\n".join(line for line in VALID_TEXT.splitlines() if not line.startswith("LIBSSL_URL="))
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock(text)

    def test_short_sha256_is_rejected(self) -> None:
        bad = VALID_TEXT.replace("LIBSSL_DEB_SHA256=" + ("a" * 64), "LIBSSL_DEB_SHA256=deadbeef")
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock(bad)

    def test_non_hex_sha256_is_rejected(self) -> None:
        bad = VALID_TEXT.replace("LIBSSL_SO_SHA256=" + ("b" * 64), "LIBSSL_SO_SHA256=" + ("z" * 64))
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock(bad)

    def test_non_https_url_is_rejected(self) -> None:
        bad = VALID_TEXT.replace(
            "https://snapshot.debian.org", "http://snapshot.debian.org"
        )
        with self.assertRaises(self.module.RuntimePatchLockError):
            self.module.parse_runtime_patch_lock(bad)

    def test_real_repository_lock_file_parses_and_pins_libssl(self) -> None:
        """The actual security/runtime-patches.lock this repository ships
        must itself parse cleanly and provide every required key."""
        entries = self.module.load_runtime_patch_lock()
        self.assertEqual(entries["LIBSSL_PACKAGE"], "libssl3t64")
        self.assertRegex(entries["LIBSSL_DEB_SHA256"], r"^[0-9a-f]{64}$")
        self.assertRegex(entries["LIBSSL_SO_SHA256"], r"^[0-9a-f]{64}$")
        self.assertRegex(entries["LIBCRYPTO_SO_SHA256"], r"^[0-9a-f]{64}$")
        self.assertTrue(entries["LIBSSL_URL"].startswith("https://snapshot.debian.org/"))


if __name__ == "__main__":
    unittest.main()
