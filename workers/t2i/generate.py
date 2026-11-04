"""Text-to-image GPU worker: load the model once, render every job of a spec file.

  CUDA_VISIBLE_DEVICES=<gpu> HF_HUB_OFFLINE=1 workers/t2i/.venv/bin/python workers/t2i/generate.py \
      --spec runs/<id>/assets/t2i_jobs.json --out runs/<id>/assets/images

spec: {"model": "Tongyi-MAI/Z-Image-Turbo", "jobs": [{"id", "prompt", "negative", "seed", "width", "height"}]}
Writes <out>/<id>.png and <out>/<id>.json (timing, model, seed). Existing PNGs are skipped (resume).
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
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [j for j in spec["jobs"] if not (out / f"{j['id']}.png").is_file()]
    if not jobs:
        print("[t2i] nothing to do")
        return
    t0 = time.time()
    pipe = load(spec["model"])
    print(f"[t2i] loaded {spec['model']} in {time.time() - t0:.1f}s on {torch.cuda.get_device_name(0)}", flush=True)
    for job in jobs:
        t1 = time.time()
        image = pipe(prompt=job["prompt"], height=job["height"], width=job["width"],
                     num_inference_steps=STEPS[spec["model"]], guidance_scale=0.0,
                     generator=torch.Generator("cuda").manual_seed(int(job["seed"]))).images[0]
        image.save(out / f"{job['id']}.png")
        meta = {"model": spec["model"], "seed": job["seed"], "seconds": round(time.time() - t1, 2),
                "peak_mem_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)}
        (out / f"{job['id']}.json").write_text(json.dumps(meta), encoding="utf-8")
        print(f"[t2i] {job['id']} {meta}", flush=True)


if __name__ == "__main__":
    main()
