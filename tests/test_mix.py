"""Voice-relative mixing with mocked measurements and tiny local synthetic waveforms."""
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from vg import media, mix, qa, registry, render
from vg.schemas import Asset, AudioClip, Line, LineTiming, Manifest, Music, Scene, Script, Storyboard, Timeline, Timings
from vg.timeline import build


def measured(level, duration=2, method="integrated_lufs"):
    return {"duration_sec": duration, "integrated_lufs": level if method == "integrated_lufs" else None,
            "rms_dbfs": level - 1 if level is not None else None, "level_db": level, "method": method}


class MixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.style = registry.load_style(self.root / "missing.yaml")
        self.timeline = Timeline(duration=8, clips=[], audio=[
            AudioClip(start=2, src="v2.wav", kind="voice", gain_db=-2),
            AudioClip(start=0, src="v1.wav", kind="voice"),
            AudioClip(start=0, src="music.wav", kind="music", gain_db=-20, duck_under_voice=True),
            AudioClip(start=1, src="pop.wav", kind="sfx", gain_db=-14),
            AudioClip(start=3, src="pop.wav", kind="sfx", gain_db=-14)])

    def prepare(self, **kwargs):
        with patch.object(mix, "measure_level", side_effect=[
                measured(-18, 4), measured(-30, 6), measured(-32, 0.08, "rms_dbfs")]) as measurement:
            report = mix.prepare_mix(self.root, self.timeline, style=self.style, **kwargs)
        return report, measurement

    def test_relative_gain_report_voice_order_and_repeated_sources(self):
        report, measurement = self.prepare()
        measurement.assert_any_call([self.root / "v1.wav", self.root / "v2.wav"], [0, -2])
        self.assertEqual(measurement.call_count, 3)
        music, pop, again = self.timeline.audio[2:]
        self.assertEqual((music.gain_db, pop.gain_db, again.gain_db), (2, 8, 8))
        self.assertFalse(music.duck_under_voice)
        self.assertEqual((music.fade_in, music.fade_out, music.duration), (0.3, 1, 6))
        self.assertEqual((pop.fade_in, pop.fade_out, pop.duration), (0, 0, 0.08))
        self.assertEqual([a.gain_db for a in self.timeline.audio[:2]], [-2, 0])
        self.assertEqual(report, json.loads((self.root / "render/mix.json").read_text()))
        self.assertEqual([s["src"] for s in report["voice"]["sources"]], ["v1.wav", "v2.wav"])
        self.assertEqual([c["offset_below_voice_db"] for c in report["clips"]], [10, 6, 6])
        self.assertEqual([c["output_level_db"] for c in report["clips"]], [-28, -24, -24])
        self.assertEqual(report["clips"][1]["method"], "rms_dbfs")
        self.assertEqual(self.prepare()[0], report)  # gains never accumulate

    def test_custom_offsets_and_fades(self):
        self.style.update(bgm_below_voice_db=12, sfx_below_voice_db=9,
                          bgm_fade_in_sec=0.5, bgm_fade_out_sec=2)
        report, _ = self.prepare()
        self.assertEqual([c["gain_db"] for c in report["clips"]], [0, 5, 5])
        self.assertEqual((self.timeline.audio[2].fade_in, self.timeline.audio[2].fade_out), (0.5, 2))

    def test_mix_warning_tolerances_and_missing_or_invalid_measurements(self):
        self.prepare()
        media.save(self.root, "timeline", self.timeline)
        check = qa.check_mix(self.root)
        self.assertEqual((check.id, check.passed, check.severity, check.redo_stage), ("mix", True, "warn", "render"))
        path = self.root / "render/mix.json"
        original = json.loads(path.read_text())
        for index, shift, expected in [(0, 2, True), (0, -2, True), (0, 2.01, False),
                                       (1, 3, True), (1, -3, True), (1, -3.01, False)]:
            report = json.loads(json.dumps(original))
            # Keep timeline and recorded gain consistent, then verify the actual derived offset.
            report["clips"][index]["gain_db"] += shift
            self.timeline.audio[index + 2].gain_db = report["clips"][index]["gain_db"]
            media.save(self.root, "timeline", self.timeline)
            path.write_text(json.dumps(report))
            with self.subTest(index=index, shift=shift):
                self.assertEqual(qa.check_mix(self.root).passed, expected)
            self.timeline.audio[index + 2].gain_db -= shift
        media.save(self.root, "timeline", self.timeline)
        for invalid in ("{}", "{broken", '{"voice": null, "clips": []}',
                        json.dumps({**original, "clips": []})):
            path.write_text(invalid)
            self.assertFalse(qa.check_mix(self.root).passed)
        path.unlink()
        self.assertFalse(qa.check_mix(self.root).passed)
        self.timeline.audio = []
        media.save(self.root, "timeline", self.timeline)
        self.assertTrue(qa.check_mix(self.root).passed)

    def test_unmeasurable_or_missing_voice_is_reported_without_nonfinite_json(self):
        with patch.object(mix, "measure_level", return_value=measured(None)):
            report = mix.prepare_mix(self.root, self.timeline)
        self.assertIsNone(report["voice"]["level_db"])
        self.assertEqual(self.timeline.audio[2].gain_db, -20)
        media.save(self.root, "timeline", self.timeline)
        self.assertFalse(qa.check_mix(self.root).passed)
        self.timeline.audio = self.timeline.audio[2:]
        with patch.object(mix, "measure_level", return_value=measured(-25)):
            report = mix.prepare_mix(self.root, self.timeline)
        self.assertIsNone(report["voice"])
        json.dumps(report, allow_nan=False)
        media.save(self.root, "timeline", self.timeline)
        self.assertFalse(qa.check_mix(self.root).passed)

    def test_render_passes_adjusted_timeline_and_fades_to_remotion(self):
        media.save(self.root, "timeline", self.timeline)
        with patch.object(mix, "measure_level", side_effect=[measured(-18), measured(-30), measured(-32, 0.08)]), \
                patch.object(render, "run") as remotion, \
                patch.object(render, "loudnorm", return_value=self.root / "final.mp4"):
            self.assertEqual(render.render(self.root, concurrency=2), self.root / "final.mp4")
        self.assertIn("--concurrency=2", remotion.call_args.args[0])
        props = json.loads((self.root / "render/props.json").read_text())["timeline"]
        saved = media.load(self.root, "timeline").model_dump(mode="json")
        self.assertEqual(props, saved)
        self.assertEqual(props["audio"][2]["gain_db"], 2)
        self.assertEqual(props["audio"][2]["fade_in"], 0.3)
        self.assertEqual(props["audio"][2]["fade_out"], 1)
        self.assertFalse(props["audio"][2]["duck_under_voice"])
        self.assertTrue((self.root / "render/mix.json").is_file())

    def test_timeline_music_fades_and_legacy_audio_compatibility(self):
        script = Script(title="synthetic", lines=[Line(id="L1", text="テスト")])
        timings = Timings(sample_rate=48000, total_sec=3, lines=[
            LineTiming(line_id="L1", audio="v.wav", start=0, end=3)])
        board = Storyboard(type_id="test", scenes=[Scene(id="C1", component="background", line_ids=["L1"])],
                           music=Music(asset="bgm", gain_db=-20))
        manifest = Manifest(assets=[Asset(id="bgm", kind="music", path="music.wav", origin="library",
                                          license="synthetic", commercial_ok=True)])
        timeline = build(script, timings, board, manifest)
        music = timeline.audio[-1]
        self.assertEqual((music.fade_in, music.fade_out, music.duck_under_voice), (0.3, 1, False))
        self.assertEqual(AudioClip.model_validate({"src": "old.wav", "start": 0, "kind": "music"}).fade_in, 0)
        for field in ("fade_in", "fade_out", "duration"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AudioClip(start=0, src="test.wav", kind="music", **{field: -1})


class LevelMeasurementTests(unittest.TestCase):
    def test_integrated_infinity_and_short_source_use_overall_rms(self):
        for duration, integrated, expected in [(0.08, "-70", -25), (0.39, "-22", -25),
                                                (1, "-inf", -25), (1, "-22", -22)]:
            stderr = f"Summary:\nI: {integrated} LUFS\nChannel: 1\nRMS level dB: -20\nOverall\nRMS level dB: -25"
            with patch.object(mix, "probe", return_value={"format": {"duration": duration}}), \
                    patch.object(mix, "run", return_value=CompletedProcess([], 0, "", stderr)), \
                    self.subTest(duration=duration, integrated=integrated):
                result = mix.measure_level([Path("synthetic.wav")])
                self.assertEqual(result["level_db"], expected)
                self.assertEqual(result["method"], "integrated_lufs" if expected == -22 else "rms_dbfs")

    def test_real_short_effect_and_concatenated_voice_with_gain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for name, duration, rate in [("short", 0.08, 48000), ("voice_a", 0.7, 24000), ("voice_b", 0.8, 44100)]:
                path = root / f"{name}.wav"
                media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                           f"sine=frequency=440:sample_rate={rate}:duration={duration}", str(path)])
                paths.append(path)
            effect = mix.measure_level(paths[:1])
            self.assertEqual(effect["method"], "rms_dbfs")
            self.assertAlmostEqual(effect["duration_sec"], 0.08, places=3)
            self.assertGreater(effect["level_db"], -35)
            voice = mix.measure_level(paths[1:])
            quieter = mix.measure_level(paths[1:], [-6, -6])
            self.assertEqual(voice["method"], "integrated_lufs")
            self.assertAlmostEqual(voice["duration_sec"], 1.5, places=3)
            self.assertAlmostEqual(voice["level_db"] - quieter["level_db"], 6, delta=0.15)


if __name__ == "__main__":
    unittest.main()
