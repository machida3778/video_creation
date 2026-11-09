"""Source timing integration: synthetic media only, every network call mocked."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from vg import analyze, cli, clips, library, media, mix, package, qa, registry, run as runs, tts
from vg.schemas import (Asset, Check, ClipPlan, Layout, Line, Manifest, Music, QAReport, Route,
                        Script, Storyboard)
from vg.timeline import build


class ClipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        runs.write_state(self.root, {"stages": {}})
        self.plan = ClipPlan(source="https://example.invalid/stream", license_id="hololive_clip", segments=[
            {"id": "later", "start": 2, "end": 3.2, "crop": "fit", "focus_x": 0},
            {"id": "earlier", "start": 0.4, "end": 1.2}])
        self.script = Script(title="合成テスト", lines=[
            Line(id="L1", text="面白い", start=0.2, end=1),
            Line(id="L2", text="場面です", start=1.2, end=2)])
        self.route()
        media.save(self.root, "clips", self.plan)
        media.save(self.root, "script", self.script)
        self.source = self.root / "synthetic.mp4"
        self.calls = []

    def route(self, type_id="clip_highlight", **params):
        media.save(self.root, "route", Route(type_id=type_id, params=params, confidence=1, reason="synthetic"))

    def fixture(self):
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=4",
                   "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000:duration=4",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(self.source)])

    def prepare(self, *, subtitles_fail=False):
        real_run = media.run

        def fake_run(cmd, **kwargs):
            if cmd[:2] != ["uvx", "yt-dlp"]:
                return real_run(cmd, **kwargs)
            self.calls.append(cmd)
            directory = Path(cmd[cmd.index("-P") + 1])
            if "--skip-download" in cmd:
                self.assertIn("--write-subs", cmd)
                self.assertIn("--write-auto-subs", cmd)
                self.assertEqual(cmd[cmd.index("--sub-langs") + 1], "ja,ja-orig")
                if subtitles_fail:
                    raise RuntimeError("synthetic subtitle rate limit")
                (directory / "video.ja.vtt").write_text(
                    "WEBVTT\n\n00:00:00.100 --> 00:00:01.000\n早い場面\n\n"
                    "00:00:02.100 --> 00:00:02.500\n遅い場面\n\n"
                    "00:00:03.100 --> 00:00:03.900\n末尾\n", encoding="utf-8")
                return
            self.assertIn("--force-keyframes-at-cuts", cmd)
            self.assertIn("--ffmpeg-location", cmd)
            start, end = map(float, cmd[cmd.index("--download-sections") + 1][1:].split("-"))
            # Emulate yt-dlp's section-relative clock with an exactly cut local fixture.
            real_run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                      "-ss", str(start), "-i", str(self.source), "-t", str(end-start),
                      "-c:v", "libx264", "-c:a", "aac", str(directory / "section.mp4")])

        with patch.object(library, "_video_meta", return_value={"title": "Synthetic source",
                "url": self.plan.source, "channel": "Synthetic channel", "duration": 4}), \
                patch.object(media, "run", side_effect=fake_run), \
                patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-do-not-forward"}):
            return clips.prepare(self.root)

    def test_type_and_schema(self):
        spec = registry.load_types()["clip_highlight"]
        self.assertEqual((spec.status, spec.pipeline, spec.timing), ("experimental", "clipping", "source"))
        self.assertEqual(spec.voice, {})
        self.assertEqual(spec.rights["required_evidence"], ["clip_guideline_url", "source_url"])
        self.assertEqual(spec.params, {"crop": "fill", "focus_x": 0.5})
        self.assertEqual((spec.length["youtube_shorts"].target, spec.length["youtube_shorts"].min,
                          spec.length["youtube_shorts"].max), (45, 20, 59))
        self.assertEqual(media.load(self.root, "clips"), self.plan)
        self.assertEqual([s.id for s in self.plan.segments], ["later", "earlier"])
        self.assertEqual(self.plan.total_sec, 2)
        self.assertEqual(Storyboard(type_id="clip_highlight").scenes, [])
        with self.assertRaises(ValueError):
            Storyboard(type_id="ranking")

    def test_bad_plans_fail(self):
        for changes in ({"source": "/tmp/unlicensed.mp4"}, {"guideline": "not-a-url"},
                        {"segments": []}, {"segments": [{"id": "x", "start": 0, "end": 59.1}]},
                        {"segments": [{"id": "x", "start": 0, "end": 30},
                                      {"id": "y", "start": 50, "end": 80}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ClipPlan.model_validate({**self.plan.model_dump(), **changes})
        for changes in ({"id": "../escape"}, {"start": -1}, {"end": 2}, {"end": 2.59},
                        {"end": float("inf")}, {"focus_x": 1.1}, {"focus_x": float("nan")},
                        {"crop": "cover"}):
            invalid = self.plan.model_dump()
            invalid["segments"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ClipPlan.model_validate(invalid)
        invalid = self.plan.model_dump()
        invalid["segments"][1]["id"] = "later"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            ClipPlan.model_validate(invalid)

    def test_caption_bounds_and_overlap_validate_before_network(self):
        for changes in ({"start": None}, {"end": None}, {"start": 1.1}, {"end": 2.1}):
            script = deepcopy(self.script)
            script.lines[0] = Line.model_validate({**script.lines[0].model_dump(), **changes})
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "source captions"):
                clips.source_timings(script, self.plan)
        script = deepcopy(self.script)
        script.lines[1].start = 0.9
        media.save(self.root, "script", script)
        with patch.object(library, "_video_meta") as network, self.assertRaises(ValueError):
            clips.prepare(self.root)
        network.assert_not_called()

    def test_subtitles_map_to_reordered_output_and_trim_boundaries(self):
        cues = [{"start": 0.1, "end": 1, "text": "early"},
                {"start": 2.1, "end": 2.5, "text": "late"},
                {"start": 3.1, "end": 3.9, "text": "tail"},
                {"start": 6, "end": 7, "text": "outside"}]
        self.assertEqual(clips.map_subtitles(cues, self.plan), [
            {"start": 0.1, "end": 0.5, "text": "late"},
            {"start": 1.1, "end": 1.2, "text": "tail"},
            {"start": 1.2, "end": 1.8, "text": "early"}])

    def test_sections_exact_media_licence_subtitles_resume_and_mix(self):
        self.fixture()
        timings = self.prepare()
        self.assertEqual(timings.total_sec, 2)
        self.assertTrue(all(t.audio == "" for t in timings.lines))
        self.assertEqual(len([c for c in self.calls if "--download-sections" in c]), 2)
        self.assertEqual(len([c for c in self.calls if "--skip-download" in c]), 1)
        for segment in self.plan.segments:
            info = media.probe(self.root / f"clips/{segment.id}.mp4")
            self.assertEqual(next(s for s in info["streams"] if s["codec_type"] == "video")["codec_name"], "h264")
            self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
            self.assertAlmostEqual(float(info["format"]["duration"]), segment.end-segment.start, delta=0.04)
            with wave.open(str(self.root / f"clips/{segment.id}.wav")) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels()), (48000, 1))
                self.assertAlmostEqual(wav.getnframes()/48000, segment.end-segment.start, places=3)
        manifest = media.load(self.root, "manifest")
        self.assertEqual(len(manifest.assets), 2)
        for a in manifest.assets:
            self.assertEqual((a.kind, a.origin), ("video", "library"))
            self.assertTrue(a.license_url and a.commercial_ok and a.params["credit_first"])
            self.assertEqual(a.credit, f"元動画: Synthetic source\n{self.plan.source}")
        subs = json.loads((self.root / "clips/source_subs.json").read_text())
        self.assertEqual([s["text"] for s in subs], ["遅い場面", "末尾", "早い場面"])
        timeline = build(self.script, timings, None, manifest, timing="source", clip_plan=self.plan)
        self.assertEqual(mix.prepare_mix(self.root, timeline)["voice"]["sources"][0]["start"], 0)
        mixed = mix.render_audio(self.root, timeline, self.root / "mixed.wav")
        self.assertAlmostEqual(float(media.probe(mixed)["format"]["duration"]), 2, places=3)
        self.calls.clear()
        self.prepare(subtitles_fail=True)
        self.assertFalse(any("--download-sections" in c for c in self.calls))
        self.assertEqual(media.load(self.root, "manifest"), manifest)
        self.assertEqual(json.loads((self.root / "clips/source_subs.json").read_text()), [])

    def test_licences_fail_before_download(self):
        original = self.plan.model_dump()
        spec = {"id": "bad", "name": "synthetic", "commercial_ok": True, "kinds": ["video"], "url": None}
        for changes, entry in (({"license_id": "unknown"}, spec),
                               ({"license_id": "bad"}, spec),
                               ({"license_id": "bad", "guideline": "https://example.invalid/terms"},
                                {**spec, "kinds": ["image"]}),
                               ({"license_id": "bad", "guideline": "https://example.invalid/terms"},
                                {**spec, "commercial_ok": False})):
            media.save(self.root, "clips", ClipPlan.model_validate({**original, **changes}))
            with patch.object(library, "load_licenses", return_value={"bad": entry}), \
                    patch.object(library, "_video_meta") as network, self.assertRaises(ValueError):
                clips.prepare(self.root)
            network.assert_not_called()

    def manifest(self):
        return Manifest(assets=[Asset(id=s.id, path=f"clips/{s.id}.mp4", kind="video", origin="library",
                                     license="synthetic", license_url="https://example.invalid/terms", commercial_ok=True)
                                for s in self.plan.segments])

    def test_timeline_crop_focus_captions_layout_and_no_tail(self):
        timings = clips.source_timings(self.script, self.plan)
        board = Storyboard(type_id="clip_highlight", header="合成タイトル", music=Music(asset="bgm"))
        manifest = self.manifest()
        manifest.assets.append(Asset(id="bgm", path="bgm.wav", kind="music", origin="generated",
                                     license="synthetic", commercial_ok=True))
        layout = Layout.model_validate({"media": {"mode": "box"}, "header": {}})
        before = board.model_dump()
        timeline = build(self.script, timings, board, manifest, layout=layout, clip_plan=self.plan,
                         params={"crop": "fill", "focus_x": 0.8})
        self.assertEqual(timeline.duration, 2)
        self.assertEqual([(c.start, c.end) for c in timeline.clips], [(0, 1.2), (1.2, 2)])
        self.assertEqual([(c.props["crop"], c.props["focus_x"], c.props["muted"]) for c in timeline.clips],
                         [("fit", 0, True), ("fill", 0.8, True)])
        self.assertEqual([(a.kind, a.start) for a in timeline.audio], [("voice", 0), ("voice", 1.2), ("music", 0)])
        self.assertEqual([(c.start, c.end) for c in timeline.captions], [(0.2, 1), (1.2, 2)])
        self.assertEqual(timeline.layout.header.text, "合成タイトル")
        self.assertEqual(board.model_dump(), before)
        with self.assertRaisesRegex(ValueError, "stale"):
            build(self.script, timings.model_copy(update={"total_sec": 3}), board, manifest, layout=layout,
                  clip_plan=self.plan)

    def test_cli_make_replaces_tts_and_allows_no_storyboard(self):
        timings = clips.source_timings(self.script, self.plan)
        media.save(self.root, "timings", timings)
        media.save(self.root, "manifest", self.manifest())
        self.route(crop="fit", focus_x=0.3)
        with patch.object(tts, "Voicevox", side_effect=AssertionError("no engine")):
            cli.stage(self.root, "timeline")
        self.assertEqual(media.load(self.root, "timeline").clips[1].props["crop"], "fit")
        with patch.object(cli, "stage") as stage:
            self.assertEqual(cli.main(["make", str(self.root)]), 0)
            self.assertEqual([c.args[1] for c in stage.call_args_list], ["clips", *cli.PIPELINE[1:]])
        with patch.object(clips, "prepare") as prepare:
            self.assertEqual(cli.main(["clips", str(self.root)]), 0)
            prepare.assert_called_once_with(self.root)
        with self.assertRaisesRegex(ValueError, "vg clips"):
            cli.stage(self.root, "tts")
        cli.stage(self.root, "assets")
        self.assertEqual(media.load(self.root, "storyboard").scenes, [])

    def test_source_qa_uses_audio_hook_keeps_originality_and_blocks_missing_rights(self):
        timings = clips.source_timings(self.script, self.plan)
        manifest = self.manifest()
        media.save(self.root, "manifest", manifest)
        media.save(self.root, "timings", timings)
        media.save(self.root, "timeline", build(self.script, timings, None, manifest,
                   timing="source", clip_plan=self.plan))
        metrics = analyze.analyze_run(self.root)
        self.assertEqual((metrics["first_speech_sec"], metrics["first_text_sec"]), (0, 0.2))
        self.assertTrue(qa.check_hook(metrics, registry.load_style()).passed)
        self.assertEqual(qa.check_speech_rate(metrics, registry.load_style()).severity, "warn")
        self.assertEqual(qa.check_yomi(self.root).detail, "no synthesis")
        self.assertTrue(qa.check_source_rights(self.root)[0].passed)
        other = self.root / "others" / "other"
        other.mkdir(parents=True)
        media.save(other, "script", self.script)
        self.assertFalse(qa.check_originality(self.root, runs_root=other.parent).passed)
        manifest.assets[0].license_url = None
        media.save(self.root, "manifest", manifest)
        media.save(self.root, "qa", QAReport(checks=[Check(id="rights", passed=True)]))
        with self.assertRaisesRegex(ValueError, "source rights"):
            package.package(self.root)
        self.assertFalse(media.load(self.root, "qa").passed)
        self.route("ranking")
        self.assertEqual(qa.check_source_rights(self.root), [])
        self.assertFalse(qa.check_yomi(self.root).passed)

    def test_source_packaging_can_repair_stale_metadata(self):
        media.save(self.root, "manifest", self.manifest())
        media.save(self.root, "qa", QAReport(checks=[Check(id="metadata", passed=False)]))
        # It must reach deliverable checks; stale metadata is regenerated later in packaging.
        with self.assertRaises(FileNotFoundError):
            package.package(self.root)


if __name__ == "__main__":
    unittest.main()
