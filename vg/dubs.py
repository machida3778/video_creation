"""Uploads still waiting for the English dub, which only YouTube Studio can add."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .media import ROOT


def _records(root: Path):
    for record in sorted((root / "runs").glob("*/**/published_youtube.json")):
        data = json.loads(record.read_text(encoding="utf-8"))
        if data.get("dub_en"):
            yield record, data


def pending(root: Path = ROOT) -> list[dict]:
    rows = []
    for record, data in _records(root):
        if data.get("dub_en_done") or data.get("deleted"):
            continue
        dub = Path(data["dub_en"])
        rows.append({"video_id": data["video_id"], "dub": str(dub), "exists": dub.is_file(),
                     "publish_at": data.get("publish_at") or data.get("uploaded_at", ""),
                     "studio": f"https://studio.youtube.com/video/{data['video_id']}/translations",
                     "variant": str(record.parent.relative_to(root))})
    return sorted(rows, key=lambda row: row["publish_at"])


def mark_done(video_id: str, root: Path = ROOT) -> Path:
    for record, data in _records(root):
        if data.get("video_id") == video_id:
            data["dub_en_done"] = datetime.now(timezone.utc).isoformat()
            record.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return record
    raise ValueError(f"no uploaded video {video_id} with an English dub")


def _jst(stamp: str) -> str:
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp
    return moment.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%m/%d %H:%M JST")


def report(root: Path = ROOT) -> str:
    rows = pending(root)
    if not rows:
        return "英語音声の追加待ちはありません。"
    lines = [f"英語音声の追加待ち: {len(rows)}本（Studio → 字幕 → 言語を追加 → 英語 → 音声）"]
    for i, row in enumerate(rows, 1):
        missing = "" if row["exists"] else "  ※音声ファイルが見つかりません"
        when = _jst(row["publish_at"])
        lines += [f"{i}. {when}  {row['video_id']}{missing}",
                  f"   {row['studio']}", f"   {row['dub']}"]
    lines.append("追加したら: vg dubs --done VIDEO_ID")
    return "\n".join(lines)
