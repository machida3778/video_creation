"""Bilingual drill with synthetic WAVs and an offline frame-clock renderer check."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

from vg import assets, cli, english, media, registry, run as runs, tts, yomi
from vg.schemas import (Asset, Brief, Layout, Line, Manifest, Route, Scene, Script, Storyboard, Visual, VoiceRequest)
from vg.timeline import build


ROOT = Path(__file__).resolve().parents[1]


def wav_bytes(seconds, sample_rate=48000):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0\0" * round(sample_rate * seconds))
    return output.getvalue()


class EnglishDrillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.voices = registry.load_voices()
        self.script = Script(title="瞬間英作文", cast={"narrator": "vv_himari", "english": "kokoro_heart"},
            voice_overrides={"english": {"speed": 0.9}}, lines=[
                line for i in range(7) for line in (
                    Line(id=f"J{i}", speaker="narrator", text="私は学生です。", pause_after=3),
                    Line(id=f"E{i}", speaker="english", text="I am a student."))])
        self.engine = MagicMock(spec=tts.Voicevox)
        self.engine.style_id.return_value = 1
        self.engine.query.return_value = {"prePhonemeLength": 0, "postPhonemeLength": 0,
            "accent_phrases": [{"moras": [{"text": "ワタシ", "vowel_length": 1}]}]}
        self.engine.synthesis.return_value = wav_bytes(1)
        self.jobs = []

    def worker(self, jobs):
        self.jobs.extend(jobs)
        for job in jobs:
            Path(job["output"]).write_bytes(wav_bytes(1.5, 24000))

    def synthesize(self):
        with patch.object(english, "synthesize", side_effect=self.worker), \
                patch.object(tts.narrator, "synthesize", side_effect=AssertionError("no Irodori")):
            return tts.synthesize(self.root, self.script, self.voices, self.engine)

    def board(self, timings):
        scenes = []
        for i, (line, timing) in enumerate(zip(self.script.lines, timings.lines)):
            start = 0 if i == 0 else timing.start
            scenes.append(Scene(id=f"C{i}", line_ids=[line.id], component="ken_burns_image",
                visual=Visual(kind="library", ref="classroom"),
                props={"overlays": [{"component": "countdown_ring", "props": {
                    "seconds": 3, "start": round(timing.end-start, 6)}}]} if line.pause_after else {}))
        return Storyboard(type_id="english_drill", scenes=scenes)

    def test_type_cast_and_requested_english_voice(self):
        spec = registry.load_types()["english_drill"]
        self.assertEqual((spec.status, spec.pipeline, spec.timing), ("experimental", "narration_stills", "narration"))
        self.assertEqual(spec.components, ["ken_burns_image", "countdown_ring"])
        self.assertEqual(spec.params, {"items": 7, "think_sec": 3})
        cast = registry.cast_for_type(spec, self.voices, registry.load_voice_defaults())
        self.assertEqual({r: self.voices[v].lang for r, v in cast.items()}, {"narrator": "ja", "english": "en"})
        requested = registry.cast_for_type(spec, self.voices, registry.load_voice_defaults(),
                                          {"english": "kokoro_michael", "narrator": "kokoro_heart"})
        self.assertEqual(requested["english"], "kokoro_michael")
        self.assertEqual(self.voices[requested["narrator"]].lang, "ja")
        runs.write_state(self.root, {"stages": {}})
        media.save(self.root, "brief", Brief(request="synthetic", topic="synthetic",
                   cast={"english": VoiceRequest(raw="Michael (English)")}))
        media.save(self.root, "route", Route(type_id="english_drill", confidence=1, reason="synthetic"))
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.cmd_cast(self.root), 0)
            self.assertEqual(json.loads(output.getvalue())["cast"]["english"], "kokoro_michael")

    def test_mixed_tts_dispatch_pause_seconds_sample_rate_and_cache(self):
        timings = self.synthesize()
        self.assertEqual([(t.start, t.end) for t in timings.lines[:3]], [(0.05, 1.05), (4.08, 5.58), (5.61, 6.61)])
        self.assertEqual(timings.sample_rate, 48000)
        self.assertEqual(len(timings.lines), 14)
        self.assertLess(timings.total_sec + 0.5, 59)
        self.assertEqual(self.engine.synthesis.call_count, 7)
        # Identical English lines share one cached raw synthesis, each retains its own timed WAV.
        self.assertEqual(len(self.jobs), 1)
        self.assertEqual((self.jobs[0]["voice"], self.jobs[0]["speed"], self.jobs[0]["text"]),
                         ("af_heart", 0.9, "I am a student."))
        for timing in timings.lines:
            with wave.open(str(self.root / timing.audio)) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels()), (48000, 1))
        self.assertEqual(timings.lines[1].spans, [])
        self.jobs.clear()
        self.assertEqual(self.synthesize(), timings)
        self.assertEqual(self.jobs, [])
        self.script.lines[1].text = "I am happy."
        self.synthesize()
        self.assertEqual([j["text"] for j in self.jobs], ["I am happy."])

    def test_english_voice_terms_and_engine_validate_before_worker(self):
        for changes in ({"lang": "ja"}, {"status": "pending"}, {"commercial_ok": False},
                        {"speaker": "unknown"}, {"model": "unlicensed/model"},
                        {"speed": float("nan")}, {"speed": 0}):
            voices = dict(self.voices)
            voices["kokoro_heart"] = voices["kokoro_heart"].model_copy(update=changes)
            script = self.script.model_copy(update={"voice_overrides": {}})
            with self.subTest(changes=changes), patch.object(english, "synthesize") as worker, \
                    self.assertRaisesRegex(ValueError, "commercial English Kokoro"):
                tts.synthesize(self.root, script, voices, self.engine)
            worker.assert_not_called()

    def test_manifest_records_both_voices_and_is_idempotent(self):
        timings = self.synthesize()
        manifest = tts.record_voices(None, self.script, timings, self.voices, engine_version="synthetic")
        self.assertEqual(len(manifest.assets), 14)
        japanese, english_voice = manifest.assets[:2]
        self.assertEqual(japanese.credit, self.voices["vv_himari"].credit)
        self.assertEqual((english_voice.tool, english_voice.license, english_voice.license_url),
                         ("kokoro:hexgrad/Kokoro-82M", "Apache-2.0", self.voices["kokoro_heart"].license_url))
        self.assertEqual(english_voice.params["speed"], 0.9)
        self.assertEqual(tts.record_voices(manifest, self.script, timings, self.voices,
                                          engine_version="synthetic"), manifest)
        self.assertEqual(len(manifest.assets), 14)

    def test_timeline_reuses_one_picture_holds_question_and_localizes_ring(self):
        timings = self.synthesize()
        board = self.board(timings)
        before = deepcopy(board.model_dump())
        manifest = Manifest(assets=[Asset(id="classroom", path="classroom.png", kind="image", origin="generated",
                                         license="synthetic", commercial_ok=True)])
        layout = Layout.model_validate({"caption": {"box": "#ffe600", "fill": "#000000", "stroke": None}})
        timeline = build(self.script, timings, board, manifest, layout=layout)
        images = [c for c in timeline.clips if c.component == "ken_burns_image"]
        rings = [c for c in timeline.clips if c.component == "countdown_ring"]
        self.assertEqual({c.props["src"] for c in images}, {"classroom.png"})
        self.assertEqual((len(images), len(rings), len(timeline.audio)), (14, 7, 14))
        self.assertTrue(all(a.kind == "voice" for a in timeline.audio))
        for i, ring in enumerate(rings):
            speech = timings.lines[2*i]
            self.assertAlmostEqual(ring.start + ring.props["start"], speech.end)
            self.assertLessEqual(ring.props["start"] + ring.props["seconds"], ring.end-ring.start)
            question = [c for c in timeline.captions if c.speaker == "narrator"][i]
            self.assertAlmostEqual(question.end, speech.end + 3)
        self.assertAlmostEqual(timeline.duration, timings.total_sec + 0.5)
        self.assertEqual(timeline.layout.caption.box, "#ffe600")
        self.assertEqual(board.model_dump(), before)

    def test_t2i_plans_one_image_for_all_scenes(self):
        timings = self.synthesize()
        board = self.board(timings)
        board.scenes[0].visual = Visual(kind="t2i", prompt="Original simple character in a classroom", seed=17)
        for scene in board.scenes[1:]:
            scene.visual.ref = "img_C0"
        self.assertEqual([j["id"] for j in assets.t2i_jobs(board)], ["img_C0"])

    def test_yomi_reviews_japanese_and_keeps_english_as_english(self):
        media.save(self.root, "script", self.script)
        rows = yomi.reading_table(self.root, self.engine)
        self.assertEqual(self.engine.query.call_count, 7)
        self.assertTrue(all("Kokoro" in row["engine_reading"] and not row["flagged"] for row in rows[1::2]))
        self.assertTrue(all(row["engine_reading"] == "ワタシ" for row in rows[0::2]))

    def test_narration_ignores_source_caption_times(self):
        self.script.lines[0].start, self.script.lines[0].end = 20, 21
        timings = self.synthesize()
        self.assertEqual((timings.lines[0].start, timings.lines[0].end), (0.05, 1.05))

    def test_ring_frame_window_counts_to_zero_and_video_crop_preserves_defaults(self):
        # Use installed TypeScript/React without a browser, models or any network.
        # Hooks provide a deterministic Remotion frame clock for static SVG/media markup.
        found = os.environ.get("VG_NODE") or shutil.which("node")
        node = Path(found) if found else Path.home() / ".local/opt/node/bin/node"
        if not node.is_file() or not (ROOT / "render/node_modules/typescript").exists():
            self.skipTest("renderer dependencies unavailable")
        code = r'''
const fs = require('fs');
const vm = require('vm');
const ts = require('./render/node_modules/typescript');
const React = require('./render/node_modules/react');
const {renderToStaticMarkup} = require('./render/node_modules/react-dom/server');
let frame = 0;
const remotion = {useCurrentFrame: () => frame, useVideoConfig: () => ({fps: 30}),
  staticFile: x => x, AbsoluteFill: ({children, ...props}) => React.createElement('div', props, children),
  OffthreadVideo: props => React.createElement('video', props)};
function load(file, imports) {
  const exports = {};
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: {jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, esModuleInterop: true}
  }).outputText;
  vm.runInNewContext(js, {exports, require: name => name === 'react' ? React :
    name === 'remotion' ? remotion : imports[name] || {}});
  return exports;
}
const ring = load('render/src/components/CountdownRing.tsx', {'./Caption': {FONT: 'sans-serif'}});
const frames = [29, 30, 60, 90, 119, 120];
const rings = frames.map(f => {frame = f; return renderToStaticMarkup(
  React.createElement(ring.CountdownRing, {seconds: 3, start: 1}));});
const registry = load('render/src/components/registry.tsx', {'./CountdownRing': ring});
const videos = [{}, {crop: 'fit'}, {crop: 'fill', focus_x: 0.2}].map(props => renderToStaticMarkup(
  React.createElement(registry.ClipView, {clip: {component: 'video_clip', props: {src: 'fake.mp4', ...props}}, frames: 90})));
process.stdout.write(JSON.stringify({rings, videos}));
'''
        result = subprocess.run([str(node), "-e", code], cwd=ROOT, capture_output=True, text=True, check=True)
        output = json.loads(result.stdout)
        self.assertEqual((output["rings"][0], output["rings"][-1]), ("", ""))
        for svg, number in zip(output["rings"][1:-1], (3, 2, 1, 0)):
            self.assertIn(f">{number}</text>", svg)
            self.assertIn('fill="#00edff"', svg)
        for video, fit, position in zip(output["videos"], ("cover", "contain", "cover"), ("50%", "50%", "20%")):
            self.assertIn(f"object-fit:{fit}", video)
            self.assertIn(f"object-position:{position} 50%", video)


if __name__ == "__main__":
    unittest.main()
