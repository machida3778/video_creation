from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vg.media import load, save
from vg.package import package
from vg.schemas import Asset, Brief, Check, Line, Manifest, QAReport, Research, Script, Source


class PackageTests(unittest.TestCase):
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
        (self.run / "final.mp4").write_bytes(b"test video")
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
        self.assertEqual({p.name for p in destination.iterdir()}, {"final.mp4", "final.srt", "publish.json"})
        for name in ("final.mp4", "final.srt", "publish.json"):
            self.assertEqual((destination / name).read_bytes(), (self.run / name).read_bytes())
        publish = load(destination, "publish")
        self.assertEqual(publish.title, "タイトル")
        self.assertEqual(publish.description, "冒頭の説明\n\n出典\n- 資料 https://example.test/source\n\nVOICEVOX:ずんだもん")
        self.assertEqual(publish.hashtags, ["#動画", "#test"])
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
        self.assertEqual(publish.hashtags, [])
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


if __name__ == "__main__":
    unittest.main()
