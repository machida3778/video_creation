"""Local media tools and validated, atomic stage storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from .schemas import STAGE_MODELS

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe"
_SECRET_NAMES = {"OPENAI_API_KEY", "GEMINI_API_KEY", "ARK_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"}


def scrubbed_env():
    return {key: value for key, value in os.environ.items() if key not in _SECRET_NAMES}


def run(cmd, **kw):
    env = dict(kw.pop("env", scrubbed_env()))
    for key in _SECRET_NAMES:
        env.pop(key, None)
    kw.update(env=env, check=True, capture_output=True, text=True)
    try:
        return subprocess.run(cmd, **kw)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Command failed (exit {exc.returncode}): {exc.stderr[-6000:]}") from exc


def probe(path) -> dict:
    return json.loads(run([str(FFPROBE), "-v", "error", "-show_streams", "-show_format",
                           "-of", "json", str(path)]).stdout)


def _stage_path(run_dir, name):
    STAGE_MODELS[name]  # Reject unknown stages before creating anything.
    return Path(run_dir) / ("assets/manifest.json" if name == "manifest" else f"{name}.json")


def load(run_dir, name):
    return STAGE_MODELS[name].model_validate_json(_stage_path(run_dir, name).read_text(encoding="utf-8"))


def save(run_dir, name, model):
    model = STAGE_MODELS[name].model_validate(model)
    path = _stage_path(run_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(model.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
