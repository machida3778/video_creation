"""Narrator protocol and mixed TTS tests; synthetic WAVs, no GPU/network/models."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch
import wave

from vg import assets, narrator, registry, tts
from vg.schemas import Asset, Line, Manifest, Script, VoiceSpec


ROOT = Path(__file__).resolve().parents[1]


def wav_bytes(seconds=0.25):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\0\0" * round(48000 * seconds))
    return data.getvalue()


def voice(**updates):
    return VoiceSpec.model_validate({
        "id": "synthetic_narrator", "engine": "irodori", "name": "Synthetic",
        "model": narrator.CHECKPOINT, "voice_description": "合成テスト用の落ち着いた声",
        "license": "MIT", "license_url": "https://example.invalid/model", "commercial_ok": True,
        **updates,
    })


class NarratorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.jobs = []

    def mapping(self, path):
        return str(path).replace(str(self.root), "/mapped", 1)

    def fake_worker(self, command, min_mem_mb):
        self.assertEqual(min_mem_mb, 12000)
        arguments = shlex.split(command)
        self.assertEqual(arguments[:2], ["tools/irodori/.venv/bin/python", "workers/tts_irodori/synth.py"])
        spec_path = Path(arguments[arguments.index("--spec") + 1].replace("/mapped", str(self.root), 1))
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(spec["checkpoint"], narrator.CHECKPOINT)
        self.jobs = spec["jobs"]
        for job in self.jobs:
            self.assertTrue(job["output"].startswith("/mapped/"))
            for ref in job["ref_wavs"]:
                self.assertTrue(ref.startswith("/mapped/"))
                self.assertTrue(Path(ref.replace("/mapped", str(self.root), 1)).is_file())
            path = Path(job["output"].replace("/mapped", str(self.root), 1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav_bytes())

    def test_batch_spec_yomi_refs_speed_seed_and_resume(self):
        refs = self.root / "references"
        refs.mkdir()
        for name in ["ref_02.wav", "ref_01.wav", "unrelated.wav"]:
            (refs / name).write_bytes(wav_bytes())
        voices = {"narrator": voice(reference=str(refs), speed=1.25, seed=0),
                  "design": voice(id="other_voice"), "character": voice(engine="voicevox")}
        lines = [Line(id="L1", text="表示", yomi="ヨミ"), Line(id="L2", text="本文"),
                 Line(id="L3", text="設計", speaker="design"),
                 Line(id="L4", text="別エンジン", speaker="character")]
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(narrator, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker:
            outputs = narrator.synthesize(self.root, lines, voices)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(set(outputs), {"L1", "L2", "L3"})
            first, second, designed = self.jobs
            self.assertEqual(first["text"], "ヨミ")
            self.assertEqual(second["text"], "本文")
            self.assertEqual(first["caption"], voices["narrator"].voice_description)
            self.assertEqual(first["ref_wavs"], ["/mapped/references/ref_01.wav", "/mapped/references/ref_02.wav"])
            self.assertEqual(first["duration_scale"], 0.8)
            self.assertEqual(first["seed"], 0)
            self.assertEqual(first["seed"], second["seed"])
            stable_seed = designed["seed"]
            self.assertEqual(designed["ref_wavs"], [])
            self.assertTrue(all(path.is_file() for path in outputs.values()))
            self.assertEqual(narrator.synthesize(self.root, lines, voices), outputs)
            self.assertEqual(worker.call_count, 1)
            outputs["L3"].unlink()
            narrator.synthesize(self.root, lines, voices)
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(len(self.jobs), 1)
            self.assertEqual(self.jobs[0]["seed"], stable_seed)
            self.assertNotEqual(stable_seed, narrator._seed(voice(id="another_voice")))

    def test_relative_reference_and_bad_inputs_fail_before_gpu(self):
        directory = self.root / "voices/test"
        directory.mkdir(parents=True)
        (directory / "ref_01.wav").write_bytes(wav_bytes())
        with patch.object(narrator, "ROOT", self.root), \
                patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(narrator, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker:
            narrator.synthesize(self.root, [Line(id="L1", text="合成")],
                                {"narrator": voice(reference="voices/test")})
            self.assertEqual(self.jobs[0]["ref_wavs"], ["/mapped/voices/test/ref_01.wav"])
            worker.reset_mock()
            for updates, error in [({"speed": 0}, ValueError), ({"speed": float("nan")}, ValueError),
                                   ({"reference": "missing"}, FileNotFoundError),
                                   ({"model": "unsupported"}, ValueError)]:
                with self.subTest(updates=updates), self.assertRaises(error):
                    narrator.synthesize(self.root, [Line(id="L2", text="合成")], {"narrator": voice(**updates)})
            with self.assertRaises(ValueError):
                narrator.synthesize(self.root, [Line(id="../escape", text="合成")], {"narrator": voice()})
            worker.assert_not_called()

    def test_audition_index_and_both_cli_genders(self):
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(narrator, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker:
            for gender in ("male", "female"):
                directory = self.root / gender
                self.assertEqual(narrator.main(["audition", "--gender", gender, "--n", "3",
                                                "--out", str(directory)]), 0)
                index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
                self.assertEqual([entry["caption"] for entry in index], list(narrator.DESCRIPTIONS[gender]))
                self.assertEqual([entry["seed"] for entry in index], [1000, 1001, 1002])
                self.assertEqual([entry["wav"] for entry in index], [f"{gender}_{k}.wav" for k in range(1, 4)])
                self.assertTrue(all((directory / entry["wav"]).is_file() for entry in index))
                self.assertEqual({job["text"] for job in self.jobs}, {narrator.AUDITION_TEXT})
                self.assertTrue(all(not job["ref_wavs"] for job in self.jobs))
                self.assertTrue(all(job["duration_scale"] == 1.0 for job in self.jobs))
            self.assertEqual(worker.call_count, 2)
            self.assertEqual(narrator.audition(self.root / "direct", [(narrator.DESCRIPTIONS["female"][0], 17)])[0],
                             {"wav": "female_1.wav", "caption": narrator.DESCRIPTIONS["female"][0], "seed": 17})

    def test_freeze_reference_names_caption_seed_and_yaml(self):
        candidate = self.root / "candidate.wav"
        candidate.write_bytes(wav_bytes(0.5))
        directory = self.root / "frozen"
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(narrator, "_run_on_lab_gpu", side_effect=self.fake_worker) as worker, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            narrator.main(["freeze", "--wav", str(candidate), "--caption", "落ち着いた声",
                           "--seed", "42", "--voice-dir", str(directory)])
            self.assertEqual((directory / "ref_01.wav").read_bytes(), candidate.read_bytes())
            self.assertEqual(sorted(p.name for p in directory.glob("ref_*.wav")),
                             ["ref_01.wav", "ref_02.wav", "ref_03.wav", "ref_04.wav"])
            self.assertEqual([job["id"] for job in self.jobs], ["ref_02", "ref_03", "ref_04"])
            self.assertEqual(len({job["text"] for job in self.jobs}), 3)
            for job in self.jobs:
                self.assertEqual(job["caption"], "落ち着いた声")
                self.assertEqual(job["seed"], 42)
                self.assertEqual(job["ref_wavs"], ["/mapped/frozen/ref_01.wav"])
            self.assertIn(f'reference: "{directory}"', output.getvalue())
            self.assertIn("status: active\nseed: 42", output.getvalue())
            narrator.freeze(candidate, "落ち着いた声", 42, directory)
            self.assertEqual(worker.call_count, 1)
            candidate.write_bytes(wav_bytes(0.1))
            with self.assertRaises(FileExistsError):
                narrator.freeze(candidate, "違う声", 12, directory)

    def test_missing_worker_output_is_reported(self):
        with patch.object(assets, "remote", side_effect=self.mapping), \
                patch.object(narrator, "_run_on_lab_gpu"):
            with self.assertRaisesRegex(RuntimeError, "did not write"):
                narrator.audition(self.root, [("男性", 1)])

    def test_gpu_lock_busy_retry_offline_mapping_and_credential_scrub(self):
        locks = self.root / "locks"
        results = [subprocess.CompletedProcess([], 1, "", "gpu_free: busy"),
                   subprocess.CompletedProcess([], 0, "", "")]
        with patch.object(assets, "idle_gpus", return_value=[(16000, "gpu-one", 0), (24000, "gpu-two", 2)]) as idle, \
                patch.object(assets, "LOCKS", locks), patch.object(assets, "remote", return_value="/mapped repo"), \
                patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret", "ANTHROPIC_API_KEY": "synthetic-secret"}), \
                patch.object(narrator.subprocess, "run", side_effect=results) as process, \
                contextlib.redirect_stdout(io.StringIO()):
            narrator._run_on_lab_gpu("worker --spec synthetic", 12000)
        idle.assert_called_once_with(12000)
        self.assertEqual(process.call_count, 2)
        for invocation, gpu in zip(process.call_args_list, (0, 2)):
            command = invocation.args[0][-1]
            self.assertIn(f"cd '/mapped repo' && bash scripts/lab/gpu_free.sh {gpu} &&", command)
            self.assertIn(f"CUDA_VISIBLE_DEVICES={gpu}", command)
            self.assertIn("HF_HUB_OFFLINE=1", command)
            self.assertIn("-u OPENAI_API_KEY", command)
            self.assertNotIn("workers/t2i/cache.py", command)
            self.assertNotIn("OPENAI_API_KEY", invocation.kwargs["env"])
            self.assertNotIn("ANTHROPIC_API_KEY", invocation.kwargs["env"])
        self.assertEqual(list(locks.iterdir()), [])

    def test_gpu_failures_release_lock_and_no_idle_means_no_launch(self):
        locks = self.root / "locks"
        with patch.object(assets, "idle_gpus", return_value=[]) as idle, \
                patch.object(narrator.subprocess, "run") as process:
            with self.assertRaisesRegex(RuntimeError, "no idle"):
                narrator._run_on_lab_gpu("worker", 12000)
            process.assert_not_called()
        with patch.object(assets, "idle_gpus", return_value=[(16000, "gpu-one", 0)]), \
                patch.object(assets, "LOCKS", locks), patch.object(assets, "remote", return_value="/mapped"), \
                patch.object(narrator.subprocess, "run", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                narrator._run_on_lab_gpu("worker", 12000)
            self.assertEqual(list(locks.iterdir()), [])
            with patch.object(assets, "_lock", return_value=None), \
                    self.assertRaisesRegex(RuntimeError, "held by our own jobs"):
                narrator._run_on_lab_gpu("worker", 12000)


class MixedTTSTests(unittest.TestCase):
    def test_voicevox_only_keeps_its_existing_path(self):
        engine = MagicMock(spec=tts.Voicevox)
        engine.query.return_value = {"prePhonemeLength": 0.0, "postPhonemeLength": 0.0,
                                     "accent_phrases": [{"moras": [{"text": "ア", "vowel_length": 0.1}]}]}
        engine.synthesis.return_value = wav_bytes(0.25)
        script = Script(title="Synthetic", cast={"narrator": "vv", "unused": "not_loaded"},
                        lines=[Line(id="L1", text="ア")])
        with tempfile.TemporaryDirectory() as temporary, patch.object(narrator, "synthesize") as batch:
            directory = Path(temporary)
            timings = tts.synthesize(directory, script,
                                     {"vv": voice(engine="voicevox", speaker="合成/ノーマル")}, engine)
            batch.assert_not_called()
            self.assertEqual((directory / "audio/L1.wav").read_bytes(), engine.synthesis.return_value)
            span, = timings.lines[0].spans
            self.assertEqual((span.start, span.end), (0.05, 0.3))

    def test_mixed_batch_real_durations_spans_and_licence_records(self):
        voices = {"vv": voice(id="vv", engine="voicevox", speaker="合成/ノーマル", model=None,
                              license="Synthetic character terms", credit="VOICEVOX:合成"),
                  "ir": voice(id="ir")}
        script = Script(title="Synthetic", cast={"host": "vv", "guest": "ir"},
                        voice_overrides={"guest": {"speed": 1.5}, "host": {"speed": 1.25}}, lines=[
                            Line(id="L1", speaker="guest", text="一", pause_after=0.1),
                            Line(id="L2", speaker="host", text="二", yomi="ニ"),
                            Line(id="L3", speaker="guest", text="三")])
        query = {"speedScale": 1.0, "prePhonemeLength": 0.1, "postPhonemeLength": 0.1,
                 "accent_phrases": [{"moras": [{"text": "ニ", "vowel_length": 0.2}]}]}
        engine = MagicMock(spec=tts.Voicevox)
        engine.style_id.return_value = 7
        engine.query.return_value = query
        engine.synthesis.return_value = wav_bytes(0.8)

        def fake_narrator(run, lines, effective):
            self.assertEqual([line.id for line in lines], ["L1", "L3"])
            self.assertEqual(effective["guest"].speed, 1.5)
            (run / "audio").mkdir()
            result = {}
            for line, seconds in zip(lines, (0.5, 1.25)):
                result[line.id] = run / "audio" / f"{line.id}.wav"
                result[line.id].write_bytes(wav_bytes(seconds))
            return result

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(narrator, "synthesize", side_effect=fake_narrator) as batch:
            run = Path(temporary)
            timings = tts.synthesize(run, script, voices, engine)
            batch.assert_called_once()
            engine.style_id.assert_called_once_with("合成/ノーマル")
            self.assertEqual(engine.query.call_args.args[:2], ("ニ", 7))
            self.assertEqual(engine.query.call_args.args[2].speed, 1.25)
            self.assertEqual([line.audio for line in timings.lines], [f"audio/L{k}.wav" for k in range(1, 4)])
            self.assertEqual([(line.start, line.end) for line in timings.lines],
                             [(0.05, 0.55), (0.68, 1.48), (1.51, 2.76)])
            self.assertEqual(timings.total_sec, 2.79)
            self.assertEqual(timings.sample_rate, 48000)
            self.assertEqual(timings.lines[0].spans, [])
            self.assertEqual(timings.lines[2].spans, [])
            span, = timings.lines[1].spans
            self.assertEqual((span.text, span.start, span.end), ("ニ", 0.88, 1.28))
            self.assertEqual(tts.wav_seconds((run / "audio/L2.wav").read_bytes()), 0.8)
        manifest = Manifest(assets=[Asset(id="keep", path="image.png", kind="image", origin="generated",
                                         license="MIT", commercial_ok=True)])
        recorded = tts.record_voices(manifest, script, timings, voices, engine_version="synthetic-version")
        first, second, third = [a for a in recorded.assets if a.kind == "voice"]
        self.assertEqual(first.tool, f"irodori:{narrator.CHECKPOINT}")
        self.assertEqual(first.license, "MIT")
        self.assertEqual(first.params["speed"], 1.5)
        self.assertEqual(second.tool, "voicevox:synthetic-version:合成/ノーマル")
        self.assertEqual(second.license, "Synthetic character terms")
        self.assertEqual(second.credit, "VOICEVOX:合成")
        self.assertEqual(third.tool, first.tool)
        self.assertEqual(len(tts.record_voices(recorded, script, timings, voices).assets), 4)

    def test_narrator_only_never_constructs_voicevox(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "audio").mkdir()
            path = run / "audio/L1.wav"
            path.write_bytes(wav_bytes())
            with patch.object(narrator, "synthesize", return_value={"L1": path}), \
                    patch.object(tts, "Voicevox") as engine:
                timings = tts.synthesize(run, Script(title="Synthetic", cast={"narrator": "ir"},
                                                    lines=[Line(id="L1", text="合成")]), {"ir": voice()})
                self.assertAlmostEqual(timings.lines[0].end - timings.lines[0].start, 0.25)
                engine.assert_not_called()

    def test_registry_seed_is_optional_and_pending_voices_stay_pending(self):
        voices = registry.load_voices()
        for name in ("narrator_male_calm", "narrator_female_announcer"):
            self.assertIsNone(voices[name].seed)
            self.assertEqual(voices[name].status, "pending")


class WorkerTests(unittest.TestCase):
    def test_runtime_once_upstream_defaults_requests_pcm_and_resume(self):
        # No upstream checkout or ML packages are required to run the public tests.
        # Capture constructor arguments so unspecified options retain upstream defaults.
        runtime_api = ModuleType("irodori_tts.inference_runtime")
        runtime_api.RuntimeKey = MagicMock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
        runtime_api.SamplingRequest = MagicMock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
        runtime_api.download_hf_checkpoint = MagicMock(return_value="/synthetic/model.safetensors")
        runtime_api.default_runtime_device = lambda: "cuda"
        runtime_api.resolve_cfg_scales = MagicMock(side_effect=lambda **kwargs: (
            kwargs["cfg_scale_text"], kwargs["cfg_scale_caption"],
            kwargs["cfg_scale_speaker"] if kwargs["use_speaker_condition"] else 0, []))
        audio = MagicMock()
        audio.ndim = 2
        audio.detach.return_value = audio
        audio.to.return_value = audio
        audio.mean.return_value = audio
        runtime = MagicMock()
        runtime.model_cfg = SimpleNamespace(use_caption_condition=True, use_speaker_condition_resolved=True)
        runtime.synthesize.return_value = SimpleNamespace(audio=audio, sample_rate=48000)
        runtime_api.InferenceRuntime = MagicMock()
        runtime_api.InferenceRuntime.from_key.return_value = runtime
        numpy, torch = MagicMock(), MagicMock()
        pcm = numpy.rint.return_value.astype.return_value
        pcm.__len__.return_value = 4800
        pcm.tobytes.return_value = b"\0\0" * 4800
        torch.cuda.is_available.return_value = True
        modules = {"torch": torch, "numpy": numpy, "irodori_tts": ModuleType("irodori_tts"),
                   "irodori_tts.inference_runtime": runtime_api}
        spec = importlib.util.spec_from_file_location("synthetic_irodori_worker", ROOT / "workers/tts_irodori/synth.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, modules), \
                patch.object(sys, "path", sys.path.copy()), patch.object(random, "seed") as random_seed:
            directory = Path(temporary)
            outputs = [directory / f"{k}.wav" for k in range(3)]
            outputs[2].write_bytes(wav_bytes())
            jobs = [{"id": str(k), "text": f"合成{k}", "caption": "落ち着いた声" if k == 0 else None,
                     "ref_wavs": [] if k == 0 else [str(directory / "ref.wav")],
                     "seed": 100 + k, "duration_scale": 0.8, "output": str(output)}
                    for k, output in enumerate(outputs)]
            path = directory / "jobs.json"
            path.write_text(json.dumps({"checkpoint": narrator.CHECKPOINT, "jobs": jobs}), encoding="utf-8")
            worker.main(["--spec", str(path)])
            runtime_api.download_hf_checkpoint.assert_called_once_with(narrator.CHECKPOINT)
            runtime_api.InferenceRuntime.from_key.assert_called_once()
            key = runtime_api.InferenceRuntime.from_key.call_args.args[0]
            self.assertEqual((key.model_device, key.codec_device), ("cuda", "cuda"))
            self.assertEqual((key.model_precision, key.codec_precision), ("fp32", "fp32"))
            self.assertEqual(key.checkpoint, "/synthetic/model.safetensors")
            self.assertEqual(set(runtime_api.RuntimeKey.call_args.kwargs),
                             {"checkpoint", "model_device", "codec_device", "model_precision", "codec_precision"})
            self.assertEqual(runtime.synthesize.call_count, 2)
            first, second = [invocation.args[0] for invocation in runtime.synthesize.call_args_list]
            self.assertTrue(first.no_ref)
            self.assertIsNone(first.ref_wavs)
            self.assertFalse(second.no_ref)
            self.assertEqual(second.ref_wavs, jobs[1]["ref_wavs"])
            self.assertEqual((first.cfg_scale_text, first.cfg_scale_caption, first.cfg_scale_speaker), (3, 3, 0))
            self.assertEqual(second.cfg_scale_speaker, 5)
            self.assertEqual(first.duration_scale, 0.8)
            self.assertEqual(set(runtime_api.SamplingRequest.call_args.kwargs),
                             {"text", "caption", "ref_wavs", "no_ref", "seed", "duration_scale",
                              "cfg_scale_text", "cfg_scale_caption", "cfg_scale_speaker"})
            first_scales, second_scales = [c.kwargs for c in runtime_api.resolve_cfg_scales.call_args_list]
            self.assertEqual((first_scales["cfg_scale_text"], first_scales["cfg_scale_caption"],
                              first_scales["cfg_scale_speaker"]), (3, 3, 5))
            self.assertEqual(first_scales["cfg_guidance_mode"], "independent")
            self.assertIsNone(first_scales["cfg_scale"])
            self.assertTrue(first_scales["use_caption_condition"])
            self.assertFalse(first_scales["use_speaker_condition"])
            self.assertFalse(second_scales["use_caption_condition"])
            self.assertTrue(second_scales["use_speaker_condition"])
            self.assertEqual(first.text, "合成0")
            self.assertEqual(first.caption, "落ち着いた声")
            for mocked in (random_seed, numpy.random.seed, torch.manual_seed, torch.cuda.manual_seed_all):
                self.assertEqual(mocked.call_args_list, [call(100), call(101)])
            self.assertEqual(audio.mean.call_args_list, [call(dim=0), call(dim=0)])
            for k, output in enumerate(outputs[:2]):
                with wave.open(str(output)) as wav:
                    self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (48000, 1, 2))
                    self.assertEqual(wav.getnframes(), 4800)
                self.assertEqual(json.loads(output.with_suffix(".json").read_text()), {"seconds": 0.1, "seed": 100 + k})
            worker.main(["--spec", str(path)])
            self.assertEqual(runtime_api.InferenceRuntime.from_key.call_count, 1)
            self.assertEqual(runtime.synthesize.call_count, 2)
            with self.assertRaisesRegex(ValueError, "absolute .wav"):
                worker.synthesize({"jobs": [{"output": "relative.wav"}]})


if __name__ == "__main__":
    unittest.main()
