"""Deterministic media and caption checks. Blocking failures prevent packaging."""
from __future__ import annotations

from fractions import Fraction
import json
import math
from pathlib import Path
import re

from . import registry
from .media import FFMPEG, load, probe, run, save
from .schemas import Check, QAReport, Timeline
from .analyze import analyze_run, scene_changes, interval_stats
from .disclosure import realistic_assets
from . import run as runs
from .captions import band_width, caption_box, em

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


def check_mix(run_dir, style=None) -> Check:
    """Validate measured source levels plus applied gains against the voice reference."""
    style = registry.load_style() if style is None else style
    audio = [a for a in load(run_dir, "timeline").audio if a.kind != "voice"]
    path = Path(run_dir) / "render" / "mix.json"
    passed, detail = True, "no music or SFX"
    if audio:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            voice = report["voice"]["level_db"]
            if not isinstance(voice, (int, float)) or not math.isfinite(voice):
                raise ValueError("voice level unavailable")
            entries = report["clips"]
            if len(entries) != len(audio):
                raise ValueError("mix entries do not match timeline")
            details = []
            for clip, entry in zip(audio, entries):
                if (clip.kind, clip.src, clip.start, clip.gain_db) != (
                        entry["kind"], entry["src"], entry["start"], entry["gain_db"]):
                    raise ValueError("mix entries do not match timeline")
                level = entry["level_db"]
                if not isinstance(level, (int, float)) or not math.isfinite(level):
                    raise ValueError(f"{clip.kind} level unavailable")
                offset = voice - (level + clip.gain_db)
                target = style["bgm_below_voice_db" if clip.kind == "music" else "sfx_below_voice_db"]
                tolerance = 2 if clip.kind == "music" else 3
                passed &= math.isfinite(offset) and abs(offset - target) <= tolerance + 1e-6
                if clip.kind == "music":
                    passed &= not clip.duck_under_voice
                details.append(f"{clip.kind} {clip.src}: {offset:.2f} dB below voice (target {target:g} ±{tolerance})")
            detail = "; ".join(details)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            passed, detail = False, f"mix unavailable/invalid ({exc}); rerender to measure relative audio levels"
    return Check(id="mix", passed=bool(passed), severity="warn", redo_stage="render", detail=detail)


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
        font_px, limit = caption_box(caption.style, timeline.layout)
        measured = [font_px * em(line, caption.emphasis) for line in lines]
        widths.extend(measured)
        violations += int(len(lines) > 2 or any(width > limit for width in measured))
    notes = []
    for name in ("header", "footer"):
        band = getattr(timeline.layout, name) if timeline.layout else None
        if band is None or not (band.text or "").strip():
            continue
        # Band text: at most two lines inside the 780 px safe width (outline included) and the band's height.
        rows = band.text.split("\n")
        outline = band.size * 0.16 if band.stroke else 0
        widest = max(band_width(row, band.size) for row in rows) + outline
        bad = len(rows) > 2 or widest > 780 or len(rows) * band.size * 1.25 > band.height
        violations += int(bad)
        notes.append(f"{name}={len(rows)} line(s), width {widest:.0f}/780 px{' VIOLATION' if bad else ''}")
    return Check(id="safe_area", passed=violations == 0, redo_stage="timeline",
                 detail=f"captions={len(timeline.captions)}, max lines={max_lines}/2, "
                        f"max width={max(widths, default=0):.2f}/{caption_box('default', timeline.layout)[1]:.0f} px, "
                        f"bands={', '.join(notes) or 'none'}, violations={violations}")


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


def check_hook(metrics, style) -> Check:
    text, speech = metrics["first_text_sec"], metrics["first_speech_sec"]
    return Check(id="hook", passed=text is not None and speech is not None
                 and 0 <= text <= style["first_text_max_sec"] and 0 <= speech <= style["first_speech_max_sec"],
                 redo_stage="storyboard", detail=f"first text={text}s (max {style['first_text_max_sec']:g}s); "
                 f"first speech={speech}s (max {style['first_speech_max_sec']:g}s)")


