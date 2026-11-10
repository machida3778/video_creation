"""Validate planned recipes and their compatibility with the local voice registry."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vg import assets, registry, site
from vg.registry import cast_for_type, load_types, load_voice_defaults, load_voices


ROOT = Path(__file__).resolve().parents[1]
TYPE_IDS = {
    "trivia_explainer", "news_digest", "ranking", "ranking_montage", "book_intro", "history_story",
    "horror_story", "dialogue_explainer", "chat_story", "data_comparison",
    "map_explainer", "quiz", "english_lesson", "ai_cinematic", "what_if",
    "ai_microdrama", "surreal_asmr", "streamer_highlight", "clip_highlight", "english_drill",
}
COMPONENTS = {
    "hook_title", "caption", "ken_burns_image", "video_clip", "source_card",
    "timeline", "quote_card", "rank_card", "rank_scroll", "countdown",
    "chart_bar", "chart_line", "chart_race", "map_view", "quiz_timer",
    "choice_cards", "answer_reveal", "chat_bubbles", "character_pair",
    "book_card", "end_card", "bgm", "sfx", "rank_label", "section_card", "countdown_ring",
}
MODEL_KEYS = {
    "tts", "t2i", "image_edit", "i2v", "t2v", "s2v", "music", "foley",
    "asr", "vlm", "upscale", "tts_en",
}
QA_KEYS = {
    "duration", "loudness", "safe_area", "yomi", "facts", "originality",
    "rights", "disclosure", "sensitivity", "confidence",
}


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.types = load_types(ROOT / "types")
        cls.voices = load_voices(ROOT / "config" / "voices.yaml")
        cls.defaults = load_voice_defaults(ROOT / "config" / "voices.yaml")

    def test_type_ids(self):
        self.assertEqual(set(self.types), TYPE_IDS)
        self.assertEqual(len(list((ROOT / "types").glob("*/type.yaml"))), 20)
        for spec in self.types.values():
            with self.subTest(type_id=spec.id):
                self.assertIn(spec.status, ("planned", "experimental", "stable"))

    def test_voice_registry(self):
        self.assertTrue(self.voices)
        self.assertIn("narrator", self.defaults)
        for role, voice_id in self.defaults.items():
            with self.subTest(role=role):
                self.assertIn(voice_id, self.voices)

    def test_cast_for_every_role(self):
        for spec in self.types.values():
            if not spec.voice:
                continue
            with self.subTest(type_id=spec.id):
                cast = cast_for_type(spec, self.voices, self.defaults)
                self.assertEqual(set(cast), set(spec.voice))
                for role, voice_id in cast.items():
                    with self.subTest(role=role):
                        self.assertIn(voice_id, self.voices)
                        voice = self.voices[voice_id]
                        self.assertEqual(voice.status, "active")
                        self.assertFalse(set(voice.restricted_content) & set(spec.content))

    def test_component_and_model_vocabularies(self):
        for spec in self.types.values():
            with self.subTest(type_id=spec.id):
                self.assertTrue(set(spec.components) <= COMPONENTS,
                                f"Unknown components: {set(spec.components) - COMPONENTS}")
                self.assertTrue(set(spec.models) <= MODEL_KEYS,
                                f"Unknown models: {set(spec.models) - MODEL_KEYS}")

    def test_recipe_quality_requirements(self):
        for spec in self.types.values():
            with self.subTest(type_id=spec.id):
                self.assertEqual(set(spec.length), {"youtube_shorts"} if spec.id in {
                    "ranking_montage", "clip_highlight", "english_drill"}
                                 else {"youtube_shorts", "tiktok", "x"})
                for length in spec.length.values():
                    self.assertLessEqual(length.min, length.target)
                    self.assertLessEqual(length.target, length.max)
                self.assertTrue(set(spec.qa) <= QA_KEYS)
                self.assertTrue({"duration", "loudness", "safe_area", "originality", "rights"}
                                <= set(spec.qa))
                if spec.voice:
                    self.assertIn("yomi", spec.qa)
                if spec.research["required"]:
                    self.assertIn("facts", spec.qa)
                if spec.pipeline == "generated_video" or spec.params.get("realistic_imagery"):
                    self.assertIn("disclosure", spec.qa)
                if spec.id in {"news_digest", "streamer_highlight"}:
                    self.assertIn("sensitivity", spec.qa)
                self.assertTrue(2 <= len(spec.use_when) <= 4)
                self.assertTrue(1 <= len(spec.avoid_when) <= 3)
                self.assertTrue(2 <= len(spec.risks) <= 4)
                self.assertTrue(spec.monetization)

    def test_news_voice_fallback(self):
        spec = self.types["news_digest"]
        self.assertIn("criticism", spec.content)
        self.assertEqual(cast_for_type(spec, self.voices, self.defaults),
                         {"narrator": "narrator_female"})
        # Without the narrator voices, the VOICEVOX voice whose terms allow criticism is chosen.
        voices = {k: (v.model_copy(update={"status": "pending"}) if v.engine == "irodori" else v)
                  for k, v in self.voices.items()}
        self.assertEqual(cast_for_type(spec, voices, self.defaults), {"narrator": "vv_himari"})


class SiteConfigurationTests(unittest.TestCase):
    """Site configuration must work without real hosts, accounts or private files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = self.root / "local/site.env"
        self.settings.parent.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(site, "SITE_FILE", self.settings).start()

    def test_literal_values_comments_and_environment_override(self):
        self.settings.write_text("# private settings\nLAB_AMPERE_HOSTS='gpu-a gpu-b' # eligible\n"
                                 "LAB_GPU_HOST_REGEX='\\bgpu-[ab]\\b'\nEMPTY=\n"
                                 "LITERAL='$(a-command-that-must-not-run)'\n", encoding="utf-8")
        values = site.load()
        self.assertEqual(values["LAB_AMPERE_HOSTS"], "gpu-a gpu-b")
        self.assertEqual(values["LAB_GPU_HOST_REGEX"], r"\bgpu-[ab]\b")
        self.assertEqual(values["EMPTY"], "")
        self.assertEqual(values["LITERAL"], "$(a-command-that-must-not-run)")
        with patch.dict(os.environ, LAB_AMPERE_HOSTS="gpu-override"):
            self.assertEqual(site.require("LAB_AMPERE_HOSTS"), "gpu-override")

    def test_missing_or_malformed_config(self):
        self.assertEqual(site.load(), {})
        with self.assertRaisesRegex(RuntimeError, "LAB_AMPERE_HOSTS.*local/site.env"):
            assets.idle_gpus(24000)
        with patch.dict(os.environ, LAB_AMPERE_HOSTS=""):
            with self.assertRaisesRegex(RuntimeError, "Missing site setting"):
                site.require("LAB_AMPERE_HOSTS")
        for content in ("not an assignment", "BAD-KEY=value", "A=unquoted spaces", "A='unfinished"):
            with self.subTest(content=content):
                self.settings.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    site.load()

    def test_path_mapping_matches_prefix_only(self):
        with patch.dict(os.environ, REMOTE_PATH_LOCAL_PREFIX="/cpu/user/",
                        REMOTE_PATH_GPU_PREFIX="/gpu/user/"):
            self.assertEqual(assets.remote(Path("/cpu/user/project")), "/gpu/user/project")
            self.assertEqual(assets.remote(Path("/cpu/user")), "/gpu/user")
            self.assertEqual(assets.remote(Path("/other/cpu/user/project")), "/other/cpu/user/project")
            self.assertEqual(assets.remote(Path("/cpu/username/project")), "/cpu/username/project")
        with self.assertRaisesRegex(RuntimeError, "REMOTE_PATH_LOCAL_PREFIX"):
            assets.remote(Path("/cpu/user/project"))

    def test_gpu_discovery_uses_configured_hosts(self):
        result = subprocess.CompletedProcess([], 0, "0, GPU-id, 24576, 0\n---\n", "")
        with patch.dict(os.environ, LAB_AMPERE_HOSTS="gpu-a gpu-b"), \
                patch.object(assets.subprocess, "run", return_value=result) as run:
            self.assertEqual(assets.idle_gpus(24000), [(24576, "gpu-a", 0), (24576, "gpu-b", 0)])
        self.assertEqual([call.args[0][-2] for call in run.call_args_list], ["gpu-a", "gpu-b"])

    def test_preference_default_precedence_and_legacy_fallback(self):
        private = self.root / "local/preferences.yaml"
        legacy = self.root / "config/preferences.yaml"
        legacy.parent.mkdir()
        with patch.object(registry, "PREFERENCES_FILE", private), \
                patch.object(registry, "LEGACY_PREFERENCES_FILE", legacy):
            self.assertEqual(registry.load_preferences(), {})
            legacy.write_text("tone: legacy\n", encoding="utf-8")
            self.assertEqual(registry.load_preferences(), {"tone": "legacy"})
            private.write_text("tone: private\n", encoding="utf-8")
            self.assertEqual(registry.load_preferences(), {"tone": "private"})
            self.assertEqual(registry.load_preferences(legacy), {"tone": "legacy"})

    def test_shell_loader_quotes_override_and_missing_setting(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        loader = scripts / "site.sh"
        shutil.copyfile(ROOT / "scripts/site.sh", loader)
        self.settings.write_text("LAB_AMPERE_HOSTS='gpu-a gpu-b'\n"
                                 "LAB_GPU_HOST_REGEX='\\bgpu-[ab]\\b'\n", encoding="utf-8")
        command = 'source "$1"; site_require LAB_AMPERE_HOSTS; printf "%s\\n%s\\n" "$LAB_AMPERE_HOSTS" "$LAB_GPU_HOST_REGEX"'
        result = subprocess.run(["/bin/bash", "-eu", "-c", command, "test", str(loader)],
                                env={"LAB_AMPERE_HOSTS": "gpu-override"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["gpu-override", r"\bgpu-[ab]\b"])
        self.settings.unlink()
        result = subprocess.run(["/bin/bash", "-eu", "-c", 'source "$1"; site_require NQSV_HOST',
                                 "test", str(loader)], env={}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing site setting NQSV_HOST", result.stderr)


if __name__ == "__main__":
    unittest.main()
