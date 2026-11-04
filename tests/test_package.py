from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from vg.media import FFMPEG, load, run, save
from vg.package import package
from vg import qa
from vg.schemas import Asset, Brief, Check, Line, Manifest, Publish, QAReport, Research, Script, Source


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.video_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.video_temp.cleanup)
        cls.video = Path(cls.video_temp.name) / "synthetic.mp4"
        # Red before 0.5 s, blue thereafter: verifies cover timestamp as well as dimensions.
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
             "color=c=blue:s=108x192:r=30:d=1,drawbox=color=red:t=fill:enable='lt(t,0.5)'",
             "-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p", str(cls.video)])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "runs" / "synthetic"
        self.run.mkdir(parents=True)
        self.patcher = patch("vg.package.ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        save(self.run, "brief", Brief(request="test", topic="test", platforms=["youtube_shorts", "x"],
                                     constraints=["#動画", "keep short", "#test"]))
        save(self.run, "script", Script(title="タイトル", lines=[Line(id="L01", text="冒頭の説明")]))
        save(self.run, "research", Research(sources=[Source(id="S1", title="資料", url="https://example.test/source",
                                                          accessed="2026-11-04")]))
        common = dict(origin="generated", license="test", commercial_ok=True)
        save(self.run, "manifest", Manifest(assets=[
            Asset(id="voice", path="audio.wav", kind="voice", credit="VOICEVOX:ずんだもん", **common),
            Asset(id="voice2", path="audio2.wav", kind="voice", credit="VOICEVOX:ずんだもん", **common),
            Asset(id="image", path="image.png", kind="image", params={"realistic": True}, **common),
            Asset(id="other", path="other.png", kind="image", credit="  ", **common)]))
        (self.run / "final.mp4").write_bytes(self.video.read_bytes())
        (self.run / "final.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8")

    def test_refuses_missing_or_failed_qa(self):
        with self.assertRaisesRegex(ValueError, "qa.json"):
            package(self.run)
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=False)]))
        with self.assertRaisesRegex(ValueError, "passed QA"):
            package(self.run)
        self.assertFalse((self.run / "publish.json").exists())
        self.assertFalse((self.root / "outbox").exists())

    def test_passed_package(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True),
                                             Check(id="silence", passed=False, severity="warn")]))
        destination = package(self.run)
        self.assertEqual(destination, self.root / "outbox" / "synthetic")
        self.assertEqual({p.name for p in destination.iterdir()},
                         {"final.mp4", "final.srt", "publish.json", "cover.jpg", "cover_small.jpg", "post_kit"})
        for name in ("final.mp4", "final.srt", "publish.json", "cover.jpg", "cover_small.jpg"):
            self.assertEqual((destination / name).read_bytes(), (self.run / name).read_bytes())
        for name, dimensions in [("cover.jpg", (1080, 1920)), ("cover_small.jpg", (360, 640))]:
            with Image.open(destination / name) as image:
                self.assertEqual(image.size, dimensions)
                self.assertEqual(image.format, "JPEG")
                red, _, blue = image.getpixel((dimensions[0] // 2, dimensions[1] // 2))
                self.assertGreater(blue, red + 100)
        check = next(c for c in load(self.run, "qa").checks if c.id == "metadata")
        self.assertTrue(check.passed)
        self.assertEqual(check.severity, "block")
        publish = load(destination, "publish")
        self.assertEqual(publish.title, "タイトル")
        self.assertEqual(publish.description, "冒頭の説明\n\n出典\n- 資料 https://example.test/source\n\nVOICEVOX:ずんだもん")
        self.assertEqual(publish.hashtags, ["#動画", "#test", "#short_video"])
        self.assertEqual(publish.platforms, ["youtube_shorts", "x"])
        self.assertTrue(publish.synthetic_media)

    def test_optional_sources_credits_and_disclosure(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        (self.run / "research.json").unlink()
        save(self.run, "brief", Brief(request="test", topic="test"))
        common = dict(license="test", commercial_ok=True)
        save(self.run, "manifest", Manifest(assets=[
            Asset(id="voice", path="v.wav", kind="voice", origin="generated", params={"realistic": True}, **common),
            Asset(id="image", path="i.png", kind="image", origin="library", params={"realistic": True}, **common),
            Asset(id="art", path="a.png", kind="image", origin="generated", params={"realistic": False}, **common)]))
        publish = load(package(self.run), "publish")
        self.assertEqual(publish.description, "冒頭の説明")
        self.assertEqual(publish.hashtags, ["#short_video", "#short", "#video"])
        self.assertTrue(publish.synthetic_media)

    def test_prompt_disclosure_and_nonvisual_assets(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        common = dict(origin="generated", license="test", commercial_ok=True)
        for kind, params, expected in [("image", {"prompt": "CINEMATIC PHOTO of a synthetic object"}, True),
                                        ("video", {"prompt": "photoreal rendering"}, True),
                                        ("voice", {"realistic": True, "prompt": "photo"}, False),
                                        ("image", {"prompt": "flat vector illustration"}, False)]:
            with self.subTest(kind=kind, params=params):
                save(self.run, "manifest", Manifest(assets=[Asset(id="synthetic", path="test", kind=kind,
                                                                params=params, **common)]))
                self.assertEqual(load(package(self.run), "publish").synthetic_media, expected)

    def test_missing_deliverable_does_not_publish(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        (self.run / "final.srt").unlink()
        with self.assertRaises(FileNotFoundError):
            package(self.run)
        self.assertFalse((self.root / "outbox").exists())

    def test_query_and_hashtag_derivation_and_cap(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        script = load(self.run, "script")
        script.search_query = "合成テスト"
        save(self.run, "script", script)
        brief = load(self.run, "brief")
        brief.constraints = ["#既存"]
        save(self.run, "brief", brief)
        publish = load(package(self.run), "publish")
        self.assertEqual(publish.search_query, script.search_query)
        self.assertEqual(publish.hashtags, ["#既存", "#short_video", "#合成テスト"])
        self.assertTrue(publish.description.startswith(script.lines[0].text))
        self.assertEqual(publish.description.count(script.search_query), 1)
        self.assertLess(publish.description.index(script.search_query), publish.description.index("出典"))
        script.lines[0].text = "合成テストを説明します"
        save(self.run, "script", script)
        brief.constraints = ["#a", "#a", "#b", "#c", "#d", "#e", "#f"]
        save(self.run, "brief", brief)
        publish = load(package(self.run), "publish")
        self.assertEqual(publish.description.count(script.search_query), 1)
        self.assertEqual(publish.hashtags, ["#a", "#b", "#c", "#d", "#e"])

    def test_metadata_gate_and_recovery(self):
        save(self.run, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        script = load(self.run, "script")
        script.title = "長" * 101
        save(self.run, "script", script)
        with self.assertRaisesRegex(ValueError, "metadata"):
            package(self.run)
        self.assertFalse(load(self.run, "qa").passed)
        self.assertFalse((self.root / "outbox").exists())
        script.title = "修正"
        save(self.run, "script", script)
        self.assertTrue(package(self.run).is_dir())
        self.assertTrue(load(self.run, "qa").passed)

    def test_metadata_pending_boundaries_and_existing_publish(self):
        pending = qa.check_metadata(self.run)
        self.assertTrue(pending.passed)
        self.assertIn("pending", pending.detail)
        valid = dict(title="あ" * 100, description="q" + "あ" * 4999, search_query="q",
                     hashtags=["#a", "#b", "#c"], platforms=["x"], synthetic_media=False)
        save(self.run, "publish", Publish(**valid))
        self.assertTrue(qa.check_metadata(self.run).passed)
        for changes in [dict(title="あ" * 101), dict(description="q" * 5001),
                        dict(hashtags=["#a", "#b"]), dict(hashtags=["#a"] * 6),
                        dict(search_query="absent")]:
            with self.subTest(changes=list(changes)):
                save(self.run, "publish", Publish(**(valid | changes)))
                check = qa.check_metadata(self.run)
                self.assertFalse(check.passed)
                self.assertEqual((check.severity, check.redo_stage), ("block", "package"))


if __name__ == "__main__":
    unittest.main()
