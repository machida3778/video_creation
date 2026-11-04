"""Variant workflows with only temporary specifications and fake, licensed media."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

import yaml

from vg import assets, bgm, cli, registry, run as runs
from vg.media import FFMPEG, load, run, save
from vg.package import compare, package
from vg.schemas import (Asset, Brief, Check, Line, LineTiming, Manifest, Music, QAReport,
                        Research, Route, Scene, Script, Storyboard, Timeline, Timings, Visual)
from vg.timeline import build
from vg.variants import feedback, metadata, mix, summaries


class VariantTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / "runs" / "example"
        self.run.mkdir(parents=True)
        runs.write_state(self.run, {"request": "依頼", "created": runs.now(), "stages": {}})
        save(self.run, "brief", Brief(request="依頼", topic="話題", tone="明るい calm"))
        save(self.run, "route", Route(type_id="test", confidence=1, reason="test"))
        save(self.run, "research", Research())
        self.v1 = runs.new_variant(self.run, note="短い台本")
        self.v2 = runs.new_variant(self.run, note="映像と声と音楽を変更")
        self.make_specs(self.v1, ["A1", "A2", "A3", "A4", "A5", "A6"], [2, 4], "voice1", "one")
        self.make_specs(self.v2, ["B1", "B2", "B3", "B4"], [1, 3], "voice2", "two")
        self.library = self.root / "config/bgm.yaml"
        self.library.parent.mkdir()
        tracks = []
        for tid, moods in [("one", ["calm"]), ("two", ["明るい", "calm"]), ("three", ["暗い"])]:
            file = f"assets/media/bgm/{tid}.wav"
            (self.root / file).parent.mkdir(parents=True, exist_ok=True)
            (self.root / file).write_bytes(f"music {tid}".encode())
            tracks.append(dict(id=tid, title=f"曲{tid}", artist="作者", file=file, license="CC-BY-4.0",
                               license_url="https://example.test/license", credit=f"音楽: {tid} by 作者",
                               commercial_ok=True, mood=moods, bpm=100, length_sec=60, gain_db=-23))
        self.library.write_text(yaml.safe_dump({"tracks": tracks}, allow_unicode=True), encoding="utf-8")
        self.tracks = registry.load_bgm(self.library)
        self.types = {"test": SimpleNamespace(id="test", name="明るい解説", description="calm science")}

    def make_specs(self, variant, ids, lengths, voice, track):
        save(variant, "script", Script(title=f"台本{voice}", cast={"narrator": voice},
                                      voice_overrides={"narrator": {"speed": 1.1 if voice == "voice1" else 1.4}},
                                      lines=[Line(id=lid, text=f"本文{lid}", fact_ids=["F1"]) for lid in ids]))
        scenes, entries, offset = [], [], 0
        for index, length in enumerate(lengths):
            aid = f"image_{index}"
            path = f"assets/images/{aid}.png"
            (variant / path).parent.mkdir(parents=True, exist_ok=True)
            (variant / path).write_bytes(f"{variant.name} image {index}".encode())
            entries.append(Asset(id=aid, path=path, kind="image", origin="generated", license="Apache-2.0",
                                 commercial_ok=True))
            scenes.append(Scene(id=f"C{index}", line_ids=ids[offset:offset + length], component="ken_burns_image",
                                visual=Visual(kind="t2i", prompt=f"image {index}", ref=aid)))
            offset += length
        music = f"assets/bgm/{track}.wav"
        (variant / music).parent.mkdir(parents=True, exist_ok=True)
        (variant / music).write_bytes(f"music {track}".encode())
        entries.append(Asset(id=f"bgm_{track}", path=music, kind="music", origin="library", license="CC-BY-4.0",
                             license_url="https://example.test/license", credit=f"音楽: {track} by 作者",
                             commercial_ok=True, params={"track": track, "title": f"曲{track}", "artist": "作者"}))
        entries.append(Asset(id="voice_old", path="audio/old.wav", kind="voice", origin="generated",
                             license="test", commercial_ok=True))
        save(variant, "manifest", Manifest(assets=entries))
        save(variant, "storyboard", Storyboard(type_id="test", scenes=scenes,
                                              music=Music(track=track, asset=f"bgm_{track}")))
        # These stale generated artifacts must never be copied by mix / --from.
        for name in ("timings.json", "timeline.json", "qa.json", "publish.json", "final.mp4", "final.srt"):
            (variant / name).write_text("stale", encoding="utf-8")

    def test_numbering_shared_copies_state_and_resolve(self):
        v4 = runs.new_variant(self.run, "v4", "指定")
        self.assertEqual(runs.new_variant(self.run).name, "v3")
        self.assertEqual(runs.new_variant(self.run).name, "v5")
        self.assertEqual([p.name for p in runs.variants(self.run)], ["v1", "v2", "v3", "v4", "v5"])
        for name in ("brief", "route", "research"):
            self.assertFalse((v4 / f"{name}.json").is_symlink())
            self.assertEqual((v4 / f"{name}.json").read_bytes(), (self.run / f"{name}.json").read_bytes())
        self.assertEqual(runs.read_state(v4)["stages"], {})
        self.assertEqual(metadata(v4)["parts"], dict.fromkeys(("script", "voice", "visuals", "bgm")))
        with patch.object(runs, "RUNS", self.root / "runs"):
            self.assertEqual(runs.resolve("example:v2"), self.v2.resolve())
            self.assertEqual(runs.resolve("latest:v1"), self.v1.resolve())
            self.assertEqual(runs.resolve(str(v4)), v4.resolve())
        with self.assertRaises(FileExistsError):
            runs.new_variant(self.run, "v1")
        for bad in ("../v1", "v0", "v01", "bad"):
            with self.assertRaises(ValueError):
                runs.variant_dir(self.run, bad)

    def test_mix_provenance_assets_and_no_stale_outputs(self):
        mixed = mix(self.run, "v1", voice="v2", visuals="v2", bgm="v2", note="明るく")
        script = load(mixed, "script")
        self.assertEqual(script.title, load(self.v1, "script").title)
        self.assertEqual(script.lines, load(self.v1, "script").lines)
        self.assertEqual(script.cast, {"narrator": "voice2"})
        self.assertEqual(script.voice_overrides, load(self.v2, "script").voice_overrides)
        self.assertEqual(metadata(mixed)["parts"], dict(script="v1", voice="v2", visuals="v2", bgm="v2"))
        self.assertIn("再割当", metadata(mixed)["note"])
        self.assertEqual(load(mixed, "storyboard").music.track, "two")
        entries = load(mixed, "manifest").assets
        self.assertFalse(any(a.kind == "voice" for a in entries))
        for entry in entries:
            self.assertEqual((mixed / entry.path).read_bytes(), (self.v2 / entry.path).read_bytes())
        self.assertEqual(next(a for a in entries if a.kind == "music").credit, "音楽: two by 作者")
        for name in ("timings.json", "timeline.json", "qa.json", "publish.json", "final.mp4", "final.srt", "audio"):
            self.assertFalse((mixed / name).exists(), name)
        # Assets stage must reuse the copied t2i images without contacting a GPU.
        board = load(mixed, "storyboard")
        with patch.object(assets, "_run_on_lab_gpu", side_effect=AssertionError("GPU called")):
            assets.generate(mixed, board, load(mixed, "manifest"), bgm_tracks={})
        descendant = mix(self.run, mixed.name, bgm="v1")
        self.assertEqual(metadata(descendant)["parts"], dict(script="v1", voice="v2", visuals="v2", bgm="v1"))

    def test_script_replacement_keeps_voice_and_remaps_inherited_visuals(self):
        mixed = mix(self.run, "v1", script="v2")
        script = load(mixed, "script")
        self.assertEqual(script.lines, load(self.v2, "script").lines)
        self.assertEqual(script.cast, load(self.v1, "script").cast)
        self.assertEqual(metadata(mixed)["parts"]["script"], "v2")
        self.assertEqual([lid for sc in load(mixed, "storyboard").scenes for lid in sc.line_ids],
                         [line.id for line in script.lines])

    def test_mix_copies_used_sfx_from_visuals(self):
        board = load(self.v2, "storyboard")
        board.scenes[0].props["sfx"] = [{"id": "pop", "at": 0.1}]
        save(self.v2, "storyboard", board)
        path = "assets/sfx/pop.wav"
        (self.v2 / path).parent.mkdir(parents=True)
        (self.v2 / path).write_bytes(b"synthetic effect")
        manifest = load(self.v2, "manifest")
        manifest.assets.append(Asset(id="sfx_pop", path=path, kind="sfx", origin="generated",
                                     license="original (procedurally generated by vg sfx)", commercial_ok=True))
        save(self.v2, "manifest", manifest)
        mixed = mix(self.run, "v1", visuals="v2")
        self.assertEqual((mixed / path).read_bytes(), b"synthetic effect")
        copied = next(a for a in load(mixed, "manifest").assets if a.id == "sfx_pop")
        self.assertEqual(copied.license, manifest.assets[-1].license)
        # A self-contained mix can reuse the copied effects with no global sound library or GPU.
        with patch.object(assets, "_run_on_lab_gpu", side_effect=AssertionError("GPU called")):
            assets.generate(mixed, load(mixed, "storyboard"), load(mixed, "manifest"),
                            bgm_tracks={}, library_root=self.root / "missing")

    def test_proportional_remap_and_shorter_script(self):
        mixed = mix(self.run, "v1", visuals="v2")
        board = load(mixed, "storyboard")
        self.assertEqual([sc.line_ids for sc in board.scenes], [["A1"], ["A2", "A3", "A4", "A5", "A6"]])
        short = runs.new_variant(self.run)
        self.make_specs(short, ["only"], [1], "voice1", "one")
        mixed = mix(self.run, short.name, visuals="v2")
        board, script = load(mixed, "storyboard"), load(mixed, "script")
        self.assertEqual(len(board.scenes), 2)
        self.assertEqual([lid for sc in board.scenes for lid in sc.line_ids], ["only"])
        timings = Timings(sample_rate=48000, total_sec=2,
                          lines=[LineTiming(line_id="only", audio="audio/only.wav", start=0, end=2)])
        timeline = build(script, timings, board, load(mixed, "manifest"))
        self.assertEqual(len([c for c in timeline.clips if c.layer == 0]), 1)

    def test_bgm_library_asset_and_missing_file(self):
        variant = runs.new_variant(self.run)
        board = Storyboard(type_id="test", scenes=[Scene(id="C1", line_ids=["L1"], component="background")],
                           music=Music(track="two"))
        manifest = assets.generate(variant, board, bgm_tracks=self.tracks, library_root=self.root)
        entry = manifest.assets[0]
        self.assertEqual((entry.kind, entry.origin), ("music", "library"))
        self.assertEqual((entry.license, entry.license_url, entry.credit),
                         ("CC-BY-4.0", "https://example.test/license", "音楽: two by 作者"))
        self.assertEqual(board.music.asset, "bgm_two")
        self.assertEqual(board.music.gain_db, -23)
        self.assertEqual((variant / entry.path).read_bytes(), b"music two")
        other = runs.new_variant(self.run)
        (self.root / self.tracks["two"].file).unlink()
        with self.assertRaisesRegex(FileNotFoundError, "assets/media/bgm/two.wav"):
            assets.generate(other, board, bgm_tracks=self.tracks, library_root=self.root)
        data = yaml.safe_load(self.library.read_text())
        data["tracks"][0]["commercial_ok"] = False
        self.library.write_text(yaml.safe_dump(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not usable commercially"):
            registry.load_bgm(self.library)

    def test_suggestions_ranking_penalty_and_ten_video_window(self):
        outbox = self.root / "outbox"
        result = bgm.suggest(self.run, tracks=self.tracks, types=self.types, outbox=outbox)
        self.assertEqual([row["id"] for row in result], ["two", "one", "three"])
        self.assertIn("明るい", result[0]["reasons"][0])
        for index in range(11):
            # Exercise both legacy and nested publishing layouts.
            directory = outbox / f"run{index}" / "v1" if index else outbox / "legacy"
            directory.mkdir(parents=True)
            path = directory / "publish.json"
            path.write_text(json.dumps({"music_track": "one" if index == 0 else "two"}))
            os.utime(path, ns=(index + 1, index + 1))
        result = bgm.suggest(self.run, n=2, tracks=self.tracks, types=self.types, outbox=outbox)
        self.assertEqual([row["id"] for row in result], ["one", "three"])
        full = bgm.suggest(self.run, n=3, tracks=self.tracks, types=self.types, outbox=outbox)
        self.assertEqual(full[-1]["score"], -8)
        self.assertIn("10回", full[-1]["reasons"][1])

    def test_feedback_append_show_and_preferences(self):
        self.assertEqual(feedback(self.run), [])
        first = feedback(self.run, "声を落ち着かせて", "v1")[0]
        second = feedback(self.run, "BGMはv2が良い")[0]
        self.assertEqual(feedback(self.run), [first, second])
        self.assertEqual(first["variant"], "v1")
        self.assertIn("time", first)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["feedback", str(self.run), "--show"]), 0)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], [first, second])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["feedback", str(self.run), "字幕も変更", "--variant", "v2"])
        self.assertEqual(len(feedback(self.run)), 3)
        path = self.root / "config/preferences.yaml"
        self.assertEqual(registry.load_preferences(path), {})
        path.write_text("tone: 落ち着いた\n", encoding="utf-8")
        self.assertEqual(registry.load_preferences(path), {"tone": "落ち着いた"})
        self.assertEqual(registry.load_bgm(self.root / "missing.yaml"), {})

    def test_cli_mix_stages_and_clone(self):
        with patch.object(cli, "stage") as stage, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["mix", str(self.run), "--base", "v1", "--bgm", "v2", "--voice", "v2"]), 0)
        mixed = self.run / "variants/v3"
        self.assertEqual(stage.call_args_list, [call(mixed, name) for name in ("tts", "assets", "timeline", "render", "qa")])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["variant", "new", str(self.run), "--id", "v7", "--from", "v1", "--note", "複製"])
        clone = self.run / "variants/v7"
        self.assertEqual(load(clone, "script"), load(self.v1, "script"))
        self.assertEqual(load(clone, "storyboard"), load(self.v1, "storyboard"))
        self.assertFalse((clone / "timeline.json").exists())
        (clone / "storyboard.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["variant", "new", str(self.run), "--from", "v7"])
        partial = self.run / "variants/v4"
        self.assertEqual(load(partial, "script"), load(clone, "script"))
        self.assertFalse((partial / "storyboard.json").exists())

    def test_variant_package_and_comparison(self):
        video = self.root / "synthetic.mp4"
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i",
             "color=c=blue:s=108x192:r=30:d=1", "-c:v", "libx264", "-threads", "2", str(video)])
        for variant in (self.v1, self.v2):
            (variant / "final.mp4").write_bytes(video.read_bytes())
            save(variant, "timeline", Timeline(duration=12.5, clips=[]))
            save(variant, "qa", QAReport(checks=[Check(id="test", passed=True),
                                                Check(id="warning", passed=False, severity="warn")]))
            with patch("vg.package.ROOT", self.root):
                target = package(variant)
            self.assertEqual(target, self.root / "outbox" / self.run.name / variant.name)
            self.assertEqual(load(target, "publish").music_track, "one" if variant == self.v1 else "two")
            self.assertIn("音楽:", load(target, "publish").description)
        # An unpackaged, incomplete variant must not appear in the comparison.
        runs.new_variant(self.run, note="未完成")
        with patch.object(registry, "load_bgm", return_value=self.tracks), patch.object(registry, "load_voices", return_value={}):
            path = compare(self.run, outbox=self.root / "outbox")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(path, self.root / "outbox/example/README.md")
        for expected in ("## v1", "## v2", "短い台本", "12.5秒", "voice1", "曲two / 作者", "シーン数: 2",
                         "QA: 合格", "警告", "--base v3 --bgm v2 --voice v1"):
            self.assertIn(expected, text)
        self.assertNotIn("## v3", text)
        self.assertEqual(summaries(self.run)[0]["duration"], 12.5)

    def test_bad_asset_paths_and_missing_sources_do_not_allocate_variants(self):
        manifest = load(self.v2, "manifest")
        manifest.assets[0].path = "../../outside.png"
        save(self.v2, "manifest", manifest)
        with self.assertRaisesRegex(ValueError, "relative"):
            mix(self.run, "v1", visuals="v2")
        self.assertEqual(len(runs.variants(self.run)), 2)


if __name__ == "__main__":
    unittest.main()
