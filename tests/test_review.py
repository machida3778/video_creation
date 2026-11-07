from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vg import cli, daily, review
from vg.daily import JST


class ReviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.cfg = dict(daily.DEFAULTS, enabled=True, list=str(self.root / "requests.md"))
        for module in (daily, review):
            patcher = patch.object(module, "ROOT", self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    def at(self, *args):
        return datetime(*args, tzinfo=JST)

    def write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_due_once_a_day(self):
        self.assertTrue(review.due(self.at(2026, 11, 7, 9), self.state)["due"])
        review.done("  ランキング型が \n 伸びた ", self.at(2026, 11, 7, 9), self.state)
        self.assertFalse(review.due(self.at(2026, 11, 7, 23), self.state)["due"])
        self.assertTrue(review.due(self.at(2026, 11, 8, 0, 30), self.state)["due"])
        review.done("二回目", self.at(2026, 11, 8, 1), self.state)
        log = review._tail(self.state / review.LOG, 7)
        self.assertEqual([e["summary"] for e in log], ["ランキング型が 伸びた", "二回目"])

    def test_metrics_are_joined_with_production_choices(self):
        run = self.root / "runs/r1"
        variant = run / "variants/v1"
        self.write(run / "route.json", {"type_id": "ranking", "params": {"items": 5}})
        self.write(run / "brief.json", {"tone": "淡々と"})
        self.write(variant / "script.json", {
            "search_query": "q", "hook_options": ["a", "b", "c", "d"],
            "cast": {"narrator": "narrator_female"}, "voice_overrides": {"narrator": {"speed": 1.4}},
            "lines": [{"id": "l1", "text": "フック"}, {"id": "l2", "text": "本文"}]})
        self.write(variant / "publish.json", {"music_track": "escort", "hashtags": ["#x"]})
        self.write(variant / "timings.json", {"total_sec": 48.2})
        self.write(variant / "qa.json", {"checks": [{"id": "speech_rate", "passed": False, "severity": "warn"},
                                                    {"id": "hook", "passed": True, "severity": "block"}]})
        self.write(variant / "published_youtube.json", {"video_id": "AAA"})
        self.write(variant / "covers_report.json", {"options": [], "chosen_id": "c1", "chosen_by": "judge", "scores": {
            "judge": "claude-subagent", "round": 2, "options": {
                "c1": {"items": [2, 2, 2, 1, 2, 2, 1, 1], "total": 13, "note": "n"},
                "c2": {"items": [1] * 8, "total": 8, "note": "m"}}}})
        self.write(self.state / "state.json", {"days": {"2026-11-07 09:00": {
            "request": "自動の項目", "slot": "2026-11-07T09:00:00+09:00", "status": "scheduled", "video_id": "AAA"}}})
        Path(self.cfg["list"]).write_text("- [x] 自動の項目\n  - 自動追加 2026-11-06: 理由\n", encoding="utf-8")
        history = {"list": [], "videos": [
            {"id": "AAA", "published": "2026-11-04T00:00:00Z", "privacy": "public", "views": 2000, "likes": 20,
             "shares": 4},
            {"id": "BBB", "published": "2026-11-07T00:00:00Z", "privacy": "public", "views": 0, "likes": 0}]}
        with patch.object(daily, "now", return_value=self.at(2026, 11, 7, 12)):
            out = review.data(cfg=self.cfg, state_dir=self.state, root=self.root, history=history)
        first, second = out["videos"]
        self.assertEqual((first["likes_per_1k_views"], first["shares_per_1k_views"]), (10.0, 2.0))
        self.assertTrue(first["mature"])
        self.assertFalse(second["mature"])
        self.assertEqual(out["mature_videos"], 1)
        self.assertEqual((first["request_source"], first["day_off"]), ("auto", True))
        p = first["production"]
        self.assertEqual((p["run"], p["type"], p["hook"], p["narrator"], p["speed"], p["bgm"]),
                         ("runs/r1", "ranking", "フック", "narrator_female", 1.4, "escort"))
        self.assertEqual((p["narration_seconds"], p["hook_options"], p["qa_warnings"]), (48.2, 4, ["speech_rate"]))
        cover = p["cover"]
        self.assertEqual((cover["id"], cover["chosen_by"], cover["judge_round"]), ("c1", "judge", 2))
        self.assertEqual((cover["judge_scores"]["total"], cover["other_totals"]), (13, {"c2": 8}))
        self.assertIsNone(second["production"])
        self.assertTrue(out["due"])

    def test_cli_done_and_due(self):
        with patch.object(review, "done", return_value={"date": "2026-11-07"}) as done:
            self.assertEqual(cli.main(["daily", "review", "--done", "要約"]), 0)
            done.assert_called_once_with("要約")
        with patch.object(review, "due", return_value={"due": True}) as due:
            self.assertEqual(cli.main(["daily", "review", "--due"]), 0)
            due.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
