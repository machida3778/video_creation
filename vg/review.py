"""Daily performance review: what made the channel's videos grow or stall, once a day.

The first daily-post session of each JST day runs ``vg daily review``: it joins every upload's YouTube
statistics and analytics with the production choices recorded in its run (type, hook, length, voice,
BGM, cover, slot, whether the request was the user's or added automatically). An independent subagent
reads it, compares good and weak videos and rewrites the private lessons file ``local/insights.md``,
which make-video reads before choosing the type, hook, script and cover. ``vg daily review --done``
records that today's review happened, so later sessions that day skip it.
Produced titles and topics stay in ignored ``local/`` and ``runs/`` files only.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import warnings

from . import daily
from .media import ROOT

INSIGHTS = ROOT / "local/insights.md"
MARK = "review.json"           # in the daily state directory: the last review
LOG = "reviews.jsonl"          # one line per review: date and summary


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def due(at: datetime | None = None, state_dir: Path = daily.STATE_DIR, insights: Path = INSIGHTS) -> dict:
    at = (at or daily.now()).astimezone(daily.JST)
    last = _read(state_dir / MARK).get("date")
    return {"due": last != at.date().isoformat(), "last": last, "insights": str(insights),
            "insights_exists": insights.is_file()}


def done(summary: str, at: datetime | None = None, state_dir: Path = daily.STATE_DIR) -> dict:
    at = (at or daily.now()).astimezone(daily.JST)
    entry = {"date": at.date().isoformat(), "at": at.isoformat(timespec="seconds"),
             "summary": " ".join(summary.split())}
    state_dir.mkdir(parents=True, exist_ok=True)
    daily._atomic_write(state_dir / MARK, json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
    with (state_dir / LOG).open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _uploads(root: Path) -> dict[str, Path]:
    """video id -> the run or variant directory whose published_youtube.json records it."""
    out: dict[str, Path] = {}
    for record in [*root.glob("runs/*/published_youtube.json"), *root.glob("runs/*/variants/*/published_youtube.json")]:
        video_id = _read(record).get("video_id")
        if video_id:
            out[video_id] = record.parent
    return out


def _first(base: Path, name: str) -> dict:
    """A stage file of the variant, falling back to its run (variants inherit brief, route and research)."""
    for candidate in (base, base.parent.parent if base.parent.name == "variants" else base):
        data = _read(candidate / name)
        if data:
            return data
    return {}


def production(base: Path) -> dict:
    """The choices that may explain a video's performance, from its run files."""
    script, publish = _first(base, "script.json"), _first(base, "publish.json")
    route, brief = _first(base, "route.json"), _first(base, "brief.json")
    lines = script.get("lines") or []
    narrator = (script.get("cast") or {}).get("narrator")
    speed = ((script.get("voice_overrides") or {}).get("narrator") or {}).get("speed")
    report = _read(base / "covers_report.json")
    covers = {o.get("id"): o for o in _read(base / "covers.json").get("options", [])}
    from .thumbnail import scores, selected_cover
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            chosen = selected_cover(base)
    except Exception:  # noqa: BLE001 - older runs without a valid report
        chosen = report.get("chosen_id")
    # Scores survive a new render only for an unchanged cover, so they describe the cover that is used now.
    judged = scores(report.get("scores")) or {}
    judge_scores = (judged.get("options") or {}).get(chosen)
    qa = _read(base / "qa.json").get("checks", [])
    run = base.parent.parent if base.parent.name == "variants" else base
    return {
        "run": str(run.relative_to(ROOT)) if run.is_relative_to(ROOT) else str(run),
        "type": route.get("type_id"), "type_params": route.get("params"),
        "tone": brief.get("tone"), "search_query": script.get("search_query"),
        "hook": lines[0].get("text") if lines else None, "hook_options": len(script.get("hook_options") or []),
        "lines": len(lines), "narration_seconds": _read(base / "timings.json").get("total_sec"),
        "narrator": narrator, "speed": speed, "bgm": publish.get("music_track"),
        "hashtags": publish.get("hashtags"),
        "cover": {"id": chosen, "template": (covers.get(chosen) or {}).get("template"),
                  "text": (covers.get(chosen) or {}).get("text"),
                  "cover_only_image": bool((covers.get(chosen) or {}).get("prompt")),
                  "chosen_by": report.get("chosen_by"), "judge_scores": judge_scores,
                  "judge_round": judged.get("round") if judge_scores else None,
                  "other_totals": {key: value.get("total") for key, value in (judged.get("options") or {}).items()
                                   if key != chosen and isinstance(value, dict)}},
        "qa_warnings": [c.get("id") for c in qa if not c.get("passed") and c.get("severity") == "warn"],
        "english": (base / "en.json").is_file(),
    }


def _slots(state_dir: Path) -> dict[str, dict]:
    """video id -> {slot, day_off, request} from the daily state."""
    out = {}
    for entry in daily.load_state(state_dir)["days"].values():
        if entry.get("video_id") and entry.get("slot"):
            slot = datetime.fromisoformat(entry["slot"]).astimezone(daily.JST)
            out[entry["video_id"]] = {"slot": daily.label(slot), "day_off": daily.is_day_off(slot.date()),
                                      "request": entry.get("request")}
    return out


def data(limit: int = 50, *, cfg: dict | None = None, state_dir: Path = daily.STATE_DIR, root: Path = ROOT,
         history: dict | None = None) -> dict:
    """Everything the reviewer needs in one JSON: metrics joined with production choices."""
    cfg = daily.config() if cfg is None else cfg
    history = daily.history(limit, cfg) if history is None else history
    items = daily.read_items(cfg)
    auto = {i.text for i in items if any("自動追加" in note for note in i.notes)}
    uploads, slots = _uploads(root), _slots(state_dir)
    videos = []
    for video in history.get("videos", []):
        row = dict(video)
        views = row.get("views") or 0
        if views:
            row["likes_per_1k_views"] = round(1000 * row.get("likes", 0) / views, 1)
            if "shares" in row:
                row["shares_per_1k_views"] = round(1000 * row["shares"] / views, 1)
            if "subscribers_gained" in row:
                row["subs_per_1k_views"] = round(1000 * row["subscribers_gained"] / views, 1)
        try:
            published = datetime.fromisoformat(row["published"].replace("Z", "+00:00"))
            row["age_days"] = round((daily.now() - published).total_seconds() / 86400, 1)
        except (KeyError, ValueError):
            pass
        # Analytics lag about two days; younger videos give early signals only.
        row["mature"] = row.get("age_days", 0) >= 2 and row.get("privacy") == "public"
        slot = slots.get(row["id"], {})
        if slot:
            row |= {"slot": slot["slot"], "day_off": slot["day_off"],
                    "request_source": "auto" if slot.get("request") in auto else "user"}
        if row["id"] in uploads:
            row["production"] = production(uploads[row["id"]])
        else:
            row["production"] = None  # uploaded by hand or before the harness recorded uploads
        videos.append(row)
    out = {k: v for k, v in history.items() if k not in ("videos", "list")}
    out |= due(state_dir=state_dir) | {"videos": videos,
                                        "mature_videos": sum(1 for v in videos if v["mature"]),
                                        "past_reviews": _tail(state_dir / LOG, 7)}
    return out


def _tail(path: Path, n: int) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    return [json.loads(line) for line in lines if line.strip()]
