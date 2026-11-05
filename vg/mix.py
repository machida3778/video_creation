"""Measure local audio and set music/effect gains relative to the narration."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from .media import FFMPEG, probe, run
from .registry import load_style
from .schemas import Timeline

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|inf|nan)"


def measure_level(paths: list[Path], gains_db: list[float] | None = None) -> dict:
    """Measure concatenated audio, without timeline gaps; RMS covers sub-400 ms effects.

    Resample to a common stereo format before concatenation so differing voice formats work.
    Voice gains are included in the reference; accompaniment is measured at its source level.
    """
    gains_db = [0.0] * len(paths) if gains_db is None else gains_db
    if not paths or len(paths) != len(gains_db):
        raise ValueError("measurement needs audio paths and one gain per input")
    duration = sum(float(probe(path)["format"]["duration"]) for path in paths)
    if not math.isfinite(duration) or duration <= 0 or not all(math.isfinite(g) for g in gains_db):
        raise ValueError("measurement requires positive duration and finite gains")
    cmd = [str(FFMPEG), "-hide_banner", "-nostdin"]
    filters = []
    for i, (path, gain) in enumerate(zip(paths, gains_db)):
        cmd += ["-i", str(path)]
        filters.append(f"[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"asetpts=PTS-STARTPTS,volume={gain}dB[a{i}]")
    inputs = "".join(f"[a{i}]" for i in range(len(paths)))
    filters.append(f"{inputs}concat=n={len(paths)}:v=0:a=1,"
                   "ebur128=peak=true,astats=metadata=0:reset=0[out]")
    result = run(cmd + ["-filter_complex", ";".join(filters), "-map", "[out]", "-vn", "-f", "null", "-"])

    def number(pattern, text):
        match = re.search(pattern + r"\s*(" + _NUMBER + r")", text, re.I)
        if not match:
            raise ValueError("ffmpeg did not return audio level measurements")
        value = float(match[1])
        return value if math.isfinite(value) else None

    integrated = number(r"\bI:", result.stderr.rsplit("Summary:", 1)[-1])
    rms = number(r"RMS level dB:", result.stderr.rsplit("Overall", 1)[-1])
    # ebur128 also uses -70 LUFS for an empty gate, including silence and very short clips.
    use_rms = duration < 0.4 or integrated is None or integrated <= -70
    level = rms if use_rms else integrated
    return {"duration_sec": duration, "integrated_lufs": integrated, "rms_dbfs": rms,
            "level_db": level, "method": "rms_dbfs" if use_rms else "integrated_lufs"}


def prepare_mix(run_dir, timeline: Timeline, *, style=None) -> dict:
    """Rewrite gains in place, and save an auditable mix report before Remotion runs.

    Missing narration or silent sources leave gains unchanged and are reported by QA.
    Repeated renders always recompute from source measurements, so gains do not accumulate.
    """
    run_dir = Path(run_dir)
    style = load_style() if style is None else style
    voices = sorted((a for a in timeline.audio if a.kind == "voice"), key=lambda a: a.start)
    voice = measure_level([run_dir / a.src for a in voices], [a.gain_db for a in voices]) if voices else None
    if voice is not None:
        voice["sources"] = [{"src": a.src, "start": a.start, "gain_db": a.gain_db} for a in voices]
    reference = voice["level_db"] if voice else None
    clips, measured = [], {}
    for audio in timeline.audio:
        if audio.kind == "voice":
            continue
        if audio.src not in measured:
            measured[audio.src] = measure_level([run_dir / audio.src])
        source = measured[audio.src]
        offset = style["bgm_below_voice_db" if audio.kind == "music" else "sfx_below_voice_db"]
        level = source["level_db"]
        if reference is not None and level is not None:
            audio.gain_db = reference - offset - level
        audio.duration = source["duration_sec"]
        if audio.kind == "music":
            audio.duck_under_voice = False
            audio.fade_in = style["bgm_fade_in_sec"]
            audio.fade_out = style["bgm_fade_out_sec"]
        output = level + audio.gain_db if level is not None else None
        clips.append({**source, "kind": audio.kind, "src": audio.src, "start": audio.start,
                      "target_below_voice_db": offset, "gain_db": audio.gain_db,
                      "output_level_db": output,
                      "offset_below_voice_db": reference - output if reference is not None and output is not None else None,
                      "fade_in": audio.fade_in, "fade_out": audio.fade_out,
                      "duck_under_voice": audio.duck_under_voice})
    report = {"schema_version": 1, "voice": voice, "clips": clips}
    path = run_dir / "render" / "mix.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report
