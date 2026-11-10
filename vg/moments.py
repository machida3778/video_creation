"""Where a public video is worth watching: `vg moments SOURCE --out DIR`.

SOURCE is a video URL, or a channel URL (`/@handle`, `/@handle/streams`, `/@handle/videos`) from which the newest
public uploads are taken (members-only, premium, upcoming and live items are skipped). For each video the signal is

1. YouTube's "most replayed" heatmap (the `next` endpoint of the web client; no login, no key), else
2. live-chat replay density (`--chat`; slow for long streams: the whole chat is downloaded), else
3. evenly spaced times.

Peaks are picked with a minimum gap, frames are read at each time straight from the stream (one URL lookup, one
ffmpeg seek per frame) and laid out in a numbered contact sheet. An agent looks at the sheet and passes the chosen
time to `vg image add RUN URL --at SEC` (stills) or to clips.json (clip_highlight). Writes DIR/<video id>/
moments.json and sheet.jpg, and DIR/moments.json with every video. Nothing here records a licence: only
`vg image add` / `vg clips` put material into a run.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .media import FFMPEG, run, scrubbed_env

SKIP_AVAILABILITY = {"subscriber_only", "premium_only", "needs_auth", "private"}
CELL_W = 480
FONTS = [Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
         Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc")]


def _font(size: int):
    for path in FONTS:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


# ---------------------------------------------------------------- sources
def is_channel(url: str) -> bool:
    return bool(re.search(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|c/[^/?#]+)(/(streams|videos|featured))?/?$", url))


def channel_videos(url: str, count: int, *, scan: int = 15, min_sec: float = 60) -> list[dict]:
    """Newest public uploads of a channel, members-only and live/upcoming items skipped."""
    if not re.search(r"/(streams|videos)/?$", url):
        url = url.rstrip("/") + "/streams"
    result = run(["uvx", "yt-dlp", "--flat-playlist", "--dump-json", "--playlist-items", f"1-{scan}", url],
                 env=scrubbed_env())
    picked = []
    for raw in result.stdout.splitlines():
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if item.get("availability") in SKIP_AVAILABILITY or item.get("live_status") in ("is_live", "is_upcoming"):
            continue
        title = item.get("title") or ""
        if re.search(r"メン限|メンバー限定|members?[- ]only", title, re.I):
            continue
        if (item.get("duration") or 0) < min_sec:
            continue
        picked.append({"id": item["id"], "title": title, "duration": item.get("duration"),
                       "url": f"https://www.youtube.com/watch?v={item['id']}", "views": item.get("view_count")})
        if len(picked) >= count:
            break
    return picked


def video_id(url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})", url)
    if not match:
        raise ValueError(f"not a YouTube video URL: {url}")
    return match.group(1)


def video_meta(url: str) -> dict:
    result = run(["uvx", "yt-dlp", "--no-playlist", "--skip-download", "--dump-json", url], env=scrubbed_env())
    info = json.loads(result.stdout)
    if info.get("availability") not in (None, "public", "unlisted"):
        raise ValueError(f"{url} is {info.get('availability')}: only public videos")
    return {"id": info["id"], "title": info.get("title") or "", "url": info.get("webpage_url") or url,
            "channel": info.get("channel") or info.get("uploader") or "", "duration": info.get("duration") or 0,
            "views": info.get("view_count"), "was_live": info.get("was_live"), "heatmap": info.get("heatmap")}


# ---------------------------------------------------------------- signals
def parse_heatmap(data: dict) -> list[dict]:
    """Heatmap markers from a `next` response: [{start, end, value}] in seconds, value 0..1."""
    for mutation in data.get("frameworkUpdates", {}).get("entityBatchUpdate", {}).get("mutations", []):
        entity = (mutation.get("payload") or {}).get("macroMarkersListEntity") or {}
        markers = entity.get("markersList") or {}
        if markers.get("markerType") != "MARKER_TYPE_HEATMAP":
            continue
        out = []
        for m in markers.get("markers", []):
            start = int(m.get("startMillis", 0)) / 1000
            out.append({"start": start, "end": start + int(m.get("durationMillis", 0)) / 1000,
                        "value": float(m.get("intensityScoreNormalized", 0))})
        return out
    return []


def fetch_heatmap(vid: str) -> list[dict]:
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20251101.00.00", "hl": "ja"}},
            "videoId": vid}
    request = urllib.request.Request("https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return parse_heatmap(json.load(response))
    except (OSError, ValueError):
        return []


def chat_offsets(lines) -> list[float]:
    """Seconds of each chat message in a yt-dlp live_chat.json (one JSON object per line)."""
    out = []
    for raw in lines:
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        replay = item.get("replayChatItemAction") or {}
        offset = replay.get("videoOffsetTimeMsec")
        if offset is None:
            continue
        for action in replay.get("actions", []):
            if "addChatItemAction" in action:
                out.append(int(offset) / 1000)
                break
    return out


def chat_density(offsets: list[float], duration: float, bin_sec: float = 20.0) -> list[dict]:
    if not offsets or duration <= 0:
        return []
    bins = [0] * max(1, math.ceil(duration / bin_sec))
    for t in offsets:
        if 0 <= t < duration:
            bins[int(t // bin_sec)] += 1
    top = max(bins) or 1
    return [{"start": i * bin_sec, "end": min(duration, (i + 1) * bin_sec), "value": n / top}
            for i, n in enumerate(bins)]


def fetch_chat(url: str, duration: float, timeout: float = 900) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["uvx", "yt-dlp", "--no-playlist", "--no-progress", "--skip-download", "--write-subs",
                            "--sub-langs", "live_chat", "-P", tmp, "-o", "chat.%(ext)s", url],
                           capture_output=True, timeout=timeout, env=scrubbed_env(), check=False)
        except subprocess.TimeoutExpired:
            return []
        files = list(Path(tmp).glob("chat*.json"))
        if not files:
            return []
        with files[0].open(encoding="utf-8", errors="replace") as handle:
            return chat_density(chat_offsets(handle), duration)


def peaks(signal: list[dict], count: int, *, min_gap: float, skip_start: float = 0.0) -> list[dict]:
    """Highest bins first, at least `min_gap` seconds apart; each peak is the bin's middle."""
    chosen: list[dict] = []
    for item in sorted(signal, key=lambda s: -s["value"]):
        middle = (item["start"] + item["end"]) / 2
        if middle < skip_start or any(abs(middle - c["t"]) < min_gap for c in chosen):
            continue
        chosen.append({"t": round(middle, 2), "value": round(item["value"], 3)})
        if len(chosen) >= count:
            break
    return sorted(chosen, key=lambda c: c["t"])


