import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from vg import media, qa
from vg.render import loudnorm, write_srt
from vg.schemas import Brief, Caption, Check, Length, Line, LineTiming, Manifest, QAReport, Route, Script, Timeline, Timings


class MediaQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.good = cls.root / "good"
        cls.black = cls.root / "black"
        cls.wrong = cls.root / "wrong"
        cls.silent = cls.root / "silent"
        cls.quiet = cls.root / "quiet.mp4"
        for directory, size, gap, silent in [(cls.good, "1080x1920", False, False),
                                             (cls.black, "1080x1920", True, False),
                                             (cls.wrong, "320x240", False, False),
                                             (cls.silent, "320x240", False, True)]:
            directory.mkdir()
            raw = directory / "raw.mp4"
            cls.make_clip(raw, size=size, gap=gap, silent=silent)
            if silent:
                raw.replace(directory / "final.mp4")
            else:
                loudnorm(raw, directory / "final.mp4")
            media.save(directory, "brief", Brief(request="テスト", topic="テスト", max_sec=4))
            media.save(directory, "timeline", Timeline(duration=3.2, clips=[], captions=[
                Caption(start=0.1, end=3.0, text="テスト字幕")]))
            media.save(directory, "script", Script(title="合成テスト", lines=[Line(id="L1", text="あ" * 24)]))
            review = directory / "yomi_check.json"
            review.write_text(json.dumps({"lines": [], "reviewed": True}), encoding="utf-8")
            script_time = (directory / "script.json").stat().st_mtime_ns
            os.utime(review, ns=(script_time + 1_000_000, script_time + 1_000_000))
            media.save(directory, "timings", Timings(sample_rate=48000, total_sec=3.2,
                       lines=[LineTiming(line_id="L1", audio="test.wav", start=0.1, end=3.1)]))
            media.save(directory, "manifest", Manifest())
        cls.make_clip(cls.quiet, size="320x240", volume="0.02")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def make_clip(path, size, gap=False, silent=False, volume="1"):
        cmd = [str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
               f"color=c=blue:s={size}:r=30:d=3.2", "-f", "lavfi", "-i",
               "anullsrc=r=48000:cl=mono" if silent else "sine=frequency=440:sample_rate=48000:duration=3.2",
               "-t", "3.2"]
        if gap:
            cmd += ["-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,1,2)'"]
        cmd += ["-af", f"volume={volume}", "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(path)]
        media.run(cmd)

    def test_good_media_checks(self):
        checks = qa.run_checks(self.good, runs_root=self.root / "empty_runs")
        self.assertEqual({c.id for c in checks}, {"format", "duration", "loudness", "black_frames",
                                                "silence", "safe_area", "caption_timing", "hook", "tempo",
                                                "speech_rate", "caption_coverage", "originality", "disclosure",
                                                "sfx_density", "metadata", "yomi", "mix"})
        for check in checks:
            with self.subTest(check=check.id):
                self.assertTrue(check.passed, check.detail)
                self.assertNotIn("\n", check.detail)

    def test_black_gap_and_wrong_size(self):
        check = qa.check_black_frames(self.black)
        self.assertFalse(check.passed, check.detail)
        self.assertEqual(check.redo_stage, "assets")
        self.assertFalse(qa.check_format(self.wrong).passed)

    def test_trailing_black_is_allowed(self):
        with patch.object(qa, "run", return_value=subprocess.CompletedProcess([], 0, "",
                          "black_start:2.5 black_end:3.2 black_duration:0.7")):
            self.assertTrue(qa.check_black_frames(self.good).passed)

    def test_silence_is_warning(self):
        check = qa.check_silence(self.silent)
        self.assertFalse(check.passed, check.detail)
        self.assertEqual(check.severity, "warn")
        self.assertTrue(QAReport(checks=[check]).passed)

    def test_loudnorm_quiet_input(self):
        before, _ = qa.measure_loudness(self.quiet)
        self.assertLess(before, -30)
        target = self.root / "normalized.mp4"
        loudnorm(self.quiet, target)
        after, peak = qa.measure_loudness(target)
        self.assertLessEqual(abs(after + 14), 1.5)
        self.assertLessEqual(peak, -1)
        self.assertEqual(media.probe(target)["streams"][0]["codec_name"], "h264")

    def test_write_srt(self):
        timeline = Timeline(duration=3662, clips=[], captions=[
            Caption(start=0.001, end=1.234, text="一行目\n二行目"),
            Caption(start=3661.007, end=3661.9996, text="最後")])
        path = self.root / "subtitles.srt"
        write_srt(timeline, path)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "1\n00:00:00,001 --> 00:00:01,234\n一行目\n二行目\n\n"
                         "2\n01:01:01,007 --> 01:01:02,000\n最後\n\n")

    def test_safe_area(self):
        def check(text, style="default"):
            return qa.check_safe_area(Timeline(duration=2, clips=[], captions=[
                Caption(start=0, end=1, text=text, style=style)]))
        self.assertFalse(check("あ" * 13).passed)
        self.assertTrue(check("あ" * 11).passed)
        self.assertTrue(check("a" * 21).passed)
        self.assertFalse(check("a" * 22).passed)
        self.assertTrue(check("ｱ" * 21).passed)
        self.assertFalse(check("あ\nい\nう").passed)
        self.assertFalse(check("あ" * 10, "title").passed)

    def test_caption_timing(self):
        timeline = Timeline(duration=3, clips=[], captions=[Caption(start=0, end=1, text="a"),
                                                          Caption(start=0.9, end=2, text="b")])
        self.assertFalse(qa.check_caption_timing(timeline).passed)
        timeline.captions[1].start = 1
        self.assertTrue(qa.check_caption_timing(timeline).passed)
        timeline.captions[1].end = 1.5
        self.assertFalse(qa.check_caption_timing(timeline).passed)
        timeline.captions[1].end = 4
        self.assertFalse(qa.check_caption_timing(timeline).passed)

    def test_duration_platform_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            media.save(directory, "brief", Brief(request="a", topic="a", platforms=["youtube_shorts", "x"]))
            media.save(directory, "route", Route(type_id="example", confidence=1, reason="test"))
            from types import SimpleNamespace
            spec = SimpleNamespace(length={"youtube_shorts": Length(min=1, target=3, max=4),
                                           "x": Length(min=2, target=2.5, max=3)})
            with patch.object(qa.registry, "load_types", return_value={"example": spec}):
                check = qa.check_duration(directory, {"format": {"duration": "3.2"}})
                self.assertFalse(check.passed)
                self.assertEqual(check.redo_stage, "script")

    def test_stage_storage_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            media.save(directory, "brief", Brief(request="日本語", topic="字幕"))
            self.assertEqual(media.load(directory, "brief").request, "日本語")
            self.assertIn("日本語", (Path(directory) / "brief.json").read_text())
            media.save(directory, "manifest", {"assets": []})
            self.assertTrue((Path(directory) / "assets/manifest.json").exists())
            with self.assertRaises(ValidationError):
                media.save(directory, "brief", {"request": "missing topic"})
            self.assertEqual(media.load(directory, "brief").topic, "字幕")
            self.assertFalse(list(Path(directory).glob(".*.json.*")))

    def test_scrubbed_environment_and_failure(self):
        fake = {key: "test-secret" for key in media._SECRET_NAMES}
        with patch.dict(os.environ, {**fake, "PATH": "/test"}, clear=True):
            self.assertEqual(media.scrubbed_env(), {"PATH": "/test"})
            with patch.object(media.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as call:
                media.run(["dummy"], env={**fake, "PATH": "/node"})
                self.assertEqual(call.call_args.kwargs["env"], {"PATH": "/node"})
                self.assertTrue(call.call_args.kwargs["check"])
            with patch.object(media.subprocess, "run", side_effect=subprocess.CalledProcessError(
                    2, ["dummy"], stderr="test failure")):
                with self.assertRaisesRegex(RuntimeError, "test failure"):
                    media.run(["dummy"])

    def test_report_merge_and_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            media.save(path, "qa", QAReport(attempt=2, checks=[Check(id="facts", passed=True),
                                                               Check(id="format", passed=False)]))
            report = qa.write_report(path, [Check(id="format", passed=True)])
            self.assertEqual(report.attempt, 2)
            self.assertTrue(report.passed)
            self.assertEqual(len(report.checks), 2)
            (path / "final.mp4").touch()
            previous_time = (path / "qa.json").stat().st_mtime_ns
            os.utime(path / "final.mp4", ns=(previous_time + 1000000, previous_time + 1000000))
            report = qa.write_report(path, [])
            self.assertEqual(report.attempt, 3)
            os.utime(path / "final.mp4", ns=(1, 1))
            self.assertEqual(qa.write_report(path, []).attempt, 3)


if __name__ == "__main__":
    unittest.main()
