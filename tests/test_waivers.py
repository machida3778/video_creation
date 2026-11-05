import json
import tempfile
import unittest
from pathlib import Path

from vg import qa
from vg.schemas import Check


class WaiverTests(unittest.TestCase):
    def test_user_waiver_passes_a_taste_check_and_keeps_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "qa_waivers.json").write_text(json.dumps(
                {"tempo": {"reason": "user chose this variant", "by": "user", "date": "2026-11-05"}}))
            report = qa.write_report(run, [Check(id="tempo", passed=False, detail="hold 9s")])
            self.assertTrue(report.passed)
            self.assertIn("WAIVED by user", report.checks[0].detail)
            self.assertIn("hold 9s", report.checks[0].detail)

    def test_safety_checks_cannot_be_waived(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            for check_id in ("rights", "facts", "disclosure", "yomi", "originality"):
                (run / "qa_waivers.json").write_text(json.dumps({check_id: {"reason": "x"}}))
                with self.assertRaises(ValueError):
                    qa.write_report(run, [Check(id=check_id, passed=False)])


if __name__ == "__main__":
    unittest.main()
