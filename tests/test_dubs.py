import json
from pathlib import Path
import tempfile
import unittest

from vg import dubs


class DubListTests(unittest.TestCase):
    def test_pending_and_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1 = root / "runs/r1/variants/v1"
            v1.mkdir(parents=True)
            (v1 / "dub_en.m4a").write_bytes(b"x")
            (v1 / "published_youtube.json").write_text(json.dumps(
                {"video_id": "abc", "dub_en": str(v1 / "dub_en.m4a"), "publish_at": "2026-11-06T11:00:00Z"}))
            ja_only = root / "runs/r2"
            ja_only.mkdir(parents=True)
            (ja_only / "published_youtube.json").write_text(json.dumps({"video_id": "def"}))
            rows = dubs.pending(root)
            self.assertEqual([r["video_id"] for r in rows], ["abc"])
            self.assertIn("11/06 20:00 JST", dubs.report(root))
            self.assertIn("studio.youtube.com/video/abc/translations", dubs.report(root))
            dubs.mark_done("abc", root)
            self.assertEqual(dubs.pending(root), [])
            self.assertIn("ありません", dubs.report(root))
            with self.assertRaises(ValueError):
                dubs.mark_done("def", root)


if __name__ == "__main__":
    unittest.main()
