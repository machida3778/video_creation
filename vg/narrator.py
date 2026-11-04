"""Design, freeze and synthesize Irodori narrators on one idle lab GPU."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from . import assets
from .run import ROOT
from .schemas import Line, VoiceSpec

CHECKPOINT = "Aratako/Irodori-TTS-v4.1-Small"
MIN_MEM_MB = 12000
DESCRIPTIONS = {
    "male": ("落ち着いた低めの成人男性ナレーター", "明瞭で爽やかな若い男性ナレーター",
             "渋く深みのあるドキュメンタリー風の男性"),
    "female": ("落ち着いた成人女性のナレーター", "明るく親しみやすい女性ナレーター",
               "アナウンサーのように明瞭な女性"),
}
AUDITION_TEXT = "タコには心臓が3つあります。海の中で体の色を変え、岩や砂に似た姿になることもできます。"
REFERENCE_TEXTS = (
    "朝の公園では、木々の間から柔らかな光が差し込み、小さな鳥の声が静かに聞こえてきます。",
    "図書館の窓辺に座り、ページをゆっくりめくると、遠い町の暮らしや風景が浮かんできます。",
    "午後の空には白い雲が流れています。温かいお茶を用意して、今日の出来事を振り返りましょう。",
)


def _run_on_lab_gpu(command: str, min_mem_mb: int) -> None:
    """Use assets' GPU selection and shared locks, without its t2i cache wrapper."""
    candidates = assets.idle_gpus(min_mem_mb)
    if not candidates:
        raise RuntimeError(f"no idle lab GPU with >= {min_mem_mb} MiB; try later")
    # Scrub credentials from the local SSH subprocess too. Remote login environments
    # are scrubbed by SCRUB immediately before launching the model worker.
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    for total, host, gpu in candidates:
        lock = assets._lock(host, gpu)
        if lock is None:
            continue
        try:
            cmd = (f"cd {shlex.quote(assets.remote(ROOT))} && bash scripts/lab/gpu_free.sh {gpu} && "
                   f"{assets.SCRUB} CUDA_VISIBLE_DEVICES={gpu} CUDA_DEVICE_ORDER=PCI_BUS_ID "
                   f"HF_HUB_OFFLINE=1 {command}")
            proc = subprocess.run([*assets.SSH, host, cmd], capture_output=True, text=True, env=env)
        finally:
            lock.rmdir()
        if proc.returncode == 0:
            print(f"[narrator] ran on {host} GPU {gpu} ({total} MiB)")
            return
        if "gpu_free" in proc.stderr and proc.returncode == 1:
            continue
        raise RuntimeError(f"{host} GPU {gpu}: worker failed ({proc.returncode}): {proc.stderr[-2000:]}")
    raise RuntimeError("all idle GPUs became busy (or are held by our own jobs) before the job started")


