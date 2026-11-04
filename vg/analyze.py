"""Offline measurements of arbitrary media and the harness's timeline specifications."""
from __future__ import annotations

from fractions import Fraction
import math
from pathlib import Path
import re
from statistics import median

from .media import FFMPEG, load, probe, run

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|inf|nan)"
TITLE_COMPONENTS = {"title_card", "hook_title", "end_card"}


def interval_stats(changes: list[float], duration: float) -> dict:
    """Include leading/trailing holds. p90 uses the nearest-rank definition."""
    boundaries = sorted({0.0, duration, *(t for t in changes if 0 < t < duration)})
    intervals = [b - a for a, b in zip(boundaries, boundaries[1:])]
    ordered = sorted(intervals)
    return {"intervals_sec": intervals, "median_sec": median(intervals) if intervals else 0.0,
            "p90_sec": ordered[math.ceil(0.9 * len(ordered)) - 1] if ordered else 0.0,
            "max_sec": max(intervals, default=0.0)}


def scene_changes(path) -> list[float]:
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(path), "-an", "-vf",
                  "select='gt(scene,0.30)',showinfo", "-fps_mode", "vfr", "-f", "null", "-"])
    return sorted({float(t) for t in re.findall(r"\bpts_time:([-+\d.eE]+)", result.stderr)})


def measure_loudness(path) -> dict:
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af",
                  "ebur128=peak=true", "-f", "null", "-"])
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    values = {}
    for key, label, unit in [("integrated_lufs", "I", "LUFS"), ("lra_lu", "LRA", "LU"),
                             ("true_peak_dbtp", "Peak", "dBFS")]:
        match = re.search(r"\b" + label + r":\s*(" + NUMBER + r")\s*" + unit, summary, re.I)
        if not match:
            raise ValueError(f"ffmpeg did not return ebur128 {label}")
        number = float(match[1])
        # Silence can return -inf. Keep JSON standards compliant while reporting unavailable values.
        values[key] = number if math.isfinite(number) else None
    return values


def covered_seconds(spans, duration: float) -> float:
    """Union of clipped spans; overlapping captions/titles count only once."""
    total, latest = 0.0, 0.0
    for start, end in sorted((max(0.0, s), min(duration, e)) for s, e in spans):
        total += max(0.0, end - max(start, latest))
        latest = max(latest, end)
    return total


def audio_activity(path, duration: float) -> float:
    result = run([str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af",
                  "aresample=48000:async=1:first_pts=0,"
                  f"apad=whole_dur={duration:.9f},atrim=duration={duration:.9f},"
                  "silencedetect=noise=-35dB:d=0.01", "-f", "null", "-"])
    spans, start = [], None
    for kind, value in re.findall(r"silence_(start|end):\s*([-+\d.eE]+)", result.stderr):
        if kind == "start":
            start = float(value)
        elif start is not None:
            spans.append((start, float(value)))
            start = None
    if start is not None:
        spans.append((start, duration))
    return max(0.0, min(1.0, 1 - covered_seconds(spans, duration) / duration))


