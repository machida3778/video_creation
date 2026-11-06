"""Daily autopost: the user's request checklist, publishing slots and per-slot state.

The list (default ``local/requests.md``, git-ignored) is a Markdown checklist written by the user::

    - [ ] pending request            one line = one video, processed top first
    - [x] posted request
      - 2026-11-06(金) 20:00 公開予約 https://youtube.com/shorts/ID      (notes added by vg)
    - [-] request that could not be made (skipped, with the reason as a note)

Slots are Asia/Tokyo times from ``local/preferences.yaml`` ``autopost``: weekdays ``weekday``,
Saturdays, Sundays and Japanese national holidays ``holiday``. Each is one time ("18:00") or a list
(["07:30", "12:15", "20:00"]) for several videos a day. Each run fills the earliest open slot of the
day; a slot closer than ``min_gap_minutes`` to a video already scheduled that day counts as filled.
A video is uploaded as private with ``publishAt`` set to its slot, so YouTube publishes it then.
State entries are keyed by slot ("YYYY-MM-DD HH:MM"); older day keys are converted when read.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from .media import ROOT

JST = ZoneInfo("Asia/Tokyo")
DEFAULTS = {"enabled": False, "list": "local/requests.md", "weekday": "18:00", "holiday": "10:00",
            "min_lead_minutes": 20, "min_work_minutes": 30, "min_gap_minutes": 180, "variants": 1}
STATE_DIR = ROOT / "local/daily"
WEEKDAYS = "月火水木金土日"
UPLOAD_JOURNAL = "upload_started.json"   # written next to the run before an upload, removed after it
# Exit codes of `vg daily status --check` (0 = a video should be made now).
DISABLED, SCHEDULED, TOO_LATE = 10, 11, 12
ACTIVE = ("in_progress", "failed")       # entries a session may still finish and publish

TEMPLATE = """# 動画リクエスト

作ってほしい動画を1行に1本ずつ書いてください。上から順に作り、
平日{weekday}・土日祝{holiday}に YouTube へ予約投稿します。

- 未投稿: `- [ ] 内容`（例: `- [ ] 虹が丸く見える理由`）
- 投稿済み: `- [x] 内容`。下に日時とURLが自動で付きます。
- リストが空になったら、これまでの傾向から Claude が1件追加して作ります（「自動追加」と付きます）。
- 失敗した回は理由が付き、次の機会にもう一度試します。2回続けて失敗した項目や作れない項目は `- [-]`（見送り）になり、次の項目へ進みます。
- 今作れる形式: 根拠つき雑学（「〜の理由」など）、データのランキング、二人の掛け合い解説。ニュース・医療やお金の助言は作りません。
- 投稿をやめるときは local/preferences.yaml の `autopost: enabled` を false にします。

## リスト

