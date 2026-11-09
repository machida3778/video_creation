"""TTS stage: script.json + config/voices.yaml -> audio/<line>.wav + timings.json.

VOICEVOX (local engine, scripts/setup/voicevox.sh): audio_query gives mora lengths, from which we derive
phrase spans; spans are scaled to the real WAV length (synthesis adds frame rounding and interrogative
upspeak that the query does not show). Lines with `yomi` are synthesized from the reading instead of the
display text.
Irodori narrators are synthesized in one GPU batch and timed from their WAVs;
they provide no phrase spans.
"""
from __future__ import annotations

import io
import hashlib
import math
import os
import wave
from pathlib import Path

import httpx

from . import narrator
from . import media
from .registry import load_voices
from .schemas import Asset, LineTiming, Manifest, Script, Span, Storyboard, Timings, VoiceSpec

VOICEVOX_URL = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
SAMPLE_RATE = 48000
LEAD_IN = 0.05  # speech starts at once: the first seconds decide whether viewers stay
LINE_GAP = 0.03  # tight tempo between lines (shorts); use pause_after for a deliberate beat


def music_timings(script: Script, board: Storyboard) -> Timings:
    """Display slots, without synthesis: one storyboard scene per script line."""
    ids = [lid for scene in board.scenes for lid in scene.line_ids]
    if any(len(scene.line_ids) != 1 for scene in board.scenes) or ids != [line.id for line in script.lines]:
        raise ValueError("music storyboard must cover every script line exactly once, in order, one per scene")
    cursor, lines = 0.0, []
    for scene in board.scenes:
        sec = scene.props.get("sec")
        if isinstance(sec, bool) or not isinstance(sec, (int, float)) or not math.isfinite(sec) or sec <= 0:
            raise ValueError(f"scene {scene.id}: music timing requires finite positive props.sec")
        lines.append(LineTiming(line_id=scene.line_ids[0], audio="", start=round(cursor, 6),
                                end=round(cursor + sec, 6)))
        cursor += sec
    return Timings(sample_rate=SAMPLE_RATE, total_sec=round(cursor, 6), lines=lines)


class Voicevox:
    def __init__(self, base: str = VOICEVOX_URL, timeout: float = 120):
        self.http = httpx.Client(base_url=base, timeout=timeout, trust_env=False)
        self._styles: dict[str, int] | None = None

    def version(self) -> str:
        return self.http.get("/version").raise_for_status().json()

    def style_id(self, speaker: str) -> int:
        """'キャラ名/スタイル名' -> style id from GET /speakers."""
        if self._styles is None:
            self._styles = {f"{sp['name']}/{st['name']}": st["id"]
                            for sp in self.http.get("/speakers").raise_for_status().json() for st in sp["styles"]}
        if speaker not in self._styles:
            raise KeyError(f"VOICEVOX has no style '{speaker}'")
        return self._styles[speaker]

    def query(self, text: str, style: int, voice: VoiceSpec) -> dict:
        q = self.http.post("/audio_query", params={"text": text, "speaker": style}).raise_for_status().json()
        q.update(speedScale=voice.speed, pitchScale=voice.pitch, intonationScale=voice.intonation,
                 prePhonemeLength=0.02, postPhonemeLength=0.03, outputSamplingRate=SAMPLE_RATE,
                 outputStereo=False)
        return q

    def synthesis(self, q: dict, style: int) -> bytes:
        return self.http.post("/synthesis", params={"speaker": style}, json=q).raise_for_status().content