def even_times(duration: float, count: int, skip_start: float = 0.0) -> list[dict]:
    usable = max(1.0, duration - skip_start)
    return [{"t": round(skip_start + usable * (i + 0.5) / count, 2), "value": None} for i in range(count)]


def choose_moments(signal: list[dict], duration: float, count: int, *, window: tuple[float, float] | None = None,
                   min_gap: float | None = None, skip_start: float = 120.0) -> tuple[str, list[dict]]:
    """("signal" | "window" | "even", moments). A window (e.g. the opening talk of a game stream, where the
    avatar is large) limits both the signal and the even fallback; otherwise long streams skip their opening
    waiting screen."""
    if window:
        start, end = max(0.0, window[0]), min(duration or window[1], window[1])
        if end <= start:
            raise ValueError(f"window {window} lies outside the video ({duration:.0f} s)")
        inside = [s for s in signal if start <= (s["start"] + s["end"]) / 2 <= end]
        if not inside:
            return "window", [{"t": round(start + (end - start) * (i + 0.5) / count, 2), "value": None}
                              for i in range(count)]
        gap = min_gap if min_gap is not None else max(5.0, (end - start) / (count * 3))
        return "signal", peaks(inside, count, min_gap=gap)
    skip = skip_start if duration > skip_start * 4 else 0.0
    if not signal:
        return "even", even_times(duration, count, skip)
    gap = min_gap if min_gap is not None else max(20.0, duration / (count * 3))
    return "signal", peaks(signal, count, min_gap=gap, skip_start=skip)


