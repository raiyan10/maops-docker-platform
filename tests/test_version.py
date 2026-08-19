import unittest
from pathlib import Path
from unittest.mock import patch

from app import version


class VersionTests(unittest.TestCase):
    def test_reads_repository_version_file(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        expected = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(version.get_version(), expected)

    def test_reads_fresh_on_every_call(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        expected = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
        # Not cached at import time: patching the source path changes the result.
        with patch.object(version, "_VERSION_FILE", Path("/nonexistent/VERSION")):
            with self.assertRaises(FileNotFoundError):
                version.get_version()
        # Original behavior is restored after the patch context exits.
        self.assertEqual(version.get_version(), expected)

    def test_strips_surrounding_whitespace(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_version_file = Path(tmp_dir) / "VERSION"
            fake_version_file.write_text("  1.2.3  \n", encoding="utf-8")
            with patch.object(version, "_VERSION_FILE", fake_version_file):
                self.assertEqual(version.get_version(), "1.2.3")


if __name__ == "__main__":
    unittest.main()
