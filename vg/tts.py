"""TTS stage: script.json + config/voices.yaml -> audio/<line>.wav + timings.json.

VOICEVOX (local engine, scripts/setup/voicevox.sh): audio_query gives mora lengths, from which we derive
phrase spans; spans are scaled to the real WAV length (synthesis adds frame rounding and interrogative
upspeak that the query does not show). Lines with `yomi` are synthesized from the reading instead of the
display text.
"""
from __future__ import annotations

import io
import os
import wave
from pathlib import Path

import httpx

from .registry import load_voices
from .schemas import Asset, LineTiming, Manifest, Script, Span, Timings, VoiceSpec

VOICEVOX_URL = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
SAMPLE_RATE = 48000
LEAD_IN = 0.05  # speech starts at once: the first seconds decide whether viewers stay
LINE_GAP = 0.03  # tight tempo between lines (shorts); use pause_after for a deliberate beat


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
    engine = engine or Voicevox()
    (run / "audio").mkdir(exist_ok=True)
    cursor, lines = LEAD_IN, []
    for line in script.lines:
        voice = voices[script.cast[line.speaker]]
        if script.voice_overrides.get(line.speaker):
            voice = voice.model_copy(update=script.voice_overrides[line.speaker])
        if voice.engine != "voicevox":
            raise NotImplementedError(f"engine {voice.engine} ({voice.id}) is not wired yet")
        style = engine.style_id(voice.speaker)
        q = engine.query(line.yomi or line.text, style, voice)
        data = engine.synthesis(q, style)
        spans, predicted = phrase_spans(q)
        actual = wav_seconds(data)
        k = actual / predicted if predicted > 0 else 1.0
        rel = f"audio/{line.id}.wav"
        (run / rel).write_bytes(data)
        lines.append(LineTiming(
            line_id=line.id, audio=rel, start=round(cursor, 3), end=round(cursor + actual, 3),
            spans=[Span(text=s.text, start=round(cursor + s.start * k, 3), end=round(cursor + s.end * k, 3))
                   for s in spans]))
        cursor += actual + line.pause_after + LINE_GAP
    return Timings(sample_rate=SAMPLE_RATE, total_sec=round(cursor, 3), lines=lines)


def record_voices(manifest: Manifest | None, script: Script, timings: Timings,
                  voices: dict[str, VoiceSpec] | None = None, engine_version: str = "") -> Manifest:
    """Replace the manifest's voice assets with one per synthesized line (licence + required credit)."""
    voices = voices or load_voices()
    manifest = manifest or Manifest()
    speaker = {line.id: line.speaker for line in script.lines}
    keep = [a for a in manifest.assets if a.kind != "voice"]
    for t in timings.lines:
        v = voices[script.cast[speaker[t.line_id]]]
        keep.append(Asset(id=f"voice_{t.line_id}", path=t.audio, kind="voice", origin="generated",
                          tool=f"{v.engine}{':' + engine_version if engine_version else ''}:{v.speaker or v.model}",
                          params={"voice_id": v.id, "speed": v.speed}, license=v.license,
                          license_url=v.license_url, commercial_ok=v.commercial_ok, credit=v.credit))
    manifest.assets = keep
    return manifest