def contact_sheet(path, output: Path, duration: float) -> Path:
    """Six evenly spaced samples, including repeated samples for very short clips."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pad before resampling to guarantee all six cells even for a one-frame source.
    run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(path), "-an", "-vf",
         f"tpad=stop_mode=clone:stop_duration={duration:.9f},"
         f"fps={6 / duration:.12f}:start_time=0:round=down,scale=360:-1,tile=3x2",
         "-frames:v", "1", "-q:v", "4", str(output)])
    return output


def analyze(video, *, sheet_path: Path | None = None) -> dict:
    video = Path(video)
    info = probe(video)
    streams = [s for s in info["streams"] if s["codec_type"] == "video"]
    if not streams:
        raise ValueError("analysis requires a video stream")
    v = streams[0]
    duration = float(info["format"].get("duration", v.get("duration", 0)))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("analysis requires a finite positive duration")
    try:
        fps = float(Fraction(v.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    changes = scene_changes(video)
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    sheet_path = sheet_path if sheet_path is not None else video.with_name(video.stem + ".contact.jpg")
    return {"duration": duration, "width": v["width"], "height": v["height"], "fps": fps,
            "scene_changes": changes, "first_change_sec": next((t for t in changes if t > 0), None),
            "visual_intervals": interval_stats(changes, duration),
            "loudness": measure_loudness(video) if has_audio else None,
            "audio_above_minus35_share": audio_activity(video, duration) if has_audio else 0.0,
            "contact_sheet": str(contact_sheet(video, sheet_path, duration))}


def display_characters(text: str) -> int:
    return sum(not c.isspace() for c in text)


def analyze_run(run_dir) -> dict:
    """Read specifications only; motion itself does not substitute for an explicit cut."""
    timeline, timings, script = (load(run_dir, name) for name in ("timeline", "timings", "script"))
    changes = {clip.start for clip in timeline.clips}
    for clip in timeline.clips:
        cut = clip.props.get("cut_sec", 0)
        if (clip.component == "ken_burns_image" and not isinstance(cut, bool)
                and isinstance(cut, (int, float)) and math.isfinite(cut) and cut > 0):
            # The renderer rounds cut_sec to frames before applying internal cuts.
            frames = math.floor(cut * timeline.fps + 0.5)
            if frames > 0:
                for offset in range(frames, math.ceil((clip.end - clip.start) * timeline.fps), frames):
                    t = clip.start + offset / timeline.fps
                    if t < clip.end - 1e-6:
                        changes.add(t)
    text_spans = [(c.start, c.end) for c in timeline.captions if c.text.strip()]
    text_spans.extend((c.start, c.end) for c in timeline.clips
                      if c.component in TITLE_COMPONENTS and isinstance(c.props.get("text"), str)
                      and c.props["text"].strip())
    speech = [t for t in timings.lines if t.end > t.start]
    seconds = sum(t.end - t.start for t in speech)
    characters = sum(display_characters(line.text) for line in script.lines)
    caption_lengths = [display_characters(c.text) for c in timeline.captions]
    changes = sorted(changes)
    return {"duration": timeline.duration, "visual_change_times": changes,
            "first_change_sec": next((t for t in changes if t > 0), None),
            "visual_intervals": interval_stats(changes, timeline.duration),
            "first_text_sec": min((s for s, _ in text_spans), default=None),
            "first_speech_sec": min((t.start for t in speech), default=None),
            "caption_coverage": covered_seconds(text_spans, timeline.duration) / timeline.duration,
            "speech_characters": characters, "speech_seconds": seconds,
            "speech_cps": characters / seconds if seconds else 0.0,
            "mean_caption_characters": sum(caption_lengths) / len(caption_lengths) if caption_lengths else 0.0}


def japanese_summary(result: dict) -> str:
    def seconds(value):
        return "なし" if value is None else f"{value:.2f}秒"
    loudness = result["loudness"]
    audio = "音声なし" if loudness is None else (
        f"音量 {loudness['integrated_lufs']} LUFS / LRA {loudness['lra_lu']} LU / "
        f"真のピーク {loudness['true_peak_dbtp']} dBTP")
    gaps = result["visual_intervals"]
    return (f"尺 {result['duration']:.2f}秒、{result['width']}×{result['height']}、{result['fps']:.2f} fps\n"
            f"画面変化 {len(result['scene_changes'])}回、最初 {seconds(result['first_change_sec'])}、"
            f"間隔 中央値 {gaps['median_sec']:.2f} / p90 {gaps['p90_sec']:.2f} / 最大 {gaps['max_sec']:.2f}秒\n"
            f"{audio}、−35 dBFS超の割合 {result['audio_above_minus35_share']:.1%}\n"
            f"コンタクトシート: {result['contact_sheet']}")