def check_tempo(run_dir, metrics, style) -> Check:
    longest = metrics["visual_intervals"]["max_sec"]
    try:
        changes = scene_changes(_final(run_dir))
        detected = interval_stats(changes, metrics["duration"])
        timestamps = ",".join(f"{t:.3f}" for t in changes)
        secondary = (f"scene threshold=0.30, changes={len(changes)}, timestamps=[{timestamps}]s, "
                     f"longest={detected['max_sec']:.3f}s")
    except (RuntimeError, ValueError, OSError) as exc:
        # Pixel detection is supplementary and cannot change a specification-based decision.
        secondary = f"scene detection unavailable ({type(exc).__name__})"
    return Check(id="tempo", passed=longest <= style["max_static_sec"] + 1e-6,
                 redo_stage="storyboard", detail=f"spec longest hold={longest:.3f}s "
                 f"(max {style['max_static_sec']:g}s); secondary: {secondary}")


def check_speech_rate(metrics, style) -> Check:
    rate = metrics["speech_cps"]
    return Check(id="speech_rate", passed=rate >= style["min_speech_cps"], severity="warn", redo_stage="tts",
                 detail=f"display characters={metrics['speech_characters']}, speech={metrics['speech_seconds']:.3f}s; "
                 f"rate={rate:.3f} cps (min {style['min_speech_cps']:g})")


def check_caption_coverage(metrics, style) -> Check:
    coverage = metrics["caption_coverage"]
    return Check(id="caption_coverage", passed=coverage >= style["caption_coverage_min"] - 1e-9,
                 severity="warn", redo_stage="timeline", detail=f"caption/title coverage={coverage:.4f} "
                 f"(min {style['caption_coverage_min']:g}); duration={metrics['duration']:.3f}s")


def _run_parent(path: Path) -> Path:
    return path.parent.parent if path.parent.name == "variants" else path


def _trigrams(text: str) -> set[str]:
    # Preserve a meaningful comparison for identical scripts shorter than three characters.
    return {text[i:i+3] for i in range(len(text)-2)} if len(text) >= 3 else {text} if text else set()


def check_originality(run_dir, *, runs_root: Path | None = None, style=None) -> Check:
    style = registry.load_style() if style is None else style
    root = runs.RUNS if runs_root is None else Path(runs_root)
    run_dir = Path(run_dir).resolve()
    current = _trigrams("".join(line.text for line in load(run_dir, "script").lines))
    highest, compared = 0.0, 0
    for path in sorted([*root.glob("*/script.json"), *root.glob("*/variants/*/script.json")]):
        if _run_parent(path.parent.resolve()) == _run_parent(run_dir):
            continue
        if _run_parent(path.parent.resolve()).name.endswith("-smoke"):
            continue  # fixed smoke-test fixture, identical on purpose
        other = _trigrams("".join(line.text for line in load(path.parent, "script").lines))
        union = current | other
        similarity = len(current & other) / len(union) if union else 0.0
        highest = max(highest, similarity)
        compared += 1
    return Check(id="originality", passed=highest <= style["originality_max_similarity"], redo_stage="script",
                 detail=f"max character-3-gram Jaccard={highest:.4f} "
                 f"(max {style['originality_max_similarity']:g}); compared scripts={compared}")


def check_disclosure(run_dir) -> Check:
    ids = realistic_assets(load(run_dir, "manifest"))
    exists = (Path(run_dir) / "publish.json").is_file()
    flag = load(run_dir, "publish").synthetic_media if exists else None
    # Packaging creates this flag automatically. Only an existing hand-made false flag blocks QA.
    return Check(id="disclosure", passed=not ids or flag is not False, redo_stage="package",
                 detail=f"realistic image/video assets={len(ids)}; synthetic_media={flag}; "
                 + ("publish present" if exists else f"publish pending; package sets synthetic_media={bool(ids)}"))


def check_metadata(run_dir) -> Check:
    if not (Path(run_dir) / "publish.json").is_file():
        return Check(id="metadata", passed=True, redo_stage="package",
                     detail="publish pending; metadata is checked after packaging")
    publish = load(run_dir, "publish")
    title, description, tags = len(publish.title), len(publish.description), len(publish.hashtags)
    query_present = not publish.search_query or publish.search_query in publish.description
    return Check(id="metadata", passed=title <= 100 and description <= 5000
                 and 3 <= tags <= 5 and query_present, redo_stage="package",
                 detail=f"title={title}/100; description={description}/5000; hashtags={tags} (3..5); "
                        f"search_query present={bool(query_present)}")


