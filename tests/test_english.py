"""English localisation uses only temporary synthetic media and stubbed TTS/API."""
import json
from pathlib import Path
import shutil
import tempfile
import sys
import types
import unittest
from unittest.mock import Mock, patch
import warnings

from pydantic import ValidationError

from vg import cli, english, media, mix, package, publish_youtube as youtube, qa, registry, render
from vg.schemas import (Asset, AudioClip, Brief, Check, Clip, English, EnglishLine, Line, LineTiming,
                        Manifest, Publish, QAReport, Research, Script, Source, Timeline, Timings)


def tone(path, seconds=1.5, frequency=440):
    path.parent.mkdir(parents=True, exist_ok=True)
    media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
               f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}", str(path)])


class EnglishSchemaTests(unittest.TestCase):
    def test_coverage_title_duplicate_and_blank(self):
        script = Script(title="test", lines=[Line(id="L1", text="合成"), Line(id="L2", text="試験")])
        spec = English(title="Why?", description="Synthetic", lines=[EnglishLine(id="L2", text="Two."),
                                                                     EnglishLine(id="L1", text="One.")])
        self.assertIs(spec.validate_script(script), spec)
        for ids in (["L1"], ["L1", "L2", "L3"]):
            with self.assertRaisesRegex(ValueError, "coverage"):
                spec.model_copy(update={"lines": [EnglishLine(id=i, text="test") for i in ids]}).validate_script(script)
        for changes in ({"title": "a"*101}, {"lines": [EnglishLine(id="L1", text="test")]*2},
                        {"lines": [{"id": "L1", "text": "  "}]}):
            with self.assertRaises(ValidationError):
                English.model_validate(spec.model_dump() | changes)

    def test_wrapping_and_proportional_picture_times(self):
        text = "Natural English subtitles follow the Japanese picture and keep every word. " * 4
        spec = English(title="Why?", description="Synthetic", lines=[EnglishLine(id="L1", text=text)])
        timing = LineTiming(line_id="L1", audio="unused.wav", start=2, end=10)
        cues = english.captions(spec, [timing])
        self.assertGreater(len(cues), 1)
        self.assertEqual((cues[0].start, cues[-1].end), (2, 10))
        self.assertEqual(" ".join(c.text.replace("\n", " ") for c in cues), text.strip())
        for cue in cues:
            self.assertLessEqual(len(cue.text.splitlines()), 2)
            self.assertTrue(all(len(row) <= 42 for row in cue.text.splitlines()))
            self.assertAlmostEqual((cue.end-cue.start)/8, len(cue.text.replace("\n", " ")) /
                                   sum(len(c.text.replace("\n", " ")) for c in cues))

    def test_english_registry_does_not_change_japanese_resolution(self):
        voices = registry.load_voices()
        self.assertEqual(voices[registry.load_voice_defaults()["narrator_en"]].speaker, "am_michael")
        self.assertEqual(voices["vv_himari"].lang, "ja")
        self.assertIsNone(registry.resolve_voice("Heart (English)", voices))

    def test_worker_batches_without_importing_real_models(self):
        from workers.tts_kokoro import synth
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pipeline = Mock(return_value=[("text", "phonemes", [0.0]*2400)])
            kokoro = types.ModuleType("kokoro")
            kokoro.KPipeline = Mock(return_value=pipeline)
            numpy = MagicMock()
            numpy.asarray.side_effect = lambda value: value
            numpy.rint.return_value.astype.return_value.tobytes.return_value = b"\x00\x00"*2400
            torch = Mock()
            torch.is_tensor.return_value = False
            spacy = Mock()
            spacy.util.is_package.return_value = True
            modules = {"kokoro": kokoro, "numpy": numpy, "torch": torch, "spacy": spacy}
            jobs = [{"text": f"Line {i}.", "voice": "af_heart", "output": str(root / f"{i}.wav")} for i in range(2)]
            with patch.dict(sys.modules, modules), patch.dict(synth.os.environ, {}, clear=True), \
                    patch.object(synth.shutil, "which", return_value="/usr/bin/espeak-ng"):
                synth.synthesize({"checkpoint": english.CHECKPOINT, "device": "cpu", "jobs": jobs})
                self.assertIs(sys.modules["misaki.espeak"].EspeakFallback, synth.ExternalEspeak)
                self.assertEqual(synth.os.environ["HF_HUB_OFFLINE"], "1")
                synth.synthesize({"checkpoint": english.CHECKPOINT, "device": "cpu", "jobs": jobs})
            kokoro.KPipeline.assert_called_once_with(lang_code="a", repo_id=english.CHECKPOINT, device="cpu")
            self.assertEqual(pipeline.call_count, 2)
            self.assertEqual(len(list(root.glob("*.wav"))), 2)

    def test_external_espeak_only_uses_a_subprocess(self):
        from workers.tts_kokoro import synth
        with patch.object(synth.subprocess, "run", return_value=Mock(stdout="t^ʃa^ɪ")) as external:
            self.assertEqual(synth.ExternalEspeak()(types.SimpleNamespace(text="chai")), ("ʧI", 2))
        self.assertEqual(external.call_args.args[0][0], "espeak-ng")
        self.assertEqual(external.call_args.kwargs["input"], "chai")


class EnglishMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / "source.mp4"
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "color=c=blue:s=108x192:r=30:d=4", "-f", "lavfi", "-i",
                   "sine=frequency=440:sample_rate=48000:duration=4", "-c:v", "libx264", "-threads", "2",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(cls.video)])

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temp_case.cleanup)
        self.base = Path(self.temp_case.name)
        self.directory = self.fixture(self.base / "variant")

    def fixture(self, directory):
        directory.mkdir(parents=True)
        (directory / "final.mp4").write_bytes(self.video.read_bytes())
        media.save(directory, "brief", Brief(request="synthetic", topic="synthetic"))
        media.save(directory, "script", Script(title="合成", lines=[Line(id="L1", text="合成一"), Line(id="L2", text="合成二")]))
        timings = Timings(sample_rate=48000, total_sec=3.5, lines=[
            LineTiming(line_id="L1", audio="ja1.wav", start=0.1, end=1.6),
            LineTiming(line_id="L2", audio="ja2.wav", start=1.9, end=3.4)])
        media.save(directory, "timings", timings)
        for name, duration in (("ja1.wav", 1.5), ("ja2.wav", 1.5), ("music.wav", 4), ("effect.wav", 0.1)):
            tone(directory / name, duration)
        timeline = Timeline(duration=4, clips=[], audio=[
            AudioClip(start=t.start, src=t.audio, kind="voice") for t in timings.lines] + [
            AudioClip(start=0, src="music.wav", kind="music", gain_db=-12, fade_in=0.3, fade_out=1),
            AudioClip(start=2.4, src="effect.wav", kind="sfx", gain_db=-8)])
        media.save(directory, "timeline", timeline)
        media.save(directory, "en", English(title="Why does it work?", description="Synthetic\nVOICE:synthetic",
            lines=[EnglishLine(id="L1", text="One synthetic line."), EnglishLine(id="L2", text="Another synthetic line.")]))
        media.save(directory, "manifest", Manifest(assets=[Asset(id="ja", path="ja1.wav", kind="voice", origin="generated",
            license="synthetic", credit="VOICE:synthetic", commercial_ok=True)]))
        (directory / "final.srt").write_text("1\n00:00:00,100 --> 00:00:01,600\n合成\n\n")
        return directory

    def stub_tts(self, jobs, *, cpu=False):
        for job in jobs:
            # First line needs fitting; the second does not.
            tone(Path(job["output"]), 1.92 if job["text"].startswith("One") else 1.5)

    def generate(self):
        english.generate(self.directory, tts=self.stub_tts)

    def test_batch_resume_timing_audio_licence_and_qa(self):
        other = self.fixture(self.base / "other")
        stub = Mock(side_effect=self.stub_tts)
        originals = [(d / "final.mp4").read_bytes() for d in (self.directory, other)]
        english.generate_many([self.directory, other], tts=stub)
        self.assertEqual(stub.call_count, 1)
        self.assertEqual(len(stub.call_args.args[0]), 4)
        for directory, original in zip((self.directory, other), originals):
            self.assertEqual((directory / "final.mp4").read_bytes(), original)
            fitted = media.load(directory / "en", "timings")
            self.assertEqual([t.start for t in fitted.lines], [0.1, 1.9])
            self.assertLessEqual(fitted.lines[0].end, 1.901)
            self.assertLess(abs(english.audio_duration(directory / "dub_en.m4a")-4), 0.05)
            checks = qa.check_english(directory)
            self.assertTrue(all(c.passed for c in checks), [(c.id, c.detail) for c in checks])
            en_assets = [a for a in media.load(directory, "manifest").assets if a.params.get("lang") == "en"]
            self.assertEqual(len(en_assets), 2)
            self.assertTrue(all(a.license == "Apache-2.0" and a.params["atempo"] <= 1.15 for a in en_assets))
            self.assertTrue(any(a.id == "ja" for a in media.load(directory, "manifest").assets))
        english.generate_many([self.directory, other], tts=stub)
        self.assertEqual(stub.call_count, 1)
        # Metadata edits refit without reloading the TTS model.
        spec = media.load(self.directory, "en")
        spec.title = "Another hook?"
        media.save(self.directory, "en", spec)
        self.assertFalse(next(c for c in qa.check_english(self.directory) if c.id == "en_fresh").passed)
        english.generate(self.directory, tts=stub)
        self.assertEqual(stub.call_count, 1)
        # Only the edited narration is synthesized on a resumed build.
        spec.lines[1].text = "A changed line."
        media.save(self.directory, "en", spec)
        english.generate(self.directory, tts=stub)
        self.assertEqual(stub.call_count, 2)
        self.assertEqual(len(stub.call_args.args[0]), 1)

    def test_long_dub_fails_named_line_without_truncation(self):
        def long(jobs, **kwargs):
            for job in jobs:
                tone(Path(job["output"]), 3)
        with self.assertRaisesRegex(ValueError, r"L1.*max 1.15x.*shorten"):
            english.generate(self.directory, tts=long)
        self.assertFalse((self.directory / "dub_en.m4a").exists())

    def test_missing_optional_bad_coverage_credits_and_srt(self):
        spec = media.load(self.directory, "en")
        spec.description = "No credits"
        media.save(self.directory, "en", spec)
        self.assertFalse(qa.check_english(self.directory)[0].passed)
        spec.description = "Synthetic\nVOICE:synthetic"
        media.save(self.directory, "en", spec)
        self.generate()
        (self.directory / "final_en.srt").write_text("1\n00:00:00,000 --> 00:00:09,000\ninvalid\n")
        checks = qa.check_english(self.directory)
        self.assertFalse(next(c for c in checks if c.id == "en_srt").passed)
        qa.write_report(self.directory, checks)
        (self.directory / "en.json").unlink()
        optional = qa.check_english(self.directory)
        self.assertEqual((optional[0].passed, optional[0].severity), (False, "warn"))
        self.assertTrue(qa.write_report(self.directory, optional).passed)

    def test_rate_warning_block_and_duration_loudness_failures(self):
        self.generate()
        fitted = media.load(self.directory / "en", "timings")
        tone(self.directory / fitted.lines[1].audio, 1.6)
        fitted.lines[1].end = fitted.lines[1].start+1.6
        media.save(self.directory / "en", "timings", fitted)
        for words, expected_block, expected_warning in ((4, False, False), (5, False, True), (6, True, None)):
            spec = media.load(self.directory, "en")
            spec.lines[1].text = "test "*words
            media.save(self.directory, "en", spec)
            checks = qa.check_english(self.directory)
            rate = next(c for c in checks if c.id == "en_speech_rate")
            self.assertEqual(not rate.passed, expected_block, rate.detail)
            if expected_warning is not None:
                warning = next(c for c in checks if c.id == "en_speech_rate_warning")
                self.assertEqual(not warning.passed, expected_warning)
        # Format and duration are measured from the delivered media, not a report.
        short = self.directory / "dub_en.m4a"
        short.unlink()
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(self.video),
                   "-vn", "-t", "1", "-af", "volume=-12dB", "-c:a", "aac", "-ar", "48000", str(short)])
        checks = qa.check_english(self.directory)
        self.assertFalse(next(c for c in checks if c.id == "en_dub_duration").passed)
        self.assertFalse(next(c for c in checks if c.id == "en_loudness").passed)

    def test_source_lines_and_custom_sections(self):
        spec = media.load(self.directory, "en")
        media.save(self.directory, "research", Research(sources=[Source(id="S1", title="Synthetic source",
            url="https://example.test/source", accessed="2026-11-06")]))
        with self.assertRaisesRegex(ValueError, "source/credit"):
            english.validate(self.directory)
        spec.description += "\n- Synthetic source https://example.test/source"
        media.save(self.directory, "en", spec)
        english.validate(self.directory)
        media.save(self.directory, "publish", Publish(title="合成", description="合成\n\nCredits\nCustom licence credit",
            platforms=["youtube_shorts"], synthetic_media=False))
        with self.assertRaisesRegex(ValueError, "Custom licence credit"):
            english.validate(self.directory)

    def test_cli_validate_checks_english_line_coverage(self):
        spec = media.load(self.directory, "en")
        spec.lines.pop()
        media.save(self.directory, "en", spec)
        with patch("builtins.print"):
            self.assertEqual(cli.cmd_validate(self.directory), 1)

    def test_packaging_and_mock_uploads(self):
        self.generate()
        media.save(self.directory, "qa", QAReport(checks=[Check(id="synthetic", passed=True)]))
        with patch.object(package, "ROOT", self.base):
            destination = package.package(self.directory)
        for name in ("en.json", "final_en.srt", "dub_en.m4a", "ENGLISH.txt"):
            self.assertTrue((destination / name).is_file())
        plan = youtube.preview(self.directory)
        self.assertEqual(plan["body"]["localizations"]["en"]["title"], "Why does it work?")
        self.assertEqual(plan["body"]["snippet"]["defaultAudioLanguage"], "ja")
        client = Mock()
        client.videos().insert().next_chunk.return_value = (None, {"id": "synthetic-id"})
        client.captions().insert().execute.side_effect = [RuntimeError("Japanese captions unavailable"), {}]
        with patch.object(youtube, "_service", return_value=client), \
                patch.object(youtube, "_media_upload", side_effect=lambda path, *args, **kw: path), \
                warnings.catch_warnings(record=True):
            result = youtube.upload(self.directory)
        self.assertIn("localizations", client.videos().insert.call_args.kwargs["part"])
        captions = [call.kwargs["body"]["snippet"] for call in client.captions().insert.call_args_list if "body" in call.kwargs]
        self.assertEqual([(c["language"], c["name"]) for c in captions], [("ja", "日本語"), ("en", "English")])
        self.assertEqual(result["captions_en"], "ok")
        self.assertIn("error:", result["captions"])
        self.assertEqual(result["dub_en_note"], english.MANUAL_AUDIO)
        self.assertEqual(result["dub_en"], str(self.directory / "dub_en.m4a"))
        self.assertEqual(json.loads((self.directory / "published_youtube.json").read_text()), result)
        client.captions().insert().execute.side_effect = [{}, RuntimeError("English captions unavailable")]
        with patch.object(youtube, "_service", return_value=client), \
                patch.object(youtube, "_media_upload", return_value="synthetic"), warnings.catch_warnings(record=True):
            result = youtube.upload(self.directory, force=True)
        self.assertEqual(result["captions"], "ok")
        self.assertIn("error:", result["captions_en"])

    def test_unmuted_embedded_audio_is_preserved_by_shared_mixer(self):
        timeline = Timeline(duration=4, clips=[Clip(start=0, end=4, component="video_clip",
                                                  props={"src": "final.mp4", "muted": False})])
        audio = mix.render_audio(self.directory, timeline, self.directory / "embedded.wav")
        self.assertIsNotNone(mix.measure_level([audio])["level_db"])
        self.assertAlmostEqual(english.audio_duration(audio), 4, places=2)

    def test_japanese_render_uses_shared_audio_mixer(self):
        original_run = media.run

        def synthetic_remotion(command, **kwargs):
            if command[0] == "npx":
                shutil.copy2(self.video, command[5])
                return Mock()
            return original_run(command, **kwargs)

        with patch.object(render, "run", side_effect=synthetic_remotion):
            final = render.render(self.directory, concurrency=1)
        self.assertEqual(final, self.directory / "final.mp4")
        self.assertAlmostEqual(english.audio_duration(final), 4, delta=0.05)
        self.assertAlmostEqual(mix.measure_level([final])["integrated_lufs"], -14, delta=1)

    def test_cli_batches_parent_and_dispatch_maps_paths(self):
        other = self.fixture(self.base / "other")
        with patch.object(cli.runs, "resolve", return_value=self.base), \
                patch.object(cli.runs, "variants", return_value=[self.directory, other]), \
                patch.object(cli.runs, "mark"), patch.object(english, "generate_many") as generate:
            self.assertEqual(cli.main(["en", "synthetic", "--cpu"]), 0)
            generate.assert_called_once_with([self.directory, other], cpu=True)
        job = {"text": "One", "voice": "af_heart", "speed": 1, "output": str(self.directory / "en/raw.wav")}
        with patch.object(english.assets, "remote", side_effect=lambda p: str(p)), \
                patch.object(english.narrator, "_run_on_lab_gpu") as dispatch:
            english.synthesize([job], cpu=True)
        self.assertIn("tts_kokoro/synth.py", dispatch.call_args.args[0])
        self.assertEqual(dispatch.call_args.args[1], 1024)
        self.assertEqual(json.loads((self.directory / "en/kokoro_jobs.json").read_text())["device"], "cpu")


if __name__ == "__main__":
    unittest.main()
