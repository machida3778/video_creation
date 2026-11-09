"""Outside pictures with a licence record: `vg image add RUN SOURCE --id ID --license L --subject S`.

Licences come from config/licenses.yaml. A `video` licence takes a still from the source video at --at
seconds (a clip under a creator's clip guideline, e.g. a talent's public stream); an `image` licence copies
the image itself. The picture lands in RUN/assets/library/<id>.jpg (1080 px wide at most) and the manifest
records the licence, its URL, the source video's title and URL, and the credit the guideline requires.
Storyboards use the asset id as `visual.ref` with `visual.kind: library`.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import yaml
from PIL import Image

from .media import FFMPEG, ROOT, load, run, save, scrubbed_env
from .schemas import Asset, Manifest

LICENSES_FILE = ROOT / "config" / "licenses.yaml"
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


def load_licenses(path: Path | None = None) -> dict[str, dict]:
    path = Path(path) if path is not None else LICENSES_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for item in data.get("licenses", []):
        if not {"id", "name", "kinds", "commercial_ok"} <= set(item):
            raise ValueError(f"{path}: licence entry needs id, name, kinds, commercial_ok: {item}")
        out[item["id"]] = item
    return out


def _video_meta(url: str) -> dict:
    result = run(["uvx", "yt-dlp", "--no-playlist", "--skip-download", "--dump-json", url], env=scrubbed_env())
    info = json.loads(result.stdout)
    if info.get("availability") not in (None, "public", "unlisted"):
        raise ValueError(f"source is {info.get('availability')}: only public videos can be clipped")
    return {"title": info.get("title") or "", "url": info.get("webpage_url") or url,
            "channel": info.get("channel") or info.get("uploader") or "", "duration": info.get("duration")}


def _still_from_video(url: str, at: float, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run(["uvx", "yt-dlp", "--no-playlist", "--no-progress", "--ffmpeg-location", str(Path(FFMPEG).parent),
             "-f", "bv*[width<=1920][height<=1920]/b", "--download-sections", f"*{max(0.0, at):.2f}-{at + 3:.2f}",
             "--force-keyframes-at-cuts", "-P", tmp, "-o", "clip.%(ext)s", url], env=scrubbed_env())
        clips = sorted(Path(tmp).glob("clip.*"))
        if not clips:
            raise FileNotFoundError("yt-dlp did not produce the clip")
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(clips[0]),
             "-frames:v", "1", "-vf", "scale='min(1080,iw)':-2", "-q:v", "2", str(target)])


def _image(source: str, target: Path) -> None:
    downloaded = bool(re.match(r"^https?://", source))
    if downloaded:
        import httpx
        response = httpx.get(source, follow_redirects=True, timeout=60,
                             headers={"User-Agent": "video-creation-harness/0.1"})
        response.raise_for_status()
        raw = target.with_suffix(".download")
        raw.write_bytes(response.content)
    else:
        raw = Path(source).expanduser()
        if not raw.is_file():
            raise FileNotFoundError(source)
    try:
        with Image.open(raw) as image:
            image = image.convert("RGB")
            if image.width > 1080:
                image = image.resize((1080, round(image.height * 1080 / image.width)))
            image.save(target, quality=92)
    finally:
        if downloaded:  # only the temporary download, never the user's own file
            raw.unlink(missing_ok=True)


def add_image(run_dir: Path, source: str, *, asset_id: str, license_id: str, subject: str,
              page: str | None = None, at: float | None = None, guideline: str | None = None,
              licenses: dict[str, dict] | None = None) -> Asset:
    licenses = load_licenses() if licenses is None else licenses
    if license_id not in licenses:
        raise ValueError(f"unknown licence {license_id!r}; known: {sorted(licenses)} ({LICENSES_FILE})")
    spec = licenses[license_id]
    if not spec["commercial_ok"]:
        raise ValueError(f"licence {license_id} does not allow commercial use")
    if not ID_PATTERN.match(asset_id):
        raise ValueError(f"asset id must match {ID_PATTERN.pattern}")
    url = guideline or spec.get("url")
    if not url:
        raise ValueError(f"licence {license_id} needs --guideline <URL of the published guideline>")
    kind = "video" if at is not None else "image"
    if kind not in spec["kinds"]:
        raise ValueError(f"licence {license_id} allows {spec['kinds']}, not {kind}"
                         + (" (give --at SECONDS to take a still from the source video)" if kind == "image" else ""))
    run_dir = Path(run_dir)
    manifest = load(run_dir, "manifest") if (run_dir / "assets" / "manifest.json").is_file() else Manifest()
    relative = f"assets/library/{asset_id}.jpg"
    target = run_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    params: dict = {"subject": subject, "source": source, "license_id": license_id}
    if page:
        params["page"] = page
    meta = {"title": "", "url": page or source, "channel": ""}
    if kind == "video":
        if not re.match(r"^https?://", source):
            raise ValueError("a clip still needs the source video's URL (credited in the description)")
        meta = _video_meta(source)
        if meta["duration"] and at >= meta["duration"]:
            raise ValueError(f"--at {at} is beyond the video's {meta['duration']} s")
        _still_from_video(source, at, target)
        params.update(at=at, source_title=meta["title"], source_channel=meta["channel"], source_url=meta["url"])
    else:
        _image(source, target)
    credit = None
    if spec.get("credit"):
        credit = spec["credit"].format(title=meta["title"] or subject, url=meta["url"], channel=meta["channel"]).strip()
        params["credit_first"] = bool(spec.get("credit_first"))
    asset = Asset(id=asset_id, path=relative, kind="image", origin="library", tool="vg image add", params=params,
                  license=spec["name"], license_url=url, commercial_ok=True, credit=credit)
    manifest.assets = [a for a in manifest.assets if a.id != asset_id] + [asset]
    save(run_dir, "manifest", manifest)
    return asset