def check_yomi(run_dir) -> Check:
    run_dir = Path(run_dir)
    path, script = run_dir / "yomi_check.json", run_dir / "script.json"
    passed = False
    if not path.is_file():
        detail = "missing yomi_check.json; run `vg yomi RUN`, fix readings, rerun `vg tts RUN`, then `vg yomi RUN --approve`"
    elif not script.is_file():
        detail = "missing script.json; write the script and run `vg yomi RUN`"
    else:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            reviewed = isinstance(report, dict) and report.get("reviewed") is True
            fresh = path.stat().st_mtime_ns > script.stat().st_mtime_ns
            passed = reviewed and fresh
            detail = f"reviewed={reviewed}; newer than script={fresh}"
            if not passed:
                detail += "; run `vg yomi RUN`, fix readings, rerun tts, then `vg yomi RUN --approve`"
        except (ValueError, OSError) as exc:
            detail = f"invalid yomi_check.json ({type(exc).__name__}); run `vg yomi RUN`"
    return Check(id="yomi", passed=passed, severity="block", redo_stage="script", detail=detail)


def check_sfx_density(timeline, style=None) -> Check:
    style = registry.load_style() if style is None else style
    times = sorted(a.start for a in _timeline(timeline).audio if a.kind == "sfx")
    left, highest = 0, 0
    for right, t in enumerate(times):
        # Half-open windows [start, start+10): effects exactly 10s apart do not coincide.
        while t - times[left] >= 10:
            left += 1
        highest = max(highest, right - left + 1)
    return Check(id="sfx_density", passed=highest <= style["sfx_max_per_10s"], severity="warn",
                 redo_stage="storyboard", detail=f"effects={len(times)}; max starts per 10s={highest} "
                 f"(max {style['sfx_max_per_10s']})")


def check_english(run_dir) -> list[Check]:
    from . import english
    from .mix import measure_level
    from .render import _timestamp

    directory = Path(run_dir)
    if not (directory / "en.json").is_file():
        return [Check(id="english", passed=False, severity="warn", redo_stage="en",
                      detail="en.json missing; English localisation is optional")]
    checks = []
    try:
        spec = english.validate(directory)
        duration = english.audio_duration(directory / "final.mp4")
        ordered = english.ordered_timings(load(directory, "script"), load(directory, "timings"), duration)
        checks.append(Check(id="en_schema", passed=True, redo_stage="en", detail="coverage and sources/credits valid"))
    except (OSError, ValueError, KeyError, StopIteration, RuntimeError) as exc:
        return [Check(id="en_schema", passed=False, redo_stage="en", detail=str(exc))]

    def check(name, action):
        try:
            detail = action()
            checks.append(Check(id=name, passed=True, redo_stage="en", detail=detail))
        except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError) as exc:
            checks.append(Check(id=name, passed=False, redo_stage="en", detail=str(exc)))

    def subtitles():
        cues = english.captions(spec, ordered)
        expected = "".join(f"{i}\n{_timestamp(c.start)} --> {_timestamp(c.end)}\n{c.text}\n\n"
                           for i, c in enumerate(cues, 1))
        actual = (directory / "final_en.srt").read_text(encoding="utf-8")
        if actual != expected:
            raise ValueError("invalid/stale English SRT; must cover all lines at Japanese line times")
        if any(not 0 <= c.start < c.end <= duration + 0.001 for c in cues):
            raise ValueError("English SRT outside video duration")
        return f"{len(cues)} cues; <=42 chars, <=2 rows, Japanese line times, within {duration:.3f}s"

    def dub_duration():
        path = directory / "dub_en.m4a"
        actual = english.audio_duration(path)
        audio = next(s for s in probe(path)["streams"] if s["codec_type"] == "audio")
        if abs(actual-duration) > 0.05 or audio["codec_name"] != "aac" or str(audio["sample_rate"]) != "48000":
            raise ValueError(f"English dub must be AAC 48 kHz and within 50 ms: {actual:.3f}s vs {duration:.3f}s")
        return f"AAC 48 kHz; dub={actual:.3f}s, Japanese={duration:.3f}s"

    def loudness():
        ja = measure_level([directory / "final.mp4"])["integrated_lufs"]
        en = measure_level([directory / "dub_en.m4a"])["integrated_lufs"]
        if ja is None or en is None or abs(ja-en) > 1:
            raise ValueError(f"English/Japanese loudness must differ by <=1 LU: en={en}, ja={ja}")
        return f"en={en:.2f}, ja={ja:.2f} LUFS"

    def speech():
        fitted = load(directory / "en", "timings")
        by_id = {t.line_id: t for t in fitted.lines}
        if len(by_id) != len(fitted.lines) or set(by_id) != {t.line_id for t in ordered}:
            raise ValueError("English fitted timings must cover every line exactly once")
        texts = {line.id: line.text for line in spec.lines}
        rates = []
        for i, timing in enumerate(ordered):
            fit = by_id[timing.line_id]
            actual = english.audio_duration(directory / fit.audio)
            end = ordered[i+1].start if i+1 < len(ordered) else duration
            if (abs(fit.start-timing.start) > 0.001 or fit.end > end+0.001
                    or abs(fit.end-fit.start-actual) > 0.001):
                raise ValueError(f"{timing.line_id}: invalid English fitted timing/slot")
            rate = english.word_rate(texts[timing.line_id], actual)
            rates.append((timing.line_id, rate))
        detail = "; ".join(f"{lid}: {rate:.3f} words/sec" for lid, rate in rates)
        if any(rate > 3.3+1e-9 for _, rate in rates):
            raise ValueError("shorten English line (>3.3 words/sec): " + detail)
        checks.append(Check(id="en_speech_rate_warning", passed=all(rate <= 3.0 for _, rate in rates),
                            severity="warn", redo_stage="en", detail=detail + " (warning above 3.0)"))
        return detail + " (max 3.3)"

    def fresh():
        marker = json.loads((directory / "en/build.json").read_text())
        voice = registry.load_voices()[marker["voice_id"]]
        if marker["fingerprint"] != english.fingerprint(directory, voice):
            raise ValueError("English outputs stale; rerun vg en after input changes")
        return "English outputs match current inputs"

    check("en_srt", subtitles)
    check("en_dub_duration", dub_duration)
    check("en_loudness", loudness)
    check("en_speech_rate", speech)
    check("en_fresh", fresh)
    return checks


