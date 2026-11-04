"""Ranking/dialogue recipes and clip-local speaker animation, using synthetic specs only."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest

import yaml

from vg.registry import (cast_for_type, load_characters, load_types, load_voice_defaults,
                         load_voices)
from vg.schemas import Line, LineTiming, Scene, Script, Storyboard, Timings
from vg.timeline import build


ROOT = Path(__file__).resolve().parents[1]


class CharacterTimingTests(unittest.TestCase):
    def setUp(self):
        # The explainer opens: a/b must follow props.roles, not first-appearance order.
        self.script = Script(title="合成テスト", cast={"questioner": "vv_zundamon", "explainer": "vv_metan"}, lines=[
            Line(id="L1", speaker="explainer", text="最初の答えです。"),
            Line(id="L2", speaker="questioner", text="なぜそうなるの？"),
            Line(id="L3", speaker="explainer", text="理由を説明します。"),
            Line(id="L4", speaker="questioner", text="これならわかるね。"),
        ])
        self.timings = Timings(sample_rate=24000, total_sec=6.8, lines=[
            LineTiming(line_id="L1", audio="audio/L1.wav", start=0.25, end=1.2),
            LineTiming(line_id="L2", audio="audio/L2.wav", start=1.4, end=2.5),
            LineTiming(line_id="L3", audio="audio/L3.wav", start=3.2, end=4.6),
            LineTiming(line_id="L4", audio="audio/L4.wav", start=5.1, end=6.8),
        ])
        self.props = {
            "roles": {"a": "questioner", "b": "explainer"},
            "a": {"name": "聞き役", "src": "fake/a.png", "color": "#2f7d32"},
            "b": {"name": "解説役", "src": "fake/b.png", "color": "#b0306a"},
            "speaking": [{"speaker": "a", "start": 99, "end": 100}],
        }

    def board(self, overlay=True):
        return Storyboard(type_id="dialogue_explainer", scenes=[
            Scene(id=f"C{i}", line_ids=ids,
                  component="ken_burns_image" if overlay else "character_pair",
                  props={"overlays": [{"component": "character_pair", "props": deepcopy(self.props)}]}
                  if overlay else deepcopy(self.props))
            for i, ids in enumerate((["L1", "L2"], ["L3", "L4"]))
        ])

    def test_speaking_intervals_for_base_and_overlay_clips(self):
        expected = [
            [{"speaker": "b", "start": 0.25, "end": 1.2}, {"speaker": "a", "start": 1.4, "end": 2.5}],
            [{"speaker": "b", "start": 0.0, "end": 1.4}, {"speaker": "a", "start": 1.9, "end": 3.6}],
        ]
        for overlay in (False, True):
            with self.subTest(overlay=overlay):
                board = self.board(overlay)
                before = board.model_dump()
                timeline = build(self.script, self.timings, board)
                pairs = [c for c in timeline.clips if c.component == "character_pair"]
                self.assertEqual([c.start for c in pairs], [0, 3.2])
                self.assertEqual([c.layer for c in pairs], [int(overlay)] * 2)
                self.assertEqual([c.props["speaking"] for c in pairs], expected)
                # Retain the initial audio delay, between-turn gaps, and no animation during the tail.
                self.assertAlmostEqual(pairs[-1].end - pairs[-1].start - expected[-1][-1]["end"], 0.5)
                self.assertEqual(board.model_dump(), before)
                self.assertEqual(build(self.script, self.timings, board), timeline)
                self.assertEqual({c.speaker: c.style for c in timeline.captions},
                                 {"questioner": "speaker_a", "explainer": "speaker_b"})

    def test_every_character_pair_is_filled_and_roles_can_be_swapped(self):
        board = self.board(overlay=False)
        other = deepcopy(self.props)
        other["roles"] = {"a": "explainer", "b": "questioner"}
        board.scenes[0].props["overlays"] = [{"component": "character_pair", "props": other}]
        timeline = build(self.script, self.timings, board)
        pairs = [c for c in timeline.clips if c.component == "character_pair"]
        self.assertEqual([c.props["speaking"][0]["speaker"] for c in pairs], ["b", "a", "b"])

    def test_invalid_role_maps_fail_clearly(self):
        for roles in (None, {}, {"a": "questioner"}, {"a": "questioner", "b": "questioner"},
                      {"a": "questioner", "b": "absent"}):
            with self.subTest(roles=roles):
                board = self.board(overlay=False)
                board.scenes[0].props["roles"] = roles
                with self.assertRaisesRegex(ValueError, "character_pair"):
                    build(self.script, self.timings, board)


class NewTypeRegistryTests(unittest.TestCase):
    def test_original_characters_and_voice_references(self):
        characters = load_characters()
        voices = load_voices()
        self.assertEqual(len(characters), 2)
        self.assertEqual({c["role"] for c in characters.values()}, {"questioner", "explainer"})
        for key, character in characters.items():
            with self.subTest(character=key):
                self.assertEqual(character["id"], key)
                self.assertIn(character["voice_id"], voices)
                self.assertEqual(voices[character["voice_id"]].status, "active")
                self.assertEqual(character["portrait"], f"assets/media/characters/{key}.png")
                self.assertEqual(character["license"], "generated (Z-Image Turbo, Apache-2.0)")
                self.assertTrue(character["prompt"])
                self.assertIsInstance(character["seed"], int)

    def test_character_loader_rejects_bad_entries(self):
        entries = list(load_characters().values())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "characters.yaml"
            for field, value in (("voice_id", "unknown"), ("portrait", "assets/media/characters/../other.png"),
                                 ("seed", True), ("role", "narrator"), ("color", "green"), ("prompt", "")):
                with self.subTest(field=field):
                    invalid = deepcopy(entries)
                    invalid[0][field] = value
                    path.write_text(yaml.safe_dump({"characters": invalid}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_characters(path)
            path.write_text(yaml.safe_dump({"characters": [entries[0], entries[0]]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate character id"):
                load_characters(path)

    def test_recipes_validate_and_use_implemented_components(self):
        types = load_types()
        registry = (ROOT / "render/src/components/registry.tsx").read_text(encoding="utf-8")
        component_list = re.search(r"export const COMPONENTS = \[(.*?)\];", registry, re.S)
        self.assertIsNotNone(component_list)
        implemented = set(re.findall(r"'([^']+)'", component_list.group(1)))
        dispatched = set(re.findall(r"case '([^']+)':", registry))
        self.assertEqual(implemented, dispatched)
        self.assertTrue({"rank_card", "rank_scroll", "countdown", "character_pair"} <= implemented)
        voices, defaults = load_voices(), load_voice_defaults()
        for type_id in ("ranking", "dialogue_explainer"):
            with self.subTest(type_id=type_id):
                spec = types[type_id]
                self.assertEqual(spec.status, "experimental")
                self.assertTrue(set(spec.components) <= implemented, set(spec.components) - implemented)
                self.assertIn("ken_burns_image", spec.components)
                self.assertTrue(spec.research["required"])
                cast = cast_for_type(spec, voices, defaults)
                if type_id == "dialogue_explainer":
                    self.assertEqual(cast, {c["role"]: c["voice_id"] for c in load_characters().values()})
                else:
                    self.assertEqual(set(cast), {"narrator"})
                    self.assertEqual(spec.params["items"], 5)
                for stage in ("script", "storyboard"):
                    self.assertTrue((ROOT / "types" / type_id / "prompts" / f"{stage}.md").is_file())


if __name__ == "__main__":
    unittest.main()
