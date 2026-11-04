"""Text-to-image GPU worker: load the model once, render every job of a spec file.

  CUDA_VISIBLE_DEVICES=<gpu> HF_HUB_OFFLINE=1 workers/t2i/.venv/bin/python workers/t2i/generate.py \
      --spec runs/<id>/assets/t2i_jobs.json --out runs/<id>/assets/images

spec: {"model": "Tongyi-MAI/Z-Image-Turbo", "jobs": [{"id", "prompt", "negative", "seed", "width", "height", "output"}]}
Writes each job's absolute output path and an adjacent JSON (timing, model, seed).
Legacy specs can use --out for <out>/<id>.png. Existing PNGs are skipped (resume).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch

STEPS = {"Tongyi-MAI/Z-Image-Turbo": 9}  # model card: 9 steps = 8 DiT forwards, guidance 0


def load(model: str):
    if model == "Tongyi-MAI/Z-Image-Turbo":
        from diffusers import ZImagePipeline
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.bfloat16, low_cpu_mem_usage=False)
    else:
        raise ValueError(f"unsupported model {model}")
    if torch.cuda.get_device_properties(0).total_memory < 40 * 2**30:
        pipe.enable_model_cpu_offload()  # 24 GB cards: text encoder and DiT take turns on the GPU
        return pipe
    return pipe.to("cuda")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    jobs = [(j, output_path(j, a.out)) for j in spec["jobs"]]
    jobs = [(job, path) for job, path in jobs if not path.is_file()]
    if not jobs:
        print("[t2i] nothing to do")
        return
    t0 = time.time()
    pipe = load(spec["model"])
    print(f"[t2i] loaded {spec['model']} in {time.time() - t0:.1f}s on {torch.cuda.get_device_name(0)}", flush=True)
    for job, path in jobs:
        path.parent.mkdir(parents=True, exist_ok=True)
        t1 = time.time()
        image = pipe(prompt=job["prompt"], height=job["height"], width=job["width"],
                     num_inference_steps=STEPS[spec["model"]], guidance_scale=0.0,
                     generator=torch.Generator("cuda").manual_seed(int(job["seed"]))).images[0]
        image.save(path)
        meta = {"model": spec["model"], "seed": job["seed"], "seconds": round(time.time() - t1, 2),
                "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        path.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        print(f"[t2i] {job['id']} {meta}", flush=True)


def output_path(job: dict, out: str | None) -> Path:
    if "output" in job:
        path = Path(job["output"])
        if not path.is_absolute() or path.suffix != ".png":
            raise ValueError("job output must be an absolute PNG path")
        return path
    if out is None:
        raise ValueError("job needs an absolute output path or --out")
    return Path(out) / f"{job['id']}.png"


if __name__ == "__main__":
    main()