def phrase_spans(q: dict) -> tuple[list[Span], float]:
    """Accent-phrase spans (kana text) and the predicted total length, in seconds of output audio."""
    speed = q.get("speedScale", 1.0) or 1.0
    pause_scale = q.get("pauseLengthScale", 1.0)
    t = q.get("prePhonemeLength", 0.1)
    spans = []
    for phrase in q["accent_phrases"]:
        start = t
        for mora in phrase["moras"]:
            t += (mora.get("consonant_length") or 0.0) + mora["vowel_length"]
        text = "".join(m["text"] for m in phrase["moras"])
        if phrase.get("is_interrogative"):
            t += 0.15  # upspeak mora added at synthesis
        spans.append(Span(text=text, start=start / speed, end=t / speed))
        if phrase.get("pause_mora"):
            t += phrase["pause_mora"]["vowel_length"] * pause_scale
    t += q.get("postPhonemeLength", 0.1)
    return spans, t / speed


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def synthesize(run: Path, script: Script, voices: dict[str, VoiceSpec] | None = None,
               engine: Voicevox | None = None) -> Timings:
    voices = voices or load_voices()
    effective = {role: voices[script.cast[role]].model_copy(update=script.voice_overrides.get(role, {}))
                 for role in dict.fromkeys(line.speaker for line in script.lines)}
    for voice in effective.values():
        if voice.engine not in ("voicevox", "irodori", "kokoro"):
            raise NotImplementedError(f"engine {voice.engine} ({voice.id}) is not wired yet")
    for line in script.lines:
        if Path(line.id).name != line.id or line.id in ("", ".", ".."):
            raise ValueError(f"invalid audio line id: {line.id!r}")
    narrator_lines = [line for line in script.lines if effective[line.speaker].engine == "irodori"]
    narrated = narrator.synthesize(run, narrator_lines, effective) if narrator_lines else {}
    (run / "audio").mkdir(exist_ok=True)
    kokoro = _kokoro_lines(run, script, effective)
    cursor, lines = LEAD_IN, []
    for line in script.lines:
        voice = effective[line.speaker]
        rel = f"audio/{line.id}.wav"
        if voice.engine in ("irodori", "kokoro"):
            data = Path((kokoro if voice.engine == "kokoro" else narrated)[line.id]).read_bytes()
            spans, predicted = [], 0.0
        else:
            engine = engine or Voicevox()
            style = engine.style_id(voice.speaker)
            q = engine.query(line.yomi or line.text, style, voice)
            data = engine.synthesis(q, style)
            spans, predicted = phrase_spans(q)
            (run / rel).write_bytes(data)
        actual = wav_seconds(data)
        k = actual / predicted if predicted > 0 else 1.0
        lines.append(LineTiming(
            line_id=line.id, audio=rel, start=round(cursor, 3), end=round(cursor + actual, 3),
            spans=[Span(text=s.text, start=round(cursor + s.start * k, 3), end=round(cursor + s.end * k, 3))
                   for s in spans]))
        cursor += actual + line.pause_after + LINE_GAP
    return Timings(sample_rate=SAMPLE_RATE, total_sec=round(cursor, 3), lines=lines)


def _kokoro_lines(run: Path, script: Script, effective: dict[str, VoiceSpec]) -> dict[str, Path]:
    """Use the existing offline batch worker, with content-addressed raw WAVs for safe retries."""
    from . import english
    jobs, outputs, raw = [], {}, {}
    for line in script.lines:
        voice = effective[line.speaker]
        if voice.engine != "kokoro":
            continue
        if (voice.lang != "en" or voice.status != "active" or not voice.commercial_ok
                or voice.model not in (None, english.CHECKPOINT) or voice.speaker not in ("af_heart", "am_michael")
                or not math.isfinite(voice.speed) or voice.speed <= 0):
            raise ValueError(f"{voice.id}: requires an active commercial English Kokoro voice and positive speed")
        text = line.yomi or line.text
        token = hashlib.sha256((voice.model_dump_json() + text).encode()).hexdigest()[:24]
        path = (run / "audio" / f"kokoro_{token}.wav").resolve()
        raw[line.id] = path
        outputs[line.id] = run / "audio" / f"{line.id}.wav"
        if not path.is_file() and not any(j["output"] == str(path) for j in jobs):
            jobs.append({"text": text, "voice": voice.speaker, "speed": voice.speed, "output": str(path)})
    if jobs:
        english.synthesize(jobs)
    for lid, path in raw.items():
        media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                   "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(outputs[lid])])
    return outputs


def record_voices(manifest: Manifest | None, script: Script, timings: Timings,
                  voices: dict[str, VoiceSpec] | None = None, engine_version: str = "") -> Manifest:
    """Replace the manifest's voice assets with one per synthesized line (licence + required credit)."""
    voices = voices or load_voices()
    manifest = manifest or Manifest()
    speaker = {line.id: line.speaker for line in script.lines}
    keep = [a for a in manifest.assets if a.kind != "voice" or a.params.get("lang") == "en"]
    for t in timings.lines:
        v = voices[script.cast[speaker[t.line_id]]]
        if script.voice_overrides.get(speaker[t.line_id]):
            v = v.model_copy(update=script.voice_overrides[speaker[t.line_id]])
        tool = (f"irodori:{v.model or narrator.CHECKPOINT}" if v.engine == "irodori" else
                f"kokoro:{v.model or 'hexgrad/Kokoro-82M'}" if v.engine == "kokoro" else
                f"{v.engine}{':' + engine_version if engine_version else ''}:{v.speaker or v.model}")
        keep.append(Asset(id=f"voice_{t.line_id}", path=t.audio, kind="voice", origin="generated",
                          tool=tool,
                          params={"voice_id": v.id, "speed": v.speed},
                          license="MIT" if v.engine == "irodori" else v.license,
                          license_url=v.license_url, commercial_ok=v.commercial_ok, credit=v.credit))
    manifest.assets = keep
    return manifest