"""

ITEM = re.compile(r"^\s*[-*] \[( |x|X|-|)\] (.*\S)\s*$")
NOTE = re.compile(r"^\s{2,}[-*] (?!\[[ xX]?\] )(.*\S)\s*$")


def config(prefs: dict | None = None) -> dict:
    if prefs is None:
        from .registry import load_preferences
        prefs = load_preferences()
    cfg = dict(DEFAULTS)
    cfg.update((prefs or {}).get("autopost") or {})
    return cfg


def now() -> datetime:
    return datetime.now(JST)


def is_day_off(day: date) -> bool:
    import jpholiday

    return day.weekday() >= 5 or jpholiday.is_holiday(day)


def times(value) -> list[time]:
    """Slot times from one "HH:MM" string, a comma-separated string or a list; sorted, without repeats."""
    values = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = set()
    for raw in values:
        if str(raw).strip():
            hour, minute = (int(part) for part in str(raw).strip().split(":"))
            out.add(time(hour, minute))
    if not out:
        raise ValueError(f"no slot time in {value!r} (local/preferences.yaml autopost)")
    return sorted(out)


def slots(day: date, cfg: dict) -> list[datetime]:
    return [datetime.combine(day, moment, JST) for moment in times(cfg["holiday" if is_day_off(day) else "weekday"])]


def key(moment: datetime) -> str:
    """State key of a slot."""
    return f"{moment.astimezone(JST):%Y-%m-%d %H:%M}"


def label(moment: datetime) -> str:
    moment = moment.astimezone(JST)
    return f"{moment:%Y-%m-%d}({WEEKDAYS[moment.weekday()]}) {moment:%H:%M}"


def times_label(value) -> str:
    return "・".join(f"{moment:%H:%M}" for moment in times(value))


# ---------------------------------------------------------------- files
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _locked(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def list_path(cfg: dict) -> Path:
    path = Path(cfg["list"])
    return path if path.is_absolute() else ROOT / path


def init(cfg: dict) -> Path:
    path = list_path(cfg)
    if not path.exists():
        _atomic_write(path, TEMPLATE.format(weekday=times_label(cfg["weekday"]), holiday=times_label(cfg["holiday"])))
    return path


@dataclass
class Item:
    line: int                       # index of the checkbox line
    done: bool
    text: str
    notes: list[str] = field(default_factory=list)
    end: int = 0                    # index after the item's last note line
    skipped: bool = False           # "- [-]": could not be made

    @property
    def pending(self) -> bool:
        return not self.done and not self.skipped


def parse(text: str) -> list[Item]:
    items: list[Item] = []
    current: Item | None = None
    for index, line in enumerate(text.splitlines()):
        match = ITEM.match(line)
        if match:
            current = Item(index, match.group(1) in ("x", "X"), match.group(2).strip(), [], index + 1,
                           skipped=match.group(1) == "-")
            items.append(current)
            continue
        note = NOTE.match(line)
        if note and current is not None and current.end == index:
            current.notes.append(note.group(1))
            current.end = index + 1
            continue
        current = None
    return items


def read_items(cfg: dict) -> list[Item]:
    path = list_path(cfg)
    return parse(path.read_text(encoding="utf-8")) if path.exists() else []


def _edit_list(cfg: dict, change) -> None:
    """Read-modify-write under a lock; retry if the user saved the file in between (editors ignore the lock)."""
    path = init(cfg)
    with (path.parent / f".{path.name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for _ in range(5):
            before = path.read_text(encoding="utf-8")
            lines = before.splitlines()
            change(lines, parse(before))
            if path.read_text(encoding="utf-8") == before:
                _atomic_write(path, "\n".join(lines).rstrip("\n") + "\n")
                return
        raise RuntimeError(f"{path} keeps changing; try again")


def _find(items: list[Item], request: str) -> Item | None:
    """The first unchecked item with this text, else any item with it."""
    return (next((item for item in items if item.text == request and item.pending), None)
            or next((item for item in items if item.text == request), None))


def append_item(cfg: dict, request: str, note: str) -> None:
    request = " ".join(request.split())
    if not request:
        raise ValueError("empty request")

    def change(lines, _items):
        while lines and not lines[-1].strip():
            lines.pop()
        lines.extend([f"- [ ] {request}", f"  - {note}"])
    _edit_list(cfg, change)


def add_note(cfg: dict, request: str, note: str, *, mark: str | None = None) -> None:
    """Add a note under the item; mark "x" (posted) or "-" (skipped) also changes its checkbox."""
    def change(lines, items):
        item = _find(items, request)
        if item is None:  # the user removed or reworded the line; keep a record anyway
            while lines and not lines[-1].strip():
                lines.pop()
            lines.extend([f"- [{mark or ' '}] {request}", f"  - {note}"])
            return
        if mark:
            lines[item.line] = re.sub(r"\[( |x|X|-|)\]", f"[{mark}]", lines[item.line], count=1)
        lines.insert(item.end, f"  - {note}")
    _edit_list(cfg, change)


# ---------------------------------------------------------------- state
def load_state(state_dir: Path = STATE_DIR) -> dict:
    """{"days": {slot key: entry}}; entries written under a bare day key move to their slot's key."""
    path = state_dir / "state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": {}}
    days = state.setdefault("days", {})
    for old in [name for name in days if len(name) == 10]:
        if days[old].get("slot"):
            new = key(datetime.fromisoformat(days[old]["slot"]))
            if new not in days:
                days[new] = days.pop(old)
    return state


