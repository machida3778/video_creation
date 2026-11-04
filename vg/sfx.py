"""Original procedural effects synthesized with local ffmpeg, without samples or APIs."""
from __future__ import annotations

import math
from pathlib import Path

from . import registry
from .media import FFMPEG, ROOT, run

# Deterministic 48 kHz mono sources with bounded envelopes and no external samples.
SOURCES = {
    "whoosh": (0.35, "anoisesrc=color=white:seed=71:sample_rate=48000:duration=0.35,"
               "highpass=f=300,asendcmd=c='0.0 lowpass f 600;0.05 lowpass f 1200;"
               "0.10 lowpass f 2400;0.15 lowpass f 4000;0.20 lowpass f 6000;0.27 lowpass f 9000',"
               "lowpass=f=600,"
               "volume='0.5*sin(PI*t/0.35)*sin(PI*t/0.35)':eval=frame,"
               "afade=t=in:d=0.04,afade=t=out:st=0.22:d=0.13"),
    "pop": (0.08, "aevalsrc=0.65*sin(2*PI*(950*t-3500*t*t))*exp(-65*t):s=48000:d=0.08,"
            "afade=t=in:d=0.002,afade=t=out:st=0.06:d=0.02"),
    "ding": (0.6, "aevalsrc=(0.38*sin(2*PI*1400*t)+0.2*sin(2*PI*2100*t))*exp(-8*t):s=48000:d=0.6,"
             "afade=t=in:d=0.003,afade=t=out:st=0.5:d=0.1"),
    "tick": (0.025, "aevalsrc=0.65*sin(2*PI*3200*t)*exp(-400*t):s=48000:d=0.025,"
             "afade=t=in:d=0.001,afade=t=out:st=0.015:d=0.01"),
}


def events(value) -> list[tuple[str, float]]:
    """Normalize a scene's sfx prop and reject malformed IDs or offsets before rendering."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [{"id": value, "at": 0}]
    if not isinstance(value, list):
        raise ValueError("sfx must be an id or a list of {id, at}")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "at"} or not isinstance(item.get("id"), str):
            raise ValueError("sfx entries must be {id, at}")
        offset = item.get("at", 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or offset < 0:
            raise ValueError("sfx at must be a finite nonnegative number")
        result.append((item["id"], float(offset)))
    return result


def asset_id(effect_id: str) -> str:
    return f"sfx_{effect_id}"


def build(*, library_root: Path | None = None) -> list[Path]:
    root = Path(library_root) if library_root is not None else ROOT
    outputs = []
    for effect in registry.load_sfx().values():
        if effect.id not in SOURCES:
            raise ValueError(f"unknown procedural SFX {effect.id!r}")
        duration, source = SOURCES[effect.id]
        output = root / effect.file
        output.parent.mkdir(parents=True, exist_ok=True)
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", source,
             "-t", str(duration), "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(output)])
        outputs.append(output)
    return outputs
