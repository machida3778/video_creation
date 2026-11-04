"""Stage artifacts of one video run (v0). Each stage writes one of these as JSON into runs/<id>/.

brief.json -> route.json -> research.json -> script.json -> factcheck.json -> timings.json
-> storyboard.json -> assets/manifest.json -> timeline.json -> qa.json -> publish.json

LLM steps (Claude) write brief/route/research/script/factcheck/storyboard; deterministic tools (vg) write the rest.
Times are seconds on the final video's clock; paths are relative to the run directory.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 0
Platform = Literal["youtube_shorts", "tiktok", "x"]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------- brief / route
class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str | None = None          # the user's words, e.g. 「落ち着いた男性ナレーターで」
    voice_id: str | None = None     # resolved entry of config/voices.yaml (None = type default)


class Brief(Spec):
    request: str                    # the original one-line request, verbatim
    topic: str
    language: Literal["ja"] = "ja"
    platforms: list[Platform] = ["youtube_shorts"]
    target_sec: float = Field(55, gt=0)
    max_sec: float = Field(60, gt=0)
    tone: str | None = None
    voice: VoiceRequest = VoiceRequest()
    cast: dict[str, VoiceRequest] = {}   # dialogue types: role -> voice request
    constraints: list[str] = []


class Route(Spec):
    type_id: str
    params: dict[str, Any] = {}     # variant parameters of the type
    confidence: float = Field(ge=0, le=1)
    reason: str
    new_type: bool = False          # True when /new-video-type created type_id for this request


# ---------------------------------------------------------------- research / script
class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # "S1"
    url: str
    title: str
    publisher: str | None = None
    published: str | None = None    # ISO date if known
    accessed: str                   # ISO date


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # "F1"
    claim: str
    source_ids: list[str]
    status: Literal["confirmed", "reported", "disputed"] = "reported"


class Research(Spec):
    sources: list[Source] = []
    facts: list[Fact] = []
    notes: str | None = None


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # "L01"
    speaker: str = "narrator"       # role key; resolved through Script.cast
    text: str                       # display text (subtitle)
    yomi: str | None = None         # kana reading for TTS when it differs from text
    emphasis: list[str] = []        # words to highlight in the subtitle
    fact_ids: list[str] = []        # every factual claim cites Research.facts
    pause_after: float = 0.0


class Script(Spec):
    title: str
    lines: list[Line] = Field(min_length=1)
    cast: dict[str, str] = {"narrator": ""}   # role -> voice_id (config/voices.yaml)
    # role -> per-video overrides of the registry voice: {"speed": 1.4, "pitch": 0.0, "intonation": 1.2}
    voice_overrides: dict[str, dict[str, float]] = {}

    @model_validator(mode="after")
    def _ids_and_cast(self):
        ids = [line.id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate line ids")
        missing = {line.speaker for line in self.lines} - set(self.cast)
        if missing:
            raise ValueError(f"speakers without a cast entry: {sorted(missing)}")
        return self


class FactCheckItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str
    verdict: Literal["supported", "unsupported", "overstated", "not_factual"]
    comment: str = ""


class FactCheck(Spec):
    checker: str                    # e.g. "claude-subagent", "codex"
    items: list[FactCheckItem]
    passed: bool


# ---------------------------------------------------------------- audio timings
class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    start: float
    end: float


class LineTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str
    audio: str                      # audio/L01.wav
    start: float
    end: float
    spans: list[Span] = []          # phrase / mora level timing when the engine gives it


class Timings(Spec):
    sample_rate: int
    total_sec: float
    lines: list[LineTiming]


# ---------------------------------------------------------------- storyboard
class Visual(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["t2i", "i2v", "t2v", "s2v", "library", "text", "chart", "none"]
    prompt: str | None = None
    negative: str | None = None
    ref: str | None = None          # keyframe / reference asset id
    seed: int | None = None
    params: dict[str, Any] = {}


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # "C01"
    line_ids: list[str]             # contiguous script lines this scene covers
    component: str                  # render component, e.g. ken_burns_image, title_card, video_clip
    props: dict[str, Any] = {}      # component-specific; validated by the component registry
    visual: Visual = Visual(kind="none")
    caption_style: str | None = None
    transition: str | None = None


class Music(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: str | None = None        # config/bgm.yaml track id (vg assets copies it in and records the licence)
    asset: str | None = None        # manifest asset id (set by vg assets for library tracks)
    prompt: str | None = None
    gain_db: float = -20.0  # under the voice; ducked further while someone speaks


class Storyboard(Spec):
    type_id: str
    scenes: list[Scene] = Field(min_length=1)
    music: Music | None = None


# ---------------------------------------------------------------- assets
class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    path: str
    kind: Literal["voice", "image", "video", "music", "sfx", "font", "character_art", "other"]
    origin: Literal["generated", "library", "rendered"]
    tool: str | None = None         # model or engine id (docs/MODELS.md), e.g. voicevox:3
    params: dict[str, Any] = {}     # prompt, seed, steps ... (reproducibility)
    license: str
    license_url: str | None = None
    commercial_ok: bool
    credit: str | None = None       # text the license requires in the description


class Manifest(Spec):
    assets: list[Asset] = []


# ---------------------------------------------------------------- timeline (renderer input)
class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    end: float
    layer: int = 0
    component: str
    props: dict[str, Any] = {}      # asset paths resolved


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    end: float
    text: str
    style: str = "default"
    emphasis: list[str] = []
    speaker: str | None = None


class AudioClip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    src: str
    kind: Literal["voice", "music", "sfx"]
    gain_db: float = 0.0
    duck_under_voice: bool = False


class Timeline(Spec):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration: float = Field(gt=0)
    clips: list[Clip]
    captions: list[Caption] = []
    audio: list[AudioClip] = []

    @model_validator(mode="after")
    def _within_duration(self):
        for item in [*self.clips, *self.captions]:
            if not 0 <= item.start < item.end <= self.duration + 1e-6:
                raise ValueError(f"item outside 0..{self.duration}: {item.start}-{item.end}")
        return self


# ---------------------------------------------------------------- QA / publish
class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # e.g. duration, loudness, safe_area, yomi, facts, rights
    passed: bool
    severity: Literal["block", "warn"] = "block"
    detail: str = ""
    redo_stage: str | None = None   # stage to go back to when it fails


class QAReport(Spec):
    attempt: int = 1
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed or c.severity == "warn" for c in self.checks)


class Publish(Spec):
    title: str
    description: str                # includes sources and required credits
    hashtags: list[str] = []
    platforms: list[Platform]
    synthetic_media: bool           # disclosure flag for realistic AI-generated content
    video: str = "final.mp4"
    captions: str | None = "final.srt"
    music_track: str | None = None  # library id, for recent-use penalties


# ---------------------------------------------------------------- registries (types/, config/voices.yaml)
class Style(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    first_text_max_sec: float = Field(0.3, ge=0)
    first_speech_max_sec: float = Field(0.3, ge=0)
    max_static_sec: float = Field(3.5, gt=0)
    min_speech_cps: float = Field(7.0, ge=0)
    caption_coverage_min: float = Field(0.85, ge=0, le=1)
    originality_max_similarity: float = Field(0.6, ge=0, le=1)
    sfx_gain_db: float = -14
    sfx_max_per_10s: int = Field(3, ge=0)


class SfxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    file: str
    license: str
    commercial_ok: bool

    @model_validator(mode="after")
    def _commercial_file(self):
        from pathlib import PurePosixPath
        path = PurePosixPath(self.file)
        if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("assets", "sfx") or len(path.parts) < 3:
            raise ValueError("SFX file must be a path under assets/sfx/")
        if not self.commercial_ok:
            raise ValueError(f"{self.id}: not usable commercially")
        return self


class BgmTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    title: str
    artist: str
    file: str                      # repository-relative assets/media/bgm/... path
    license: str
    license_url: str
    credit: str | None = None
    commercial_ok: bool
    mood: list[str] = []
    bpm: float | None = Field(default=None, gt=0)
    length_sec: float | None = Field(default=None, gt=0)
    gain_db: float | None = None

    @model_validator(mode="after")
    def _commercial_file(self):
        from pathlib import PurePosixPath
        path = PurePosixPath(self.file)
        if path.is_absolute() or ".." in path.parts or path.parts[:3] != ("assets", "media", "bgm") or len(path.parts) < 4:
            raise ValueError("BGM file must be a path under assets/media/bgm/")
        if not self.commercial_ok:
            raise ValueError(f"{self.id}: not usable commercially")
        return self


class Length(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: float
    max: float
    min: float = 0


class TypeSpec(BaseModel):
    """types/<id>/type.yaml: one production recipe."""
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str                       # Japanese display name
    status: Literal["stable", "experimental", "planned"] = "planned"
    version: int = 1
    priority: Literal["P1", "P2", "P3"] = "P2"
    description: str
    use_when: list[str] = []        # example requests / situations (router hints)
    avoid_when: list[str] = []
    pipeline: Literal["narration_stills", "narration_video", "dialogue", "music_motion", "data_animation",
                      "generated_video", "clipping"]
    timing: Literal["narration", "music", "fixed"]
    length: dict[Platform, Length]
    research: dict[str, Any] = {"required": False}
    # role -> voice ids in order of preference ("default" = config/voices.yaml defaults.<role>, or
    # defaults.narrator); the first active voice whose terms allow the type's `content` is used.
    voice: dict[str, list[str]] = {"narrator": ["default"]}
    components: list[str] = []
    models: list[str] = []          # docs/MODELS.md keys the type needs
    params: dict[str, Any] = {}     # variant parameters and their defaults
    qa: list[str] = []
    content: list[ContentFlag] = []  # sensitive content the type may contain (checked against voice terms)
    risks: list[str] = []
    monetization: str = ""          # why it can count as original / editorial content


ContentFlag = Literal["political", "religious", "criticism", "adult"]


class VoiceSpec(BaseModel):
    """config/voices.yaml entry: a concrete voice the request can name."""
    model_config = ConfigDict(extra="forbid")
    id: str
    engine: Literal["voicevox", "irodori", "qwen3_tts", "aivisspeech", "style_bert_vits2", "kokoro"]
    status: Literal["active", "pending"] = "active"   # pending = reference voice not made / not auditioned yet
    speaker: str | None = None      # VOICEVOX "キャラ名/スタイル名" (style id resolved from GET /speakers)
    model: str | None = None        # HF model id for narrator engines
    voice_description: str | None = None   # voice-design prompt (irodori / qwen3_tts)
    reference: str | None = None    # frozen reference audio (assets/voices/...), our own or licensed
    name: str                       # display name, e.g. ずんだもん
    aliases: list[str] = []         # words that select this voice in a request
    gender: Literal["male", "female", "neutral"] | None = None
    tone: list[str] = []            # e.g. 落ち着いた, 明るい, ニュース, 低め
    role: Literal["narrator", "character", "both"] = "both"
    speed: float = 1.0
    pitch: float = 0.0
    intonation: float = 1.0
    license: str
    license_url: str
    credit: str | None = None       # required credit text, e.g. VOICEVOX:ずんだもん
    commercial_ok: bool
    restricted_content: list[ContentFlag] = []   # content the voice's terms forbid
    notes: str = ""


STAGE_MODELS = {
    "brief": Brief, "route": Route, "research": Research, "script": Script, "factcheck": FactCheck,
    "timings": Timings, "storyboard": Storyboard, "manifest": Manifest, "timeline": Timeline,
    "qa": QAReport, "publish": Publish,
}
