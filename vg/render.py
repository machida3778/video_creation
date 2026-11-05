"""Render a timeline with Remotion, then normalize its audio and export SRT."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re

from .media import FFMPEG, ROOT, load, run, save, scrubbed_env
from .mix import prepare_mix


def _timestamp(seconds):
    milliseconds = round(seconds * 1000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def write_srt(timeline, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{i}\n{_timestamp(c.start)} --> {_timestamp(c.end)}\n{c.text}\n\n"
                            for i, c in enumerate(timeline.captions, 1)), encoding="utf-8")
    return path


def loudnorm(src, dst):
    """Use measured loudnorm values in a second pass, preserving the video stream."""
    target = "loudnorm=I=-14:TP=-1.5:LRA=11"
    first = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(src), "-vn",
                 "-af", target + ":print_format=json", "-f", "null", "-"])
    blocks = re.findall(r'\{\s*"input_i".*?\}', first.stderr, re.S)
    if not blocks:
        raise ValueError("ffmpeg did not return loudnorm measurements")
    measured = json.loads(blocks[-1])
    fields = {"measured_I": "input_i", "measured_TP": "input_tp", "measured_LRA": "input_lra",
              "measured_thresh": "input_thresh", "offset": "target_offset"}
    if not all(math.isfinite(float(measured[key])) for key in fields.values()):
        raise ValueError("Cannot normalize audio with non-finite loudness measurements")
    settings = ":".join(f"{key}={measured[value]}" for key, value in fields.items())
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(src), "-map", "0:v:0",
         "-map", "0:a:0", "-c:v", "copy", "-af", target + ":" + settings + ":linear=true",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(dst)])
    return Path(dst)


def render(run_dir, concurrency=None) -> Path:
    concurrency = 8 if concurrency is None else concurrency
    if not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    run_dir = Path(run_dir).resolve()
    timeline = load(run_dir, "timeline")
    prepare_mix(run_dir, timeline)
    save(run_dir, "timeline", timeline)
    render_dir = run_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    props = render_dir / "props.json"
    props.write_text(json.dumps({"timeline": timeline.model_dump(mode="json")},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    env = scrubbed_env()
    env["PATH"] = str(Path.home() / ".local/opt/node/bin") + os.pathsep + env.get("PATH", "")
    raw = render_dir / "raw.mp4"
    run(["npx", "remotion", "render", "src/index.ts", "Short", str(raw), f"--props={props}",
         f"--public-dir={run_dir}", "--codec=h264", "--audio-codec=aac", "--pixel-format=yuv420p",
         f"--concurrency={concurrency}", "--log=error"], cwd=ROOT / "render", env=env)
    final = loudnorm(raw, run_dir / "final.mp4")
    write_srt(timeline, run_dir / "final.srt")
    return final