def save_state(state: dict, state_dir: Path = STATE_DIR) -> None:
    _atomic_write(state_dir / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _published(run: str | None) -> dict | None:
    """published_youtube.json of the run or its single variant, if it was uploaded."""
    if not run:
        return None
    base = Path(run) if Path(run).is_absolute() else ROOT / run
    for candidate in [base, *sorted((base / "variants").glob("v*"))]:
        record = candidate / "published_youtube.json"
        if record.is_file():
            data = json.loads(record.read_text(encoding="utf-8"))
            # Records always carry uploaded_at; fall back to the file time for hand-made ones.
            data.setdefault("uploaded_at", datetime.fromtimestamp(record.stat().st_mtime, timezone.utc).isoformat())
            return data | {"variant": str(candidate)}
    return None


def _posted(state: dict) -> dict[str, dict]:
    """Requests already uploaded in some slot: scheduled in the state, or recorded in published_youtube.json
    by an upload whose session ended before the state was updated. The editable list is not authoritative."""
    entries = [e for e in state["days"].values() if e.get("request")]
    posted = {e["request"]: e for e in entries if e.get("status") == "scheduled"}  # authoritative first
    for e in entries:
        if e["request"] not in posted and e.get("status") != "superseded" and _published(e.get("run")):
            posted[e["request"]] = e
    return posted


def _uploaded_on(record: dict | None) -> date | None:
    """JST day of an upload record (published_youtube.json), or None."""
    try:
        return datetime.fromisoformat(str(record["uploaded_at"]).replace("Z", "+00:00")).astimezone(JST).date()
    except (KeyError, TypeError, ValueError):
        return None


def _filled(entry: dict | None, day: date) -> bool:
    """The entry's slot has its video: scheduled, or uploaded that day by a session killed before the state."""
    if not entry:
        return False
    if entry.get("status") == "scheduled":
        return True
    return entry.get("status") in ACTIVE and _uploaded_on(_published(entry.get("run"))) == day


def _today(state: dict, day: date) -> dict[str, dict]:
    return {name: entry for name, entry in state["days"].items() if name[:10] == day.isoformat()}


def _current(state: dict, day: date) -> str | None:
    """Key of the day's entry a session is working on: the latest started in-progress or failed one."""
    active = [(entry.get("started", ""), name) for name, entry in _today(state, day).items()
              if entry.get("status") in ACTIVE and entry.get("run")]
    return max(active)[1] if active else None


def status(at: datetime | None = None, cfg: dict | None = None, state_dir: Path = STATE_DIR) -> dict:
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    today = at.date()
    state = load_state(state_dir)
    lead, work, gap = (int(cfg[name]) for name in ("min_lead_minutes", "min_work_minutes", "min_gap_minutes"))
    taken = [datetime.fromisoformat(entry["slot"]) for entry in _today(state, today).values()
             if entry.get("slot") and _filled(entry, today)]
    table = []
    for moment in slots(today, cfg):
        entry = state["days"].get(key(moment))
        near = any(abs((moment - other).total_seconds()) < gap * 60 for other in taken)
        table.append({"slot": moment.isoformat(), "key": key(moment), "filled": _filled(entry, today) or near,
                      "minutes_left": int((moment - at).total_seconds() // 60) - lead,
                      "status": (entry or {}).get("status")})
    # The earliest open slot that still leaves time to make a video, else the earliest open slot.
    open_slots = [row for row in table if not row["filled"] and row["minutes_left"] >= 0]
    target = next((row for row in open_slots if row["minutes_left"] >= work), open_slots[0] if open_slots else None)
    entry = state["days"].get(target["key"]) if target else None
    items = read_items(cfg)
    posted = _posted(state)
    pending = [item for item in items if item.pending and item.text not in posted]
    following = pending[0].text if pending else None
    resume = None
    if entry and not _filled(entry, today) and entry.get("status") not in ("skipped", "superseded") \
            and entry.get("run") and (ROOT / entry["run"]).exists() and not _published(entry["run"]):
        resume = {"request": entry["request"], "run": entry["run"]}
    elif following:
        for name in sorted(state["days"], reverse=True):
            old = state["days"][name]
            if old.get("request") == following and old.get("status") != "skipped" and old.get("run") \
                    and (ROOT / old["run"]).exists() and not _published(old["run"]):
                resume = {"request": following, "run": old["run"]}
                break
    current = resume["request"] if resume else following
    failures = sum(1 for name, old in state["days"].items() if (not target or name != target["key"])
                   and old.get("request") == current and old.get("status") in ("failed", "missed"))
    minutes_left = target["minutes_left"] if target else -1
    return {"now": at.isoformat(), "date": today.isoformat(), "day_off": is_day_off(today),
            "slot": target["slot"] if target else None, "slots": table, "enabled": bool(cfg["enabled"]),
            "scheduled": target is None, "minutes_left": minutes_left, "lead_ok": minutes_left >= work,
            "pending": len(pending), "next": following, "resume": resume, "failures": failures,
            "entry": entry, "list": str(list_path(cfg))}


def check(at: datetime | None = None, cfg: dict | None = None, state_dir: Path = STATE_DIR) -> int:
    """0 when a video should be made now; DISABLED, SCHEDULED (no open slot left today) or TOO_LATE."""
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    _repair(cfg, state_dir)
    _mark_missed(at, cfg, state_dir)
    info = status(at, cfg, state_dir)
    if not info["enabled"]:
        return DISABLED
    if info["scheduled"]:
        return SCHEDULED
    if not info["lead_ok"]:
        return TOO_LATE
    return 0


def _mark_missed(at: datetime, cfg: dict, state_dir: Path) -> None:
    """Today's unfinished entries whose slot can no longer be met become "missed" (counted as a failure)."""
    with _locked(state_dir):
        state = load_state(state_dir)
        missed = []
        for name, entry in _today(state, at.date()).items():
            if entry.get("request") and entry.get("slot") and entry.get("status") in ACTIVE \
                    and not _filled(entry, at.date()) \
                    and datetime.fromisoformat(entry["slot"]) - timedelta(minutes=int(cfg["min_lead_minutes"])) < at:
                entry["status"] = "missed"
                missed.append(entry)
        if missed:
            save_state(state, state_dir)
    for entry in missed:
        when = datetime.fromisoformat(entry["slot"]).astimezone(JST)
        add_note(cfg, entry["request"], f"{when:%m/%d %H:%M}枠 間に合わず（次の機会に再挑戦）")


def _repair(cfg: dict, state_dir: Path) -> None:
    """Make interrupted runs count once: a slot whose upload is recorded only in published_youtube.json
    becomes scheduled, and every uploaded request whose list item is still unchecked is ticked again."""
    with _locked(state_dir):
        state = load_state(state_dir)
        changed = False
        seen = set()  # a run resumed for a later slot was uploaded for that slot, not the earlier ones
        for name in sorted(state["days"], reverse=True):
            entry = state["days"][name]
            if entry.get("run") in seen:
                continue
            seen.add(entry.get("run"))
            if entry.get("status") in ("scheduled", "superseded", "skipped"):
                continue
            record = _published(entry.get("run"))
            if record and str(_uploaded_on(record)) == name[:10]:
                entry.update(status="scheduled", video_id=record.get("video_id"), url=record.get("url"),
                             variant=record["variant"])
                entry.pop("upload_started", None)
                changed = True
        if changed:
            save_state(state, state_dir)
    posted = _posted(load_state(state_dir))
    for item in read_items(cfg):
        done = posted.get(item.text)
        if item.pending and done:
            when = label(datetime.fromisoformat(done["slot"])) if done.get("slot") else ""
            add_note(cfg, item.text, f"{when} 公開予約 {done.get('url', '')}".strip(), mark="x")


def start(request: str, run: str, *, at: datetime | None = None, cfg: dict | None = None,
          state_dir: Path = STATE_DIR) -> dict:
    """Register the run for today's next open slot."""
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    run_path = Path(run).resolve()
    try:
        run = str(run_path.relative_to(ROOT))
    except ValueError:
        run = str(run_path)
    with _locked(state_dir):
        info = status(at, cfg, state_dir)
        if info["scheduled"]:
            raise ValueError(f"{info['date']} has no open slot left")
        state = load_state(state_dir)
        name = key(datetime.fromisoformat(info["slot"]))
        entry = state["days"].get(name) or {}
        if request not in {item.text for item in read_items(cfg) if item.pending}:
            raise ValueError("the request must be an unchecked item of the list, copied verbatim from `vg daily next`")
        attempts = entry.get("attempts", 0) + 1 if entry.get("request") == request else 1
        entry.update(request=request, run=run, status="in_progress", attempts=attempts,
                     slot=info["slot"], started=at.isoformat())
        entry.setdefault("errors", [])
        state["days"][name] = entry
        save_state(state, state_dir)
    return entry


def fail(reason: str, *, at: datetime | None = None, cfg: dict | None = None, state_dir: Path = STATE_DIR) -> dict:
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    reason = " ".join(reason.split())
    with _locked(state_dir):
        state = load_state(state_dir)
        name = _current(state, at.date())
        if name is None:
            target = status(at, cfg, state_dir)["slot"]
            name = key(datetime.fromisoformat(target)) if target else key(at)
        entry = state["days"].setdefault(name, {"errors": [], "attempts": 0})
        entry["status"] = "failed"
        entry.setdefault("errors", []).append(f"{at:%H:%M} {reason}")
        save_state(state, state_dir)
    if entry.get("request"):
        add_note(cfg, entry["request"], f"{at:%m/%d %H:%M} 失敗: {reason}（次の機会に再挑戦）")
    return entry


def skip(reason: str, request: str | None = None, *, at: datetime | None = None, cfg: dict | None = None,
         state_dir: Path = STATE_DIR) -> str:
    """Give up on an item (cannot be made, or failed repeatedly): mark it "- [-]" so the list moves on."""
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    reason = " ".join(reason.split())
    with _locked(state_dir):
        state = load_state(state_dir)
        name = _current(state, at.date())
        entry = state["days"].get(name) if name else None
        request = request or (entry or {}).get("request")
        if not request:
            raise ValueError("no request to skip: pass --request")
        if entry and entry.get("request") == request and entry.get("status") != "scheduled":
            entry["status"] = "skipped"
            entry.setdefault("errors", []).append(f"{at:%H:%M} skipped: {reason}")
            save_state(state, state_dir)
    add_note(cfg, request, f"{at:%m/%d} 見送り: {reason}", mark="-")
    return request


def add_auto(request: str, reason: str, *, at: datetime | None = None, cfg: dict | None = None) -> None:
    cfg = config() if cfg is None else cfg
    at = (at or now()).astimezone(JST)
    append_item(cfg, request, f"自動追加 {at:%Y-%m-%d}: {' '.join(reason.split())}")


def publish(run_dir: Path, *, confirm: bool, dry_run: bool = False, at: datetime | None = None,
            cfg: dict | None = None, state_dir: Path = STATE_DIR) -> dict:
    """Schedule the run for the slot it was started for: private upload with publishAt, then tick the list."""
    from . import publish_youtube

    cfg = config() if cfg is None else cfg
    if not cfg["enabled"]:
        raise ValueError("autopost is disabled (local/preferences.yaml autopost.enabled)")
    at = (at or now()).astimezone(JST)
    run_dir = Path(run_dir).resolve()
    # The lock covers the one-video-per-slot check, the upload and the state update.
    with _locked(state_dir):
        state = load_state(state_dir)
        name = _current(state, at.date())
        if name is None:
            if any(_filled(entry, at.date()) for entry in _today(state, at.date()).values()):
                raise ValueError(f"{at.date()} already has a scheduled video and no started run")
            raise ValueError("run `vg daily start` for today's request first")
        entry = state["days"][name]
        base = run_dir.parent.parent if run_dir.parent.name == "variants" else run_dir
        if base != (ROOT / entry["run"]).resolve():
            raise ValueError(f"{run_dir} is not today's run ({entry['run']})")
        plan_at = entry["slot"]
        if _filled(entry, at.date()):
            raise ValueError(f"the slot {plan_at} already has a scheduled video")
        if datetime.fromisoformat(plan_at) - timedelta(minutes=int(cfg["min_lead_minutes"])) < at:
            raise ValueError(f"too late for the slot {plan_at}")
        if dry_run or not confirm:
            return publish_youtube.preview(run_dir, "private", plan_at)
        # The journal lives with the run, so a session killed during an upload for any earlier slot is
        # reconciled against the channel before this run can be uploaded again.
        journal_path = run_dir / UPLOAD_JOURNAL
        result, earlier = None, None
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            result = _find_upload(run_dir, journal)
            if result is not None and journal.get("slot", plan_at) != plan_at:
                earlier = journal["slot"]  # uploaded for an earlier slot, which it already used
        if result is None:
            from .media import load

            _atomic_write(journal_path, json.dumps({"at": datetime.now(timezone.utc).isoformat(), "slot": plan_at,
                                                    "title": load(run_dir, "publish").title},
                                                   ensure_ascii=False) + "\n")
            result = publish_youtube.upload(run_dir, privacy="private", publish_at=plan_at)
        journal_path.unlink(missing_ok=True)
        variant = str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir)
        done = dict(status="scheduled", video_id=result["video_id"], url=result["url"], variant=variant,
                    scheduled_at=datetime.now(timezone.utc).isoformat())
        state = load_state(state_dir)
        if earlier:
            slot_key = key(datetime.fromisoformat(earlier))
            day = state["days"].setdefault(slot_key, {"request": entry["request"], "run": entry["run"], "errors": []})
            day.update(done, slot=earlier, recovered=True)
            state["days"][name]["status"] = "superseded"
        else:
            state["days"][name].update(done)
        save_state(state, state_dir)
    when = label(datetime.fromisoformat(earlier or plan_at))
    add_note(cfg, entry["request"], f"{when} 公開予約 {result['url']}", mark="x")
    if earlier:
        raise ValueError(f"this request was already uploaded for {when} (recovered after an interrupted upload); "
                         "this slot is still open: take the next item with `vg daily next`")
    return result | {"publish_at": plan_at}



def _find_upload(run_dir: Path, journal: dict) -> dict | None:
    """After an interrupted upload: the channel's video with the journal's title uploaded since then, or None.

    Raises ValueError when YouTube cannot be asked, so an uncertain outcome never leads to a second upload.
    """
    from .publish_youtube import _save_json, _service

    title = journal["title"]
    since = datetime.fromisoformat(journal["at"]) - timedelta(minutes=10)
    yt = uploads = page = None
    for _ in range(20):  # 50 uploads per page; stop at the first page entirely older than the journal
        try:
            if yt is None:
                yt = _service()
                channel = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
                uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
            response = yt.playlistItems().list(part="snippet", playlistId=uploads, maxResults=50,
                                               pageToken=page).execute()
        except Exception as exc:
            raise ValueError(f"an earlier upload may have succeeded and YouTube cannot be checked now: {exc}") from exc
        rows = response.get("items", [])
        for row in rows:
            snippet = row["snippet"]
            added = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            if snippet["title"] == title and added >= since:
                video_id = snippet["resourceId"]["videoId"]
                record = {"video_id": video_id, "url": f"https://youtube.com/shorts/{video_id}",
                          "privacy": "private", "uploaded_at": snippet["publishedAt"],
                          "thumbnail": "unknown (recovered)", "captions": "unknown (recovered)", "recovered": True}
                _save_json(run_dir / "published_youtube.json", record)
                return record
        page = response.get("nextPageToken")
        older = all(datetime.fromisoformat(r["snippet"]["publishedAt"].replace("Z", "+00:00")) < since for r in rows)
        if not page or older:
            return None
    raise ValueError("an earlier upload may have succeeded: too many uploads to check; verify in YouTube Studio")


# ---------------------------------------------------------------- trends
def _seconds(iso: str) -> int | None:
    match = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not match:
        return None
    d, h, m, s = (int(x or 0) for x in match.groups())
    return ((d * 24 + h) * 60 + m) * 60 + s


def history(limit: int = 30, cfg: dict | None = None) -> dict:
    """The list (the user's own requests) plus the channel's recent uploads with statistics."""
    cfg = config() if cfg is None else cfg
    out: dict = {"list": [{"text": i.text, "done": i.done, "notes": i.notes} for i in read_items(cfg)],
                 "videos": []}
    try:
        from .publish_youtube import _service

        yt = _service()
        channel = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        ids: list[str] = []
        page = None
        while len(ids) < limit:
            response = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50,
                                               pageToken=page).execute()
            ids += [row["contentDetails"]["videoId"] for row in response.get("items", [])]
            page = response.get("nextPageToken")
            if not page:
                break
        ids = ids[:limit]
        today = now()
        for start_index in range(0, len(ids), 50):
            response = yt.videos().list(part="snippet,statistics,contentDetails,status",
                                        id=",".join(ids[start_index:start_index + 50])).execute()
            for video in response.get("items", []):
                stats, snippet, status_ = video.get("statistics", {}), video["snippet"], video.get("status", {})
                if status_.get("privacyStatus") == "private" and not status_.get("publishAt"):
                    continue  # old hidden uploads are not part of the channel's current trend
                published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
                days = max((today - published).total_seconds() / 86400, 1 / 24)
                views = int(stats.get("viewCount", 0))
                out["videos"].append({
                    "id": video["id"], "title": snippet["title"], "published": snippet["publishedAt"],
                    "privacy": status_.get("privacyStatus"), "publish_at": status_.get("publishAt"),
                    "seconds": _seconds(video.get("contentDetails", {}).get("duration", "")),
                    "views": views, "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                    "views_per_day": round(views / days, 1)})
    except Exception as exc:  # trends still work from the list alone
        out["api_error"] = f"{type(exc).__name__}: {exc}"
    if out["videos"]:
        try:
            by_id = _analytics([v["id"] for v in out["videos"]], min(v["published"][:10] for v in out["videos"]))
            for video in out["videos"]:
                video.update(by_id.get(video["id"], {}))
        except Exception as exc:  # the permission may not be granted yet; views/likes still work
            out["analytics_error"] = f"{type(exc).__name__}: {exc}"
    return out


ANALYTICS_METRICS = {"averageViewPercentage": "avg_view_percent", "averageViewDuration": "avg_view_seconds",
                     "shares": "shares", "subscribersGained": "subscribers_gained"}


def _analytics(video_ids: list[str], start: str) -> dict[str, dict]:
    """Retention and sharing per video from the YouTube Analytics API (about two days behind)."""
    from .publish_youtube import analytics_service

    yta = analytics_service()
    out: dict[str, dict] = {}
    for first in range(0, len(video_ids), 200):
        ids = video_ids[first:first + 200]
        response = yta.reports().query(ids="channel==MINE", startDate=start, endDate=now().date().isoformat(),
                                       metrics=",".join(ANALYTICS_METRICS), dimensions="video",
                                       filters="video==" + ",".join(ids), maxResults=200).execute()
        names = [header["name"] for header in response.get("columnHeaders", [])]
        for row in response.get("rows", []):
            values = dict(zip(names, row))
            out[values["video"]] = {ANALYTICS_METRICS[key]: values[key] for key in ANALYTICS_METRICS if key in values}
    return out
