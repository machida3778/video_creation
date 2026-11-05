from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vg import cli, daily
from vg.media import save
from vg.schemas import Publish
from vg.daily import JST

UPLOADED = {"video_id": "abc", "url": "https://youtube.com/shorts/abc"}


class DailyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.cfg = dict(daily.DEFAULTS, enabled=True, list=str(self.root / "requests.md"))
        patcher = patch.object(daily, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, *args):
        return datetime(*args, tzinfo=JST)

    def write_list(self, *items):
        path = daily.init(self.cfg)
        path.write_text(path.read_text(encoding="utf-8") + "".join(f"{i}\n" for i in items), encoding="utf-8")
        return path

    def make_run(self, name="r1"):
        variant = self.root / "runs" / name / "variants" / "v1"
        variant.mkdir(parents=True)
        save(variant, "publish", Publish(title="タイトルA", description="d", platforms=["youtube_shorts"],
                                         synthetic_media=False))
        return self.root / "runs" / name, variant

    def test_slots_weekday_weekend_and_holidays(self):
        hours = {d: daily.slot(d, self.cfg).hour for d in (
            date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8),   # Friday, Saturday, Sunday
            date(2026, 11, 23), date(2026, 11, 24),                     # 勤労感謝の日 (Monday), Tuesday
            date(2026, 5, 6))}                                          # 振替休日
        self.assertEqual(list(hours.values()), [18, 10, 10, 10, 18, 10])
        self.assertEqual(daily.slot(date(2026, 11, 6), self.cfg).utcoffset().total_seconds(), 9 * 3600)

    def test_parse_items_notes_and_forgiving_markers(self):
        items = daily.parse("# h\n- 説明: `- [ ] 例`\n- [ ] 項目A\n  - 11/06 失敗: GPU\n* [X] 項目C\n\n- [] 項目B\n"
                            "  - [ ] 入れ子は項目\n")
        self.assertEqual([(i.text, i.done, i.notes) for i in items],
                         [("項目A", False, ["11/06 失敗: GPU"]), ("項目C", True, []), ("項目B", False, []),
                          ("入れ子は項目", False, [])])

    def test_schedule_ticks_the_item_and_blocks_a_second_video(self):
        self.write_list("- [ ] 項目A", "- [ ] 項目B")
        info = daily.status(self.at(2026, 11, 6, 0, 30), self.cfg, self.state)
        self.assertEqual((info["next"], info["pending"], info["lead_ok"]), ("項目A", 2, True))
        self.assertEqual(daily.check(self.at(2026, 11, 6, 0, 30), self.cfg, self.state), 0)
        run, variant = self.make_run()
        daily.start("項目A", str(run), at=self.at(2026, 11, 6, 0, 31), cfg=self.cfg, state_dir=self.state)
        with patch("vg.publish_youtube.upload", return_value=dict(UPLOADED)) as upload:
            result = daily.publish(variant, confirm=True, at=self.at(2026, 11, 6, 1, 10), cfg=self.cfg,
                                   state_dir=self.state)
        upload.assert_called_once_with(variant, privacy="private", publish_at="2026-11-06T18:00:00+09:00")
        self.assertEqual(result["publish_at"], "2026-11-06T18:00:00+09:00")
        items = daily.read_items(self.cfg)
        self.assertTrue(items[0].done)
        self.assertEqual(items[0].notes, ["2026-11-06(金) 18:00 公開予約 https://youtube.com/shorts/abc"])
        self.assertFalse(items[1].done)
        self.assertEqual(daily.check(self.at(2026, 11, 6, 5, 0), self.cfg, self.state), daily.SCHEDULED)
        with self.assertRaisesRegex(ValueError, "already has a scheduled"):
            daily.publish(variant, confirm=True, at=self.at(2026, 11, 6, 5, 0), cfg=self.cfg, state_dir=self.state)
        tomorrow = daily.status(self.at(2026, 11, 7, 0, 30), self.cfg, self.state)
        self.assertEqual((tomorrow["next"], tomorrow["slot"]), ("項目B", "2026-11-07T10:00:00+09:00"))

    def test_publish_refusals(self):
        self.write_list("- [ ] 項目A")
        run, variant = self.make_run()
        other, other_variant = self.make_run("r2")
        at = self.at(2026, 11, 6, 1, 0)
        with self.assertRaisesRegex(ValueError, "vg daily start"):
            daily.publish(variant, confirm=True, at=at, cfg=self.cfg, state_dir=self.state)
        daily.start("項目A", str(run), at=at, cfg=self.cfg, state_dir=self.state)
        with self.assertRaisesRegex(ValueError, "not today's run"):
            daily.publish(other_variant, confirm=True, at=at, cfg=self.cfg, state_dir=self.state)
        with self.assertRaisesRegex(ValueError, "too late"):
            daily.publish(variant, confirm=True, at=self.at(2026, 11, 6, 17, 45), cfg=self.cfg, state_dir=self.state)
        with self.assertRaisesRegex(ValueError, "disabled"):
            daily.publish(variant, confirm=True, at=at, cfg=dict(self.cfg, enabled=False), state_dir=self.state)
        with patch("vg.publish_youtube.preview", return_value={"plan": 1}) as preview, \
                patch("vg.publish_youtube.upload") as upload:
            self.assertEqual(daily.publish(variant, confirm=False, at=at, cfg=self.cfg, state_dir=self.state),
                             {"plan": 1})
        preview.assert_called_once_with(variant, "private", "2026-11-06T18:00:00+09:00")
        upload.assert_not_called()

    def test_failure_note_resume_and_missed_slot(self):
        self.write_list("- [ ] 項目A")
        run, _ = self.make_run()
        saturday = (2026, 11, 7)
        daily.start("項目A", str(run), at=self.at(*saturday, 0, 30), cfg=self.cfg, state_dir=self.state)
        daily.fail("GPUが空いていない", at=self.at(*saturday, 1, 0), cfg=self.cfg, state_dir=self.state)
        self.assertEqual(daily.read_items(self.cfg)[0].notes, ["11/07 01:00 失敗: GPUが空いていない（次の機会に再挑戦）"])
        resume = daily.status(self.at(*saturday, 5, 0), self.cfg, self.state)["resume"]
        self.assertEqual(resume, {"request": "項目A", "run": "runs/r1"})
        self.assertEqual(daily.check(self.at(*saturday, 9, 30), self.cfg, self.state), daily.TOO_LATE)
        self.assertEqual(daily.check(self.at(*saturday, 12, 0), self.cfg, self.state), daily.TOO_LATE)
        self.assertEqual(daily.load_state(self.state)["days"]["2026-11-07"]["status"], "missed")
        self.assertIn("11/07 間に合わず（次の機会に再挑戦）", daily.read_items(self.cfg)[0].notes)
        # The next day resumes the same run for the same unchecked request.
        self.assertEqual(daily.status(self.at(2026, 11, 8, 0, 30), self.cfg, self.state)["resume"],
                         {"request": "項目A", "run": "runs/r1"})

    def test_auto_item_and_disabled(self):
        daily.add_auto("  雷が  ジグザグな理由 ", "理科の「なぜ」がよく見られている", at=self.at(2026, 11, 6, 0, 40),
                       cfg=self.cfg)
        item = daily.read_items(self.cfg)[0]
        self.assertEqual((item.text, item.done), ("雷が ジグザグな理由", False))
        self.assertEqual(item.notes, ["自動追加 2026-11-06: 理科の「なぜ」がよく見られている"])
        self.assertEqual(daily.check(self.at(2026, 11, 6, 0, 40), dict(self.cfg, enabled=False), self.state),
                         daily.DISABLED)

    def test_skip_start_validation_and_upload_repair(self):
        self.write_list("- [ ] ニュースのまとめ", "- [ ] 項目A")
        at = self.at(2026, 11, 6, 0, 30)
        run, variant = self.make_run()
        with self.assertRaisesRegex(ValueError, "unchecked item"):
            daily.start("言い換えた依頼", str(run), at=at, cfg=self.cfg, state_dir=self.state)
        daily.skip("ニュースは作らない", "ニュースのまとめ", at=at, cfg=self.cfg, state_dir=self.state)
        items = daily.read_items(self.cfg)
        self.assertEqual((items[0].skipped, items[0].pending, items[0].notes), (True, False, ["11/06 見送り: ニュースは作らない"]))
        self.assertEqual(daily.status(at, self.cfg, self.state)["next"], "項目A")
        daily.start("項目A", str(run), at=at, cfg=self.cfg, state_dir=self.state)
        # The upload happened but the session was killed before the state and list were updated.
        (variant / "published_youtube.json").write_text(
            '{"video_id": "abc", "url": "https://youtube.com/shorts/abc", "uploaded_at": "2026-11-05T16:05:00Z"}')
        self.assertEqual(daily.check(self.at(2026, 11, 6, 3, 0), self.cfg, self.state), daily.SCHEDULED)
        self.assertEqual(daily.load_state(self.state)["days"]["2026-11-06"]["status"], "scheduled")
        self.assertTrue(daily.read_items(self.cfg)[1].done)
        self.assertEqual(daily.read_items(self.cfg)[1].notes, ["2026-11-06(金) 18:00 公開予約 https://youtube.com/shorts/abc"])

    def test_failure_count_for_repeated_days(self):
        self.write_list("- [ ] 項目A")
        run, _ = self.make_run()
        for day in (7, 8):
            daily.start("項目A", str(run), at=self.at(2026, 11, day, 0, 30), cfg=self.cfg, state_dir=self.state)
            daily.fail("QAが通らない", at=self.at(2026, 11, day, 1, 0), cfg=self.cfg, state_dir=self.state)
        self.assertEqual(daily.status(self.at(2026, 11, 9, 0, 30), self.cfg, self.state)["failures"], 2)

    def test_lost_tick_is_restored_and_never_made_twice(self):
        self.write_list("- [ ] 項目A", "- [ ] 項目B")
        run, variant = self.make_run()
        at = self.at(2026, 11, 6, 0, 30)
        daily.start("項目A", str(run), at=at, cfg=self.cfg, state_dir=self.state)
        with patch("vg.publish_youtube.upload", return_value=dict(UPLOADED)), \
                patch.object(daily, "add_note", side_effect=KeyboardInterrupt):  # killed before the tick
            with self.assertRaises(KeyboardInterrupt):
                daily.publish(variant, confirm=True, at=at, cfg=self.cfg, state_dir=self.state)
        self.assertFalse(daily.read_items(self.cfg)[0].done)
        # Even before the repair, the posted request is not offered again.
        self.assertEqual(daily.status(self.at(2026, 11, 7, 0, 30), self.cfg, self.state)["next"], "項目B")
        self.assertEqual(daily.check(self.at(2026, 11, 6, 5, 0), self.cfg, self.state), daily.SCHEDULED)
        first = daily.read_items(self.cfg)[0]
        self.assertTrue(first.done)
        self.assertEqual(first.notes, ["2026-11-06(金) 18:00 公開予約 https://youtube.com/shorts/abc"])

    def test_list_edit_retries_when_the_file_changes_underneath(self):
        path = self.write_list("- [ ] 項目A")
        original = Path.read_text
        calls = {"n": 0}

        def flaky(self_path, *args, **kwargs):
            text = original(self_path, *args, **kwargs)
            if self_path == path:
                calls["n"] += 1
                if calls["n"] == 2:  # the user saves between our read and our write
                    path.write_text(text + "- [ ] ユーザーが追加\n", encoding="utf-8")
                    return original(self_path, *args, **kwargs)
            return text

        with patch.object(Path, "read_text", flaky):
            daily.add_note(self.cfg, "項目A", "メモ")
        items = daily.read_items(self.cfg)
        self.assertEqual([(i.text, i.notes) for i in items], [("項目A", ["メモ"]), ("ユーザーが追加", [])])

    def test_yesterdays_interrupted_upload_is_not_made_again(self):
        self.write_list("- [ ] 項目A", "- [ ] 項目B")
        run, variant = self.make_run()
        daily.start("項目A", str(run), at=self.at(2026, 11, 6, 0, 30), cfg=self.cfg, state_dir=self.state)
        # Uploaded, then killed before the state was updated; nothing ran again that day.
        (variant / "published_youtube.json").write_text(
            '{"video_id": "abc", "url": "https://youtube.com/shorts/abc", "uploaded_at": "2026-11-05T16:05:00Z"}')
        tomorrow = self.at(2026, 11, 7, 0, 30)
        info = daily.status(tomorrow, self.cfg, self.state)
        self.assertEqual((info["next"], info["resume"]), ("項目B", None))
        self.assertEqual(daily.check(tomorrow, self.cfg, self.state), 0)
        self.assertEqual(daily.load_state(self.state)["days"]["2026-11-06"]["status"], "scheduled")
        self.assertTrue(daily.read_items(self.cfg)[0].done)

    def interrupt_upload(self, variant, at):
        with patch("vg.publish_youtube.upload", side_effect=KeyboardInterrupt):  # killed mid-upload
            with self.assertRaises(KeyboardInterrupt):
                daily.publish(variant, confirm=True, at=at, cfg=self.cfg, state_dir=self.state)
        return variant / daily.UPLOAD_JOURNAL

    def recovered(self, uploaded_at):
        def find(run_dir, journal):  # what the real _find_upload records when the video is on the channel
            record = dict(UPLOADED, uploaded_at=uploaded_at, recovered=True)
            (run_dir / "published_youtube.json").write_text(json.dumps(record), encoding="utf-8")
            return record
        return find

    def test_interrupted_upload_is_reconciled_the_same_day(self):
        self.write_list("- [ ] 項目A")
        run, variant = self.make_run()
        friday = self.at(2026, 11, 6, 1, 0)
        daily.start("項目A", str(run), at=friday, cfg=self.cfg, state_dir=self.state)
        journal = self.interrupt_upload(variant, friday)
        self.assertEqual(json.loads(journal.read_text(encoding="utf-8"))["slot"], "2026-11-06T18:00:00+09:00")
        retry = self.at(2026, 11, 6, 5, 0)
        with patch.object(daily, "_find_upload", side_effect=self.recovered("2026-11-05T16:01:00Z")) as find, \
                patch("vg.publish_youtube.upload") as upload:
            result = daily.publish(variant, confirm=True, at=retry, cfg=self.cfg, state_dir=self.state)
        upload.assert_not_called()
        self.assertEqual(find.call_args.args[1]["title"], "タイトルA")
        self.assertEqual((result["video_id"], journal.exists()), ("abc", False))
        self.assertEqual(daily.check(retry, self.cfg, self.state), daily.SCHEDULED)

    def test_interrupted_upload_found_next_day_keeps_its_own_day(self):
        self.write_list("- [ ] 項目A", "- [ ] 項目B")
        run, variant = self.make_run()
        friday = self.at(2026, 11, 6, 1, 0)
        daily.start("項目A", str(run), at=friday, cfg=self.cfg, state_dir=self.state)
        self.interrupt_upload(variant, friday)
        saturday = self.at(2026, 11, 7, 0, 30)
        self.assertEqual(daily.status(saturday, self.cfg, self.state)["resume"], {"request": "項目A", "run": "runs/r1"})
        daily.start("項目A", str(run), at=saturday, cfg=self.cfg, state_dir=self.state)
        with patch.object(daily, "_find_upload", side_effect=ValueError("YouTube cannot be checked now")), \
                patch("vg.publish_youtube.upload") as upload:
            with self.assertRaisesRegex(ValueError, "cannot be checked"):
                daily.publish(variant, confirm=True, at=saturday, cfg=self.cfg, state_dir=self.state)
        upload.assert_not_called()
        with patch.object(daily, "_find_upload", side_effect=self.recovered("2026-11-05T16:01:00Z")), \
                patch("vg.publish_youtube.upload") as upload:
            with self.assertRaisesRegex(ValueError, "already uploaded for 2026-11-06"):
                daily.publish(variant, confirm=True, at=saturday, cfg=self.cfg, state_dir=self.state)
        upload.assert_not_called()
        days = daily.load_state(self.state)["days"]
        self.assertEqual((days["2026-11-06"]["status"], days["2026-11-07"]["status"]), ("scheduled", "superseded"))
        item = daily.read_items(self.cfg)[0]
        self.assertEqual((item.done, item.notes[-1]), (True, "2026-11-06(金) 18:00 公開予約 https://youtube.com/shorts/abc"))
        info = daily.status(saturday, self.cfg, self.state)
        self.assertEqual((info["scheduled"], info["resume"], info["next"]), (False, None, "項目B"))
        self.assertEqual(daily.check(saturday, self.cfg, self.state), 0)

    def test_recovery_cut_off_before_the_tick_uses_the_earlier_slot(self):
        self.write_list("- [ ] 項目A", "- [ ] 項目B")
        run, variant = self.make_run()
        friday = self.at(2026, 11, 6, 1, 0)
        daily.start("項目A", str(run), at=friday, cfg=self.cfg, state_dir=self.state)
        self.interrupt_upload(variant, friday)
        saturday = self.at(2026, 11, 7, 0, 30)
        daily.start("項目A", str(run), at=saturday, cfg=self.cfg, state_dir=self.state)
        with patch.object(daily, "_find_upload", side_effect=self.recovered("2026-11-05T16:01:00Z")), \
                patch.object(daily, "add_note", side_effect=KeyboardInterrupt):  # killed before the tick
            with self.assertRaises(KeyboardInterrupt):
                daily.publish(variant, confirm=True, at=saturday, cfg=self.cfg, state_dir=self.state)
        self.assertEqual(daily.check(saturday, self.cfg, self.state), 0)
        item = daily.read_items(self.cfg)[0]
        self.assertEqual((item.done, item.notes[-1]), (True, "2026-11-06(金) 18:00 公開予約 https://youtube.com/shorts/abc"))
        self.assertEqual(daily.status(saturday, self.cfg, self.state)["next"], "項目B")

    def test_find_upload_pages_through_the_channel(self):
        _, variant = self.make_run()

        def row(title, when, video):
            return {"snippet": {"title": title, "publishedAt": when, "resourceId": {"videoId": video}}}

        class Call:
            def __init__(self, value):
                self.value = value

            def execute(self):
                if isinstance(self.value, Exception):
                    raise self.value
                return self.value

        class FakeYouTube:
            def __init__(self, pages):
                self.pages, self.tokens = pages, []

            def channels(self):
                return SimpleNamespace(list=lambda **kw: Call(
                    {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}))

            def playlistItems(self):
                def listing(**kw):
                    self.tokens.append(kw["pageToken"])
                    return Call(self.pages[kw["pageToken"]])
                return SimpleNamespace(list=listing)

        journal = {"at": "2026-11-05T16:00:00+00:00", "title": "タイトルA"}
        newer = [row("別の動画", "2026-11-06T10:00:00Z", f"n{i}") for i in range(50)]
        found = FakeYouTube({None: {"items": newer, "nextPageToken": "p2"},
                             "p2": {"items": [row("タイトルA", "2026-11-05T16:01:00Z", "abc")], "nextPageToken": "p3"}})
        with patch("vg.publish_youtube._service", return_value=found):
            record = daily._find_upload(variant, journal)
        self.assertEqual((record["video_id"], found.tokens), ("abc", [None, "p2"]))
        self.assertEqual(json.loads((variant / "published_youtube.json").read_text())["video_id"], "abc")
        (variant / "published_youtube.json").unlink()
        older = FakeYouTube({None: {"items": [row("タイトルA", "2026-11-01T00:00:00Z", "old")], "nextPageToken": "p2"}})
        with patch("vg.publish_youtube._service", return_value=older):
            self.assertIsNone(daily._find_upload(variant, journal))
        self.assertEqual(older.tokens, [None])
        broken = FakeYouTube({None: RuntimeError("quota")})
        with patch("vg.publish_youtube._service", return_value=broken):
            with self.assertRaisesRegex(ValueError, "cannot be checked"):
                daily._find_upload(variant, journal)

    def test_unattended_publish_guards(self):
        with patch.dict(os.environ, {"VG_HEADLESS": "1"}):
            os.environ.pop("VG_AUTOPOST", None)
            self.assertEqual(cli.main(["daily", "publish", "x", "--confirm"]), 2)
        with patch.dict(os.environ, {"VG_HEADLESS": "1", "VG_AUTOPOST": "1"}):
            self.assertEqual(cli.main(["publish", "youtube", "x", "--confirm"]), 2)


if __name__ == "__main__":
    unittest.main()
