"""Cover/posting contracts with synthetic local files and mocked Google clients only."""
import builtins
import contextlib
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import warnings

from PIL import Image
from pydantic import ValidationError
import yaml

from vg import cli, media, package as packaging, publish_youtube as youtube, thumbnail
from vg.package import post_kit, x_length
from vg.schemas import Asset, Brief, Check, CoverOption, Covers, Line, Manifest, Publish, QAReport, Scene, Script, Storyboard, Timeline


ROOT = Path(__file__).resolve().parents[1]


def option(**changes):
    return CoverOption(**(dict(id="question", template="question", text="なぜへこむ？", emphasis=["なぜ"],
                               image="image", line_id="L1", palette="yellow", ai_generated=False,
                               photorealistic=False) | changes))


def publish(**changes):
    return Publish(**(dict(title="タイトル", description="最初の文。次の文。\n出典", hashtags=["#解説", "#科学", "#動画"],
                          platforms=["youtube_shorts"], synthetic_media=True) | changes))


class CoverTests(unittest.TestCase):
    def test_stage_and_schema_boundaries(self):
        for count in (1, 2, 3):
            self.assertEqual(len(Covers(options=[option(id=f"c{i}") for i in range(count)]).options), count)
        for options in ([], [option()] * 2, [option(id=f"c{i}") for i in range(4)]):
            with self.assertRaises(ValidationError):
                Covers(options=options)
        for changes in (dict(text="あ" * 21), dict(text="あ\nい\nう\nえ"), dict(text="\n"),
                        dict(text="あ\tい"), dict(badge="第1位"), dict(template="other"), dict(id="../bad"),
                        dict(emphasis=["なし"]), dict(template="ranking", badge=""), dict(id="sheet")):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                option(**changes)
        with warnings.catch_warnings(record=True) as notices:
            warnings.simplefilter("always")
            self.assertEqual(len(option(text="あ" * 20, emphasis=[]).text), 20)
        self.assertTrue(any("12" in str(item.message) for item in notices))
        self.assertEqual(option(text="なぜ\nへこむ？").text.count("\n"), 1)
        self.assertEqual(option(template="ranking", badge="第1位").badge, "第1位")
        with self.assertRaisesRegex(ValidationError, "collide"):
            Covers(options=[option(id="c"), option(id="c_120")])

    def test_cover_only_image_from_a_prompt(self):
        own = option(image="", prompt="one empty chair, close-up")
        self.assertEqual(own.image, "cover_question")
        self.assertEqual(option(image="cover_question", prompt="x").image, "cover_question")
        for changes in (dict(image=""), dict(image="img_C1", prompt="x"), dict(image="", prompt=" ")):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                option(**changes)
        # Round trip: the saved JSON reloads unchanged.
        again = CoverOption.model_validate_json(own.model_dump_json())
        self.assertEqual((again.image, again.prompt), ("cover_question", "one empty chair, close-up"))
        # A report written before the optional prompt fields existed still matches the same option.
        from vg import thumbnail
        legacy = {k: v for k, v in option().model_dump().items() if k not in ("prompt", "negative", "seed")}
        self.assertTrue(thumbnail._same(legacy, option()))
        self.assertFalse(thumbnail._same(legacy, option(text="なぜ別？")))
        self.assertFalse(thumbnail._same({"broken": True}, option()))

    def test_report_contrast_chars_lines_and_safe_region(self):
        for palette in thumbnail.PALETTES:
            report = thumbnail.option_report(option(palette=palette))
            self.assertTrue(report["passed"])
            self.assertEqual(report["chars"], 6)
            self.assertGreaterEqual(report["min_glyph_size"], 140)
            self.assertTrue(all(value >= 4.5 for value in report["contrast"].values()))
        self.assertEqual(thumbnail.contrast("#000000", "#000000"), 1)
        self.assertEqual(thumbnail.contrast("#ffffff", "#000000"), 21)
        with patch.dict(thumbnail.PALETTES, yellow="#000000"):
            self.assertFalse(thumbnail.option_report(option())["checks"]["contrast"])
        placement = thumbnail.layout(option())
        placement["box"]["x"] = 0
        self.assertFalse(thumbnail.option_report(option(), placement)["checks"]["safe_region"])
        report = thumbnail.option_report(option(text="あいうえおか\nき", emphasis=[]))
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["min_glyph_size"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertFalse(thumbnail.option_report(option(text="あ" * 20, emphasis=[]))["checks"]["lines"])
        self.assertFalse(thumbnail.option_report(option(template="ranking", badge="第1234567位"))["checks"]["safe_region"])

    def test_stills_previews_sheet_and_manifest_payoff_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (108, 192), "red").save(root / "focal.png")
            media.save(root, "covers", Covers(options=[option(), option(id="answer", template="reveal")]))
            media.save(root, "script", Script(title="test", lines=[Line(id="L1", text="test")]))
            media.save(root, "manifest", Manifest(assets=[Asset(id="image", path="focal.png", kind="image",
                origin="generated", license="synthetic fixture", commercial_ok=True)]))
            calls = []
            real_run = media.run

            def render_or_ffmpeg(command, **kwargs):
                if command[0] != "npx":
                    return real_run(command, **kwargs)
                calls.append(command)
                target = Path(command[6])
                scale = 2 if "--scale=2" in command else 1
                Image.new("RGB", (1080 * scale, 1920 * scale), "red").save(target)
                self.assertNotIn("OPENAI_API_KEY", kwargs["env"])

            with patch.object(thumbnail, "run", side_effect=render_or_ffmpeg):
                report_path = thumbnail.thumbnail(root)
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(item["passed"] for item in json.loads(report_path.read_text())["options"]))
            self.assertEqual(thumbnail.selected_cover(root), "question")
            for suffix, dimensions in ((".jpg", (1080, 1920)), (".png", (2160, 3840)),
                                       ("_3x4.jpg", (1080, 1440)), ("_4x5.jpg", (1080, 1350)),
                                       ("_1x1.jpg", (1080, 1080)), ("_120.jpg", (120, 213)),
                                       ("_180.jpg", (180, 320))):
                with Image.open(root / "thumbs" / ("question" + suffix)) as image:
                    self.assertEqual(image.size, dimensions)
            with Image.open(root / "thumbs/sheet.jpg") as sheet:
                self.assertEqual(sheet.size, (660, 1360))
            report = json.loads(report_path.read_text())
            report["chosen_id"] = "answer"
            report_path.write_text(json.dumps(report))
            self.assertEqual(thumbnail.selected_cover(root), "answer")
            media.save(root, "covers", Covers(options=[option(id="answer", text="なぜ？")]))
            with self.assertRaisesRegex(ValueError, "chosen cover"):
                thumbnail.selected_cover(root)
            media.save(root, "covers", Covers(options=[option(), option(id="answer", template="reveal")]))
            report["chosen_id"] = "unknown"
            report_path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "chosen cover"):
                thumbnail.selected_cover(root)
            media.save(root, "covers", Covers(options=[option(line_id="missing")]))
            with self.assertRaisesRegex(ValueError, "payoff line"):
                thumbnail.thumbnail(root)

    def test_cli_thumbnail(self):
        with patch.object(cli.runs, "resolve", return_value=Path("/tmp/synthetic")), \
                patch.object(thumbnail, "thumbnail", return_value=Path("/tmp/synthetic/covers_report.json")) as render, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["thumbnail", "synthetic:v2"]), 0)
            render.assert_called_once_with(Path("/tmp/synthetic"))


