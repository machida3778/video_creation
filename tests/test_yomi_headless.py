"""Reading reviews and unattended entry points, using only temporary fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from vg import cli, media, qa, yomi
from vg.schemas import Line, Script


ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.http = Mock()

    def style_id(self, speaker):
        return 42

    def query(self, text, style, voice):
        self.calls.append((text, style, voice))
        return {"accent_phrases": [
            {"moras": [{"text": "ア"}, {"text": "イ"}], "pause_mora": {"text": "、"}},
            {"moras": [{"text": "ウ"}], "pause_mora": None},
            {"moras": [{"text": "エ"}], "pause_mora": {"text": "、"}}]}


class ReadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.engine = FakeEngine()

    def script(self, lines, **kwargs):
        media.save(self.run, "script", Script(title="synthetic", cast={"narrator": "vv_himari"},
                                               lines=lines, **kwargs))

    def test_reading_flags_pauses_yomi_and_voice_overrides(self):
        cases = [
            ("かなだけ", None, set()),
            ("12こと３こ", None, {"has_digits"}),
            ("AIとＡとé", None, {"has_latin"}),
            ("東京都", None, {"has_rare_kanji_run", "has_proper_noun_hint"}),
            ("山田さん", None, {"has_proper_noun_hint"}),
            ("ジョン氏", None, {"has_proper_noun_hint"}),
            ("アオイ社", None, {"has_proper_noun_hint"}),
            ("青森県", "アオモリケン", {"has_rare_kanji_run", "has_proper_noun_hint"}),
            ("AIで12こ", "エーアイデジュウニコ", {"has_latin", "has_digits"}),
            ("漢字かな", "", set()),
        ]
        self.script([Line(id=f"L{i}", text=text, yomi=reading)
                     for i, (text, reading, _) in enumerate(cases)],
                    voice_overrides={"narrator": {"speed": 1.37, "pitch": 0.04}})
        rows = yomi.reading_table(self.run, engine=self.engine)
        self.assertEqual(len(rows), len(cases))
        for i, ((text, reading, flags), row) in enumerate(zip(cases, rows)):
            with self.subTest(text=text):
                self.assertEqual(row["line_id"], f"L{i}")
                self.assertEqual(row["text"], text)
                self.assertEqual(row["yomi"], reading)
                self.assertEqual(row["engine_reading"], "アイ、ウエ、")
                self.assertEqual({key for key, value in row.items() if key.startswith("has_") and value}, flags)
                self.assertEqual(row["flagged"], bool(flags) and not bool(reading))
                sent, style, voice = self.engine.calls[i]
                self.assertEqual(sent, reading or text)
                self.assertEqual(style, 42)
                self.assertEqual(voice.speed, 1.37)
                self.assertEqual(voice.pitch, 0.04)
        self.engine.http.close.assert_not_called()

    def test_owned_client_is_closed_even_on_failure(self):
        self.script([Line(id="L1", text="かな")])
        with patch.object(yomi, "Voicevox", return_value=self.engine):
            yomi.reading_table(self.run)
        self.engine.http.close.assert_called_once_with()
        self.engine.http.close.reset_mock()
        self.engine.query = Mock(side_effect=RuntimeError("fake query failed"))
        with patch.object(yomi, "Voicevox", return_value=self.engine), self.assertRaises(RuntimeError):
            yomi.reading_table(self.run)
        self.engine.http.close.assert_called_once_with()

    def test_cli_review_approval_and_reset_for_variant(self):
        variant = self.run / "variants/v2"
        variant.mkdir(parents=True)
        media.save(variant, "script", Script(title="synthetic", cast={"narrator": "vv_himari"},
                                             lines=[Line(id="L1", text="AIが12こ")]))
        (self.run / "state.json").write_text("{}", encoding="utf-8")
        (variant / "state.json").write_text("{}", encoding="utf-8")
        output = io.StringIO()
        with patch.object(yomi, "Voicevox", return_value=self.engine), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["yomi", f"{self.run}:v2"]), 0)
            report = json.loads((variant / "yomi_check.json").read_text())
            self.assertIs(report["reviewed"], False)
            self.assertTrue(report["lines"][0]["flagged"])
            self.assertEqual(cli.main(["yomi", f"{self.run}:v2", "--approve"]), 0)
            self.assertIs(json.loads((variant / "yomi_check.json").read_text())["reviewed"], True)
            self.assertEqual(cli.main(["yomi", f"{self.run}:v2"]), 0)
            self.assertIs(json.loads((variant / "yomi_check.json").read_text())["reviewed"], False)
        self.assertIn("L1\t*DL\tAIが12こ\tアイ、ウエ、", output.getvalue())

    def test_smoke_generates_approved_readings_before_qa(self):
        stages = []

        def fake_stage(run, name):
            stages.append(name)
            if name == "tts":
                self.assertFalse((run / "yomi_check.json").exists())
            elif name == "qa":
                report = json.loads((run / "yomi_check.json").read_text())
                self.assertIs(report["reviewed"], True)
                self.assertEqual(len(report["lines"]), 3)

        with patch.object(cli.runs, "new_run", return_value=self.run), \
                patch.object(cli, "stage", side_effect=fake_stage), \
                patch.object(yomi, "Voicevox", return_value=self.engine), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_smoke(), 0)
        self.assertEqual(stages, cli.PIPELINE)
        self.assertEqual(len(self.engine.calls), 3)


class YomiQATests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.script = self.run / "script.json"
        self.script.write_text("{}", encoding="utf-8")
        os.utime(self.script, ns=(2_000_000_000, 2_000_000_000))
        self.review = self.run / "yomi_check.json"

    def test_missing_review_is_blocking_and_explains_command(self):
        check = qa.check_yomi(self.run)
        self.assertEqual((check.id, check.severity, check.redo_stage), ("yomi", "block", "script"))
        self.assertFalse(check.passed)
        self.assertIn("vg yomi", check.detail)

    def test_review_requires_literal_true_and_strictly_newer_file(self):
        for reviewed in [False, True, "true", 1, None]:
            for modified in [1_000_000_000, 2_000_000_000, 3_000_000_000]:
                with self.subTest(reviewed=reviewed, modified=modified):
                    self.review.write_text(json.dumps({"lines": [], "reviewed": reviewed}), encoding="utf-8")
                    os.utime(self.review, ns=(modified, modified))
                    self.assertEqual(qa.check_yomi(self.run).passed,
                                     reviewed is True and modified > 2_000_000_000)

    def test_malformed_review_and_missing_script_fail_cleanly(self):
        for text in ["invalid", "[]", "null", "42", "{}"]:
            with self.subTest(text=text):
                self.review.write_text(text, encoding="utf-8")
                self.assertFalse(qa.check_yomi(self.run).passed)
        self.review.write_text('{"reviewed": true}', encoding="utf-8")
        self.script.unlink()
        self.assertFalse(qa.check_yomi(self.run).passed)


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="headless repo ")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        for directory in ["scripts/setup", "config", ".venv/bin", "bin", "local", "outbox"]:
            (self.repo / directory).mkdir(parents=True)
        for path in ["scripts/run_request.sh", "scripts/run_queue.sh", "config/headless.yaml"]:
            shutil.copyfile(ROOT / path, self.repo / path)
        (self.repo / ".venv/bin/python").symlink_to(sys.executable)
        (self.repo / "scripts/site.sh").write_text(
            "export SITE_LOADED=1\nexport SITE_MODEL_API_KEY=site-fake-secret\n", encoding="utf-8")
        (self.repo / "scripts/setup/voicevox.sh").write_text(
            'printf "%s\\n" "$*" >> "$TEST_REPO/voicevox.calls"\nexit "${TEST_VOICEVOX_STATUS:-0}"\n',
            encoding="utf-8")
        self.executable("node", "pass\n")
        self.executable("timeout", "import os, sys\nos.execvpe(sys.argv[2], sys.argv[2:], os.environ)\n")
        self.executable("claude", '''import json, os, pathlib, sys
repo = pathlib.Path(os.environ["TEST_REPO"])
record = {"args": sys.argv[1:], "secret_names": [name for name in os.environ
          if "API_KEY" in name.upper() or "API_TOKEN" in name.upper()
          or name in ("ANTHROPIC_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK")],
          "site": os.environ.get("SITE_LOADED"), "stdin": sys.stdin.read(),
          "oauth": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}
with (repo / "claude.calls").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\\n")
if os.environ.get("TEST_APPEND_REQUEST"):
    with (repo / "local/requests.txt").open("a", encoding="utf-8") as handle:
        handle.write(os.environ["TEST_APPEND_REQUEST"] + "\\n")
print("fake claude completed")
sys.exit(7 if sys.argv[sys.argv.index("-p") + 1] == "/make-video fail" else 0)
''')
        self.env = {**os.environ, "HOME": str(self.repo / "home"),
                    "PATH": f"{self.repo / 'bin'}:{os.environ.get('PATH', '')}",
                    "TEST_REPO": str(self.repo), "RUN_REQUEST_DRY": "0",
                    "OPENAI_API_KEY": "fake-openai-secret", "ANTHROPIC_API_KEY": "fake-anthropic-secret",
                    "GEMINI_API_KEY": "fake-gemini-secret", "ARK_API_KEY": "fake-ark-secret",
                    "GOOGLE_API_KEY": "fake-google-secret", "ANTHROPIC_AUTH_TOKEN": "fake-auth-secret",
                    "OTHER_API_TOKEN": "fake-token-secret", "CUSTOM_API_KEY": "fake-custom-secret",
                    "AWS_BEARER_TOKEN_BEDROCK": "fake-bedrock-secret",
                    "CLAUDE_CODE_OAUTH_TOKEN": "fake-subscription-login"}

    def executable(self, name, source):
        path = self.repo / "bin" / name
        path.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
        path.chmod(0o755)

    def run_script(self, script, *args, **env):
        return subprocess.run(["bash", str(self.repo / "scripts" / script), *args],
                              cwd="/tmp", env={**self.env, **env}, input="", text=True,
                              capture_output=True, timeout=20)

    def calls(self):
        path = self.repo / "claude.calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_shell_syntax_and_usage(self):
        for script in ["run_request.sh", "run_queue.sh"]:
            with self.subTest(script=script):
                result = subprocess.run(["bash", "-n", str(self.repo / "scripts" / script)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                help_result = self.run_script(script, "--help")
                self.assertEqual(help_result.returncode, 0)
                self.assertIn("usage:", help_result.stdout)
        for args in [(), ("",), ("test", "--slug", "../bad"), ("test", "--wrong", "ok")]:
            self.assertNotEqual(self.run_script("run_request.sh", *args).returncode, 0)
        self.assertNotEqual(self.run_script("run_queue.sh", "unexpected").returncode, 0)

    def test_dry_run_expands_tools_quotes_request_and_strips_keys(self):
        request = 'synthetic "quotes" $(no-command) `no-command`; keep this literal'
        result = self.run_script("run_request.sh", request, "--slug", "test", RUN_REQUEST_DRY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = shlex.split(result.stdout)
        self.assertIn("/make-video " + request, argv)
        self.assertIn(f"Bash({self.repo}/.venv/bin/vg make:*)", argv)
        self.assertNotIn("$REPO", result.stdout)
        self.assertIn("180m", argv)
        self.assertIn("acceptEdits", argv)
        self.assertNotIn("--model", argv)
        self.assertFalse((self.repo / "local/logs").exists())
        self.assertFalse((self.repo / "voicevox.calls").exists())
        self.assertFalse(self.calls())
        # Execute the displayed argv directly with a fake CLI to inspect its environment.
        invoked = subprocess.run(argv, cwd=self.repo, env=self.env, input="", text=True,
                                 capture_output=True, timeout=20)
        self.assertEqual(invoked.returncode, 0, invoked.stderr)
        self.assertEqual(self.calls()[0]["secret_names"], [])
        self.assertEqual(self.calls()[0]["oauth"], "fake-subscription-login")
        self.assertNotIn("fake-openai-secret", result.stdout)

    def test_config_defaults_custom_model_and_narrow_permissions(self):
        path = self.repo / "config/headless.yaml"
        config = yaml.safe_load(path.read_text())
        tools = config["allowed_tools"]
        self.assertTrue({"Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch", "Agent"} <= set(tools))
        for tool in tools:
            if tool.startswith("Bash("):
                self.assertTrue(tool.startswith(("Bash(.venv/bin/vg ", "Bash($REPO/.venv/bin/vg ",
                                                "Bash(uv run vg ", "Bash(ls:", "Bash(cat:",
                                                "Bash(head:", "Bash(tail:", "Bash(rg:"))
                                or tool == "Bash(pwd)", tool)
        config.pop("max_minutes")
        config["model"] = "account-model"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        result = self.run_script("run_request.sh", "test", RUN_REQUEST_DRY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = shlex.split(result.stdout)
        self.assertIn("180m", argv)
        self.assertEqual(argv[-2:], ["--model", "account-model"])
        config["max_minutes"] = 12
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        self.assertIn("12m", shlex.split(self.run_script("run_request.sh", "test", RUN_REQUEST_DRY="1").stdout))

    def test_invalid_config_fails_without_running_claude(self):
        path = self.repo / "config/headless.yaml"
        config = yaml.safe_load(path.read_text())
        for changes in [{"max_minutes": 0}, {"max_minutes": True}, {"allowed_tools": []},
                        {"allowed_tools": ["Read\nBash(*)"]}, {"model": "two\nlines"}]:
            with self.subTest(changes=changes):
                path.write_text(yaml.safe_dump({**config, **changes}), encoding="utf-8")
                result = self.run_script("run_request.sh", "test", RUN_REQUEST_DRY="1")
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls())

    def test_request_logs_starts_voicevox_and_reports_newest_readme(self):
        for name, modified in [("new", 2_000_000_000), ("old", 1_000_000_000)]:
            path = self.repo / "outbox" / name / "README.md"
            path.parent.mkdir()
            path.write_text("synthetic", encoding="utf-8")
            os.utime(path, ns=(modified, modified))
        result = self.run_script("run_request.sh", "test", "--slug", "synthetic")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / "voicevox.calls").read_text(), "start\n")
        self.assertIn(str(self.repo / "outbox/new/README.md"), result.stdout)
        self.assertIn("log:", result.stdout)
        logs = list((self.repo / "local/logs").glob("*-synthetic-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("fake claude completed", logs[0].read_text())
        call = self.calls()[0]
        self.assertEqual(call["secret_names"], [])
        self.assertEqual(call["site"], "1")
        self.assertEqual(call["oauth"], "fake-subscription-login")

    def test_failures_propagate_and_print_log_path(self):
        result = self.run_script("run_request.sh", "fail")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("log:", result.stdout)
        before = len(self.calls())
        result = self.run_script("run_request.sh", "test", TEST_VOICEVOX_STATUS="9")
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(len(self.calls()), before)
        self.assertIn("log:", result.stdout)

    def test_queue_dry_run_preserves_comments_requests_and_done(self):
        queue = self.repo / "local/requests.txt"
        original = "# synthetic queue\n\nfirst\n  # comment\nsecond"
        queue.write_text(original, encoding="utf-8")
        result = self.run_script("run_queue.sh", RUN_REQUEST_DRY="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("--permission-mode"), 2)
        self.assertEqual(queue.read_text(), original)
        self.assertFalse((self.repo / "local/requests.done").exists())
        self.assertFalse(self.calls())

    def test_queue_success_duplicates_final_line_and_stdin_isolation(self):
        queue = self.repo / "local/requests.txt"
        queue.write_text("# keep\n\nduplicate\nduplicate\nlast", encoding="utf-8")
        result = self.run_script("run_queue.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(queue.read_text(), "# keep\n\n")
        calls = self.calls()
        self.assertEqual([call["args"][1] for call in calls],
                         ["/make-video duplicate", "/make-video duplicate", "/make-video last"])
        self.assertTrue(all(call["stdin"] == "" and not call["secret_names"] for call in calls))
        done = (self.repo / "local/requests.done").read_text().splitlines()
        self.assertEqual(len(done), 3)
        self.assertTrue(done[0].endswith(" 0 duplicate"))

    def test_queue_failure_keeps_failed_and_later_requests(self):
        queue = self.repo / "local/requests.txt"
        queue.write_text("# keep\nfirst\nfail\nlater\n", encoding="utf-8")
        result = self.run_script("run_queue.sh")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(queue.read_text(), "# keep\nfail\nlater\n")
        done = (self.repo / "local/requests.done").read_text().splitlines()
        self.assertEqual(len(done), 2)
        self.assertTrue(done[0].endswith(" 0 first"))
        self.assertTrue(done[1].endswith(" 7 fail"))
        self.assertEqual(len(self.calls()), 2)

    def test_queue_preserves_requests_appended_during_run(self):
        queue = self.repo / "local/requests.txt"
        queue.write_text("first\n", encoding="utf-8")
        result = self.run_script("run_queue.sh", TEST_APPEND_REQUEST="appended")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(queue.read_text(), "appended\n")
        self.assertEqual(len(self.calls()), 1)

    def test_missing_queue_has_actionable_error(self):
        result = self.run_script("run_queue.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requests.txt", result.stderr)


if __name__ == "__main__":
    unittest.main()
