"""Media tools must resolve on clean checkouts without bundled binaries."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vg import media


class MediaToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(media, "ROOT", self.root).start()
        patch.dict(os.environ, {}, clear=True).start()
        self.which = patch.object(media.shutil, "which", return_value="/usr/bin/tool").start()

    def bundled_tool(self, name):
        path = self.root / "tools" / "ffmpeg" / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def test_environment_overrides_bundled_and_path(self):
        for name in ("ffmpeg", "ffprobe"):
            with self.subTest(name=name):
                self.bundled_tool(name)
                override = self.root / "custom tools" / name
                with patch.dict(os.environ, {f"VG_{name.upper()}": str(override)}):
                    self.assertEqual(media._resolve_tool(name), override)
        self.which.assert_not_called()

    def test_bundled_overrides_path(self):
        for name in ("ffmpeg", "ffprobe"):
            with self.subTest(name=name):
                bundled = self.bundled_tool(name)
                self.assertEqual(media._resolve_tool(name), bundled)
        self.which.assert_not_called()

    def test_path_without_bundled_tools(self):
        for name in ("ffmpeg", "ffprobe"):
            with self.subTest(name=name), patch.dict(os.environ, {f"VG_{name.upper()}": ""}):
                self.which.return_value = f"/usr/bin/{name}"
                self.assertEqual(media._resolve_tool(name), Path(f"/usr/bin/{name}"))
                self.which.assert_called_with(name)

    def test_missing_tool_has_actionable_error(self):
        self.which.return_value = None
        for name in ("ffmpeg", "ffprobe"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(FileNotFoundError, f"{name}.*VG_{name.upper()}.*PATH"):
                    media._resolve_tool(name)


if __name__ == "__main__":
    unittest.main()
