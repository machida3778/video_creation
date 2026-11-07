"""The independent cover judge's record: stored by `vg thumbnail --score/--choose`, kept by later renders."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

from PIL import Image
from pydantic import ValidationError

from vg import cli, media, thumbnail
from vg.schemas import Asset, CoverJudgement, CoverOption, CoverScore, Covers, Line, Manifest, Script


def option(**changes):
    return CoverOption(**(dict(id="question", template="question", text="合成テストの問い？", emphasis=["合成"],
                               image="image", line_id="L1", palette="yellow", ai_generated=False,
                               photorealistic=False) | changes))


def judge(round=1, **items):
    return CoverJudgement(round=round, options=[CoverScore(id=key, items=value, note=f"{key} note")
                                                for key, value in items.items()])


class CoverJudgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        Image.new("RGB", (108, 192), "red").save(self.root / "focal.png")
        media.save(self.root, "script", Script(title="合成テストの問い？架空の題材", lines=[Line(id="L1", text="test")]))
        media.save(self.root, "manifest", Manifest(assets=[Asset(
            id="image", path="focal.png", kind="image", origin="generated", license="synthetic fixture",
            commercial_ok=True)]))
        self.covers(option(), option(id="answer", template="reveal"), option(id="third"))
        for name, fake in (("run", self.still), ("previews", self.previews)):
            patcher = patch.object(thumbnail, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def covers(self, *options):
        media.save(self.root, "covers", Covers(options=list(options)))

    @staticmethod
    def still(command, **_kwargs):
        scale = 2 if "--scale=2" in command else 1
        Image.new("RGB", (1080 * scale, 1920 * scale), "red").save(Path(command[6]))

    @staticmethod
    def previews(jpeg):
        for width in (120, 180):
            Image.new("RGB", (width, width * 16 // 9), "red").save(jpeg.with_stem(f"{jpeg.stem}_{width}"))

    def report(self):
        return json.loads((self.root / "covers_report.json").read_text(encoding="utf-8"))

    def test_scores_choose_the_best_usable_cover(self):
        thumbnail.thumbnail(self.root)
        # "answer" has the best total but 0 on ④, so it is not used; the tie goes to the earlier option.
        result = thumbnail.record(self.root, judge(question=[2, 2, 1, 1, 2, 2, 1, 1], answer=[2, 2, 2, 0, 2, 2, 2, 2],
                                                   third=[2, 2, 1, 1, 2, 2, 1, 1]))
        self.assertEqual((result["best"], result["total"], result["usable"]), ("question", 12, ["question", "third"]))
        self.assertFalse(result["bar_met"])  # 12 points, but no option with 2 on ④ and ⑦
        report = self.report()
        self.assertEqual((report["chosen_id"], report["chosen_by"]), ("question", "judge"))
        self.assertEqual(list(report["scores"]["options"]), ["question", "answer", "third"])
        self.assertEqual(report["scores"]["options"]["answer"], {"items": [2, 2, 2, 0, 2, 2, 2, 2], "total": 14,
                                                                "note": "answer note"})
        self.assertEqual((report["scores"]["judge"], report["scores"]["round"]), ("claude-subagent", 1))
        self.assertEqual(thumbnail.selected_cover(self.root), "question")
        # Round two after the redo replaces the scores; a person's choice overrides the judge and keeps them.
        result = thumbnail.record(self.root, judge(round=2, third=[2, 2, 2, 2, 2, 2, 2, 1]))
        self.assertEqual((result["best"], result["bar_met"], result["strong_image"]), ("third", True, ["third"]))
        self.assertEqual(list(self.report()["scores"]["options"]), ["third"])
        result = thumbnail.record(self.root, choose="answer")
        self.assertEqual((result["chosen_id"], result["chosen_by"]), ("answer", "choose"))
        self.assertEqual(list(self.report()["scores"]["options"]), ["third"])
        self.assertEqual(thumbnail.selected_cover(self.root), "answer")

    def test_a_new_render_keeps_the_record_of_unchanged_covers(self):
        thumbnail.thumbnail(self.root)
        thumbnail.record(self.root, judge(question=[2] * 8, answer=[1] * 8))
        thumbnail.record(self.root, choose="answer")
        thumbnail.thumbnail(self.root)
        report = self.report()
        self.assertEqual((report["chosen_id"], report["chosen_by"]), ("answer", "choose"))
        self.assertEqual(set(report["scores"]["options"]), {"question", "answer"})
        # A new headline needs a new judgement: its old score goes, the choice stays while the cover passes.
        self.covers(option(), option(id="answer", template="reveal", text="合成テスト？架空"), option(id="third"))
        thumbnail.thumbnail(self.root)
        report = self.report()
        self.assertEqual(report["chosen_id"], "answer")
        self.assertEqual(set(report["scores"]["options"]), {"question"})
        self.assertFalse((self.root / thumbnail.PREVIOUS_REPORT).exists())
        # The chosen cover no longer passes its checks: the choice is dropped, with a warning.
        self.covers(option(), option(id="answer", template="reveal", text="あ" * 20, emphasis=[]), option(id="third"))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            thumbnail.thumbnail(self.root)
        self.assertTrue(any("no longer passes" in str(w.message) for w in caught))
        self.assertNotIn("chosen_id", self.report())
        self.assertEqual(thumbnail.selected_cover(self.root), "question")

    def test_a_failed_render_keeps_the_record_for_the_retry(self):
        thumbnail.thumbnail(self.root)
        thumbnail.record(self.root, judge(third=[2] * 8))
        with patch.object(thumbnail, "run", side_effect=RuntimeError("remotion failed")):
            with self.assertRaises(RuntimeError):
                thumbnail.thumbnail(self.root)
        self.assertFalse((self.root / "covers_report.json").exists())  # a half-rendered set is never trusted
        self.assertTrue((self.root / thumbnail.PREVIOUS_REPORT).exists())
        thumbnail.thumbnail(self.root)
        report = self.report()
        self.assertEqual((report["chosen_id"], list(report["scores"]["options"])), ("third", ["third"]))
        self.assertFalse((self.root / thumbnail.PREVIOUS_REPORT).exists())

    def test_hand_written_scores_are_read_and_kept(self):
        legacy = {"judge": "independent subagent", "question": {"items": [2] * 8, "total": 16},
                  "answer": {"items": [1] * 8, "total": 8}}
        normal = thumbnail.scores(legacy)
        self.assertEqual((normal["judge"], normal["options"]["question"]["total"]), ("independent subagent", 16))
        self.assertIsNone(thumbnail.scores({"judge": "nobody"}))
        self.assertIsNone(thumbnail.scores(None))
        thumbnail.thumbnail(self.root)
        report = self.report() | {"chosen_id": "answer", "scores": legacy}
        (self.root / "covers_report.json").write_text(json.dumps(report), encoding="utf-8")
        thumbnail.thumbnail(self.root)
        report = self.report()
        self.assertEqual(report["chosen_id"], "answer")
        self.assertEqual(set(report["scores"]["options"]), {"question", "answer"})

    def test_validation_and_rollback(self):
        for items in ([2] * 7, [2] * 9, [3] + [2] * 7, [-1] + [2] * 7):
            with self.subTest(items=items), self.assertRaises(ValidationError):
                CoverScore(id="question", items=items)
        with self.assertRaises(ValidationError):
            CoverJudgement(options=[CoverScore(id="a", items=[1] * 8)] * 2)
        with self.assertRaisesRegex(ValueError, "run vg thumbnail first"):
            thumbnail.record(self.root, judge(question=[2] * 8))
        thumbnail.thumbnail(self.root)
        with self.assertRaisesRegex(ValueError, "not rendered"):
            thumbnail.record(self.root, judge(missing=[2] * 8))
        with self.assertRaisesRegex(ValueError, "not a cover"):
            thumbnail.record(self.root, choose="missing")
        with self.assertRaisesRegex(ValueError, "give the judge"):
            thumbnail.record(self.root)
        # covers.json changed after the render: the judge never saw that cover.
        self.covers(option(text="合成テスト？架空"), option(id="answer", template="reveal"), option(id="third"))
        with self.assertRaisesRegex(ValueError, "changed since"):
            thumbnail.record(self.root, judge(question=[2] * 8))
        before = (self.root / "covers_report.json").read_text(encoding="utf-8")
        with patch.object(thumbnail, "selected_cover", side_effect=ValueError("selection refused")):
            with self.assertRaisesRegex(ValueError, "selection refused"):
                thumbnail.record(self.root, choose="answer")
        self.assertEqual((self.root / "covers_report.json").read_text(encoding="utf-8"), before)

    def test_cli(self):
        thumbnail.thumbnail(self.root)
        path = self.root / "cover_judge.json"
        path.write_text(json.dumps({"judge": "claude-subagent", "round": 1,
                                    "options": [{"id": "answer", "items": [2] * 8, "note": "n"}]}), encoding="utf-8")
        with patch.object(cli.runs, "resolve", return_value=self.root), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["thumbnail", "synthetic:v1", "--score", str(path)]), 0)
        self.assertEqual(json.loads(output.getvalue())["chosen_id"], "answer")
        path.write_text(json.dumps({"options": [{"id": "answer", "items": [5] * 8}]}), encoding="utf-8")
        with patch.object(cli.runs, "resolve", return_value=self.root), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.main(["thumbnail", "synthetic:v1", "--score", str(path)]), 1)
        self.assertIn("thumbnail:", error.getvalue())
        self.assertEqual(self.report()["chosen_id"], "answer")


if __name__ == "__main__":
    unittest.main()
