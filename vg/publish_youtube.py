"""Explicit human-triggered YouTube Data API v3 publishing; Google imports are lazy."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import warnings

from .media import ROOT, load
from .schemas import Publish
from .thumbnail import selected_cover
from .variants import local_file

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]
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

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes=SCOPES)
    credentials = flow.run_local_server(port=8765, open_browser=False)
    _save_json(Path(token), json.loads(credentials.to_json()), secret=True)
    return credentials


def _service(token=TOKEN):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token = Path(token)
    if not token.is_file():
        raise ValueError("OAuth token missing; run vg publish auth youtube first")
    credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_json(token, json.loads(credentials.to_json()), secret=True)
    if not credentials.valid:
        raise ValueError("OAuth token invalid; run vg publish auth youtube again")
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


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


def preview(variant_dir, privacy="private", publish_at=None, *, force=False) -> dict:
    variant_dir = Path(variant_dir)
    if (variant_dir / "published_youtube.json").exists() and not force:
        raise ValueError("variant already uploaded; use --force only to intentionally upload again")
    if not (variant_dir / "qa.json").is_file() or not load(variant_dir, "qa").passed:
        raise ValueError("YouTube upload requires passed QA")
    publish = load(variant_dir, "publish")
    body = request_body(publish, privacy, publish_at)
    return {"variant": str(variant_dir), "body": body,
            "video": str(local_file(variant_dir, "final.mp4")), "cover": str(_cover(variant_dir)),
            "captions": str(local_file(variant_dir, "final.srt"))}


def upload(variant_dir, privacy="private", publish_at=None, dry_run=False, *, force=False):
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
        return _upload(variant_dir, privacy, plan)


def _upload(variant_dir: Path, privacy: str, plan: dict):
    client = _service()
    request = client.videos().insert(part="snippet,status", body=plan["body"],
                                     media_body=_media_upload(plan["video"], "video/mp4", resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    result = {"video_id": video_id, "url": f"https://youtube.com/shorts/{video_id}",
              "privacy": privacy, "uploaded_at": datetime.now(timezone.utc).isoformat(),
              "thumbnail": "error: pending", "captions": "error: pending"}
    record = variant_dir / "published_youtube.json"
    # Persist the ID immediately: optional follow-up failures must never cause a duplicate video.
    _save_json(record, result)
    for name in ("thumbnail", "captions"):
        try:
            if name == "thumbnail":
                cover = Path(plan["cover"])
                client.thumbnails().set(videoId=video_id, media_body=_media_upload(
                    cover, "image/png" if cover.suffix == ".png" else "image/jpeg")).execute()
            else:
                client.captions().insert(part="snippet", body={"snippet": {
                    "videoId": video_id, "language": "ja", "name": "日本語", "isDraft": False}},
                    media_body=_media_upload(plan["captions"], "application/x-subrip")).execute()
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
    cover = _cover(variant_dir)
    _service().thumbnails().set(videoId=result["video_id"], media_body=_media_upload(
        cover, "image/png" if cover.suffix == ".png" else "image/jpeg")).execute()
    result["thumbnail"] = "ok"
    _save_json(record, result)
    return result


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
