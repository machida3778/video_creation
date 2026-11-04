"""Offline, atomic staging of one shared HF snapshot onto GPU-local disk.

Uses only the standard library and rsync; no CUDA imports or network access.
"""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def shared_cache() -> Path:
    hub = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if hub:
        return Path(hub).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser()
    return base / "huggingface" / "hub"


def stage_snapshot(source: Path, destination: Path, model: str, *, timeout: float = 1800) -> Path:
    """Copy main's snapshot with symlinks dereferenced so it is independent of shared blobs.

    A per-model flock serializes copying and refs updates and releases on process exit. A completed snapshot is
    renamed into place atomically; interrupted copies never look like cache hits.
    """
    source, destination = source.resolve(), destination.resolve()
    if source == destination:
        return destination
    if not model or any(part in ("", ".", "..") for part in model.split("/")):
        raise ValueError("invalid model id")
    folder = "models--" + model.replace("/", "--")
    repository = source / folder
    revision = (repository / "refs" / "main").read_text(encoding="utf-8").strip()
    if not revision or Path(revision).name != revision or revision in (".", ".."):
        raise ValueError("invalid shared model revision")
    snapshot = repository / "snapshots" / revision
    if not (snapshot / "model_index.json").is_file():
        raise FileNotFoundError(f"shared model snapshot is missing: {snapshot}")
    target = destination / folder
    cached = target / "snapshots" / revision
    destination.mkdir(parents=True, exist_ok=True)
    locks = destination / ".locks"
    locks.mkdir(exist_ok=True)
    lock = locks / (folder + ".stage.lock")
    handle = lock.open("a")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"local weight cache lock is held: {lock}")
                time.sleep(0.2)
        if not (cached / "model_index.json").is_file():
            cached.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=cached.parent) as temporary:
                staged = Path(temporary) / revision
                staged.mkdir()
                subprocess.run(["rsync", "-a", "--copy-links", "--", str(snapshot) + "/", str(staged) + "/"],
                               check=True)
                if not (staged / "model_index.json").is_file():
                    raise RuntimeError("snapshot sync did not produce model_index.json")
                if cached.is_symlink():
                    cached.unlink()
                elif cached.exists():
                    shutil.rmtree(cached)  # replace a preexisting partial snapshot under the lock
                staged.rename(cached)
        refs = target / "refs"
        refs.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=refs, delete=False) as ref_file:
            reference = Path(ref_file.name)
            ref_file.write(revision)
        try:
            reference.replace(refs / "main")
        finally:
            reference.unlink(missing_ok=True)
    finally:
        handle.close()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a worker command is required after --")
    if not args.cache.is_absolute():
        parser.error("--cache must be absolute")
    # Keep subprocess credentials stripped even when this helper is invoked directly.
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ARK_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(key, None)
    cache = stage_snapshot(shared_cache(), args.cache, args.model)
    os.environ["HF_HUB_CACHE"] = str(cache)
    os.environ["HF_HUB_OFFLINE"] = "1"
    # A first-time copy can take minutes: another user may have claimed the GPU meanwhile.
    gpu = os.environ.get("CUDA_VISIBLE_DEVICES")
    if gpu:
        subprocess.run(["bash", str(Path(__file__).resolve().parents[2] / "scripts/lab/gpu_free.sh"), gpu],
                       check=True)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
