#!/usr/bin/env python3
"""Batched Irodori worker; launch with tools/irodori/.venv/bin/python --spec FILE.

This module deliberately has no vg dependency. The upstream checkout supplies
the runtime and its environment supplies torch/numpy (no model API is used).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
import wave


def synthesize(spec: dict) -> None:
    jobs = []
    for job in spec["jobs"]:
        output = Path(job["output"])
        if not output.is_absolute() or output.suffix.lower() != ".wav":
            raise ValueError("job output must be an absolute .wav path")
        if any(not Path(p).is_absolute() for p in job.get("ref_wavs", [])):
            raise ValueError("reference WAV paths must be absolute")
        if not output.exists():
            jobs.append(job)
    if not jobs:
        return

    # The checkout is not installed into the harness environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "irodori"))
    import numpy as np
    import torch
    from irodori_tts.inference_runtime import (
        InferenceRuntime, RuntimeKey, SamplingRequest, default_runtime_device,
        download_hf_checkpoint, resolve_cfg_scales,
    )

    device = default_runtime_device()
    runtime = InferenceRuntime.from_key(RuntimeKey(
        checkpoint=str(download_hf_checkpoint(spec["checkpoint"])),
        model_device=device, codec_device=device,
        # RuntimeKey/SamplingRequest defaults match infer.py's argparse defaults.
        model_precision="fp32", codec_precision="fp32",
    ))
    for job in jobs:
        seed = int(job["seed"])
        random.seed(seed)
        np.random.seed(seed % (2**32))
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        refs = job.get("ref_wavs") or []
        caption = job.get("caption")
        text_scale, caption_scale, speaker_scale, _ = resolve_cfg_scales(
            cfg_guidance_mode="independent", cfg_scale_text=3.0,
            cfg_scale_caption=3.0, cfg_scale_speaker=5.0, cfg_scale=None,
            use_caption_condition=bool(runtime.model_cfg.use_caption_condition and caption
                                       and caption.strip()),
            use_speaker_condition=bool(runtime.model_cfg.use_speaker_condition_resolved and refs),
        )
        result = runtime.synthesize(SamplingRequest(
            text=job["text"], caption=caption, ref_wavs=refs or None,
            no_ref=not refs, seed=seed, duration_scale=job.get("duration_scale", 1.0),
            cfg_scale_text=text_scale, cfg_scale_caption=caption_scale,
            cfg_scale_speaker=speaker_scale,
        ), log_fn=None)
        audio = result.audio.detach().to(device="cpu", dtype=torch.float32)
        if audio.ndim == 2:
            audio = audio.mean(dim=0)
        if result.sample_rate != 48000:
            from torchaudio.functional import resample
            audio = resample(audio, result.sample_rate, 48000)
        pcm = np.rint(np.clip(audio.numpy(), -1.0, 1.0) * 32767).astype("<i2")
        output = Path(job["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".wav.tmp")
        with wave.open(str(temporary), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(pcm.tobytes())
        output.with_suffix(".json").write_text(
            json.dumps({"seconds": len(pcm) / 48000, "seed": seed}), encoding="utf-8")
        temporary.replace(output)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args(argv)
    synthesize(json.loads(args.spec.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
