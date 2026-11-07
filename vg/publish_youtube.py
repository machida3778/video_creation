"""Explicit human-triggered YouTube Data API v3 publishing; Google imports are lazy."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import warnings

from .media import ROOT, load
from .schemas import Publish
from .thumbnail import selected_cover
from .variants import local_file

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
# Read-only channel analytics (retention, shares) for choosing topics; requested by `vg publish auth youtube`.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
AUTH_SCOPES = [*SCOPES, ANALYTICS_SCOPE]
CLIENT_SECRET = ROOT / "local/youtube/client_secret.json"
TOKEN = ROOT / "local/youtube/token.json"


def _save_json(path: Path, data, *, secret=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if not secret:
            temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def authorize(client_secret=CLIENT_SECRET, token=TOKEN):
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=AUTH_SCOPES)
    credentials = flow.run_local_server(port=8765, open_browser=False)
    _save_json(Path(token), json.loads(credentials.to_json()), secret=True)
    return credentials


def _credentials(token=TOKEN):
    """Stored OAuth credentials with the scopes they were granted (older tokens lack analytics)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token = Path(token)
    if not token.is_file():
        raise ValueError("OAuth token missing; run vg publish auth youtube first")
    credentials = Credentials.from_authorized_user_file(str(token))
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_json(token, json.loads(credentials.to_json()), secret=True)
    if not credentials.valid:
        raise ValueError("OAuth token invalid; run vg publish auth youtube again")
    return credentials


def _service(token=TOKEN):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(token), cache_discovery=False)


def analytics_service(token=TOKEN):
    """YouTube Analytics API v2 client; needs a token authorized with ANALYTICS_SCOPE."""
    from googleapiclient.discovery import build

    credentials = _credentials(token)
    if ANALYTICS_SCOPE not in (credentials.scopes or []):
        raise ValueError("YouTube Analytics permission missing: run `vg publish auth youtube` again "
                         "(and enable the YouTube Analytics API for the OAuth client's Cloud project)")
    return build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)


def _media_upload(path, mimetype, *, resumable=False):
    from googleapiclient.http import MediaFileUpload

    return MediaFileUpload(str(path), mimetype=mimetype, resumable=resumable)


def request_body(publish: Publish, privacy="private", publish_at=None) -> dict:
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError("privacy must be private, unlisted or public")
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
              "containsSyntheticMedia": publish.synthetic_media}
    if publish_at is not None:
        if privacy != "private":
            raise ValueError("publishAt requires private privacy")
        try:
            scheduled = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValueError("publishAt requires a timezone-aware ISO8601 timestamp") from exc
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            raise ValueError("publishAt requires a timezone-aware ISO8601 timestamp")
        if scheduled <= datetime.now(timezone.utc):
            raise ValueError("publishAt must be in the future")
        status["publishAt"] = scheduled.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {"snippet": {"title": publish.title, "description": publish.description,
                        "tags": [tag.lstrip("#") for tag in publish.hashtags], "categoryId": "27",
                        "defaultLanguage": "ja", "defaultAudioLanguage": "ja"}, "status": status}


def _cover(variant_dir: Path) -> Path:
    if (variant_dir / "covers.json").is_file():
        chosen = selected_cover(variant_dir)
        jpeg = local_file(variant_dir, f"thumbs/{chosen}.jpg")
        png = variant_dir / f"thumbs/{chosen}.png"
    else:
        jpeg = local_file(variant_dir, "cover.jpg")
        png = variant_dir / "cover.png"
    if png.is_file() and png.stat().st_size <= 50_000_000:
        return local_file(variant_dir, str(png.relative_to(variant_dir)))
    return jpeg


# Thumbnail key YouTube adds only when a cover was processed as a vertical (Shorts) image.
VERTICAL = "shortsAspectRatio"
PROCESSING_TIMEOUT_SEC = 300
POLL_SEC = 15
REPAIR_ATTEMPTS = 3


