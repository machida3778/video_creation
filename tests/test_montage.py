"""Music countdown integration, using only temporary synthetic specifications and audio."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vg import analyze, assets, cli, media, mix, qa, registry, run as runs, tts
from vg.schemas import (Asset, Line, Manifest, Music, Route, Scene, Script, Storyboard)
from vg.timeline import build


class MontageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = Script(title="synthetic", lines=[
            Line(id="title", text="最強ランキング"),
            Line(id="L6", text="架空六"),
            Line(id="top", text="Top"),
            *[Line(id=f"L{i}", text=f"架空{i}") for i in range(5, 0, -1)]])
        self.board = Storyboard(type_id="ranking_montage", scenes=[
            Scene(id="title", line_ids=["title"], component="section_card", props={"text": "最強ランキング", "sec": 1}),
            Scene(id="C6", line_ids=["L6"], component="ken_burns_image", transition="mirror",
                  props={"sec": 3.4, "overlays": [
                      {"component": "rank_label", "props": {"rank": 6, "name": "架空六", "tier": "normal"}}]}),
            Scene(id="top", line_ids=["top"], component="section_card", props={"text": "Top", "sec": 1.5}),
            *[Scene(id=f"C{i}", line_ids=[f"L{i}"], component="ken_burns_image", transition="mirror",
                    props={"sec": 3.4, "captions": True, "overlays": [
                        {"component": "rank_label", "props": {"rank": i, "name": f"架空{i}",
                         "tier": "top1" if i == 1 else "top3" if i <= 3 else "top5"}}]})
              for i in range(5, 0, -1)]], music=Music(asset="bgm"))
        self.manifest = Manifest(assets=[Asset(id="bgm", path="synthetic.wav", kind="music", origin="generated",
                                               license="synthetic", commercial_ok=True)])
        self.style = registry.load_style()
        runs.write_state(self.root, {"stages": {}})
        self.route("ranking_montage")
        media.save(self.root, "script", self.script)
        media.save(self.root, "storyboard", self.board)

    def route(self, type_id):
        media.save(self.root, "route", Route(type_id=type_id, confidence=1, reason="synthetic"))

    def timeline(self):
        timings = tts.music_timings(self.script, self.board)
        timeline = build(self.script, timings, self.board, self.manifest)
        media.save(self.root, "timings", timings)
        media.save(self.root, "timeline", timeline)
        return timings, timeline

    def test_type_loads_without_voice_or_speech_qa(self):
        spec = registry.load_types()["ranking_montage"]
        self.assertEqual((spec.status, spec.pipeline, spec.timing), ("experimental", "music_motion", "music"))
        self.assertEqual(spec.voice, {})
        self.assertEqual(registry.cast_for_type(spec, {}, {}), {})
        # 10位から (user, 2026-11-10: counting down from 15 was too long); the 1st place gets a longer reveal.
        self.assertEqual(spec.params["allowed_items"], list(range(5, 11)))
        self.assertEqual((spec.params["items"], spec.params["item_sec"], spec.params["first_sec"],
                          spec.params["top_card"]), (10, 3.4, 6.0, "Top"))
        self.assertEqual(spec.components, ["ken_burns_image", "rank_label", "section_card"])
        self.assertFalse({"yomi", "facts"} & set(spec.qa))
        self.assertIn("confidence", spec.qa)
        self.assertFalse(spec.research["required"])
        self.assertEqual(spec.research["required_when"], "ranking_claims_facts")
        limit = spec.length["youtube_shorts"]
        self.assertEqual((limit.target, limit.min, limit.max), (40, 30, 59))

    def test_countdown_starts_at_tenth_place_at_most(self):
        def board(count):
            scenes = [Scene(id=f"C{i}", line_ids=[f"L{i}"], component="ken_burns_image", props={
                "sec": 3.4, "overlays": [{"component": "rank_label", "props": {"rank": i, "name": f"架空{i}"}}]})
                for i in range(count, 0, -1)]
            script = Script(title="synthetic", lines=[Line(id=f"L{i}", text=f"架空{i}") for i in range(count, 0, -1)])
            return script, Storyboard(type_id="ranking_montage", scenes=scenes)
        self.assertEqual(len(tts.music_timings(*board(10)).lines), 10)
        with self.assertRaisesRegex(ValueError, "10位"):
            tts.music_timings(*board(11))
        script, too_high = board(3)
        too_high.scenes[0].props["overlays"][0]["props"]["rank"] = 15
        with self.assertRaisesRegex(ValueError, "from 15位"):
            tts.music_timings(script, too_high)

    def test_first_place_reveal_reaches_the_picture_and_its_effects_are_levelled_to_the_music(self):
        first = self.board.scenes[-1]
        first.props["sec"] = 6.0
        first.props["overlays"][0]["props"]["reveal"] = 1.6
        first.props["sfx"] = [{"id": "riser", "at": 0}, {"id": "impact", "at": 1.6}]
        for effect in ("riser", "impact"):
            self.manifest.assets.append(Asset(id=f"sfx_{effect}", path=f"{effect}.wav", kind="sfx",
                                              origin="generated", tool="vg sfx", license="original",
                                              commercial_ok=True))
        timings, timeline = self.timeline()
        base = [c for c in timeline.clips if c.layer == 0][-1]
        self.assertEqual((base.props["tier"], base.props["reveal"], base.end - base.start), ("top1", 1.6, 6.0))
        effects = [a for a in timeline.audio if a.kind == "sfx"]
        self.assertEqual([(a.src, round(a.start - base.start, 6)) for a in effects],
                         [("riser.wav", 0.0), ("impact.wav", 1.6)])
        for name, source in (("synthetic.wav", "sine=frequency=440:sample_rate=48000:duration=24"),
                             ("riser.wav", "sine=frequency=880:sample_rate=48000:duration=1.6"),
                             ("impact.wav", "sine=frequency=110:sample_rate=48000:duration=1")):
            media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", source,
                       "-af", "volume=0.3" if name != "synthetic.wav" else "volume=1", str(self.root / name)])
        report = mix.prepare_mix(self.root, timeline, style=self.style | {"sfx_vs_music_db": 3})
        music = next(c for c in report["clips"] if c["kind"] == "music")
        for clip in (c for c in report["clips"] if c["kind"] == "sfx"):
            self.assertAlmostEqual(clip["output_level_db"], music["output_level_db"] + 3, places=1)
        media.save(self.root, "timeline", timeline)
        self.assertTrue(qa.check_mix(self.root).passed)

    def test_storyboard_timings_and_no_mutation(self):
        before = self.board.model_dump()
        timing = tts.music_timings(self.script, self.board)
        self.assertEqual([t.line_id for t in timing.lines], [l.id for l in self.script.lines])
        self.assertEqual([(t.start, t.end) for t in timing.lines[:4]], [(0, 1), (1, 4.4), (4.4, 5.9), (5.9, 9.3)])
        self.assertAlmostEqual(timing.total_sec, 22.9)
        self.assertTrue(all(t.audio == "" and t.spans == [] for t in timing.lines))
        self.assertEqual(self.board.model_dump(), before)

    def test_bad_durations_or_line_coverage_fail_before_synthesis(self):
        for sec in (None, 0, -1, True, "3.4", float("nan"), float("inf")):
            with self.subTest(sec=sec), self.assertRaisesRegex(ValueError, "props.sec"):
                board = deepcopy(self.board)
                board.scenes[0].props["sec"] = sec
                tts.music_timings(self.script, board)
        for ids in ([], ["title", "L6"], ["absent"], ["L6"]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "one per scene"):
                board = deepcopy(self.board)
                board.scenes[0].line_ids = ids
                tts.music_timings(self.script, board)

    def test_tts_stage_never_constructs_a_voice_engine_or_loads_voices(self):
        with patch.object(tts, "Voicevox", side_effect=AssertionError("no engine")), \
                patch.object(tts, "load_voices", side_effect=AssertionError("no voices")), \
                patch.object(tts.narrator, "synthesize", side_effect=AssertionError("no GPU")):
            cli.stage(self.root, "tts")
        self.assertEqual(media.load(self.root, "timings"), tts.music_timings(self.script, self.board))
        self.assertFalse((self.root / "audio").exists())
        self.assertEqual(runs.read_state(self.root)["stages"]["tts"]["status"], "done")
        (self.root / "storyboard.json").unlink()
        with self.assertRaisesRegex(ValueError, "storyboard.json before vg tts"):
            cli.stage(self.root, "tts")

    def test_make_from_tts_uses_the_music_path(self):
        original_stage, stages = cli.stage, []

        def stage(run, name):
            stages.append(name)
            if name == "tts":
                original_stage(run, name)

        with patch.object(cli, "stage", side_effect=stage), \
                patch.object(tts, "Voicevox", side_effect=AssertionError("no engine")):
            self.assertEqual(cli.main(["make", str(self.root), "--from", "tts"]), 0)
        self.assertEqual(stages, cli.PIPELINE)
        self.assertEqual(media.load(self.root, "timings").lines[0].audio, "")

    def test_timeline_has_exact_scene_slots_bgm_and_no_captions_or_voices(self):
        timings, timeline = self.timeline()
        self.assertEqual(timeline.captions, [])
        self.assertEqual(timeline.duration, timings.total_sec)
        self.assertEqual([a.kind for a in timeline.audio], ["music"])
        self.assertEqual(timeline.audio[0].src, "synthetic.wav")
        self.assertFalse(timeline.audio[0].duck_under_voice)
        base = [c for c in timeline.clips if c.layer == 0]
        self.assertEqual([(c.start, c.end) for c in base], [(t.start, t.end) for t in timings.lines])
        self.assertEqual(base[1].props["transition"], "mirror")
        self.assertEqual((base[-1].props["tier"], base[-1].props["rank"]), ("top1", 1))
        # Production entrypoint explicitly respects the route's timing.
        media.save(self.root, "manifest", self.manifest)
        cli.stage(self.root, "timeline")
        self.assertEqual(media.load(self.root, "timeline"), timeline)

    def test_labels_cards_hook_coverage_speech_yomi_and_tempo(self):
        self.timeline()
        metrics = analyze.analyze_run(self.root)
        self.assertEqual(metrics["first_text_sec"], 0)
        self.assertIsNone(metrics["first_speech_sec"])
        self.assertEqual((metrics["speech_seconds"], metrics["speech_characters"], metrics["speech_cps"]), (0, 0, 0))
        self.assertEqual(metrics["caption_coverage"], 1)
        self.assertTrue(qa.check_hook(metrics, self.style).passed)
        self.assertTrue(qa.check_caption_coverage(metrics, self.style).passed)
        for check in (qa.check_speech_rate(metrics, self.style), qa.check_yomi(self.root)):
            self.assertTrue(check.passed)
            self.assertEqual(check.detail, "no narration")
        for first_text in (None, -0.01, 0.301):
            self.assertFalse(qa.check_hook({**metrics, "first_text_sec": first_text}, self.style).passed)
        with patch.object(qa, "scene_changes", return_value=[]):
            self.assertTrue(qa.check_tempo(self.root, metrics, self.style).passed)
            held = {**metrics, "visual_intervals": {"max_sec": 6.01}}
            self.assertFalse(qa.check_tempo(self.root, held, self.style).passed)

    def test_rank_label_alone_counts_as_early_text_and_blank_labels_do_not(self):
        _, timeline = self.timeline()
        timeline.clips = [c for c in timeline.clips if c.component == "rank_label"]
        timeline.clips[0].start = 0.1
        media.save(self.root, "timeline", timeline)
        metrics = analyze.analyze_run(self.root)
        self.assertEqual(metrics["first_text_sec"], 0.1)
        self.assertTrue(qa.check_hook(metrics, self.style).passed)
        for c in timeline.clips:
            c.props["name"] = " "
        media.save(self.root, "timeline", timeline)
        metrics = analyze.analyze_run(self.root)
        self.assertIsNone(metrics["first_text_sec"])
        self.assertFalse(qa.check_hook(metrics, self.style).passed)
        self.assertEqual(metrics["caption_coverage"], 0)

    def test_narration_still_needs_speech_yomi_captions_and_tail(self):
        timings, _ = self.timeline()
        self.route("ranking")
        # Even silent input on a narration route cannot obtain the music exemptions.
        metrics = analyze.analyze_run(self.root)
        self.assertEqual(metrics["timing"], "narration")
        self.assertFalse(qa.check_hook({**metrics, "first_speech_sec": None}, self.style).passed)
        self.assertFalse(qa.check_speech_rate(metrics, self.style).passed)
        self.assertFalse(qa.check_yomi(self.root).passed)
        board = self.board.model_copy(update={"type_id": "ranking"})
        timeline = build(self.script, timings, board, self.manifest)
        self.assertTrue(timeline.captions)
        self.assertEqual(len([a for a in timeline.audio if a.kind == "voice"]), len(self.script.lines))
        self.assertAlmostEqual(timeline.duration, timings.total_sec + 0.5)
        (self.root / "route.json").unlink()
        self.assertEqual(registry.timing_for_run(self.root), "narration")
        self.assertFalse(qa.check_yomi(self.root).passed)

    def test_serif_font_manifest_is_licensed_local_and_idempotent(self):
        for board in (self.board, Storyboard(type_id="ranking_montage", scenes=[self.board.scenes[1]])):
            with self.subTest(component=board.scenes[0].component):
                manifest = assets._prepare(self.root, board)
                fonts = [a for a in manifest.assets if a.kind == "font"]
                self.assertEqual(len(fonts), 1)
                font = fonts[0]
                self.assertEqual((font.origin, font.tool, font.license, font.license_url, font.commercial_ok),
                                 ("library", "system:Noto Serif CJK JP", "SIL Open Font License 1.1",
                                  "https://openfontlicense.org/", True))
                self.assertTrue((self.root / font.path).is_file())
                self.assertFalse(Path(font.path).is_absolute())
                self.assertEqual(assets._prepare(self.root, board, manifest), manifest)
                self.assertEqual(len(manifest.assets), 1)

    def test_music_only_audio_mixes_without_a_voice_file(self):
        _, timeline = self.timeline()
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "sine=frequency=440:sample_rate=48000:duration=24", str(self.root / "synthetic.wav")])
        report = mix.prepare_mix(self.root, timeline)
        self.assertIsNone(report["voice"])
        media.save(self.root, "timeline", timeline)
        self.assertTrue(qa.check_mix(self.root).passed)
        mixed = mix.render_audio(self.root, timeline, self.root / "mixed.wav")
        self.assertAlmostEqual(float(media.probe(mixed)["format"]["duration"]), timeline.duration, places=3)
        self.route("ranking")
        self.assertFalse(qa.check_mix(self.root).passed)

    def test_rankings_are_stated_with_confidence(self):
        self.timeline()
        check = qa.check_confidence(self.root)
        self.assertTrue(check.passed, check.detail)
        hedges = ["独断で決めた最強ランキング", "根拠はありません", "根拠は、\nないはずでした", "根拠なしの最強",
                  "勝手にランキング", "個人的な順位", "主観です", "異論は認める", "諸説あり", "あくまで目安",
                  "登録者数は人気の物差しのひとつです"]
        for text in hedges:
            with self.subTest(text=text):
                self.script.lines[0].text = text
                media.save(self.root, "script", self.script)
                check = qa.check_confidence(self.root)
                self.assertFalse(check.passed)
                self.assertIn("line title", check.detail)
        for fine in ("最強ランキング", "根拠のある数字", "第1位は架空一", "勝負の行方"):
            with self.subTest(text=fine):
                self.script.lines[0].text = fine
                media.save(self.root, "script", self.script)
                self.assertTrue(qa.check_confidence(self.root).passed)
        # Cards, labels and cover headlines are read too.
        self.board.scenes[0].props["text"] = "独断ランキング"
        media.save(self.root, "storyboard", self.board)
        self.assertIn("scene title", qa.check_confidence(self.root).detail)
        # Other types are not rankings.
        self.route("trivia_explainer")
        self.assertEqual(qa.check_confidence(self.root).detail, "not a ranking")
        self.assertIn("confidence", qa.WAIVABLE)


if __name__ == "__main__":
    unittest.main()
