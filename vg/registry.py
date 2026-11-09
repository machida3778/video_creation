"""Load and check the video-type and voice registries; map a request's wording to a voice."""
from __future__ import annotations

from pathlib import Path

import yaml

from .schemas import BgmTrack, SfxSpec, Style, TypeSpec, VoiceSpec

ROOT = Path(__file__).resolve().parents[1]
TYPES_DIR = ROOT / "types"
VOICES_FILE = ROOT / "config" / "voices.yaml"
BGM_FILE = ROOT / "config" / "bgm.yaml"
STYLE_FILE = ROOT / "config" / "style.yaml"
SFX_FILE = ROOT / "config" / "sfx.yaml"
CHARACTERS_FILE = ROOT / "config" / "characters.yaml"
PREFERENCES_FILE = ROOT / "local" / "preferences.yaml"
LEGACY_PREFERENCES_FILE = ROOT / "config" / "preferences.yaml"


def load_style(path: Path | None = None) -> dict:
    """Validated tempo rules, with defaults for absent files and omitted keys."""
    path = Path(path) if path is not None else STYLE_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Style.model_validate({} if data is None else data).model_dump()


def load_sfx(path: Path | None = None) -> dict[str, SfxSpec]:
    path = Path(path) if path is not None else SFX_FILE
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    effects = {}
    for item in data.get("effects", []):
        effect = SfxSpec.model_validate(item)
        if effect.id in effects:
            raise ValueError(f"duplicate SFX id {effect.id}")
        effects[effect.id] = effect
    return effects


def load_bgm(path: Path | None = None) -> dict[str, BgmTrack]:
    """Accept a YAML list or {tracks: [...]}; an absent library is empty."""
    path = Path(path) if path is not None else BGM_FILE
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = data.get("tracks", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list of BGM tracks")
    tracks = {}
    for item in items:
        track = BgmTrack.model_validate(item)
        if track.id in tracks:
            raise ValueError(f"duplicate BGM id {track.id}")
        tracks[track.id] = track
    return tracks


def load_preferences(path: Path | None = None) -> dict:
    if path is None:
        path = PREFERENCES_FILE if PREFERENCES_FILE.exists() else LEGACY_PREFERENCES_FILE
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: preferences must be a mapping")
    return data


def load_types(types_dir: Path = TYPES_DIR) -> dict[str, TypeSpec]:
    types = {}
    for path in sorted(types_dir.glob("*/type.yaml")):
        spec = TypeSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if spec.id != path.parent.name:
            raise ValueError(f"{path}: id '{spec.id}' does not match its directory")
        types[spec.id] = spec
    return types


def timing_for_run(run: Path) -> str:
    """Legacy runs without a route retain narration timing."""
    from .media import load
    if not (Path(run) / "route.json").is_file():
        return "narration"
    spec = load_types().get(load(run, "route").type_id)
    return spec.timing if spec else "narration"


def load_voice_defaults(path: Path = VOICES_FILE) -> dict[str, str]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("defaults", {})


def cast_for_type(spec: TypeSpec, voices: dict[str, VoiceSpec], defaults: dict[str, str],
                  requested: dict[str, str | None] | None = None) -> dict[str, str]:
    """role -> voice id for a type. A voice the request named wins when it is active and its terms allow
    the type's content; otherwise the type's preference list is walked. Raises when a role has no usable voice."""
    requested = requested or {}
    cast = {}
    for role, prefs in spec.voice.items():
        chain = [requested.get(role)] + [defaults.get(role, defaults.get("narrator")) if p == "default" else p
                                         for p in prefs]
        for voice_id in chain:
            voice = voices.get(voice_id) if voice_id else None
            if voice and voice.lang == "ja" and voice.status == "active" and not set(voice.restricted_content) & set(spec.content):
                cast[role] = voice_id
                break
        else:
            raise ValueError(f"type {spec.id}: no active voice allowed for role '{role}' (tried {chain})")
    return cast


def load_voices(path: Path = VOICES_FILE) -> dict[str, VoiceSpec]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    voices = {}
    for item in data.get("voices", []):
        voice = VoiceSpec.model_validate(item)
        if voice.id in voices:
            raise ValueError(f"duplicate voice id {voice.id}")
        if not voice.commercial_ok:
            raise ValueError(f"{voice.id}: not usable commercially")
        voices[voice.id] = voice
    defaults = data.get("defaults", {})
    for role, voice_id in defaults.items():
        if voice_id not in voices:
            raise ValueError(f"default {role} -> unknown voice {voice_id}")
    return voices


def load_characters(path: Path | None = None) -> dict[str, dict]:
    """Original portrait recipes keyed by id. Loading never opens or generates portrait media."""
    import re
    from pathlib import PurePosixPath

    path = Path(path) if path is not None else CHARACTERS_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("characters"), list):
        raise ValueError(f"{path}: expected characters: [...]")
    voices = load_voices()
    characters = {}
    fields = ("id", "name", "role", "voice_id", "color", "portrait", "prompt", "license")
    for item in data["characters"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in fields):
            raise ValueError(f"{path}: character requires nonempty strings for {fields}")
        key = item["id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key) or key in characters:
            raise ValueError(f"{path}: invalid or duplicate character id {key!r}")
        if item["role"] not in ("questioner", "explainer"):
            raise ValueError(f"{key}: role must be questioner or explainer")
        if item["voice_id"] not in voices:
            raise ValueError(f"{key}: unknown voice_id {item['voice_id']!r}")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", item["color"]):
            raise ValueError(f"{key}: color must be #RRGGBB")
        if type(item.get("seed")) is not int or item["seed"] < 0:
            raise ValueError(f"{key}: seed must be a nonnegative integer")
        portrait = PurePosixPath(item["portrait"])
        if (portrait.is_absolute() or ".." in portrait.parts
                or portrait.parts[:3] != ("assets", "media", "characters") or portrait.suffix != ".png"):
            raise ValueError(f"{key}: portrait must be a PNG under assets/media/characters/")
        characters[key] = dict(item)
    return characters


GENDER_WORDS = {"male": ("男性", "男の", "男声", "おじさん", "青年", "少年"),
                "female": ("女性", "女の", "女声", "お姉さん", "少女", "アナウンサー風の女性")}


def resolve_voice(raw: str | None, voices: dict[str, VoiceSpec], role: str = "narrator", *, lang="ja") -> str | None:
    """Best voice id for the wording in a request, or None (then the type default applies).

    1. an alias or the display name appears in the text (longest match wins);
    2. otherwise score gender and tone words; ties and zero scores return None so that
       the LLM step (which sees the whole registry) decides.
    """
    if not raw:
        return None
    voices = {key: voice for key, voice in voices.items() if voice.lang == lang}
    # A voice named explicitly wins whatever its usual role (「ずんだもんで」 -> ずんだもん narrates).
    hits = [(len(alias), v.id) for v in voices.values() for alias in [v.name, *v.aliases] if alias and alias in raw]
    if hits:
        return max(hits)[1]
    candidates = [v for v in voices.values() if v.role in (role, "both") or role != "narrator"]
    scored = []
    for v in candidates:
        score = sum(2 for g, words in GENDER_WORDS.items() if v.gender == g and any(w in raw for w in words))
        score += sum(1 for t in v.tone if t in raw)
        scored.append((score, v.id))
    scored.sort(reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]
