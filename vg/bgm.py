"""Deterministic BGM suggestions using mood overlap and recent packaged videos."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re

from . import registry
from .media import load
from .run import OUTBOX
from .schemas import BgmTrack, TypeSpec


def suggest(run: Path, n: int = 3, *, tracks: dict[str, BgmTrack] | None = None,
            types: dict[str, TypeSpec] | None = None, outbox: Path | None = None) -> list[dict]:
    if n < 1:
        raise ValueError("n must be positive")
    tracks = registry.load_bgm() if tracks is None else tracks
    types = registry.load_types() if types is None else types
    outbox = OUTBOX if outbox is None else Path(outbox)
    brief, route = load(run, "brief"), load(run, "route")
    spec = types[route.type_id]
    text = " ".join([spec.id, spec.name, spec.description, brief.tone or ""]).casefold()
    words = set(re.findall(r"[\w]+", text.replace("_", " ")))
    # Support both legacy packages and outbox/<run>/<variant>/publish.json.
    published = [*outbox.glob("*/publish.json"), *outbox.glob("*/*/publish.json")]
    recent = sorted(published, key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)[:10]
    used = Counter(json.loads(path.read_text(encoding="utf-8")).get("music_track") for path in recent)
    ranked = []
    for track in tracks.values():
        matches = sorted({tag for tag in track.mood if tag and (
            tag.casefold() in words or (re.search(r"[^\x00-\x7f]", tag) and tag.casefold() in text))})
        penalty = used[track.id]
        score = len(matches) - penalty
        reasons = ["雰囲気一致: " + "、".join(matches) if matches else "雰囲気タグの一致なし"]
        if penalty:
            reasons.append(f"直近10本で{penalty}回使用（-{penalty}）")
        ranked.append({"id": track.id, "title": track.title, "artist": track.artist,
                       "score": score, "reasons": reasons})
    return sorted(ranked, key=lambda row: (-row["score"], row["id"]))[:n]
