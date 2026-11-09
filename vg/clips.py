"""Licensed source sections and subtitle drafts on the output clock; no speech synthesis."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from . import library, media, reference
from .schemas import Asset, ClipPlan, LineTiming, Manifest, Script, Timings


def source_timings(script: Script, plan: ClipPlan) -> Timings:
    lines, previous = [], 0.0
    for line in script.lines:
        if (line.start is None or line.end is None
                or not previous <= line.start < line.end <= plan.total_sec + 1e-9):
            raise ValueError(f"{line.id}: source captions need start/end in output order, non-overlapping, "
                             f"inside 0..{plan.total_sec}s")
        lines.append(LineTiming(line_id=line.id, audio="", start=line.start, end=line.end))
        previous = line.end
    return Timings(sample_rate=48000, total_sec=plan.total_sec, lines=lines)


def map_subtitles(cues: list[dict], plan: ClipPlan) -> list[dict]:
    output, cursor = [], 0.0
    for segment in plan.segments:
        for cue in cues:
            start, end = max(cue["start"], segment.start), min(cue["end"], segment.end)
            if start < end:
                output.append({"start": round(cursor + start - segment.start, 6),
                               "end": round(cursor + end - segment.start, 6), "text": cue["text"]})
        cursor += segment.end - segment.start
    return sorted(output, key=lambda c: (c["start"], c["end"]))


def _subtitles(source: str, directory: Path) -> list[dict]:
    # Separate from section downloads: subtitles are requested only once per build, failures are optional.
    try:
        media.run(["uvx", "yt-dlp", "--no-playlist", "--no-progress", "--skip-download",
                   "--write-subs", "--write-auto-subs", "--sub-langs", "ja,ja-orig", "--sub-format", "vtt",
                   "-P", str(directory), "-o", "video.%(ext)s", source], env=media.scrubbed_env())
    except RuntimeError:
        pass
    return reference._subtitles(directory)[0]


def prepare(run_dir: Path) -> Timings:
    run_dir = Path(run_dir)
    plan, script = media.load(run_dir, "clips"), media.load(run_dir, "script")
    timings = source_timings(script, plan)  # fail before network or media work
    licenses = library.load_licenses()
    if plan.license_id not in licenses:
        raise ValueError(f"unknown licence {plan.license_id!r}")
    spec = licenses[plan.license_id]
    if not spec["commercial_ok"] or "video" not in spec["kinds"]:
        raise ValueError(f"licence {plan.license_id} must allow commercial video clips")
    url = plan.guideline or spec.get("url")
    if not url:
        raise ValueError(f"licence {plan.license_id} needs --guideline <URL> in clips.json guideline")
    # Registry URLs, like per-use guidelines, must be real HTTP(S) URLs.
    ClipPlan._url(url)
    meta = library._video_meta(plan.source)
    if meta["duration"] and any(s.end > meta["duration"] for s in plan.segments):
        raise ValueError("segment ends beyond the source duration")
    manifest = media.load(run_dir, "manifest") if (run_dir / "assets/manifest.json").is_file() else Manifest()
    keep = [a for a in manifest.assets if a.tool != "vg clips"]
    old = {a.id: a for a in manifest.assets if a.tool == "vg clips"}
    directory = run_dir / "clips"
    directory.mkdir(exist_ok=True)
    credit = (spec["credit"].format(title=meta["title"], url=meta["url"], channel=meta["channel"]).strip()
              if spec.get("credit") else None)
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        temporary = Path(tmp)
        cues = _subtitles(plan.source, temporary)
        for segment in plan.segments:
            length = segment.end - segment.start
            params = {"source": plan.source, "source_url": meta["url"], "source_title": meta["title"],
                      "source_channel": meta["channel"], "start": segment.start, "end": segment.end,
                      "license_id": plan.license_id, "credit_first": bool(spec.get("credit_first"))}
            relative = f"clips/{segment.id}.mp4"
            video, wav = run_dir / relative, directory / f"{segment.id}.wav"
            previous = old.get(segment.id)
            if not (previous and previous.params == params and video.is_file() and wav.is_file()):
                section_dir = temporary / segment.id
                section_dir.mkdir()
                media.run(["uvx", "yt-dlp", "--no-playlist", "--no-progress",
                           "--ffmpeg-location", str(Path(media.FFMPEG).parent),
                           "-f", "bv*[height<=1920]+ba/b", "--merge-output-format", "mp4",
                           "--download-sections", f"*{segment.start:.6f}-{segment.end:.6f}",
                           "--force-keyframes-at-cuts", "-P", str(section_dir), "-o", "section.%(ext)s",
                           plan.source], env=media.scrubbed_env())
                sources = [p for p in sorted(section_dir.glob("section.*"))
                           if p.suffix in (".mp4", ".mkv", ".webm", ".mov")]
                if not sources:
                    raise FileNotFoundError("yt-dlp did not produce the section")
                # yt-dlp force-keyframes-at-cuts makes the downloaded section start at the requested time.
                # Re-encode to the exact requested length and a renderer-compatible codec/pixel format.
                incoming = section_dir / "exact.mp4"
                media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                           "-i", str(sources[0]), "-t", f"{length:.6f}", "-map", "0:v:0", "-map", "0:a:0",
                           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart", str(incoming)])
                audio = section_dir / "exact.wav"
                media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                           "-i", str(incoming), "-t", f"{length:.6f}", "-vn", "-ar", "48000", "-ac", "1",
                           "-c:a", "pcm_s16le", str(audio)])
                incoming.replace(video)
                audio.replace(wav)
            if any(a.id == segment.id for a in keep):
                raise ValueError(f"segment id {segment.id!r} collides with another manifest asset")
            keep.append(Asset(id=segment.id, path=relative, kind="video", origin="library", tool="vg clips",
                              params=params, license=spec["name"], license_url=url, commercial_ok=True, credit=credit))
    (directory / "source_subs.json").write_text(
        json.dumps(map_subtitles(cues, plan), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest.assets = keep
    media.save(run_dir, "manifest", manifest)
    media.save(run_dir, "timings", timings)
    return timings
