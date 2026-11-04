"""Harness improvements exercised only with synthetic specifications and temporary media."""
import contextlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from PIL import Image

from vg import analyze, assets, cli, media, qa, registry, sfx
from vg.schemas import (Asset, AudioClip, Caption, Clip, Line, LineTiming, Manifest, Publish,
                        Scene, Script, Storyboard, Timeline, Timings)
from vg.timeline import build


class SpecTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "runs" / "current" / "variants" / "v1"
        self.run.mkdir(parents=True)
        self.style = registry.load_style(self.root / "missing.yaml")
        media.save(self.run, "script", Script(title="synthetic", lines=[Line(id="L1", text="表示文字" * 24, yomi="ア")]))
        media.save(self.run, "timings", Timings(sample_rate=48000, total_sec=9.5, lines=[
            LineTiming(line_id="L1", audio="test.wav", start=0.3, end=9.3)]))
        self.timeline = Timeline(duration=10, clips=[
            Clip(start=0, end=10, component="ken_burns_image", props={"cut_sec": 2.5}),
            Clip(start=0, end=1, component="hook_title", layer=1, props={"text": "冒頭"})], captions=[
            Caption(start=0.5, end=4, text="あいう\nえお"), Caption(start=3, end=8.5, text="かきく")])
        media.save(self.run, "timeline", self.timeline)
        media.save(self.run, "manifest", Manifest())

    def test_style_defaults_partial_config_and_validation(self):
        self.assertEqual(self.style, registry.load_style())
        path = self.root / "style.yaml"
        path.write_text("max_static_sec: 2\nsfx_gain_db: -19\n", encoding="utf-8")
        loaded = registry.load_style(path)
        self.assertEqual(loaded["max_static_sec"], 2)
        self.assertEqual(loaded["sfx_gain_db"], -19)
        self.assertEqual(loaded["min_speech_cps"], 7)
        for text in ["[]", "max_static_sec: 0", "caption_coverage_min: 1.1", "min_speech_cps: .nan",
                     "sfx_max_per_10s: -1", "unknown: 1"]:
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                registry.load_style(path)

    def test_spec_measurements_union_characters_and_internal_cuts(self):
        result = analyze.analyze_run(self.run)
        self.assertEqual(result["visual_change_times"], [0, 2.5, 5, 7.5])
        self.assertEqual(result["visual_intervals"]["max_sec"], 2.5)
        self.assertEqual(result["first_text_sec"], 0)
        self.assertEqual(result["first_speech_sec"], 0.3)
        self.assertAlmostEqual(result["caption_coverage"], 0.85)
        self.assertEqual(result["speech_characters"], 96)
        self.assertAlmostEqual(result["speech_seconds"], 9)
        self.assertAlmostEqual(result["speech_cps"], 96 / 9)
        self.assertEqual(result["mean_caption_characters"], 4)

    def test_frame_rounding_and_empty_text(self):
        self.timeline.clips = [Clip(start=0, end=1, component="ken_burns_image", props={"cut_sec": 0.15}),
                               Clip(start=0, end=1, component="title_card", props={"text": None})]
        self.timeline.captions = []
        media.save(self.run, "timeline", self.timeline)
        result = analyze.analyze_run(self.run)
        self.assertAlmostEqual(result["visual_change_times"][1], 5 / 30)
        self.assertEqual(result["visual_intervals"]["max_sec"], 9 + 5 / 30)
        self.assertIsNone(result["first_text_sec"])
        self.assertEqual(result["caption_coverage"], 0)
        self.assertEqual(result["mean_caption_characters"], 0)
        self.timeline.clips[0].props["cut_sec"] = True
        self.timeline.clips[0].end = 10
        media.save(self.run, "timeline", self.timeline)
        self.assertEqual(analyze.analyze_run(self.run)["visual_intervals"]["max_sec"], 10)

    def test_intervals_include_edges_and_nearest_rank_p90(self):
        result = analyze.interval_stats([2, 3, 3], 10)
        self.assertEqual(result, {"intervals_sec": [2, 1, 7], "median_sec": 2, "p90_sec": 7, "max_sec": 7})
        self.assertEqual(analyze.interval_stats([], 10)["max_sec"], 10)

    def test_hook_rate_coverage_and_style_thresholds(self):
        metrics = analyze.analyze_run(self.run)
        self.assertTrue(qa.check_hook(metrics, self.style).passed)
        self.assertTrue(qa.check_speech_rate(metrics, self.style).passed)
        self.assertTrue(qa.check_caption_coverage(metrics, self.style).passed)
        changed = {**metrics, "first_text_sec": 0.301, "speech_cps": 6.99, "caption_coverage": 0.849}
        for check, stage in [(qa.check_hook(changed, self.style), "storyboard"),
                             (qa.check_speech_rate(changed, self.style), "tts"),
                             (qa.check_caption_coverage(changed, self.style), "timeline")]:
            self.assertFalse(check.passed)
            self.assertEqual(check.redo_stage, stage)
            self.assertRegex(check.detail, r"\d")
        for key in ("first_text_sec", "first_speech_sec"):
            self.assertFalse(qa.check_hook({**metrics, key: None}, self.style).passed)
        media.save(self.run, "timings", Timings(sample_rate=48000, total_sec=0, lines=[]))
        metrics = analyze.analyze_run(self.run)
        self.assertIsNone(metrics["first_speech_sec"])
        self.assertEqual(metrics["speech_cps"], 0)

    def test_tempo_decision_uses_spec_not_pixels(self):
        metrics = analyze.analyze_run(self.run)
        with patch.object(qa, "scene_changes", return_value=[]):
            check = qa.check_tempo(self.run, metrics, self.style)
            self.assertTrue(check.passed)
            self.assertIn("longest=10.000s", check.detail)
            self.assertFalse(qa.check_tempo(self.run, metrics, {**self.style, "max_static_sec": 2}).passed)
        with patch.object(qa, "scene_changes", side_effect=RuntimeError("test")):
            self.assertTrue(qa.check_tempo(self.run, metrics, self.style).passed)
        self.timeline.clips = []
        media.save(self.run, "timeline", self.timeline)
        with patch.object(qa, "scene_changes", return_value=[1, 2, 3, 4, 5, 6, 7, 8, 9]):
            self.assertFalse(qa.check_tempo(self.run, analyze.analyze_run(self.run), self.style).passed)

    def test_originality_excludes_entire_parent_run_and_scans_both_layouts(self):
        script = media.load(self.run, "script")
        media.save(self.run.parent / "v2", "script", script)
        media.save(self.run.parent.parent, "script", script)
        root = self.root / "runs"
        check = qa.check_originality(self.run, runs_root=root)
        self.assertTrue(check.passed)
        self.assertIn("compared scripts=0", check.detail)
        other = root / "other"
        media.save(other, "script", Script(title="other", lines=[Line(id="L1", text="abcdefghi")]))
        self.assertTrue(qa.check_originality(self.run, runs_root=root).passed)
        media.save(other / "variants" / "v1", "script", script)
        check = qa.check_originality(self.run, runs_root=root)
        self.assertFalse(check.passed)
        self.assertIn("Jaccard=1.0000", check.detail)
        self.assertIn("compared scripts=2", check.detail)
        self.assertTrue(qa.check_originality(self.run, runs_root=root,
                                           style={**self.style, "originality_max_similarity": 1}).passed)
        media.save(other, "script", script)
        self.assertFalse(qa.check_originality(self.run.parent.parent, runs_root=root).passed)
        # Line IDs, title and yomi do not affect the character n-grams; short identical texts still match.
        tiny = Script(title="tiny", lines=[Line(id="new", text="あい", yomi="アイ")])
        media.save(self.run, "script", tiny)
        media.save(other, "script", tiny)
        self.assertFalse(qa.check_originality(self.run, runs_root=root).passed)

    def test_disclosure_shared_rule_and_pending_metadata(self):
        common = dict(id="test", path="test.png", license="test", commercial_ok=True)
        for kind, origin, params, realistic in [
            ("image", "library", {"realistic": True}, True),
            ("video", "generated", {"prompt": "PHOTOREAL scene"}, True),
            ("image", "generated", {"prompt": "cinematic photo"}, True),
            ("image", "generated", {"prompt": "vector artwork"}, False),
            ("voice", "generated", {"prompt": "realistic", "realistic": True}, False)]:
            with self.subTest(kind=kind, params=params):
                media.save(self.run, "manifest", Manifest(assets=[Asset(kind=kind, origin=origin, params=params, **common)]))
                (self.run / "publish.json").unlink(missing_ok=True)
                pending = qa.check_disclosure(self.run)
                self.assertTrue(pending.passed)
                self.assertIn("pending", pending.detail)
                for flag in (False, True):
                    media.save(self.run, "publish", Publish(title="t", description="d", platforms=[], synthetic_media=flag))
                    self.assertEqual(qa.check_disclosure(self.run).passed, flag or not realistic)

    def test_originality_jaccard_exact_threshold(self):
        media.save(self.run, "script", Script(title="one", lines=[Line(id="a", text="ab"), Line(id="b", text="cde")]))
        media.save(self.root / "runs" / "other", "script", Script(title="two", lines=[Line(id="c", text="abcdf")]))
        # {abc, bcd, cde} vs {abc, bcd, cdf}: intersection=2, union=4.
        for limit, expected in [(0.5, True), (0.4999, False)]:
            check = qa.check_originality(self.run, runs_root=self.root / "runs",
                                        style={**self.style, "originality_max_similarity": limit})
            self.assertEqual(check.passed, expected)
            self.assertIn("Jaccard=0.5000", check.detail)

    def test_sfx_density_sliding_window_duplicates_and_boundary(self):
        def check(times, maximum=3):
            timeline = Timeline(duration=30, clips=[], audio=[
                AudioClip(start=t, src="sfx.wav", kind="sfx") for t in times] + [
                AudioClip(start=0, src="voice.wav", kind="voice")])
            return qa.check_sfx_density(timeline, {**self.style, "sfx_max_per_10s": maximum})
        self.assertTrue(check([]).passed)
        self.assertTrue(check([0, 1, 2, 10]).passed)
        self.assertFalse(check([0, 1, 2, 9.999]).passed)
        check_result = check([9, 10, 10, 11])
        self.assertFalse(check_result.passed)
        self.assertEqual(check_result.severity, "warn")
        self.assertTrue(check([9, 10, 10, 11], 4).passed)


class SyntheticMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / "cuts.mp4"
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "color=black:s=160x90:r=30:d=3", "-f", "lavfi", "-i",
                   "sine=frequency=440:sample_rate=48000:duration=3", "-vf",
                   "drawbox=color=white:t=fill:enable='gte(t,1)*lt(t,2)'", "-af",
                   "volume=0:enable='lt(t,1)+gte(t,2)'", "-c:v", "libx264", "-preset", "ultrafast",
                   "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.video)])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_pixel_audio_analysis_and_contact_sheet(self):
        result = analyze.analyze(self.video)
        self.assertEqual((result["width"], result["height"], result["fps"]), (160, 90, 30))
        self.assertAlmostEqual(result["duration"], 3, delta=0.05)
        self.assertEqual(len(result["scene_changes"]), 2)
        self.assertAlmostEqual(result["first_change_sec"], 1, delta=0.04)
        self.assertAlmostEqual(result["visual_intervals"]["max_sec"], 1, delta=0.04)
        self.assertAlmostEqual(result["audio_above_minus35_share"], 1 / 3, delta=0.04)
        for value in result["loudness"].values():
            self.assertTrue(math.isfinite(value))
        self.assertIn("尺 3.", analyze.japanese_summary(result))
        json.dumps(result, allow_nan=False)
        with Image.open(result["contact_sheet"]) as sheet:
            self.assertEqual(sheet.format, "JPEG")
            self.assertEqual(sheet.size, (1080, 406))
            self.assertLess(max(sheet.getpixel((180, 101))), 10)
            self.assertGreater(min(sheet.getpixel((900, 101))), 240)
            self.assertLess(max(sheet.getpixel((900, 303))), 10)

    def test_one_frame_without_audio_and_silent_audio(self):
        video = self.root / "one.mp4"
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "color=red:s=160x90:r=30", "-frames:v", "1", "-c:v", "libx264", "-threads", "2", str(video)])
        result = analyze.analyze(video)
        self.assertIsNone(result["loudness"])
        self.assertEqual(result["audio_above_minus35_share"], 0)
        self.assertIsNone(result["first_change_sec"])
        with Image.open(result["contact_sheet"]) as sheet:
            for x, y in [(180, 101), (900, 303)]:
                self.assertGreater(sheet.getpixel((x, y))[0], 240)
        silent = self.root / "silent.wav"
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "anullsrc=r=48000:cl=mono", "-t", "1", str(silent)])
        self.assertEqual(analyze.audio_activity(silent, 1), 0)
        json.dumps(analyze.measure_loudness(silent), allow_nan=False)

    def test_cli_analyze_direct_and_variant_path(self):
        output = self.root / "report" / "analysis.json"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(cli.main(["analyze", str(self.video), "--json", str(output)]), 0)
        self.assertIn("コンタクトシート", printed.getvalue())
        self.assertEqual(json.loads(output.read_text())["width"], 160)
        with patch.object(cli.runs, "resolve", return_value=self.root) as resolve, \
                patch.object(analyze, "analyze", return_value=json.loads(output.read_text())), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["analyze", "synthetic:v2/final.mp4"]), 0)
            resolve.assert_called_once_with("synthetic:v2")

    def test_audio_activity_pads_missing_audio_at_both_edges(self):
        for offset in (0, 1):
            with self.subTest(audio_start=offset):
                video = self.root / f"short_audio_{offset}.mp4"
                media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                           "color=blue:s=160x90:r=30:d=3", "-itsoffset", str(offset), "-f", "lavfi", "-i",
                           "sine=frequency=440:sample_rate=48000:duration=1", "-c:v", "libx264", "-preset",
                           "ultrafast", "-threads", "2", "-c:a", "aac", str(video)])
                self.assertAlmostEqual(analyze.audio_activity(video, 3), 1 / 3, delta=0.04)

    def test_original_sfx_build_copy_manifest_timeline_and_idempotence(self):
        paths = sfx.build(library_root=self.root)
        self.assertEqual({p.stem for p in paths}, {"whoosh", "pop", "ding", "tick"})
        for path in paths:
            with self.subTest(effect=path.stem), wave.open(str(path)) as audio:
                self.assertEqual((audio.getnchannels(), audio.getframerate(), audio.getsampwidth()), (1, 48000, 2))
                self.assertAlmostEqual(audio.getnframes() / 48000, sfx.SOURCES[path.stem][0], delta=0.002)
                self.assertTrue(any(audio.readframes(audio.getnframes())))
        run_dir = self.root / "synthetic_run"
        script = Script(title="synthetic", lines=[Line(id="L1", text="最初"), Line(id="L2", text="次へ")])
        timings = Timings(sample_rate=48000, total_sec=4, lines=[
            LineTiming(line_id="L1", audio="one.wav", start=0.2, end=1.8),
            LineTiming(line_id="L2", audio="two.wav", start=2, end=4)])
        board = Storyboard(type_id="test", scenes=[
            Scene(id="C1", component="background", line_ids=["L1"], props={"sfx": "whoosh"}),
            Scene(id="C2", component="background", line_ids=["L2"], props={"sfx": [
                {"id": "pop", "at": 0.25}, {"id": "ding", "at": 1}, {"id": "pop", "at": 2}]})])
        with patch.object(assets, "_run_on_lab_gpu", side_effect=AssertionError("GPU called")):
            manifest = assets.generate(run_dir, board, library_root=self.root)
            self.assertEqual(len(manifest.assets), 3)
            again = assets.generate(run_dir, board, manifest, library_root=self.root / "absent")
            self.assertEqual(again, manifest)
        for asset in manifest.assets:
            self.assertEqual((asset.kind, asset.origin, asset.tool), ("sfx", "generated", "vg sfx"))
            self.assertEqual(asset.license, "original (procedurally generated by vg sfx)")
            self.assertTrue(asset.commercial_ok)
            self.assertTrue((run_dir / asset.path).is_file())
        with patch("vg.timeline.load_style", return_value={"sfx_gain_db": -19}):
            timeline = build(script, timings, board, manifest)
        effects = [a for a in timeline.audio if a.kind == "sfx"]
        self.assertEqual([a.start for a in effects], [0, 2.25, 3, 4])
        self.assertTrue(all(a.gain_db == -19 for a in effects))
        self.assertTrue(all("sfx" not in c.props for c in timeline.clips))
        self.assertEqual(len([a for a in timeline.audio if a.kind == "voice"]), 2)
        with self.assertRaisesRegex(ValueError, "not in manifest"):
            build(script, timings, board)
        board.scenes[1].props["sfx"] = [{"id": "pop", "at": 2.5}]
        with self.assertRaisesRegex(ValueError, "outside scene"):
            build(script, timings, board, manifest)
        board.scenes[0].props["sfx"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown SFX"):
            assets.generate(run_dir, board, library_root=self.root)
        board.scenes[0].props["sfx"] = "tick"
        with self.assertRaisesRegex(FileNotFoundError, "vg sfx build"):
            assets.generate(run_dir, board, library_root=self.root / "absent")

    def test_invalid_sfx_events_and_cli_build(self):
        for value in [2, [{}], [{"id": "pop", "at": -1}], [{"id": "pop", "at": float("nan")}],
                      [{"id": "pop", "at": True}], [{"id": "pop", "extra": 1}]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                sfx.events(value)
        with patch.object(sfx, "build", return_value=[self.root / "tick.wav"]) as built, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["sfx", "build"]), 0)
            built.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