def _dispatch(directory: Path, jobs: list[dict], *, spec_name: str) -> None:
    """Map every path in the job spec; a resumed batch never reloads for existing WAVs."""
    missing = [job for job in jobs if not Path(job["output"]).is_file()]
    if not missing:
        return
    directory.mkdir(parents=True, exist_ok=True)
    mapped = [{**job, "output": assets.remote(job["output"]),
               "ref_wavs": [assets.remote(p) for p in job.get("ref_wavs", [])]} for job in missing]
    spec = directory / spec_name
    spec.write_text(json.dumps({"checkpoint": CHECKPOINT, "jobs": mapped},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    _run_on_lab_gpu("tools/irodori/.venv/bin/python workers/tts_irodori/synth.py "
                    f"--spec {shlex.quote(assets.remote(spec))}", MIN_MEM_MB)
    for job in missing:
        if not Path(job["output"]).is_file():
            raise RuntimeError(f"irodori worker did not write {job['output']}")


def _seed(voice: VoiceSpec) -> int:
    return (voice.seed if voice.seed is not None else
            int.from_bytes(hashlib.sha256(voice.id.encode("utf-8")).digest()[:4], "big"))


def synthesize(run: Path, lines: list[Line], voices: dict[str, VoiceSpec]) -> dict[str, Path]:
    """Batch Irodori lines. voices maps each line's speaker to its effective VoiceSpec."""
    audio = Path(run).resolve() / "audio"
    jobs, outputs = [], {}
    for line in lines:
        voice = voices[line.speaker]
        if voice.engine != "irodori":
            continue
        if voice.model not in (None, CHECKPOINT):
            raise ValueError(f"unsupported Irodori checkpoint: {voice.model}")
        if not math.isfinite(voice.speed) or voice.speed <= 0:
            raise ValueError(f"{voice.id}: speed must be positive and finite")
        if Path(line.id).name != line.id or line.id in ("", ".", ".."):
            raise ValueError(f"invalid audio line id: {line.id!r}")
        refs = []
        if voice.reference:
            directory = Path(voice.reference)
            if not directory.is_absolute():
                directory = ROOT / directory
            refs = [str(p.resolve()) for p in sorted(directory.glob("ref_*.wav")) if p.is_file()]
            if not refs:
                raise FileNotFoundError(f"{voice.id}: no ref_*.wav in {directory}; freeze the voice first")
        output = audio / f"{line.id}.wav"
        outputs[line.id] = output
        jobs.append({"id": line.id, "text": line.yomi or line.text,
                     "caption": voice.voice_description, "ref_wavs": refs,
                     "seed": _seed(voice), "duration_scale": 1 / voice.speed, "output": str(output)})
    _dispatch(audio, jobs, spec_name="irodori_jobs.json")
    return outputs


def audition(out_dir: Path, candidates: list[tuple[str, int]], *, gender: str | None = None) -> list[dict]:
    """Read the same audition text in each designed voice; save an index for selection."""
    directory = Path(out_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    jobs, index = [], []
    for k, (caption, seed) in enumerate(candidates, 1):
        prefix = gender or ("female" if "女性" in caption else "male" if "男性" in caption else "candidate")
        name = f"{prefix}_{k}"
        index.append({"wav": f"{name}.wav", "caption": caption, "seed": seed})
        jobs.append({"id": name, "text": AUDITION_TEXT, "caption": caption, "ref_wavs": [],
                     "seed": seed, "duration_scale": 1.0, "output": str(directory / f"{name}.wav")})
    _dispatch(directory, jobs, spec_name="audition_jobs.json")
    (directory / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def freeze(candidate_wav: Path, caption: str, seed: int, voice_dir: Path) -> None:
    """Keep the selected clip plus three short clones (roughly 30 seconds together)."""
    directory = Path(voice_dir).resolve()
    candidate = Path(candidate_wav).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    reference = directory / "ref_01.wav"
    if reference.exists() and reference.read_bytes() != candidate.read_bytes():
        raise FileExistsError(f"{reference} already freezes another clip; use a new voice directory")
    if candidate != reference:
        shutil.copy2(candidate, reference)
    jobs = [{"id": f"ref_{k:02}", "text": text, "caption": caption,
             "ref_wavs": [str(reference)], "seed": seed, "duration_scale": 1.0,
             "output": str(directory / f"ref_{k:02}.wav")}
            for k, text in enumerate(REFERENCE_TEXTS, 2)]
    _dispatch(directory, jobs, spec_name="freeze_jobs.json")
    print(f"reference: {json.dumps(str(voice_dir), ensure_ascii=False)}\nstatus: active\nseed: {seed}\n"
          f"voice_description: {json.dumps(caption, ensure_ascii=False)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    design = commands.add_parser("audition")
    design.add_argument("--gender", choices=DESCRIPTIONS, required=True)
    design.add_argument("--n", type=int, default=3)
    design.add_argument("--out", type=Path, required=True)
    clone = commands.add_parser("freeze")
    clone.add_argument("--wav", type=Path, required=True)
    clone.add_argument("--caption", required=True)
    clone.add_argument("--seed", type=int, required=True)
    clone.add_argument("--voice-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "audition":
        if args.n < 1:
            parser.error("--n must be at least 1")
        descriptions = DESCRIPTIONS[args.gender]
        audition(args.out, [(descriptions[k % len(descriptions)], 1000 + k) for k in range(args.n)],
                 gender=args.gender)
    else:
        freeze(args.wav, args.caption, args.seed, args.voice_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
