"""Inspect the actual VOICEVOX reading before approving a script for QA."""
from __future__ import annotations

from pathlib import Path
import re
import unicodedata

from .media import load
from .registry import load_voices
from .tts import Voicevox

_KANJI = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af々〆ヵヶ"
_KANJI_RUN = re.compile(f"[{_KANJI}]{{3,}}")
_NAME_HINT = re.compile(
    f"[{_KANJI}ァ-ヺー・ｦ-ﾟ]+[ 　]*(?:さん|氏|社|市|県|様|くん|君|ちゃん|都|府|町|村|区|駅|大学)")


def reading_table(run_dir, engine=None) -> list[dict]:
    """Return display text, supplied yomi, engine kana and heuristic flags per line.

    ``engine`` accepts the same style_id/query interface as tts.Voicevox, so a
    fake can inspect readings without an HTTP connection or audio synthesis.
    Flags are hints for review, not a claim that a particular reading is wrong.
    """
    script = load(Path(run_dir), "script")
    voices = load_voices()
    owned = engine is None
    engine = engine if engine is not None else Voicevox()
    rows = []
    try:
        for line in script.lines:
            voice = voices[script.cast[line.speaker]]
            if script.voice_overrides.get(line.speaker):
                voice = voice.model_copy(update=script.voice_overrides[line.speaker])
            if voice.engine == "kokoro" and voice.lang == "en":
                rows.append({"line_id": line.id, "text": line.text, "yomi": line.yomi,
                             "engine_reading": "English (Kokoro); review the spoken audio",
                             "has_digits": False, "has_latin": False, "has_rare_kanji_run": False,
                             "has_proper_noun_hint": False, "flagged": False})
                continue
            if voice.engine != "voicevox":
                raise NotImplementedError(f"engine {voice.engine} ({voice.id}) is not wired yet")
            query = engine.query(line.yomi or line.text, engine.style_id(voice.speaker), voice)
            reading = "".join(
                "".join(mora["text"] for mora in phrase["moras"])
                + ("、" if phrase.get("pause_mora") else "")
                for phrase in query["accent_phrases"])
            flags = {
                "has_digits": any(c.isdecimal() for c in line.text),
                "has_latin": any("LATIN" in unicodedata.name(c, "") for c in line.text),
                "has_rare_kanji_run": bool(_KANJI_RUN.search(line.text)),
                "has_proper_noun_hint": bool(_NAME_HINT.search(line.text)),
            }
            rows.append({"line_id": line.id, "text": line.text, "yomi": line.yomi,
                         "engine_reading": reading, **flags,
                         "flagged": any(flags.values()) and not bool(line.yomi)})
    finally:
        if owned:
            engine.http.close()
    return rows