def _processed(client, video_id, *, timeout=None, poll=POLL_SEC, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Wait until YouTube has finished processing the upload; False on timeout."""
    deadline = clock() + (PROCESSING_TIMEOUT_SEC if timeout is None else timeout)
    while True:
        items = client.videos().list(part="processingDetails", id=video_id).execute().get("items", [])
        status = items[0].get("processingDetails", {}).get("processingStatus") if items else None
        if status == "succeeded":
            return True
        if status in ("failed", "rejected", "terminated"):
            raise RuntimeError(f"video processing {status}")
        if clock() >= deadline:
            return False
        sleep(poll)


def _vertical(client, video_id) -> bool:
    items = client.videos().list(part="snippet", id=video_id).execute().get("items", [])
    return bool(items) and VERTICAL in items[0]["snippet"].get("thumbnails", {})


def _set_cover(client, video_id, cover: Path, *, sleep=time.sleep, attempts=2, checks=6, poll=10) -> None:
    """Set the custom cover after processing and confirm that YouTube made its vertical version.

    A cover set while the video is still processing gets only 16:9 versions (no shortsAspectRatio):
    Shorts feeds then show a small letterboxed cover or a frame instead (seen 2026-11-07).
    """
    if not _processed(client, video_id, sleep=sleep):
        raise RuntimeError("video still processing; `vg publish youtube-thumbnails` sets the cover later")
    for _ in range(attempts):
        client.thumbnails().set(videoId=video_id, media_body=_media_upload(
            cover, "image/png" if cover.suffix == ".png" else "image/jpeg")).execute()
        for _ in range(checks):
            if _vertical(client, video_id):
                return
            sleep(poll)
    raise RuntimeError("YouTube made no vertical (shortsAspectRatio) thumbnail")


def preview(variant_dir, privacy="private", publish_at=None, *, force=False) -> dict:
    variant_dir = Path(variant_dir)
    if (variant_dir / "published_youtube.json").exists() and not force:
        raise ValueError("variant already uploaded; use --force only to intentionally upload again")
    if not (variant_dir / "qa.json").is_file() or not load(variant_dir, "qa").passed:
        raise ValueError("YouTube upload requires passed QA")
    publish = load(variant_dir, "publish")
    body = request_body(publish, privacy, publish_at)
    english_files = {}
    if (variant_dir / "en.json").is_file():
        from .english import MANUAL_AUDIO, validate
        from .qa import check_english
        english = validate(variant_dir)
        failed = [c for c in check_english(variant_dir) if not c.passed and c.severity == "block"]
        if failed:
            raise ValueError("YouTube upload requires passed English QA: " + "; ".join(c.detail for c in failed))
        body["localizations"] = {"en": {"title": english.title, "description": english.description}}
        english_files = {"captions_en": str(local_file(variant_dir, "final_en.srt")),
                         "dub_en": str(local_file(variant_dir, "dub_en.m4a")), "dub_en_note": MANUAL_AUDIO}
    return {"variant": str(variant_dir), "body": body, **english_files,
            "video": str(local_file(variant_dir, "final.mp4")), "cover": str(_cover(variant_dir)),
            "captions": str(local_file(variant_dir, "final.srt"))}


def upload(variant_dir, privacy="private", publish_at=None, dry_run=False, *, force=False, cover=True):
    """Upload a QA-passed variant; cover=False leaves the cover to set_thumbnail (it waits for processing)."""
    variant_dir = Path(variant_dir)
    if dry_run:
        return preview(variant_dir, privacy, publish_at, force=force)
    import fcntl

    # Serialize live uploads of one variant, including independent CLI processes.
    with (variant_dir / ".publish_youtube.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("YouTube upload already in progress for this variant") from exc
        plan = preview(variant_dir, privacy, publish_at, force=force)
        return _upload(variant_dir, privacy, plan, cover)


def _upload(variant_dir: Path, privacy: str, plan: dict, cover: bool = True):
    client = _service()
    # Quota (official docs checked 2026-11-06): videos.insert = 1 Video Uploads
    # bucket unit; thumbnails.set ~=50 general units; captions.insert =400 each.
    # JA: 1 upload +450 general; JA+EN: 1 upload +850 general. localizations
    # rides the same insert. No audio upload API or Studio automation is used.
    part = "snippet,status" + (",localizations" if "localizations" in plan["body"] else "")
    request = client.videos().insert(part=part, body=plan["body"],
                                     media_body=_media_upload(plan["video"], "video/mp4", resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    result = {"video_id": video_id, "url": f"https://youtube.com/shorts/{video_id}",
              "privacy": privacy, "uploaded_at": datetime.now(timezone.utc).isoformat(),
              "thumbnail": "error: pending" if cover else "pending", "captions": "error: pending"}
    if plan["body"]["status"].get("publishAt"):
        result["publish_at"] = plan["body"]["status"]["publishAt"]
    if "captions_en" in plan:
        result.update(captions_en="error: pending", dub_en=plan["dub_en"], dub_en_note=plan["dub_en_note"])
    record = variant_dir / "published_youtube.json"
    # Persist the ID immediately: optional follow-up failures must never cause a duplicate video.
    _save_json(record, result)
    # Captions first: the cover waits for YouTube's processing, and an interrupted wait must not lose them.
    for name in ("captions", *(["captions_en"] if "captions_en" in plan else []), *(["thumbnail"] if cover else [])):
        try:
            if name == "thumbnail":
                _set_cover(client, video_id, Path(plan["cover"]))
            else:
                client.captions().insert(part="snippet", body={"snippet": {
                    "videoId": video_id, "language": "en" if name == "captions_en" else "ja",
                    "name": "English" if name == "captions_en" else "日本語", "isDraft": False}},
                    media_body=_media_upload(plan[name], "application/x-subrip")).execute()
            result[name] = "ok"
        except Exception as exc:  # A custom-thumbnail-ineligible channel still has a successfully uploaded video.
            result[name] = f"error: {exc}"
            warnings.warn(f"YouTube {name}: {exc}", UserWarning, stacklevel=2)
        _save_json(record, result)
    return result


def _published(variant_dir: Path) -> tuple[Path, dict]:
    record = Path(variant_dir) / "published_youtube.json"
    if not record.is_file():
        raise ValueError(f"{variant_dir} has not been uploaded to YouTube yet")
    return record, json.loads(record.read_text(encoding="utf-8"))


def set_thumbnail(variant_dir) -> dict:
    """Retry the custom thumbnail of an uploaded variant (e.g. after the channel's phone verification)."""
    variant_dir = Path(variant_dir)
    record, result = _published(variant_dir)
    _set_cover(_service(), result["video_id"], _cover(variant_dir))
    result["thumbnail"] = "ok"
    result.pop("thumbnail_repair_failures", None)
    _save_json(record, result)
    return result


def repair_thumbnails(root=None) -> list[dict]:
    """Re-set the cover of every uploaded variant whose YouTube video has no vertical thumbnail.

    Safety net for uploads whose cover could not be set after processing (timeout, interrupted run):
    one videos.list call per 50 videos when nothing needs repair. Run hourly by scripts/daily_post.sh.
    A variant is given up after REPAIR_ATTEMPTS failures, so a cover YouTube keeps refusing cannot
    spend the upload quota every hour; `vg publish youtube-thumbnail RUN:vN` still retries it by hand.
    """
    records = {}
    for path in sorted(Path(root or ROOT / "runs").glob("*/variants/*/published_youtube.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("video_id") and data.get("thumbnail_repair_failures", 0) < REPAIR_ATTEMPTS:
            records[data["video_id"]] = path
    if not records:
        return []
    client, repaired = _service(), []
    ids = list(records)
    for start in range(0, len(ids), 50):
        response = client.videos().list(part="snippet,processingDetails", id=",".join(ids[start:start + 50])).execute()
        for item in response.get("items", []):
            if (item.get("processingDetails", {}).get("processingStatus") != "succeeded"
                    or VERTICAL in item["snippet"].get("thumbnails", {})):
                continue
            path = records[item["id"]]
            try:
                result = set_thumbnail(path.parent)
                repaired.append({"variant": str(path.parent), "video_id": item["id"], "thumbnail": result["thumbnail"]})
            except Exception as exc:  # One broken variant must not stop the others.
                data = json.loads(path.read_text(encoding="utf-8"))
                data["thumbnail"] = f"error: {exc}"
                data["thumbnail_repair_failures"] = data.get("thumbnail_repair_failures", 0) + 1
                _save_json(path, data)
                repaired.append({"variant": str(path.parent), "video_id": item["id"], "thumbnail": data["thumbnail"]})
    return repaired


def set_privacy(variant_dir, privacy: str) -> dict:
    """Change an uploaded variant's visibility (e.g. private test -> public), keeping its other status flags."""
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError("privacy must be private, unlisted or public")
    variant_dir = Path(variant_dir)
    record, result = _published(variant_dir)
    publish = load(variant_dir, "publish")
    _service().videos().update(part="status", body={"id": result["video_id"], "status": {
        "privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
        "containsSyntheticMedia": bool(publish.synthetic_media)}}).execute()
    result["privacy"] = privacy
    result.setdefault("history", []).append({"privacy": privacy, "at": datetime.now(timezone.utc).isoformat()})
    _save_json(record, result)
    return result
