import unittest

from vg.captions import FONT_PX, _split_long, captions_for_line, em, layout
from vg.qa import check_safe_area
from vg.schemas import Caption, Line, LineTiming, Timeline


class CaptionLayoutTests(unittest.TestCase):
    def test_weighted_width_matches_first_match_renderer(self):
        self.assertAlmostEqual(em("強調Abｱ", ["強調", "Ab"]), 2 * 1.2 + 2 * 0.55 * 1.2 + 0.55)
        self.assertAlmostEqual(em("abcabc", ["", "ab", "abc"]), 2 * (2 * 0.55 * 1.2 + 0.55))
        self.assertAlmostEqual(em("語語", ["語", "語語"]), 2.4)

    def test_qa_accounts_for_enlarged_glyphs(self):
        def check(emphasis):
            return check_safe_area(Timeline(duration=2, clips=[], captions=[
                Caption(start=0, end=2, text="あ" * 11, emphasis=emphasis)]))
        self.assertTrue(check([]).passed)
        self.assertFalse(check(["あ"]).passed)

    def test_layout_with_emphasis_fits_each_style_and_preserves_text(self):
        examples = [("あ" * 11, ["あ"]), ("あ" * 17 + "い" * 5, ["い" * 5]),
                    ("大切な言葉を強調して読みやすい字幕を作ります。", ["大切", "読みやすい"]),
                    ("ASCII numbers 1234567890 and ｶﾀｶﾅ", ["1234567890"])]
        timing = LineTiming(line_id="L1", audio="synthetic.wav", start=0, end=10)
        for style in FONT_PX:
            for text, emphasis in examples:
                with self.subTest(style=style, text=text):
                    captions = captions_for_line(Line(id="L1", text=text, emphasis=emphasis), timing, style)
                    self.assertEqual("".join(c.text.replace("\n", "") for c in captions), text)
                    self.assertTrue(check_safe_area(Timeline(duration=10, clips=[], captions=captions)).passed)
                    for caption in captions:
                        self.assertLessEqual(len(caption.text.splitlines()), 2)

    def test_oversized_phrase_remainder_is_split_again(self):
        parts = _split_long("あ" * 17 + "い" * 5, 11.5, ["い" * 5])
        self.assertTrue(all(em(part, ["い" * 5]) <= 11.5 for part in parts))
        pages = layout("あ" * 11, emphasis=["あ"])
        self.assertTrue(all(em(row, ["あ"]) <= 11.5 for p in pages for row in p.split("\n")))


if __name__ == "__main__":
    unittest.main()
