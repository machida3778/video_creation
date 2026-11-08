from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vg import cli, usage


class UsageTest(unittest.TestCase):
    def test_both_windows_must_be_at_or_under_the_limit(self):
        now = 1_000
        ok = usage.evaluate({"five_hour": {"used_percentage": 70, "resets_at": 2_000},
                             "seven_day": {"used_percentage": 3, "resets_at": 9_000}}, 70, now)
        self.assertTrue(ok["ok"])
        high = usage.evaluate({"five_hour": {"used_percentage": 14, "resets_at": 2_000},
                               "seven_day": {"used_percentage": 70.5, "resets_at": 9_000}}, 70, now)
        self.assertFalse(high["ok"])
        self.assertIn("週間枠 70.5%", usage.summary(high))
        self.assertIn("開始しない", usage.summary(high))

    def test_missing_or_reset_window_counts_as_zero(self):
        result = usage.evaluate({"five_hour": {"used_percentage": 95, "resets_at": 500}}, 70, 1_000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["windows"]["seven_day"]["used_percentage"], 0)

    def test_limit_from_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "headless.yaml"
            path.write_text("max_usage_percent: 55\n", encoding="utf-8")
            self.assertEqual(usage.limit(path), {"five_hour": 55, "seven_day": 55})
            path.write_text("max_minutes: 10\n", encoding="utf-8")
            self.assertEqual(usage.limit(path), {"five_hour": 70, "seven_day": 70})
            path.write_text("max_usage_percent: 0\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                usage.limit(path)
            path.write_text("max_usage_percent: {five_hour: 70, seven_day: 90}\n", encoding="utf-8")
            self.assertEqual(usage.limit(path), {"five_hour": 70, "seven_day": 90})
            path.write_text("max_usage_percent: {seven_day: 90}\n", encoding="utf-8")
            self.assertEqual(usage.limit(path), {"five_hour": 70, "seven_day": 90})
            path.write_text("max_usage_percent: {weekly: 90}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                usage.limit(path)
        self.assertEqual(usage.limit(), {"five_hour": 70, "seven_day": 90})  # the repository setting

    def test_per_window_limits(self):
        limits = {"five_hour": 70, "seven_day": 90}
        weekly_77 = {"five_hour": {"used_percentage": 0, "resets_at": 2_000},
                     "seven_day": {"used_percentage": 77, "resets_at": 9_000}}
        self.assertTrue(usage.evaluate(weekly_77, limits, 1_000)["ok"])
        weekly_91 = {"seven_day": {"used_percentage": 91, "resets_at": 9_000}}
        result = usage.evaluate(weekly_91, limits, 1_000)
        self.assertFalse(result["ok"])
        self.assertIn("週間枠上限90%超", usage.summary(result))
        hour_71 = {"five_hour": {"used_percentage": 71, "resets_at": 2_000}}
        self.assertIn("5時間枠上限70%超", usage.summary(usage.evaluate(hour_71, limits, 1_000)))

    def test_check_fails_closed(self):
        with patch.object(usage, "probe", side_effect=RuntimeError("no answer")):
            code, line = usage.check()
        self.assertEqual(code, usage.UNKNOWN)
        self.assertIn("取得できない", line)
        with patch.object(usage, "probe", return_value={"five_hour": {"used_percentage": 80}}):
            self.assertEqual(usage.check()[0], usage.HIGH)
            self.assertEqual(cli.main(["usage", "--check"]), usage.HIGH)
            self.assertEqual(cli.main(["usage"]), 0)
        with patch.object(usage, "probe", return_value={"five_hour": {"used_percentage": 20}}):
            self.assertEqual(cli.main(["usage", "--check"]), 0)

    def test_probe_environment_has_no_model_credentials(self):
        with patch.dict("os.environ", {"SOME_MODEL_API_KEY": "x", "MY_API_TOKEN": "z",
                                       "AWS_BEARER_TOKEN_BEDROCK": "t", "HOME_SETTING": "kept"}):
            env = usage._clean_env()
        self.assertFalse({"SOME_MODEL_API_KEY", "MY_API_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"} & set(env))
        self.assertEqual(env["HOME_SETTING"], "kept")

    def test_runner_checks_usage_before_starting_claude(self):
        text = (Path(__file__).resolve().parents[1] / "scripts/run_request.sh").read_text(encoding="utf-8")
        self.assertLess(text.index("usage --check"), text.index("voicevox.sh start"))
        self.assertLess(text.index('RUN_REQUEST_DRY:-0} == 1 ]]; then'), text.index("usage --check"))


if __name__ == "__main__":
    unittest.main()
