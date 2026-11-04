"""Deterministic media and caption checks. Blocking failures prevent packaging."""
from __future__ import annotations

from fractions import Fraction
import math
from pathlib import Path
import re
import unicodedata

from . import registry
from .media import FFMPEG, load, probe, run, save
from .schemas import Check, QAReport, Timeline

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|inf|nan)"


def _final(run_dir):
    return Path(run_dir) / "final.mp4"


def _duration(info):
    return float(info["format"]["duration"])


def check_format(run_dir, info=None) -> Check:
    info = probe(_final(run_dir)) if info is None else info
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    v = videos[0] if videos else {}
    a = audios[0] if audios else {}
    try:
        fps = float(Fraction(v.get("avg_frame_rate", "0")))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    passed = (len(videos) == 1 and v.get("width") == 1080 and v.get("height") == 1920
              and abs(fps - 30) < 0.01 and v.get("codec_name") == "h264"
              and v.get("pix_fmt") == "yuv420p" and len(audios) == 1
              and a.get("codec_name") == "aac" and str(a.get("sample_rate")) == "48000")
    return Check(id="format", passed=passed, redo_stage="render",
                 detail=f"video={len(videos)}, {v.get('width', 0)}x{v.get('height', 0)}, {fps:.3f} fps, "
                        f"{v.get('codec_name')}/{v.get('pix_fmt')}; audio={len(audios)}, "
                        f"{a.get('codec_name')}, {a.get('sample_rate', 0)} Hz")


def check_duration(run_dir, info=None) -> Check:
    duration = _duration(probe(_final(run_dir)) if info is None else info)
    brief = load(run_dir, "brief")
    if (Path(run_dir) / "route.json").exists():
        spec = registry.load_types()[load(run_dir, "route").type_id]
        limits = [(p, spec.length[p].min, spec.length[p].max) for p in brief.platforms]
    else:
        limits = [(p, 0, brief.max_sec) for p in brief.platforms]
    passed = math.isfinite(duration) and all(lo <= duration <= hi for _, lo, hi in limits)
    return Check(id="duration", passed=passed, redo_stage="script",
                 detail=f"duration={duration:.3f}s; limits=" + ", ".join(
                     f"{p}:{lo:g}..{hi:g}s" for p, lo, hi in limits))


def measure_loudness(path):
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af",
                  "ebur128=peak=true", "-f", "null", "-"])
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    integrated = re.search(r"\bI:\s*(" + _NUMBER + r")\s*LUFS", summary, re.I)
    peak = re.search(r"\bPeak:\s*(" + _NUMBER + r")\s*dBFS", summary, re.I)
    if not integrated or not peak:
        raise ValueError("ffmpeg did not return ebur128 integrated loudness and true peak")
    return float(integrated[1]), float(peak[1])


def check_loudness(run_dir) -> Check:
    integrated, peak = measure_loudness(_final(run_dir))
    return Check(id="loudness", passed=math.isfinite(integrated) and math.isfinite(peak)
                 and abs(integrated + 14) <= 1.5 and peak <= -1.0, redo_stage="render",
                 detail=f"integrated={integrated:.2f} LUFS (target -14 ±1.5); true peak={peak:.2f} dBTP (max -1.0)")


