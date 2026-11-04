"""Assets stage: storyboard visuals (visual.kind == "t2i") -> one GPU job on a free lab GPU -> assets/ + manifest.

GPU choice: the smallest idle lab card with enough memory (big cards stay free for others). The remote
command always starts with scripts/lab/gpu_free.sh for that GPU (shared-GPU rule in AGENTS.md); if the
card got busy in the meantime the next candidate is tried. Model weights load from the shared Hugging
Face cache in offline mode.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .run import ROOT
from . import registry, site
from .schemas import Asset, BgmTrack, Manifest, Storyboard
from .variants import local_file
from .sfx import asset_id, events

# bf16 models need Ampere or newer; eligible hosts are configured per site.
T2I = {  # docs/MODELS.md: t2i first choice. ~21 GB of weights: < 40 GB cards use model CPU offload.
    "model": "Tongyi-MAI/Z-Image-Turbo",
    "license": "Apache-2.0",
    "license_url": "https://huggingface.co/Tongyi-MAI/Z-Image-Turbo",
    "min_mem_mb": 24000,
    "width": 1088,
    "height": 1920,
}
SCRUB = "env -u OPENAI_API_KEY -u GEMINI_API_KEY -u ARK_API_KEY -u GOOGLE_API_KEY -u ANTHROPIC_API_KEY"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]


def remote(path) -> str:
    """Map a CPU server path to the corresponding mount on lab GPU servers."""
    settings = site.load()
    source = site.require("REMOTE_PATH_LOCAL_PREFIX", settings)
    target = site.require("REMOTE_PATH_GPU_PREFIX", settings)
    if not all(p.startswith("/") and p.endswith("/") for p in (source, target)):
        raise ValueError("remote path prefixes must be absolute directories ending in /")
    value = str(path)
    if value.startswith(source):
        return target + value[len(source):]
    if value == source.rstrip("/"):
        return target.rstrip("/")
    return value


def idle_gpus(min_mem_mb: int, hosts: list[str] | None = None) -> list[tuple[int, str, int]]:
    """(total_mb, host, index) of GPUs with no compute process and <= 1 GiB used, smallest first."""
    if hosts is None:
        hosts = site.require("LAB_AMPERE_HOSTS").split()
    found = []
    query = ("nvidia-smi --query-gpu=index,uuid,memory.total,memory.used --format=csv,noheader,nounits; echo ---; "
             "nvidia-smi --query-compute-apps=gpu_uuid --format=csv,noheader")
    for host in hosts:
        try:
            out = subprocess.run([*SSH, host, query], capture_output=True, text=True, timeout=20, check=True).stdout
        except (subprocess.SubprocessError, OSError):
            continue
        gpus, _, apps = out.partition("---")
        busy = {line.strip() for line in apps.splitlines() if line.strip()}
        for row in gpus.strip().splitlines():
            idx, uuid, total, used = (x.strip() for x in row.split(","))
            if uuid not in busy and int(used) <= 1024 and int(total) >= min_mem_mb:
                found.append((int(total), host, int(idx)))
    return sorted(found)


def t2i_jobs(board: Storyboard, run: Path | None = None, manifest: Manifest | None = None) -> list[dict]:
    jobs = []
    existing = {a.id: a for a in manifest.assets} if manifest else {}
    for sc in board.scenes:
        v = sc.visual
        if v.kind == "t2i":
            asset = existing.get(v.ref)
            if run is not None and asset is not None and asset.kind == "image":
                try:
                    local_file(run, asset.path)
                except FileNotFoundError:
                    pass
                else:
                    continue
            if not v.prompt:
                raise ValueError(f"scene {sc.id}: t2i visual without a prompt")
            jobs.append({"id": f"img_{sc.id}", "prompt": v.prompt, "negative": v.negative or "",
                         "seed": v.seed if v.seed is not None else 1000 + len(jobs),
                         "width": int(v.params.get("width", T2I["width"])),
                         "height": int(v.params.get("height", T2I["height"])),
                         "scene": sc.id, "realistic": bool(v.params.get("realistic", False))})
    return jobs


def generate(run: Path, board: Storyboard, manifest: Manifest | None = None, *,
             bgm_tracks: dict[str, BgmTrack] | None = None, library_root: Path | None = None) -> Manifest:
    """Generate the storyboard's images, set each t2i scene's visual.ref, and return the updated manifest."""
    manifest = manifest or Manifest()
    used_sfx = dict.fromkeys(effect_id for sc in board.scenes if sc.line_ids
                            for effect_id, _ in events(sc.props.get("sfx")))
    if used_sfx:
        effects = registry.load_sfx()
        for effect_id in used_sfx:
            if effect_id not in effects:
                raise ValueError(f"unknown SFX {effect_id!r} in {registry.SFX_FILE}")
            effect = effects[effect_id]
            key = asset_id(effect_id)
            copied = next((a for a in manifest.assets if a.id == key and a.kind == "sfx"), None)
            if copied and (run / copied.path).is_file():
                local_file(run, copied.path)
                continue
            try:
                source = local_file(Path(library_root) if library_root is not None else ROOT, effect.file)
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"SFX {effect_id} is missing; run vg sfx build first") from exc
            relative = f"assets/sfx/{effect_id}.wav"
            target = run / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest.assets = [a for a in manifest.assets if a.id != key]
            manifest.assets.append(Asset(id=key, path=relative, kind="sfx", origin="generated", tool="vg sfx",
                                         params={"effect": effect_id}, license=effect.license,
                                         commercial_ok=effect.commercial_ok))
    # Resolve music before scheduling any GPU work, so missing library files fail cheaply.
    if board.music and board.music.track:
        tracks = registry.load_bgm() if bgm_tracks is None else bgm_tracks
        track_id = board.music.track
        copied = next((a for a in manifest.assets if a.id == board.music.asset and a.kind == "music"
                       and a.params.get("track") == track_id), None)
        if copied is not None and (run / copied.path).is_file():
            local_file(run, copied.path)
        else:
            if track_id not in tracks:
                raise ValueError(f"unknown BGM track {track_id!r} in {registry.BGM_FILE}")
            track = tracks[track_id]
            source = local_file(Path(library_root) if library_root is not None else ROOT, track.file)
            relative = f"assets/bgm/{track.id}{source.suffix}"
            destination = run / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            asset = Asset(id=f"bgm_{track.id}", path=relative, kind="music", origin="library",
                          params={"track": track.id, "title": track.title, "artist": track.artist},
                          license=track.license, license_url=track.license_url,
                          commercial_ok=track.commercial_ok, credit=track.credit)
            manifest.assets = [a for a in manifest.assets if a.id != asset.id]
            manifest.assets.append(asset)
            board.music.asset = asset.id
            if track.gain_db is not None and board.music.gain_db == -20.0:
                board.music.gain_db = track.gain_db
    jobs = t2i_jobs(board, run, manifest)
    if jobs:
        out = run / "assets" / "images"
        out.mkdir(parents=True, exist_ok=True)
        spec = run / "assets" / "t2i_jobs.json"
        spec.write_text(json.dumps({"model": T2I["model"], "jobs": jobs}, ensure_ascii=False, indent=2), encoding="utf-8")
        _run_on_lab_gpu(f"workers/t2i/.venv/bin/python workers/t2i/generate.py --spec {shlex.quote(remote(spec))} "
                        f"--out {shlex.quote(remote(out))}", T2I["min_mem_mb"])
        done = {a.id for a in manifest.assets}
        for job in jobs:
            path = out / f"{job['id']}.png"
            if not path.is_file():
                raise RuntimeError(f"t2i worker did not write {path}")
            if job["id"] not in done:
                manifest.assets.append(Asset(
                    id=job["id"], path=str(path.relative_to(run)), kind="image", origin="generated",
                    tool=T2I["model"], params={k: job[k] for k in ("prompt", "negative", "seed", "width", "height", "realistic")},
                    license=T2I["license"], license_url=T2I["license_url"], commercial_ok=True))
            for sc in board.scenes:
                if sc.id == job["scene"]:
                    sc.visual.ref = job["id"]
    return manifest


LOCKS = ROOT / "local" / "gpu_locks"
LOCK_STALE_SEC = 3 * 3600


def _lock(host: str, gpu: int) -> Path | None:
    """Reserve host:gpu for one of our own jobs (mkdir is atomic on the shared disk). gpu_free.sh cannot see a
    job of ours that is still loading weights on the CPU, so two of our jobs could otherwise pick the same card."""
    LOCKS.mkdir(parents=True, exist_ok=True)
    path = LOCKS / f"{host}-{gpu}"
    try:
        path.mkdir()
    except FileExistsError:
        if time.time() - path.stat().st_mtime < LOCK_STALE_SEC:
            return None
        path.rmdir()  # stale lock from a crashed run
        path.mkdir()
    return path


def _run_on_lab_gpu(command: str, min_mem_mb: int) -> None:
    candidates = idle_gpus(min_mem_mb)
    if not candidates:
        raise RuntimeError(f"no idle lab GPU with >= {min_mem_mb} MiB; try later or use an HPC cluster")
    for total, host, gpu in candidates:
        lock = _lock(host, gpu)
        if lock is None:
            continue  # one of our own jobs already holds this card
        try:
            cmd = (f"cd {shlex.quote(remote(ROOT))} && bash scripts/lab/gpu_free.sh {gpu} && "
                   f"{SCRUB} CUDA_VISIBLE_DEVICES={gpu} CUDA_DEVICE_ORDER=PCI_BUS_ID HF_HUB_OFFLINE=1 {command}")
            proc = subprocess.run([*SSH, host, cmd], capture_output=True, text=True)
        finally:
            lock.rmdir()
        if proc.returncode == 0:
            print(f"[assets] ran on {host} GPU {gpu} ({total} MiB)")
            return
        if "gpu_free" in proc.stderr and proc.returncode == 1:
            continue  # became busy: next candidate
        raise RuntimeError(f"{host} GPU {gpu}: worker failed ({proc.returncode}): {proc.stderr[-2000:]}")
    raise RuntimeError("all idle GPUs became busy (or are held by our own jobs) before the job started")