class PostKitTests(unittest.TestCase):
    def test_limits_sentence_tags_dimensions_and_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            Image.new("RGB", (1080, 1920), "red").save(root / "cover.jpg")
            for metadata in (publish(), publish(title="全" * 1000, description="最" * 4000 + "。後。",
                                               hashtags=["#タグ" * 1000] * 7),
                             publish(hashtags=[], synthetic_media=False)):
                kit = post_kit(root, metadata)
                tiktok, x = ((kit / name).read_text() for name in ("tiktok.txt", "x.txt"))
                self.assertLessEqual(len(tiktok), 2200)
                self.assertLessEqual(x_length(x), 280)
                self.assertIn(len(tiktok.splitlines()[-1].split()), (3, 4, 5))
                self.assertEqual("AIラベル" in (kit / "README.txt").read_text(), metadata.synthetic_media)
                with Image.open(kit / "cover_tiktok.jpg") as image:
                    self.assertEqual(image.size, (1080, 1920))
                with Image.open(kit / "cover_tiktok_3x4.jpg") as image:
                    self.assertEqual(image.size, (1080, 1440))
            kit = post_kit(root, publish())
            self.assertEqual((kit / "tiktok.txt").read_text().splitlines()[1], "最初の文。")
            self.assertEqual(x_length("aあＡ"), 5)


class YoutubeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.variant = self.root / "variants/v2"
        self.variant.mkdir(parents=True)
        media.save(self.variant, "qa", QAReport(checks=[Check(id="format", passed=True)]))
        media.save(self.variant, "publish", publish())
        for name in ("final.mp4", "final.srt", "cover.jpg", "cover.png"):
            (self.variant / name).write_bytes(b"synthetic")
        self.client = Mock()
        self.client.videos().insert().next_chunk.side_effect = [(None, None), (None, {"id": "test123"})]
        self.client.videos().list().execute.return_value = {"items": [{"id": "test123", "processingDetails": {
            "processingStatus": "succeeded"}, "snippet": {"thumbnails": {"shortsAspectRatio": {}}}}]}

    def test_request_body_and_schedule_rules(self):
        body = youtube.request_body(publish(), "public")
        self.assertEqual(body, {"snippet": {"title": "タイトル", "description": publish().description,
            "tags": ["解説", "科学", "動画"], "categoryId": "27", "defaultLanguage": "ja", "defaultAudioLanguage": "ja"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "embeddable": True, "containsSyntheticMedia": True}})
        scheduled = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()
        self.assertIn("publishAt", youtube.request_body(publish(), "private", scheduled)["status"])
        self.assertFalse(youtube.request_body(publish(synthetic_media=False))["status"]["containsSyntheticMedia"])
        for privacy, at in (("public", scheduled), ("unlisted", scheduled), ("other", None),
                            ("private", "invalid"), ("private", "2030-01-01T12:00:00"),
                            ("private", "2000-01-01T12:00:00Z")):
            with self.subTest(privacy=privacy, at=at), self.assertRaises(ValueError):
                youtube.request_body(publish(), privacy, at)

    def test_upload_requests_record_and_duplicate_refusal(self):
        scheduled = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with patch.object(youtube, "_service", return_value=self.client) as service, \
                patch.object(youtube, "_media_upload", side_effect=lambda path, mime, **kwargs: (str(path), mime, kwargs)):
            result = youtube.upload(self.variant, publish_at=scheduled)
            request = self.client.videos().insert.call_args.kwargs
            self.assertEqual(request["part"], "snippet,status")
            self.assertTrue(request["body"]["status"]["containsSyntheticMedia"])
            self.assertIn("publishAt", request["body"]["status"])
            self.assertTrue(request["media_body"][2]["resumable"])
            self.assertEqual(self.client.thumbnails().set.call_args.kwargs["media_body"][1], "image/png")
            self.assertEqual(self.client.captions().insert.call_args.kwargs["body"], {"snippet": {
                "videoId": "test123", "language": "ja", "name": "日本語", "isDraft": False}})
            self.assertEqual(result["url"], "https://youtube.com/shorts/test123")
            self.assertEqual((result["thumbnail"], result["captions"]), ("ok", "ok"))
            self.assertEqual(json.loads((self.variant / "published_youtube.json").read_text()), result)
            with self.assertRaisesRegex(ValueError, "already uploaded"):
                youtube.upload(self.variant)
            self.assertEqual(service.call_count, 1)
            self.assertIn("body", youtube.upload(self.variant, dry_run=True, force=True))

    def test_optional_errors_keep_uploaded_id_and_fallback_jpeg(self):
        (self.variant / "cover.png").unlink()
        self.client.thumbnails().set().execute.side_effect = RuntimeError("not eligible for custom thumbnails")
        self.client.captions().insert().execute.side_effect = RuntimeError("synthetic caption failure")
        with patch.object(youtube, "_service", return_value=self.client), \
                patch.object(youtube, "_media_upload", return_value="media"), warnings.catch_warnings(record=True) as notices:
            result = youtube.upload(self.variant)
        self.assertEqual(len(notices), 2)
        self.assertIn("not eligible", result["thumbnail"])
        self.assertIn("error:", result["captions"])
        self.assertEqual(result["video_id"], "test123")
        self.assertTrue((self.variant / "published_youtube.json").exists())
        self.assertTrue(youtube._cover(self.variant).name.endswith("jpg"))

    def test_cover_waits_for_processing_and_needs_vertical_version(self):
        def item(status, *keys):
            return {"items": [{"processingDetails": {"processingStatus": status},
                               "snippet": {"thumbnails": {key: {} for key in keys}}}]}
        client, sleeps = Mock(), []
        client.videos().list().execute.side_effect = [item("processing"), item("succeeded"),
                                                      item("succeeded", "maxres"), item("succeeded", "maxres", "shortsAspectRatio")]
        with patch.object(youtube, "_media_upload", return_value="media"):
            youtube._set_cover(client, "test123", Path("cover.png"), sleep=sleeps.append)
            self.assertEqual(client.thumbnails().set().execute.call_count, 1)
            self.assertEqual(len(sleeps), 2)
            client.videos().list().execute.side_effect = None
            client.videos().list().execute.return_value = item("succeeded", "maxres", "uhd")
            with self.assertRaisesRegex(RuntimeError, "vertical"):
                youtube._set_cover(client, "test123", Path("cover.png"), sleep=lambda _: None)
            self.assertEqual(client.thumbnails().set().execute.call_count, 3)
            client.videos().list().execute.return_value = item("processing")
            with self.assertRaisesRegex(RuntimeError, "still processing"), patch.object(youtube, "PROCESSING_TIMEOUT_SEC", 0):
                youtube._set_cover(client, "test123", Path("cover.png"), sleep=lambda _: None)
            self.assertFalse(youtube._processed(client, "test123", timeout=0, sleep=lambda _: None))

    def test_repair_resets_only_processed_covers_without_vertical_version(self):
        (self.variant / "published_youtube.json").write_text(json.dumps({"video_id": "test123", "thumbnail": "ok"}))
        other = self.root / "runs/b/variants/v1"
        other.mkdir(parents=True)
        (other / "published_youtube.json").write_text(json.dumps({"video_id": "fine456"}))
        runs_dir = self.root / "runs"
        (runs_dir / "a/variants").mkdir(parents=True)
        self.variant.rename(runs_dir / "a/variants/v2")
        self.client.videos().list().execute.return_value = {"items": [
            {"id": "test123", "processingDetails": {"processingStatus": "succeeded"}, "snippet": {"thumbnails": {"uhd": {}}}},
            {"id": "fine456", "processingDetails": {"processingStatus": "succeeded"},
             "snippet": {"thumbnails": {"shortsAspectRatio": {}}}}]}
        with patch.object(youtube, "_service", return_value=self.client), \
                patch.object(youtube, "set_thumbnail", return_value={"thumbnail": "ok"}) as reset:
            repaired = youtube.repair_thumbnails(runs_dir)
        reset.assert_called_once_with(runs_dir / "a/variants/v2")
        self.assertEqual([r["video_id"] for r in repaired], ["test123"])

    def test_captions_before_cover_and_deferred_cover(self):
        order = []
        self.client.captions().insert().execute.side_effect = lambda: order.append("captions")
        self.client.thumbnails().set().execute.side_effect = lambda: order.append("thumbnail")
        with patch.object(youtube, "_service", return_value=self.client), \
                patch.object(youtube, "_media_upload", return_value="media"):
            youtube.upload(self.variant)
            self.assertEqual(order, ["captions", "thumbnail"])
            order.clear()
            self.client.videos().insert().next_chunk.side_effect = [(None, {"id": "test456"})]
            result = youtube.upload(self.variant, force=True, cover=False)
        self.assertEqual(order, ["captions"])
        self.assertEqual(result["thumbnail"], "pending")

    def test_repair_gives_up_after_repeated_failures(self):
        runs_dir = self.root / "runs"
        (runs_dir / "a/variants").mkdir(parents=True)
        variant = self.variant.rename(runs_dir / "a/variants/v2")
        record = variant / "published_youtube.json"
        record.write_text(json.dumps({"video_id": "test123", "thumbnail": "ok"}))
        self.client.videos().list().execute.return_value = {"items": [
            {"id": "test123", "processingDetails": {"processingStatus": "succeeded"}, "snippet": {"thumbnails": {}}}]}
        with patch.object(youtube, "_service", return_value=self.client), \
                patch.object(youtube, "set_thumbnail", side_effect=RuntimeError("refused")) as reset:
            for _ in range(youtube.REPAIR_ATTEMPTS + 2):
                youtube.repair_thumbnails(runs_dir)
        self.assertEqual(reset.call_count, youtube.REPAIR_ATTEMPTS)
        saved = json.loads(record.read_text())
        self.assertEqual(saved["thumbnail_repair_failures"], youtube.REPAIR_ATTEMPTS)
        self.assertIn("refused", saved["thumbnail"])

    def test_large_png_falls_back(self):
        with (self.variant / "cover.png").open("wb") as handle:
            handle.truncate(50_000_001)
        self.assertEqual(youtube._cover(self.variant).name, "cover.jpg")

    def test_concurrent_upload_is_refused_before_client_creation(self):
        import fcntl

        with (self.variant / ".publish_youtube.lock").open("a") as lock, \
                patch.object(youtube, "_service", side_effect=AssertionError("network")):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "already in progress"):
                youtube.upload(self.variant)

    def test_packaged_cover_choice_copy_and_comparison(self):
        media.save(self.variant, "brief", Brief(request="synthetic", topic="synthetic", platforms=["youtube_shorts", "x"]))
        media.save(self.variant, "script", Script(title="synthetic", lines=[Line(id="L1", text="最初の文。")]))
        media.save(self.variant, "storyboard", Storyboard(type_id="synthetic", scenes=[
            Scene(id="C1", line_ids=["L1"], component="background")]))
        media.save(self.variant, "manifest", Manifest())
        media.save(self.variant, "timeline", Timeline(duration=1, clips=[]))
        (self.variant / "variant.json").write_text(json.dumps({"id": "v2", "note": "synthetic", "parts": {}}))
        covers = Covers(options=[option(), option(id="answer", template="reveal", ai_generated=True, photorealistic=True)])
        media.save(self.variant, "covers", covers)
        (self.variant / "covers_report.json").write_text(json.dumps({
            "options": [thumbnail.option_report(c) for c in covers.options], "chosen_id": "answer"}))
        thumbs = self.variant / "thumbs"
        thumbs.mkdir()
        for cover, color in zip(covers.options, ("red", "blue")):
            Image.new("RGB", (1080, 1920), color).save(thumbs / f"{cover.id}.jpg")
            Image.new("RGB", (2160, 3840), color).save(thumbs / f"{cover.id}.png")
        Image.new("RGB", (660, 680), "white").save(thumbs / "sheet.jpg")
        with patch.object(packaging, "ROOT", self.root):
            destination = packaging.package(self.variant)
        self.assertEqual((destination / "cover.jpg").read_bytes(), (thumbs / "answer.jpg").read_bytes())
        self.assertEqual((destination / "cover.png").read_bytes(), (thumbs / "answer.png").read_bytes())
        self.assertTrue((destination / "thumbs/sheet.jpg").is_file())
        self.assertTrue((destination / "post_kit/x.txt").is_file())
        self.assertTrue(media.load(destination, "publish").synthetic_media)
        self.assertEqual(youtube._cover(self.variant), thumbs / "answer.png")
        comparison = packaging.compare(self.root, outbox=self.root / "outbox").read_text()
        self.assertIn("v2/thumbs/question.jpg", comparison)
        self.assertIn("v2/thumbs/answer.jpg", comparison)
        self.assertIn("現在の表紙", comparison)
        report = json.loads((self.variant / "covers_report.json").read_text())
        del report["chosen_id"]
        (self.variant / "covers_report.json").write_text(json.dumps(report))
        with patch.object(packaging, "ROOT", self.root):
            packaging.package(self.variant)
        self.assertEqual((destination / "cover.jpg").read_bytes(), (thumbs / "question.jpg").read_bytes())

    def test_dry_run_confirm_and_qa_gate_never_create_client(self):
        with patch.object(youtube, "_service", side_effect=AssertionError("network")), \
                patch.object(cli.runs, "resolve", return_value=self.variant), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["publish", "youtube", "synthetic:v2"]), 1)
            self.assertIn("タイトル", output.getvalue())
            self.assertEqual(cli.main(["publish", "youtube", "synthetic:v2", "--confirm", "--dry-run"]), 0)
            self.assertFalse((self.variant / "published_youtube.json").exists())
            media.save(self.variant, "qa", QAReport(checks=[Check(id="format", passed=False)]))
            self.assertEqual(cli.main(["publish", "youtube", "synthetic:v2", "--confirm"]), 1)
        with patch.object(cli.runs, "resolve", return_value=self.root), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["publish", "youtube", "synthetic", "--confirm"]), 1)

    def test_cli_auth_and_upload_flags(self):
        with patch.object(youtube, "authorize") as auth, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["publish", "auth", "youtube"]), 0)
            auth.assert_called_once_with()
        with patch.object(cli.runs, "resolve", return_value=self.variant), \
                patch.object(youtube, "upload", return_value={"url": "synthetic"}) as upload, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["publish", "youtube", "synthetic:v2", "--confirm", "--force", "--privacy", "public"]), 0)
            upload.assert_called_once_with(self.variant, privacy="public", publish_at=None, dry_run=False, force=True)

    def test_authorize_flow_is_lazy_and_token_private(self):
        flow_module = types.ModuleType("google_auth_oauthlib.flow")
        flow_module.InstalledAppFlow = Mock()
        flow = flow_module.InstalledAppFlow.from_client_secrets_file.return_value
        flow.run_local_server.return_value.to_json.return_value = '{"token":"synthetic"}'
        token = self.root / "oauth/token.json"
        with patch.dict(sys.modules, {"google_auth_oauthlib": types.ModuleType("google_auth_oauthlib"),
                                      "google_auth_oauthlib.flow": flow_module}):
            youtube.authorize(self.root / "client.json", token)
        flow.run_local_server.assert_called_once_with(port=8765, open_browser=False)
        self.assertEqual(flow_module.InstalledAppFlow.from_client_secrets_file.call_args.kwargs["scopes"], youtube.AUTH_SCOPES)
        self.assertIn(youtube.ANALYTICS_SCOPE, youtube.AUTH_SCOPES)
        self.assertEqual(token.stat().st_mode & 0o777, 0o600)

    def test_import_and_dry_run_without_google_libraries(self):
        real_import = builtins.__import__

        def no_google(name, *args, **kwargs):
            if name.startswith(("google", "googleapiclient")):
                raise AssertionError("eager Google import")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_google):
            importlib.reload(youtube)
            self.assertIn("body", youtube.upload(self.variant, dry_run=True))


class HeadlessPublicationTests(unittest.TestCase):
    def test_headless_has_no_publish_or_broad_vg_permission(self):
        tools = yaml.safe_load((ROOT / "config/headless.yaml").read_text())["allowed_tools"]
        for prefix in (".venv/bin/vg", "$REPO/.venv/bin/vg", "uv run vg"):
            self.assertNotIn(f"Bash({prefix}:*)", tools)
            self.assertIn(f"Bash({prefix} make:*)", tools)
            self.assertIn(f"Bash({prefix} thumbnail:*)", tools)
        self.assertFalse(any("publish" in tool or tool == "Bash(*)" for tool in tools))


if __name__ == "__main__":
    unittest.main()
