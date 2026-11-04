"""Run directories runs/<YYYYMMDD-HHMMSS>[-slug]/ and their state.json (stage status, attempts)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUTBOX = ROOT / "outbox"
# Order of a production. LLM stages (Claude) write brief…storyboard specs; vg runs tts, timeline … package.
STAGES = ["brief", "route", "research", "script", "factcheck", "tts", "storyboard", "assets", "timeline",
          "render", "qa", "package"]


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_run(request: str, slug: str | None = None) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", (slug or "").lower()).strip("-")[:40]
    run = RUNS / (f"{stamp}-{slug}" if slug else stamp)
    run.mkdir(parents=True)
    (run / "request.txt").write_text(request.rstrip() + "\n", encoding="utf-8")
    write_state(run, {"request": request, "created": now(), "stages": {}})
    return run


def resolve(name: str) -> Path:
    """A run/variant path, a name under runs/, 'latest', or any of those plus ':vN'."""
    name = str(name)
    if ":" in name:
        parent, vid = name.rsplit(":", 1)
        path = variant_dir(resolve(parent), vid)
        if (path / "state.json").is_file():
            return path.resolve()
        raise FileNotFoundError(f"no variant directory {path}")
    if name == "latest":
        runs = sorted(p for p in RUNS.iterdir() if (p / "state.json").is_file() and not p.name.startswith("_"))
        if not runs:
            raise FileNotFoundError("no runs yet")
        return runs[-1]
    path = Path(name)
    for cand in (path, RUNS / name):
        if (cand / "state.json").is_file():
            return cand.resolve()
    raise FileNotFoundError(f"no run directory {name}")


def variant_dir(run: Path, vid: str) -> Path:
    if not re.fullmatch(r"v[1-9][0-9]*", vid):
        raise ValueError(f"invalid variant id {vid!r}; expected v1, v2, ...")
    run = Path(run)
    if run.parent.name == "variants":
        raise ValueError("expected the shared run directory, not a variant")
    return run / "variants" / vid


def variants(run: Path) -> list[Path]:
    return sorted((p for p in (Path(run) / "variants").glob("v*")
                   if re.fullmatch(r"v[1-9][0-9]*", p.name) and (p / "variant.json").is_file()),
                  key=lambda p: int(p.name[1:]))


def new_variant(run: Path, vid: str | None = None, note: str = "") -> Path:
    """Create a self-contained specification directory; never reuse an existing variant."""
    run = Path(run)
    if vid is None:
        index = 1
        while variant_dir(run, f"v{index}").exists():
            index += 1
        vid = f"v{index}"
    path = variant_dir(run, vid)
    shared = [run / f"{name}.json" for name in ("brief", "route", "research")]
    for source in shared:
        if not source.is_file():
            raise FileNotFoundError(f"variant requires shared specification: {source}")
    path.mkdir(parents=True)
    try:
        for source in shared:
            shutil.copy2(source, path / source.name)
        created = now()
        (path / "variant.json").write_text(json.dumps({
            "id": vid, "created": created, "note": note,
            "parts": dict.fromkeys(("script", "voice", "visuals", "bgm")),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        parent_state = read_state(run) if (run / "state.json").is_file() else {}
        write_state(path, {"request": parent_state.get("request", ""), "created": created, "stages": {}})
    except Exception:
        shutil.rmtree(path)
        raise
    return path


def read_state(run: Path) -> dict:
    return json.loads((run / "state.json").read_text(encoding="utf-8"))


def write_state(run: Path, state: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=run, prefix=".state.", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, run / "state.json")


def mark(run: Path, stage: str, status: str, detail: str = "") -> None:
    """status: running | done | failed | skipped."""
    state = read_state(run)
    entry = state["stages"].get(stage, {"attempt": 0})
    if status == "running":
        entry["attempt"] = entry.get("attempt", 0) + 1
    entry.update(status=status, at=now(), detail=detail)
    state["stages"][stage] = entry
    write_state(run, state)
