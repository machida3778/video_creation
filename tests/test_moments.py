"""vg moments and cropped stills: only synthetic data, no network."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from vg import library, moments


def _next_response(values, step_ms=10000):
    markers = [{"startMillis": str(i * step_ms), "durationMillis": str(step_ms), "intensityScoreNormalized": v}
               for i, v in enumerate(values)]
    return {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
        {"payload": {"macroMarkersListEntity": {"markersList": {"markerType": "MARKER_TYPE_CHAPTERS", "markers": []}}}},
        {"payload": {"macroMarkersListEntity": {"markersList": {"markerType": "MARKER_TYPE_HEATMAP",
                                                               "markers": markers}}}}]}}}


class SignalTests(unittest.TestCase):
    def test_heatmap_is_read_from_the_next_response(self):
        heat = moments.parse_heatmap(_next_response([0.1, 1, 0.5]))
        self.assertEqual(heat[1], {"start": 10.0, "end": 20.0, "value": 1.0})
        self.assertEqual(moments.parse_heatmap({}), [])

    def test_chat_density_counts_messages_per_bin(self):
        lines = [json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms), "actions": [
            {"addChatItemAction": {}}]}}) for ms in (1000, 2000, 25000)]
        lines += ["not json", json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "3000",
                                                                   "actions": [{"other": {}}]}})]
        offsets = moments.chat_offsets(lines)
        self.assertEqual(offsets, [1.0, 2.0, 25.0])
        density = moments.chat_density(offsets, 40, bin_sec=20)
        self.assertEqual([d["value"] for d in density], [1.0, 0.5])

    def test_peaks_keep_a_minimum_gap_and_skip_the_opening(self):
        signal = [{"start": t, "end": t + 10, "value": v} for t, v in
                  ((0, 1.0), (10, 0.9), (100, 0.8), (110, 0.95), (300, 0.5))]
        chosen = moments.peaks(signal, 3, min_gap=50, skip_start=20)
        self.assertEqual([c["t"] for c in chosen], [115, 305])

    def test_choose_moments_window_and_fallbacks(self):
        signal = [{"start": t, "end": t + 10, "value": t / 1000} for t in range(0, 1000, 10)]
        kind, chosen = moments.choose_moments(signal, 1000, 3, window=(100, 200))
        self.assertEqual(kind, "signal")
        self.assertTrue(all(100 <= c["t"] <= 200 for c in chosen))
        kind, chosen = moments.choose_moments([], 1000, 4, window=(100, 200))
        self.assertEqual((kind, [c["t"] for c in chosen]), ("window", [112.5, 137.5, 162.5, 187.5]))
        kind, chosen = moments.choose_moments([], 1000, 2)
        self.assertEqual((kind, [c["t"] for c in chosen]), ("even", [340.0, 780.0]))
        with self.assertRaisesRegex(ValueError, "outside"):
            moments.choose_moments([], 50, 2, window=(100, 200))

    def test_urls(self):
        self.assertTrue(moments.is_channel("https://www.youtube.com/@SomeOne"))
        self.assertTrue(moments.is_channel("https://www.youtube.com/@SomeOne/streams"))
        self.assertFalse(moments.is_channel("https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertEqual(moments.video_id("https://youtu.be/abcdefghijk?t=3"), "abcdefghijk")
        with self.assertRaises(ValueError):
            moments.video_id("https://example.com/x")

    def test_channel_videos_skip_members_only_live_and_short(self):
        rows = [{"id": "a" * 11, "availability": "subscriber_only", "title": "x", "duration": 999},
                {"id": "b" * 11, "title": "【メン限】talk", "duration": 999},
                {"id": "c" * 11, "live_status": "is_upcoming", "title": "soon", "duration": 0},
                {"id": "d" * 11, "title": "short", "duration": 30},
                {"id": "e" * 11, "title": "public talk", "duration": 4000}]
        out = SimpleNamespace(stdout="\n".join(json.dumps(r) for r in rows))
        with patch.object(moments, "run", return_value=out) as run:
            videos = moments.channel_videos("https://www.youtube.com/@x", 2)
        self.assertIn("https://www.youtube.com/@x/streams", run.call_args.args[0])
        self.assertEqual([v["id"] for v in videos], ["e" * 11])


class CropTests(unittest.TestCase):
    def test_parse_crop(self):
        self.assertEqual(library.parse_crop("0.85,0.3,0.7"), (0.85, 0.3, 0.7))
        self.assertIsNone(library.parse_crop(None))
        for bad in ("1,2", "a,b,c", "0.5,0.5,0.6", "1.2,0,1", "0.5,0,0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                library.parse_crop(bad)

    def test_vertical_crop_stays_in_the_frame(self):
        frame = Image.new("RGB", (1920, 1080), "white")
        out, box = library.vertical_crop(frame, (0.98, 0.5, 0.5))
        self.assertEqual(out.size, (1080, 1920))
        self.assertEqual((box["h"], box["w"]), (540, 304))
        self.assertEqual(box["x"] + box["w"], 1920)
        self.assertEqual(box["y"], 540)

    def test_crop_needs_a_video_still(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "--crop"):
            library.add_image(Path(tmp), "picture.png", asset_id="x", license_id="own", subject="s", crop="0.5,0,1",
                              licenses={"own": {"id": "own", "name": "own", "kinds": ["image", "video"],
                                                "commercial_ok": True, "url": "https://example.com/terms"}})


if __name__ == "__main__":
    unittest.main()