def run_checks(run_dir, *, runs_root: Path | None = None) -> list[Check]:
    info = probe(_final(run_dir))
    style = registry.load_style()
    metrics = analyze_run(run_dir)
    return [check_format(run_dir, info), check_duration(run_dir, info), check_loudness(run_dir), check_mix(run_dir, style),
            check_black_frames(run_dir, info), check_silence(run_dir, info), check_safe_area(run_dir),
            check_caption_timing(run_dir, _duration(info)), check_hook(metrics, style),
            check_tempo(run_dir, metrics, style), check_speech_rate(metrics, style),
            check_caption_coverage(metrics, style), check_originality(run_dir, runs_root=runs_root, style=style),
            check_disclosure(run_dir), check_metadata(run_dir), check_sfx_density(run_dir, style), check_yomi(run_dir),
            *check_english(run_dir)]


# Checks the human may waive for a variant they chose (taste/tempo); safety and rights checks never.
WAIVABLE = {"tempo", "speech_rate", "caption_coverage", "sfx_density", "mix", "silence", "duration"}


def load_waivers(run_dir) -> dict[str, dict]:
    """RUN[:vN]/qa_waivers.json: {"<check id>": {"reason": ..., "by": "user", "date": ...}} written only on
    the user's explicit decision (e.g. 「このv2のままでよい」)."""
    path = Path(run_dir) / "qa_waivers.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    bad = sorted(set(data) - WAIVABLE)
    if bad:
        raise ValueError(f"qa_waivers.json: checks {bad} cannot be waived")
    return data


def write_report(run_dir, checks) -> QAReport:
    checks = list(checks)
    path = Path(run_dir) / "qa.json"
    previous = load(run_dir, "qa") if path.exists() else None
    attempt = previous.attempt if previous else 1
    final = _final(run_dir)
    if previous and final.exists() and final.stat().st_mtime_ns > path.stat().st_mtime_ns:
        attempt += 1
    merged = {c.id: c for c in previous.checks} if previous else {}
    if any(c.id in ("english", "en_schema") for c in checks):
        merged = {key: value for key, value in merged.items() if key != "english" and not key.startswith("en_")}
    waivers = load_waivers(run_dir)
    for check in checks:
        check = Check.model_validate(check)
        if not check.passed and check.id in waivers:
            w = waivers[check.id]
            check = check.model_copy(update={"passed": True, "detail": f"WAIVED by {w.get('by', 'user')} "
                                             f"({w.get('date', '?')}: {w.get('reason', '')}); {check.detail}"})
        merged[check.id] = check
    report = QAReport(attempt=attempt, checks=list(merged.values()))
    save(run_dir, "qa", report)
    return report


def qa(run_dir) -> QAReport:
    return write_report(run_dir, run_checks(run_dir))