def check_black_frames(run_dir, info=None) -> Check:
    duration = _duration(probe(_final(run_dir)) if info is None else info)
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(_final(run_dir)),
                  "-an", "-vf", "blackdetect=d=0.4:pix_th=0.10", "-f", "null", "-"])
    segments = [tuple(map(float, m)) for m in re.findall(
        r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", result.stderr)]
    bad = [(s, e, d) for s, e, d in segments if d >= 0.4 - 1e-6 and e < duration - 0.5]
    return Check(id="black_frames", passed=not bad, redo_stage="assets",
                 detail=f"duration={duration:.3f}s; black segments={len(segments)}, violations={len(bad)}; "
                        + ", ".join(f"{s:.3f}..{e:.3f}s ({d:.3f}s)" for s, e, d in segments))


def check_silence(run_dir, info=None) -> Check:
    duration = _duration(probe(_final(run_dir)) if info is None else info)
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(_final(run_dir)), "-vn",
                  "-af", "silencedetect=noise=-45dB:d=1.5", "-f", "null", "-"])
    segments = []
    start = None
    for kind, value in re.findall(r"silence_(start|end):\s*([\d.]+)", result.stderr):
        if kind == "start":
            start = float(value)
        elif start is not None:
            segments.append((start, float(value)))
            start = None
    if start is not None:
        segments.append((start, duration))
    trimmed = [(max(s, 0.3), min(e, duration - 0.3)) for s, e in segments]
    bad = [(s, e) for s, e in trimmed if e - s >= 1.5 - 1e-6]
    return Check(id="silence", passed=not bad, severity="warn", redo_stage="tts",
                 detail=f"threshold=-45 dB, min=1.5s, ignored edges=0.3s; violations={len(bad)}; "
                        + ", ".join(f"{s:.3f}..{e:.3f}s ({e-s:.3f}s)" for s, e in bad))


def _timeline(value):
    return value if isinstance(value, Timeline) else load(value, "timeline")


def check_safe_area(timeline) -> Check:
    timeline = _timeline(timeline)
    widths = []
    violations = 0
    max_lines = 0
    for caption in timeline.captions:
        lines = caption.text.split("\n")
        max_lines = max(max_lines, len(lines))
        font_px = 84 if caption.style == "title" else 66
        measured = [font_px * sum(0.55 if ord(ch) < 128 or unicodedata.east_asian_width(ch) == "H"
                                 else 1.0 for ch in line) for line in lines]
        widths.extend(measured)
        violations += int(len(lines) > 2 or any(width > 780 for width in measured))
    return Check(id="safe_area", passed=violations == 0, redo_stage="timeline",
                 detail=f"captions={len(timeline.captions)}, max lines={max_lines}/2, "
                        f"max width={max(widths, default=0):.2f}/780 px, violations={violations}")


def check_caption_timing(timeline, duration=None) -> Check:
    timeline = _timeline(timeline)
    duration = timeline.duration if duration is None else duration
    captions = sorted(timeline.captions, key=lambda c: (c.start, c.end))
    short = sum(c.end - c.start < 0.6 - 1e-6 for c in captions)
    outside = sum(not (0 <= c.start < c.end <= duration + 1e-6) for c in captions)
    overlaps = 0
    latest = -math.inf
    for caption in captions:
        overlaps += int(caption.start < latest - 1e-6)
        latest = max(latest, caption.end)
    return Check(id="caption_timing", passed=not (short or outside or overlaps), redo_stage="timeline",
                 detail=f"captions={len(captions)}, min display={min((c.end-c.start for c in captions), default=0):.3f}s "
                        f"(required 0.6s), overlaps={overlaps}, outside 0..{duration:.3f}s={outside}")


def run_checks(run_dir) -> list[Check]:
    info = probe(_final(run_dir))
    return [check_format(run_dir, info), check_duration(run_dir, info), check_loudness(run_dir),
            check_black_frames(run_dir, info), check_silence(run_dir, info), check_safe_area(run_dir),
            check_caption_timing(run_dir, _duration(info))]


def write_report(run_dir, checks) -> QAReport:
    path = Path(run_dir) / "qa.json"
    previous = load(run_dir, "qa") if path.exists() else None
    attempt = previous.attempt if previous else 1
    final = _final(run_dir)
    if previous and final.exists() and final.stat().st_mtime_ns > path.stat().st_mtime_ns:
        attempt += 1
    merged = {c.id: c for c in previous.checks} if previous else {}
    for check in checks:
        check = Check.model_validate(check)
        merged[check.id] = check
    report = QAReport(attempt=attempt, checks=list(merged.values()))
    save(run_dir, "qa", report)
    return report


def qa(run_dir) -> QAReport:
    return write_report(run_dir, run_checks(run_dir))
