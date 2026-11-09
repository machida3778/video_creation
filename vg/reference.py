"""Reference videos -> reusable formats.

`vg reference add SOURCE --id ID` stores one video the user liked (URL via yt-dlp, or a local file) under
the ignored local/formats/<id>/reference/ and measures what can be measured: shot boundaries and lengths,
loudness, audio activity, subtitles (when the platform has them) and the narration rate. It also writes
frame sheets for an agent to read the layout from: the first three seconds every 0.5 s, the first frame of
each shot, an even sample of the whole video, and three full-size frames.

The agent then writes local/formats/<id>/format.json (FormatProfile in vg/schemas.py). `vg format preview`
renders the format's layout as a still beside a reference frame so it can be compared and corrected.
Only presentation and structure are reused; scripts, images and audio of the reference never enter a run.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from .analyze import audio_activity, interval_stats, measure_loudness, scene_changes
from .media import FFMPEG, ROOT, probe, run, scrubbed_env
from .schemas import FormatProfile, Layout, Timeline

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
CELL_W = 270


def formats_dir() -> Path:
    return Path(os.environ.get("VG_FORMATS_DIR", ROOT / "local" / "formats"))


def format_dir(format_id: str) -> Path:
    if not ID_PATTERN.match(format_id):
        raise ValueError(f"format id must match {ID_PATTERN.pattern}: {format_id!r}")
    return formats_dir() / format_id


# ---------------------------------------------------------------- fetching
def _is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source))


def download(url: str, target: Path) -> dict:
    """Video (<=1080x1920), Japanese subtitles if any and the info JSON. yt-dlp runs through uvx so the
    project environment needs no extra dependency; subtitle failures (rate limits) are not fatal."""
    target.mkdir(parents=True, exist_ok=True)
    cmd = ["uvx", "yt-dlp", "--ignore-errors", "--no-progress", "--no-playlist",
           "--ffmpeg-location", str(Path(FFMPEG).parent),
           "-f", "bv*[height<=1920]+ba/b", "--merge-output-format", "mp4",
           "--write-subs", "--write-auto-subs", "--sub-langs", "ja,ja-orig", "--sub-format", "vtt",
           "--write-info-json", "-P", str(target), "-o", "video.%(ext)s", url]
    try:
        run(cmd, env=scrubbed_env())
    except RuntimeError:
        if not (target / "video.mp4").is_file():
            raise
    video = target / "video.mp4"
    if not video.is_file():
        raise FileNotFoundError(f"yt-dlp did not produce {video}")
    info_path = target / "video.info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.is_file() else {}
    # The info JSON lists every format and thumbnail; keep only what describes the video.
    keep = ("id", "title", "channel", "uploader", "duration", "view_count", "like_count", "comment_count",
            "upload_date", "description", "tags", "webpage_url")
    slim = {key: info.get(key) for key in keep if info.get(key) is not None}
    (target / "info.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    info_path.unlink(missing_ok=True)
    return slim


# ---------------------------------------------------------------- subtitles
TIME = r"(\d+):(\d\d):(\d\d)\.(\d{3})"


def _seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_vtt(text: str) -> list[dict]:
    """Cues as [{start, end, text}]; tags removed, and the rolling repeats of automatic subtitles dropped
    (each cue keeps only the text not already shown by the previous cue)."""
    cues, previous = [], set()
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        match = re.search(TIME + r"\s*-->\s*" + TIME, block)
        if not match:
            continue
        body = block[match.end():].split("\n", 1)[1] if "\n" in block[match.end():] else ""
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"\[[^\]]*\]|［[^］]*］", "", body)  # [音楽] [拍手] ... are not narration
        shown = [row.strip() for row in body.split("\n") if row.strip()]
        rows = [row for row in shown if row not in previous]
        previous = set(shown)  # compare with everything the previous cue showed, not only its new rows
        if not rows:
            continue
        line = "".join(rows) if all(re.search(r"[぀-ヿ一-鿿]", r) for r in rows) else " ".join(rows)
        cues.append({"start": _seconds(*match.groups()[:4]), "end": _seconds(*match.groups()[4:]), "text": line})
    return cues


def _subtitles(directory: Path) -> tuple[list[dict], str | None]:
    files = sorted(directory.glob("video*.vtt"), key=lambda p: ("orig" not in p.name, p.name))
    # Prefer the creator's own subtitles (video.ja.vtt) over automatic ones when both exist.
    for path in sorted(files, key=lambda p: (p.name != "video.ja.vtt",)):
        cues = parse_vtt(path.read_text(encoding="utf-8", errors="replace"))
        if cues:
            return cues, path.name
    return [], None


# ---------------------------------------------------------------- frames
def _frame(video: Path, t: float, width: int) -> Image.Image:
    import subprocess
    from io import BytesIO
    result = subprocess.run([str(FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}",
                             "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
                             "-vcodec", "png", "-"], capture_output=True, check=False, env=scrubbed_env())
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(f"ffmpeg could not read a frame at {t:.2f}s: {result.stderr.decode(errors='replace')[-500:]}")
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def sheet(video: Path, times: list[float], output: Path, columns: int = 4) -> Path:
    """Frames at `times`, each labelled with its timestamp, in a grid."""
    frames = [_frame(video, t, CELL_W) for t in times]
    if not frames:
        raise ValueError("no frames for the sheet")
    cell_h = max(f.height for f in frames)
    rows = math.ceil(len(frames) / columns)
    canvas = Image.new("RGB", (columns * CELL_W, rows * (cell_h + 24)), "#202020")
    draw = ImageDraw.Draw(canvas)
    for i, (frame, t) in enumerate(zip(frames, times)):
        x, y = (i % columns) * CELL_W, (i // columns) * (cell_h + 24)
        canvas.paste(frame, (x, y + 24))
        draw.text((x + 6, y + 5), f"{t:6.2f}s", fill="#ffffff")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=85)
    return output


def analyze_reference(directory: Path, source: str) -> dict:
    video = directory / "video.mp4"
    info = probe(video)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    duration = float(info["format"].get("duration") or stream.get("duration"))
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    changes = scene_changes(video)
    shots = interval_stats(changes, duration)
    cues, subtitle_file = _subtitles(directory)
    characters = sum(len(re.sub(r"\s", "", cue["text"])) for cue in cues)
    spoken = sum(cue["end"] - cue["start"] for cue in cues)
    shot_starts = [0.0] + [t for t in changes if 0 < t < duration]
    sheets = {
        "hook": sheet(video, [min(t, duration - 0.05) for t in (0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)],
                      directory / "sheet_hook.jpg"),
        "shots": sheet(video, [min(t + 0.2, duration - 0.05) for t in shot_starts[:32]], directory / "sheet_shots.jpg",
                       columns=8 if len(shot_starts) > 16 else 4),
        "even": sheet(video, [duration * (i + 0.5) / 16 for i in range(16)], directory / "sheet_even.jpg"),
    }
    frames = []
    for share in (0.25, 0.5, 0.75):
        t = duration * share
        path = directory / f"frame_{int(share * 100):02d}.jpg"
        _frame(video, t, 1080).save(path, quality=90)
        frames.append({"t": round(t, 2), "path": path.name})
    meta_path = directory / "info.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    result = {
        "source": source, "title": meta.get("title"), "channel": meta.get("channel") or meta.get("uploader"),
        "views": meta.get("view_count"), "likes": meta.get("like_count"),
        "duration": round(duration, 3), "width": stream["width"], "height": stream["height"],
        "shots": {"count": len(shot_starts), "starts": [round(t, 2) for t in shot_starts],
                  "median_sec": round(shots["median_sec"], 2), "p90_sec": round(shots["p90_sec"], 2),
                  "max_sec": round(shots["max_sec"], 2)},
        "loudness": measure_loudness(video) if has_audio else None,
        "audio_above_minus35_share": round(audio_activity(video, duration), 3) if has_audio else 0.0,
        "subtitles": {"file": subtitle_file, "cues": len(cues), "characters": characters,
                      "spoken_sec": round(spoken, 2),
                      "cps": round(characters / spoken, 2) if spoken else None},
        "transcript": [{"start": round(c["start"], 2), "end": round(c["end"], 2), "text": c["text"]} for c in cues],
        "sheets": {key: path.name for key, path in sheets.items()},
        "frames": frames,
    }
    (directory / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                             encoding="utf-8")
    return result


def add(source: str, format_id: str, *, replace: bool = False) -> Path:
    """Fetch or copy one reference into local/formats/<id>/reference/ (or reference2/, ... for more)."""
    base = format_dir(format_id)
    base.mkdir(parents=True, exist_ok=True)
    # Fetch into a staging directory first: a failed download must not cost the earlier references.
    directory = base / ".incoming"
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir()
    try:
        if _is_url(source):
            download(source, directory)
        else:
            path = Path(source).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(source)
            run([str(FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
                 "-c", "copy", str(directory / "video.mp4")])
            for vtt in path.parent.glob(path.stem + "*.vtt"):
                shutil.copy2(vtt, directory / ("video" + vtt.name[len(path.stem):]))
        analyze_reference(directory, source)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    existing = sorted(p for p in base.glob("reference*") if p.is_dir())
    if replace:
        for path in existing:
            shutil.rmtree(path)
        existing = []
    name, n = "reference", 1
    while (base / name).exists():
        n += 1
        name = f"reference{n}"
    final = base / name
    directory.rename(final)
    return final


def summary(directory: Path) -> str:
    data = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
    shots, subs = data["shots"], data["subtitles"]
    loud = data["loudness"] or {}
    lines = [
        f"reference: {directory}",
        f"  {data.get('title') or data['source']}  ({data.get('channel') or '-'}; views {data.get('views')})",
        f"  {data['duration']:.1f}s {data['width']}x{data['height']}; shots {shots['count']} "
        f"(median {shots['median_sec']}s, p90 {shots['p90_sec']}s, max {shots['max_sec']}s)",
        f"  loudness {loud.get('integrated_lufs')} LUFS; audio active {data['audio_above_minus35_share']:.0%}",
        f"  subtitles: {subs['file'] or 'none'}; {subs['cues']} cues, {subs['cps'] or '-'} chars/s",
        "  read: " + ", ".join(f"{directory / name}" for name in data["sheets"].values()),
        "  full-size frames: " + ", ".join(f"{directory / f['path']} ({f['t']}s)" for f in data["frames"]),
    ]
    if data["transcript"]:
        lines.append("  transcript:")
        lines += [f"    {c['start']:6.2f}-{c['end']:6.2f} {c['text']}" for c in data["transcript"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- format profiles
def load_format(format_id: str) -> FormatProfile:
    path = format_dir(format_id) / "format.json"
    if not path.is_file():
        raise FileNotFoundError(f"format {format_id!r} not found: {path}")
    profile = FormatProfile.model_validate_json(path.read_text(encoding="utf-8"))
    if profile.id != format_id:
        raise ValueError(f"{path}: id {profile.id!r} differs from its directory")
    from .registry import load_types
    known = {t.id: t for t in load_types().values()}
    unusable = [t for t in profile.types if t not in known or known[t].status == "planned"]
    if unusable:
        raise ValueError(f"{path}: types not implemented: {unusable}")
    return profile


def list_formats() -> list[FormatProfile | str]:
    out = []
    for path in sorted(formats_dir().glob("*/format.json")):
        try:
            out.append(load_format(path.parent.name))
        except (ValueError, FileNotFoundError) as exc:
            out.append(f"{path.parent.name}: invalid ({exc})")
    return out


def preview_timeline(layout: Layout, media: str | None, header: str | None) -> Timeline:
    """Two seconds: the image (or a flat background) with the header and one caption page."""
    from .schemas import Caption, Clip
    clips = [Clip(start=0, end=2, layer=0, component="ken_burns_image", props={"src": media, "zoom": [1.0, 1.0]})
             if media else Clip(start=0, end=2, layer=0, component="background", props={"from": "#3a4a5e", "to": "#1b2430"})]
    captions = [Caption(start=0, end=2, text="字幕はここに入ります\nふたつめの行です", emphasis=["字幕"])]
    if layout.header is not None:
        from .captions import band_text
        text = band_text(header or layout.header.text or "見出しのサンプル\n二行まで入ります", layout.header.size)
        layout = layout.model_copy(update={"header": layout.header.model_copy(update={"text": text})})
    timeline = Timeline(duration=2, clips=clips, captions=captions, layout=layout)
    return timeline


def preview(format_id: str, *, header: str | None = None) -> Path:
    """Render the layout as a still over the reference's middle frame and save it next to that frame."""
    profile = load_format(format_id)
    base = format_dir(format_id)
    reference = base / "reference"
    out_dir = base / "preview"
    out_dir.mkdir(exist_ok=True)
    media = None
    frame = reference / "frame_50.jpg"
    if frame.is_file():
        shutil.copy2(frame, out_dir / "media.jpg")
        media = "media.jpg"
    timeline = preview_timeline(profile.layout, media, header)
    props = out_dir / "props.json"
    props.write_text(json.dumps({"timeline": timeline.model_dump(mode="json")}, ensure_ascii=False), encoding="utf-8")
    still = out_dir / "still.png"
    env = scrubbed_env()
    env["PATH"] = str(Path.home() / ".local/opt/node/bin") + os.pathsep + env.get("PATH", "")
    run(["npx", "--no-install", "remotion", "still", "src/index.ts", "Short", str(still), f"--props={props}",
         f"--public-dir={out_dir}", "--frame=20", "--log=error"], cwd=ROOT / "render", env=env)
    side = out_dir / "compare.jpg"
    images = [Image.open(still).convert("RGB")]
    if frame.is_file():
        images.insert(0, Image.open(frame).convert("RGB"))
    images = [im.resize((540, round(im.height * 540 / im.width))) for im in images]
    canvas = Image.new("RGB", (540 * len(images) + 20 * (len(images) - 1), max(im.height for im in images)), "#ffffff")
    for i, im in enumerate(images):
        canvas.paste(im, (i * 560, 0))
    canvas.save(side, quality=88)
    return side


def main_reference(a) -> int:
    if a.reference_cmd == "add":
        directory = add(a.source, a.id, replace=a.replace)
        print(summary(directory))
        return 0
    base = format_dir(a.id)
    for directory in sorted(p for p in base.glob("reference*") if p.is_dir()):
        print(summary(directory))
    return 0


def main_format(a) -> int:
    if a.format_cmd == "list":
        for item in list_formats():
            if isinstance(item, str):
                print(item)
            else:
                print(f"{item.id}\t{item.name}\t{', '.join(item.types)}\t{item.summary}")
        return 0
    if a.format_cmd == "show":
        print(load_format(a.id).model_dump_json(indent=2))
        return 0
    if a.format_cmd == "validate":
        try:
            profile = load_format(a.id)
        except (ValueError, FileNotFoundError) as exc:
            print(f"format {a.id}: {exc}", file=sys.stderr)
            return 1
        print(f"format {profile.id}: ok ({profile.name})")
        return 0
    print(preview(a.id, header=a.header))
    return 0
