"""Reference formats: subtitle parsing, layout validation, timeline/QA with a layout, clip licences."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from vg import library, media, package, qa, reference
from vg.captions import band_text, caption_box
from vg.schemas import (Asset, Caption, Clip, FormatProfile, Layout, Line, LineTiming, Manifest, Scene, Script,
                        Storyboard, Timeline, Timings)
from vg.timeline import build

VTT = """WEBVTT
Kind: captions
Language: ja

00:00:00.000 --> 00:00:01.500
<c>ホロライブで</c>

00:00:01.500 --> 00:00:03.000
ホロライブで
いちばん多いのは[音楽]

00:00:03.000 --> 00:00:04.000
[音楽]
"""


def profile(**layout) -> dict:
    return {"id": "test-format", "name": "テスト", "summary": "要約", "references": [{"source": "file.mp4"}],
            "types": ["ranking"], "structure": [{"role": "hook", "share": 0.2, "note": "問い"},
                                                {"role": "item", "share": 0.8, "note": "項目"}],
            "pacing": {"median_shot_sec": 3, "max_static_sec": 5}, "layout": layout,
            "header_rule": "問いを書く" if "header" in layout else None}


class FormatTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patcher = patch.dict(os.environ, {"VG_FORMATS_DIR": str(self.root / "formats")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_format(self, data: dict) -> None:
        directory = self.root / "formats" / data["id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "format.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_vtt_drops_rolling_repeats_tags_and_sound_labels(self):
        cues = reference.parse_vtt(VTT)
        self.assertEqual([c["text"] for c in cues], ["ホロライブで", "いちばん多いのは"])
        self.assertEqual(cues[1]["start"], 1.5)

    def test_layout_rejects_text_under_the_shorts_ui_and_overlaps(self):
        Layout.model_validate({"header": {"top": 260, "height": 200}})
        for bad in [{"caption": {"y": 1400, "size": 90}}, {"header": {"top": 1200, "height": 300}},
                    {"header": {"top": 1100, "height": 200}}, {"caption": {"max_width": 900}},
                    {"header": {"top": 100}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Layout.model_validate(bad)

    def test_profile_needs_shares_summing_to_one_and_a_header_rule(self):
        FormatProfile.model_validate(profile())
        broken = profile()
        broken["structure"][0]["share"] = 0.5
        with self.assertRaises(ValueError):
            FormatProfile.model_validate(broken)
        no_rule = profile(header={"top": 260, "height": 200})
        no_rule["header_rule"] = None
        with self.assertRaises(ValueError):
            FormatProfile.model_validate(no_rule)

    def test_load_format_checks_directory_id_and_implemented_types(self):
        self.write_format(profile())
        self.assertEqual(reference.load_format("test-format").id, "test-format")
        planned = profile()
        planned.update(id="planned-format", types=["horror_story"])
        self.write_format(planned)
        with self.assertRaisesRegex(ValueError, "not implemented"):
            reference.load_format("planned-format")
        mismatch = profile()
        mismatch["id"] = "other"
        directory = self.root / "formats" / "renamed"
        directory.mkdir(parents=True)
        (directory / "format.json").write_text(json.dumps(mismatch), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            reference.load_format("renamed")
        with self.assertRaises(ValueError):
            reference.format_dir("../escape")

    def _inputs(self, **board):
        script = Script(title="t", lines=[Line(id="L1", text="ホロライブで登録者がいちばん多いのは誰でしょうか")])
        timings = Timings(sample_rate=48000, total_sec=3.0, lines=[LineTiming(line_id="L1", audio="a.wav",
                                                                              start=0.0, end=3.0)])
        return script, timings, Storyboard(type_id="ranking", scenes=[
            Scene(id="C1", line_ids=["L1"], component="background")], **board)

    def test_timeline_applies_the_format_layout_and_header(self):
        self.write_format(profile(header={"top": 260, "height": 220, "size": 72},
                                  caption={"y": 1200, "size": 80, "max_width": 600}))
        script, timings, board = self._inputs(format="test-format", header="ホロライブ登録者数、1位は？")
        timeline = build(script, timings, board)
        self.assertEqual(timeline.layout.caption.size, 80)
        self.assertIn("\n", timeline.layout.header.text)
        self.assertEqual(caption_box("default", timeline.layout), (80, 600 - 80 * 0.2))  # minus the outline
        for caption in timeline.captions:
            self.assertTrue(all(80 * len(row) <= 584 for row in caption.text.split("\n")))
        self.assertTrue(qa.check_safe_area(timeline).passed)
        script, timings, missing = self._inputs(format="test-format")
        with self.assertRaisesRegex(ValueError, "storyboard.header"):
            build(script, timings, missing)
        script, timings, stray = self._inputs(header="見出し")
        with self.assertRaisesRegex(ValueError, "header"):
            build(script, timings, stray)

    def test_safe_area_uses_the_layout_width_and_checks_the_header(self):
        look = Layout.model_validate({"caption": {"size": 60, "max_width": 500},
                                      "header": {"top": 260, "height": 120, "size": 60, "text": "一行目\n二行目"}})
        timeline = Timeline(duration=2, clips=[Clip(start=0, end=2, component="background")],
                            captions=[Caption(start=0, end=2, text="九文字ある字幕です")], layout=look)
        self.assertFalse(qa.check_safe_area(timeline).passed)  # 9 x 60 = 540 > 500, header 2 x 75 > 120
        timeline.captions[0].text = "短い字幕"
        self.assertFalse(qa.check_safe_area(timeline).passed)
        timeline.layout = look.model_copy(update={"header": look.header.model_copy(update={"height": 200})})
        self.assertTrue(qa.check_safe_area(timeline).passed)
        self.assertEqual(Timeline.model_validate_json(timeline.model_dump_json()).layout.caption.max_width, 500)

    def test_review_findings(self):
        # Rolling captions: each cue repeats everything the previous one showed.
        rolling = ("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n一\n\n00:00:01.000 --> 00:00:02.000\n一\n二\n\n"
                   "00:00:02.000 --> 00:00:03.000\n一\n二\n三\n")
        self.assertEqual([c["text"] for c in reference.parse_vtt(rolling)], ["一", "二", "三"])
        # Title-style captions without a title look are drawn with the caption look.
        look = Layout.model_validate({"caption": {"size": 90, "box": "#000000", "stroke": None}})
        self.assertEqual(caption_box("title", look), (90, 780 - 45))
        # Footer text is checked like the header; bands must not overlap; overlays need room.
        footer = Layout.model_validate({"caption": {"y": 950}, "header": {"top": 260, "height": 120, "text": "上"},
                                        "footer": {"top": 1300, "height": 140, "size": 110, "text": "長" * 30}})
        timeline = Timeline(duration=1, clips=[Clip(start=0, end=1, component="background")], layout=footer)
        self.assertFalse(qa.check_safe_area(timeline).passed)
        for bad in [{"header": {"top": 260, "height": 200}, "footer": {"top": 300, "height": 100}},
                    {"header": {"top": 260, "height": 400}, "caption": {"y": 1150}}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Layout.model_validate(bad)
        # Emphasis only recolours band text, so it is not measured wider.
        wide = Layout.model_validate({"header": {"top": 260, "height": 200, "size": 72, "emphasis": ["一"],
                                                 "text": "一" * 10}})
        self.assertTrue(qa.check_safe_area(Timeline(duration=1, clips=[Clip(start=0, end=1, component="background")],
                                                    layout=wide)).passed)

    def test_replace_keeps_old_references_when_the_new_one_fails(self):
        base = self.root / "formats" / "keep"
        (base / "reference").mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            reference.add(str(self.root / "missing.mp4"), "keep", replace=True)
        self.assertTrue((base / "reference").is_dir())
        self.assertFalse((base / ".incoming").exists())

    def test_band_text_keeps_explicit_breaks(self):
        self.assertEqual(band_text("上\n下", 72), "上\n下")
        self.assertLessEqual(len(band_text("ホロライブ登録者数ランキングの一位は誰", 72).split("\n")), 2)


class ClipLicenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.image = self.run / "in.png"
        Image.new("RGB", (2000, 1000), "#336699").save(self.image)
        self.licenses = library.load_licenses()

    def test_repository_licences_name_terms_and_guard_kinds(self):
        self.assertEqual(self.licenses["hololive_clip"]["kinds"], ["video"])
        with self.assertRaisesRegex(ValueError, "--at"):
            library.add_image(self.run, str(self.image), asset_id="x", license_id="hololive_clip", subject="s")
        with self.assertRaisesRegex(ValueError, "--guideline"):
            library.add_image(self.run, str(self.image), asset_id="x", license_id="creator_clip", subject="s")
        with self.assertRaisesRegex(ValueError, "unknown licence"):
            library.add_image(self.run, str(self.image), asset_id="x", license_id="nope", subject="s")

    def test_image_is_recorded_and_its_credit_leads_the_description(self):
        asset = library.add_image(self.run, str(self.image), asset_id="talent", license_id="creator_clip",
                                  subject="配信者", guideline="https://example.com/guideline")
        self.assertEqual(asset.license_url, "https://example.com/guideline")
        with Image.open(self.run / asset.path) as image:
            self.assertEqual(image.width, 1080)
        manifest = media.load(self.run, "manifest")
        self.assertEqual([a.id for a in manifest.assets], ["talent"])
        self.assertTrue(manifest.assets[0].params["credit_first"])

    def test_a_local_file_is_never_deleted(self):
        named = self.run / "photo.download"
        Image.new("RGB", (10, 10)).save(named, format="PNG")
        library.add_image(self.run, str(named), asset_id="p", license_id="creator_clip", subject="s",
                          guideline="https://example.com/g")
        self.assertTrue(named.is_file())

    def test_video_still_records_the_source_title_and_url(self):
        def fake_still(url, at, target, crop=None):
            Image.new("RGB", (1920, 1080)).save(target)
            return {"x": 1, "y": 2, "w": 3, "h": 4, "frame": [1920, 1080]} if crop else None
        meta = {"title": "配信タイトル", "url": "https://www.youtube.com/watch?v=abc", "channel": "ch", "duration": 600}
        with patch.object(library, "_video_meta", return_value=meta), \
                patch.object(library, "_still_from_video", side_effect=fake_still):
            asset = library.add_image(self.run, meta["url"], asset_id="scene1", license_id="hololive_clip",
                                      subject="配信の一場面", at=12.5)
            with self.assertRaisesRegex(ValueError, "beyond"):
                library.add_image(self.run, meta["url"], asset_id="late", license_id="hololive_clip",
                                  subject="s", at=601)
        self.assertEqual(asset.credit, "元動画: 配信タイトル\nhttps://www.youtube.com/watch?v=abc")
        self.assertNotIn("crop", asset.params)
        with patch.object(library, "_video_meta", return_value=meta), \
                patch.object(library, "_still_from_video", side_effect=fake_still):
            cropped = library.add_image(self.run, meta["url"], asset_id="scene2", license_id="hololive_clip",
                                        subject="s", at=12.5, crop="0.8,0.4,0.6")
        self.assertEqual(cropped.params["crop"]["fractions"], [0.8, 0.4, 0.6])
        self.assertEqual(asset.params["at"], 12.5)
        self.assertEqual(asset.license_url, "https://hololivepro.com/terms/")
        # Creators without a published guideline (creator_open): stills from their videos, no guideline URL,
        # credited first like the clip licences; an image file is not accepted under it.
        with patch.object(library, "_video_meta", return_value=meta), \
                patch.object(library, "_still_from_video", side_effect=fake_still):
            open_still = library.add_image(self.run, meta["url"], asset_id="scene3", license_id="creator_open",
                                           subject="s", at=12.5)
        self.assertIsNone(open_still.license_url)
        self.assertTrue(open_still.commercial_ok and open_still.params["credit_first"])
        self.assertEqual(open_still.credit, "元動画: 配信タイトル\nhttps://www.youtube.com/watch?v=abc")
        with self.assertRaisesRegex(ValueError, "--at"):
            library.add_image(self.run, str(self.image), asset_id="x", license_id="creator_open", subject="s")


if __name__ == "__main__":
    unittest.main()
