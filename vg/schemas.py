"""Stage artifacts of one video run (v0). Each stage writes one of these as JSON into runs/<id>/.

brief.json -> route.json -> research.json -> script.json -> factcheck.json -> timings.json
-> storyboard.json -> assets/manifest.json -> timeline.json -> qa.json -> publish.json

LLM steps (Claude) write brief/route/research/script/factcheck/storyboard; deterministic tools (vg) write the rest.
Times are seconds on the final video's clock; paths are relative to the run directory.
"""
from __future__ import annotations

from typing import Any, Literal
import warnings

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

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
    format: str | None = None       # local/formats/<id>/format.json learned from a reference video


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
    start: float | None = Field(None, ge=0, allow_inf_nan=False)  # source caption, output clock
    end: float | None = Field(None, ge=0, allow_inf_nan=False)


class Script(Spec):
    title: str
    lines: list[Line] = Field(min_length=1)
    cast: dict[str, str] = {"narrator": ""}   # role -> voice_id (config/voices.yaml)
    hook_options: list[str] = []    # alternative first lines considered (>= 4 across >= 3 hook types)
    search_query: str | None = None  # main search phrase; used in the description and on-screen text
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


class EnglishLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str = Field(min_length=1, pattern=r"\S")


class English(Spec):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    lines: list[EnglishLine] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        ids = [line.id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate English line ids")
        return self

    def validate_script(self, script: Script):
        expected, actual = {line.id for line in script.lines}, {line.id for line in self.lines}
        if expected != actual:
            raise ValueError(f"English coverage: missing={sorted(expected - actual)}, extras={sorted(actual - expected)}")
        return self


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


class ClipSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    crop: Literal["fill", "fit"] | None = None
    focus_x: float | None = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def _duration(self):
        if self.end - self.start < 0.6 - 1e-9:
            raise ValueError("each segment must last at least 0.6 seconds (end > start)")
        return self


class ClipPlan(Spec):
    source: str
    license_id: str = Field(min_length=1)
    guideline: str | None = None
    segments: list[ClipSegment] = Field(min_length=1)  # output order, not source order

    @field_validator("source", "guideline")
    @classmethod
    def _url(cls, value):
        if value is not None:
            HttpUrl(value)
        return value

    @property
    def total_sec(self) -> float:
        return round(sum(s.end - s.start for s in self.segments), 6)

    @model_validator(mode="after")
    def _segments(self):
        if len({s.id for s in self.segments}) != len(self.segments):
            raise ValueError("duplicate segment ids")
        if self.total_sec > 59:
            raise ValueError("clip total must be <= 59 seconds")
        return self


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
    gain_db: float = -20.0  # legacy hint; render measures and sets the voice-relative gain


class Storyboard(Spec):
    type_id: str
    scenes: list[Scene] = []
    music: Music | None = None
    format: str | None = None       # format id (route.format); its layout is applied by vg timeline
    header: str | None = None       # text of the format's fixed header band, e.g. the video's question

    @model_validator(mode="after")
    def _scenes(self):
        if not self.scenes and self.type_id != "clip_highlight":
            raise ValueError("storyboard requires scenes")
        return self


def visible_chars(text: str) -> int:
    return sum(not char.isspace() for char in text)


class CoverOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    template: Literal["question", "reveal", "ranking"]
    text: str
    emphasis: list[str] = []
    badge: str | None = None
    # A scene image from the manifest, or a cover-only image: give `prompt` (and optionally negative/seed)
    # and `vg assets` generates it as cover_<id> (docs/THUMBNAILS.md 「表紙専用の画像」).
    image: str = ""
    prompt: str | None = None
    negative: str | None = None
    seed: int | None = None
    line_id: str = Field(min_length=1)
    palette: Literal["yellow", "red", "white"]
    ai_generated: bool
    photorealistic: bool

    @property
    def cover_image_id(self) -> str:
        return f"cover_{self.id}"  # scene images are always img_<scene>, so the names never collide

    @model_validator(mode="after")
    def _headline(self):
        if self.id == "sheet":
            raise ValueError("cover id 'sheet' is reserved for the contact sheet")
        if self.prompt is not None:
            if not self.prompt.strip():
                raise ValueError("cover prompt must not be empty")
            if self.image and self.image != self.cover_image_id:
                raise ValueError(f"a cover with its own prompt uses image {self.cover_image_id!r}")
            self.image = self.cover_image_id
        elif not self.image:
            raise ValueError("cover needs an image (manifest id) or a prompt for a cover-only image")
        lines = self.text.split("\n")
        chars = visible_chars(self.text)
        if not 1 <= chars <= 20 or len(lines) > 3 or any(not line.strip() for line in lines):
            raise ValueError("cover headline requires 1–20 visible chars and 1–3 nonempty lines")
        if any(ord(char) < 32 and char != "\n" for char in self.text):
            raise ValueError("cover headline contains control characters")
        if chars > 12:
            warnings.warn("cover headline exceeds recommended 12 visible chars", UserWarning, stacklevel=2)
        if self.badge is not None and self.template != "ranking":
            raise ValueError("badge is only allowed for ranking")
        if self.badge is not None and (not self.badge.strip() or "\n" in self.badge):
            raise ValueError("badge must be a nonempty single line")
        if any(not word or word not in self.text for word in self.emphasis):
            raise ValueError("emphasis words must occur in the headline")
        return self


class Covers(Spec):
    options: list[CoverOption] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _unique(self):
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate cover ids")
        files = [f"{option.id}{suffix}" for option in self.options for suffix in
                 (".jpg", "_120.jpg", "_180.jpg", "_3x4.jpg", "_4x5.jpg", "_1x1.jpg")]
        if len(set(files)) != len(files):
            raise ValueError("cover ids collide with preview filenames")
        return self


class CoverScore(BaseModel):
    """One option scored by the independent judge on the eight rubric items of docs/THUMBNAILS.md (① to ⑧)."""
    model_config = ConfigDict(extra="forbid")
    id: str
    items: list[int] = Field(min_length=8, max_length=8)
    note: str = ""

    @model_validator(mode="after")
    def _range(self):
        if any(type(value) is not int or not 0 <= value <= 2 for value in self.items):
            raise ValueError(f"{self.id}: every rubric item is scored 0, 1 or 2")
        return self

    @property
    def total(self) -> int:
        return sum(self.items)


class CoverJudgement(Spec):
    """The judge's answer, recorded with `vg thumbnail RUN:vN --score FILE` into covers_report.json."""
    judge: str = "claude-subagent"
    round: int = Field(default=1, ge=1, le=3)   # 2 after the one redo the rubric allows
    options: list[CoverScore] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _unique(self):
        ids = [option.id for option in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate cover ids in the judgement")
        return self


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


# ---------------------------------------------------------------- layout (format profiles -> renderer)
# Coordinates are pixels on the 1080x1920 frame. Essential text stays inside the safe area used by QA:
# x 120-900 and y 240-1440 (the Shorts UI covers the top bar, the right-hand buttons and the bottom
# ~480 px with the title and channel name).
Color = str


class TextBand(BaseModel):
    """A fixed band of text drawn over every scene (e.g. a header that keeps the question visible)."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str | None = None                # header: filled per video from Storyboard.header
    top: float = Field(260, ge=240, le=1300)
    height: float = Field(220, gt=40, le=600)
    background: Color | None = "#000000"   # None = transparent band
    color: Color = "#ffffff"
    accent: Color = "#ffd400"              # colour of `emphasis` words
    stroke: Color | None = None
    size: float = Field(64, ge=28, le=110)
    weight: int = Field(900, ge=400, le=900)
    emphasis: list[str] = []

    @model_validator(mode="after")
    def _inside(self):
        if self.top + self.height > 1440 + 1e-6:
            raise ValueError("band must end above y=1440 (Shorts bottom UI)")
        return self


class MediaFrame(BaseModel):
    """Where the scene's image/video sits: full bleed, or a box with a blurred or flat fill around it."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["full", "box"] = "full"
    top: float = Field(480, ge=0, le=1800)
    height: float = Field(800, gt=100, le=1920)
    side: float = Field(0, ge=0, le=300)    # left/right margin of the box
    radius: float = Field(0, ge=0, le=80)
    fill: Literal["blur", "color"] = "blur"
    fill_color: Color = "#000000"
    dim: float = Field(0.0, ge=0, le=0.9)  # darken the media (full mode) so text stays readable


class CaptionLook(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    y: float = Field(1215, ge=700, le=1400)    # centre of the caption block
    size: float = Field(66, ge=36, le=96)
    fill: Color = "#ffffff"
    stroke: Color | None = "#111111"
    stroke_ratio: float = Field(0.2, ge=0, le=0.35)
    accent: Color = "#ffd400"
    box: Color | None = None                    # background box behind each caption page
    weight: int = Field(900, ge=400, le=900)
    max_width: float = Field(780, ge=400, le=780)
    animation: Literal["pop", "none"] = "pop"

    @model_validator(mode="after")
    def _inside(self):
        # Two lines (emphasis 1.2x) around y must stay above the Shorts bottom UI.
        if self.y + self.size * 1.28 * 1.1 > 1440 + 1e-6:
            raise ValueError("caption block must end above y=1440 (Shorts bottom UI)")
        return self


class Layout(BaseModel):
    """Presentation of a format; Timeline.layout carries it to the renderer (None = default look)."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    background: Color = "#0f1419"
    media: MediaFrame = MediaFrame()
    header: TextBand | None = None
    footer: TextBand | None = None
    caption: CaptionLook = CaptionLook()
    title: CaptionLook | None = None            # title_card/hook_title text; None = renderer default
    progress_bar: Color | None = None           # thin bar at y=240 that fills over the video

    @model_validator(mode="after")
    def _no_overlap(self):
        bands = [b for b in (self.header, self.footer) if b is not None]
        if len(bands) == 2 and (self.header.top < self.footer.top + self.footer.height
                                and self.footer.top < self.header.top + self.header.height):
            raise ValueError("header and footer bands overlap")
        # Caption pages (normal and title-style) are two lines around their y, emphasis 1.2x.
        for look in [self.caption] + ([self.title] if self.title else []):
            half = look.size * 1.28 * 1.1
            for band in bands:
                if band.top < look.y + half and look.y - half < band.top + band.height:
                    raise ValueError("caption block overlaps a text band")
        # Overlay components (rank cards, character pairs) are scaled into the space between the header
        # and the captions; keep at least 420 px (scale 0.35 of the 1200 px safe area).
        top = self.header.top + self.header.height + 10 if self.header else 240
        if self.caption.y - self.caption.size * 1.45 - top < 420:
            raise ValueError("leave at least 420 px between the header band and the captions")
        return self


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
    fade_in: float = Field(0.0, ge=0, allow_inf_nan=False)  # seconds
    fade_out: float = Field(0.0, ge=0, allow_inf_nan=False)
    duration: float | None = Field(None, gt=0, allow_inf_nan=False)  # measured source seconds


class Timeline(Spec):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    duration: float = Field(gt=0)
    clips: list[Clip]
    captions: list[Caption] = []
    audio: list[AudioClip] = []
    layout: Layout | None = None    # from the storyboard's format; None keeps the default look

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
    search_query: str | None = None
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
    max_static_sec: float = Field(6.0, gt=0)
    min_speech_cps: float = Field(7.0, ge=0)
    caption_coverage_min: float = Field(0.85, ge=0, le=1)
    originality_max_similarity: float = Field(0.6, ge=0, le=1)
    bgm_below_voice_db: float = Field(10.0, ge=0)
    sfx_below_voice_db: float = Field(6.0, ge=0)
    bgm_fade_in_sec: float = Field(0.3, ge=0)
    bgm_fade_out_sec: float = Field(1.0, ge=0)
    sfx_max_per_10s: int = Field(3, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _legacy_audio_keys(cls, data):
        if isinstance(data, dict):
            data = {key: value for key, value in data.items() if key != "sfx_gain_db"}
        return data


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
    timing: Literal["narration", "music", "fixed", "source"]
    length: dict[Platform, Length]
    research: dict[str, Any] = {"required": False}
    rights: dict[str, Any] = {}
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


class FormatBeat(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    role: str                        # hook, setup, item, reveal, twist, payoff, outro ...
    share: float = Field(gt=0, le=1)  # fraction of the running time
    note: str                        # what happens on screen and in the narration


class FormatPacing(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    median_shot_sec: float = Field(gt=0)      # measured on the reference
    max_static_sec: float = Field(gt=0)       # storyboard target (QA still applies config/style.yaml)
    speech_cps: float | None = Field(None, ge=0)  # reference narration, display characters per second
    narration_speed: float = Field(1.3, ge=0.8, le=1.8)   # VOICEVOX speed to match the reference
    target_sec: float = Field(45, gt=0, le=59)


class FormatReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str                      # URL or local file the format was learned from
    title: str | None = None
    channel: str | None = None
    duration_sec: float | None = None
    analyzed: str | None = None      # local/formats/<id>/reference/analysis.json


class FormatProfile(BaseModel):
    """local/formats/<id>/format.json: a reusable video format learned from a reference the user liked.
    It copies presentation and structure only; scripts, images and audio are always made anew."""
    model_config = ConfigDict(extra="forbid")
    schema_version: int = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,40}$")
    name: str                        # Japanese display name
    summary: str                     # one or two sentences: what makes the format work
    references: list[FormatReference] = Field(min_length=1)
    types: list[str] = Field(min_length=1)    # implemented types (types/*/type.yaml) it is used with
    structure: list[FormatBeat] = Field(min_length=2)
    pacing: FormatPacing
    layout: Layout = Layout()
    script_rules: list[str] = []     # hook pattern, sentence length, tone, punchline, ending
    visual_rules: list[str] = []     # image kind (photo, character art, illustration), motion, colour
    audio_rules: list[str] = []      # BGM mood/tempo, SFX usage, voice character
    header_rule: str | None = None   # how to write Storyboard.header when layout.header is set
    avoid: list[str] = []            # what not to copy (logos, catchphrases, the reference's own content)

    @model_validator(mode="after")
    def _shares(self):
        total = sum(beat.share for beat in self.structure)
        if not 0.95 <= total <= 1.05:
            raise ValueError(f"structure shares must sum to 1 (got {total:.2f})")
        if self.layout.header is not None and not self.header_rule:
            raise ValueError("header_rule is required when the layout has a header band")
        return self


ContentFlag = Literal["political", "religious", "criticism", "adult"]


class VoiceSpec(BaseModel):
    """config/voices.yaml entry: a concrete voice the request can name."""
    model_config = ConfigDict(extra="forbid")
    id: str
    lang: Literal["ja", "en"] = "ja"
    engine: Literal["voicevox", "irodori", "qwen3_tts", "aivisspeech", "style_bert_vits2", "kokoro"]
    status: Literal["active", "pending"] = "active"   # pending = reference voice not made / not auditioned yet
    speaker: str | None = None      # VOICEVOX "キャラ名/スタイル名" (style id resolved from GET /speakers)
    model: str | None = None        # HF model id for narrator engines
    voice_description: str | None = None   # voice-design prompt (irodori / qwen3_tts)
    reference: str | None = None    # directory of frozen ref_*.wav (assets/media/voices/...), own or licensed
    seed: int | None = None         # fixed narrator seed; omitted = stable seed derived from voice id
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
    "qa": QAReport, "publish": Publish, "covers": Covers, "en": English, "clips": ClipPlan,
}