# ---------------------------------------------------------------- frames
def stream_url(url: str) -> str:
    result = run(["uvx", "yt-dlp", "--no-playlist", "-g", "-f", "bv*[height<=720][ext=mp4]/bv*[height<=720]/b",
                  url], env=scrubbed_env())
    return result.stdout.strip().splitlines()[0]


def frame_at(source: str, t: float, width: int = CELL_W) -> Image.Image | None:
    result = subprocess.run([str(FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}",
                             "-i", source, "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
                             "-vcodec", "png", "-"], capture_output=True, check=False, timeout=120,
                            env=scrubbed_env())
    if result.returncode != 0 or not result.stdout:
        return None
    return Image.open(BytesIO(result.stdout)).convert("RGB")


def _clock(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def contact_sheet(frames: list[tuple[int, dict, Image.Image | None]], title: str, output: Path,
                  columns: int = 3) -> Path:
    font, small = _font(22), _font(18)
    cell_h = max([f.height for _, _, f in frames if f is not None] or [270])
    rows = max(1, math.ceil(len(frames) / columns))
    canvas = Image.new("RGB", (columns * CELL_W, 40 + rows * (cell_h + 34)), "#181818")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 8), title[:60], fill="#ffffff", font=small)
    for i, (number, moment, frame) in enumerate(frames):
        x, y = (i % columns) * CELL_W, 40 + (i // columns) * (cell_h + 34)
        if frame is not None:
            canvas.paste(frame, (x, y + 34))
        value = "" if moment.get("value") is None else f"  熱{moment['value']:.2f}"
        draw.text((x + 6, y + 4), f"#{number}  {_clock(moment['t'])} ({moment['t']:.0f}s){value}",
                  fill="#ffd400", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=85)
    return output


# ---------------------------------------------------------------- main
def moments_for(url: str, out_dir: Path, *, count: int = 9, chat: bool = False, min_gap: float | None = None,
                skip_start: float = 120.0, frames: bool = True,
                window: tuple[float, float] | None = None) -> dict:
    meta = video_meta(url)
    duration = float(meta["duration"] or 0)
    signal, kind = (meta.pop("heatmap") or []), "heatmap"
    if signal:
        signal = [{"start": s["start_time"], "end": s["end_time"], "value": s["value"]} for s in signal]
    else:
        signal = fetch_heatmap(meta["id"])
    if not signal and chat and meta.get("was_live"):
        signal, kind = fetch_chat(meta["url"], duration), "chat"
    choice, moments = choose_moments(signal, duration, count, window=window, min_gap=min_gap,
                                     skip_start=skip_start)
    kind = kind if choice == "signal" else choice
    directory = Path(out_dir) / meta["id"]
    directory.mkdir(parents=True, exist_ok=True)
    result = {**meta, "signal": kind, "moments": moments}
    if frames and moments:
        source = stream_url(meta["url"])
        cells = [(i + 1, m, frame_at(source, m["t"])) for i, m in enumerate(moments)]
        for (number, moment, frame) in cells:
            moment["n"] = number
            moment["frame_ok"] = frame is not None
        result["sheet"] = str(contact_sheet(cells, f"{meta['title']} [{kind}]", directory / "sheet.jpg"))
    (directory / "moments.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def collect(source: str, out_dir: Path, *, videos: int = 2, count: int = 9, chat: bool = False,
            frames: bool = True, window: tuple[float, float] | None = None) -> list[dict]:
    urls = [v["url"] for v in channel_videos(source, videos)] if is_channel(source) else [source]
    if not urls:
        raise ValueError(f"no public video found on {source}")
    results = []
    for url in urls:
        try:
            results.append(moments_for(url, out_dir, count=count, chat=chat, frames=frames, window=window))
        except (ValueError, RuntimeError) as exc:
            results.append({"url": url, "error": str(exc)})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "moments.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results
