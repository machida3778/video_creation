#!/usr/bin/env python3
"""Offline batch Kokoro worker; one pipeline for all variant lines, CPU or CUDA."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import types
import wave


class ExternalEspeak:
    """Misaki token fallback via the GPL program, without loading its library.

    Convert espeak's tied IPA to Kokoro's English phoneme alphabet. This small
    adapter intentionally supports only US English (the registered voices).
    """
    def __init__(self, british=False, **kwargs):
        if british:
            raise ValueError("only US English is configured")

    def __call__(self, token):
        result = subprocess.run(["espeak-ng", "-q", "--ipa=3", "--tie=^", "-v", "en-us", "--stdin"],
                                input=token.text, text=True, capture_output=True, check=True)
        phonemes = result.stdout.strip()
        for old, new in [("a^ɪ", "I"), ("a^ʊ", "W"), ("d^ʒ", "ʤ"), ("t^ʃ", "ʧ"),
                         ("e^ɪ", "A"), ("o^ʊ", "O"), ("ɔ^ɪ", "Y"), ("ɚ", "əɹ"),
                         ("ɜː", "ɜɹ"), ("r", "ɹ"), ("ɐ", "ə"), ("ɾ", "T"), ("ʔ", "t")]:
            phonemes = phonemes.replace(old, new)
        phonemes = re.sub(r"(\S)\u0329", r"ᵊ\1", phonemes)
        return phonemes.replace("^", "").replace("ː", ""), 2


def synthesize(spec):
    jobs = []
    for job in spec["jobs"]:
        path = Path(job["output"])
        if not path.is_absolute() or path.suffix != ".wav":
            raise ValueError("output must be an absolute .wav path")
        if job["voice"] not in ("af_heart", "am_michael"):
            raise ValueError("unsupported English voice")
        if not path.is_file():
            jobs.append(job)
    if not jobs:
        return
    if spec.get("checkpoint") != "hexgrad/Kokoro-82M":
        raise ValueError("unsupported checkpoint")
    # Stock misaki.espeak loads libespeak via ctypes on import. Substitute only
    # its English fallback interface before importing Kokoro, keeping GPL code
    # in the external espeak-ng process when the host has one.
    # Without a host espeak-ng, misaki's own fallback uses the espeakng_loader wheel (GPL-3.0
    # library, used privately and not redistributed; outputs are not covered by the GPL).
    if shutil.which("espeak-ng"):
        external = types.ModuleType("misaki.espeak")
        external.EspeakFallback = ExternalEspeak
        sys.modules["misaki.espeak"] = external
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import spacy
    if not spacy.util.is_package("en_core_web_sm"):
        raise RuntimeError("install en_core_web_sm in the Kokoro worker environment first")
    import numpy as np
    import torch
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a", repo_id=spec["checkpoint"], device=spec.get("device", "cuda"))
    for job in jobs:
        chunks = [audio.detach().cpu().numpy() if torch.is_tensor(audio) else np.asarray(audio)
                  for _, _, audio in pipeline(job["text"], voice=job["voice"], speed=job.get("speed", 1.0))]
        if not chunks or not sum(len(chunk) for chunk in chunks):
            raise ValueError("Kokoro returned no speech")
        pcm = np.rint(np.clip(np.concatenate(chunks), -1, 1)*32767).astype("<i2")
        path = Path(job["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".wav.tmp")
        with wave.open(str(temporary), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(pcm.tobytes())
        temporary.replace(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    synthesize(json.loads(args.spec.read_text(encoding="utf-8")))
