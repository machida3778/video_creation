"""Optional English metadata, picture-timed captions and a manually uploaded dub."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import textwrap
from types import SimpleNamespace

from . import assets, narrator, registry
from .media import FFMPEG, ROOT, load, probe, run, save
from .mix import measure_level, render_audio
from .render import loudnorm, write_srt
from .schemas import Asset, AudioClip, Caption, Manifest, Timings, LineTiming

CHECKPOINT = "hexgrad/Kokoro-82M"
MANUAL_AUDIO = "upload manually in YouTube Studio > Subtitles > Add language > Audio"


def validate(run_dir):
    """Cross-artifact validation shared by generation, QA, package and publish."""
    directory = Path(run_dir)
    english = load(directory, "en").validate_script(load(directory, "script"))
    required = []
    if (directory / "research.json").is_file():
        required += [f"- {s.title} {s.url}" for s in load(directory, "research").sources]
    if (directory / "assets/manifest.json").is_file():
        required += [line.strip() for a in load(directory, "manifest").assets if a.credit
                     for line in a.credit.splitlines() if line.strip()]
    if (directory / "publish.json").is_file():
        # Preserve independently supplied source/credit sections, too.
        section = False
        for line in load(directory, "publish").description.splitlines():
            if not line.strip():
                section = False
            elif line.strip().lower().rstrip(":：") in ("出典", "sources", "credits", "クレジット"):
                section = True
            elif section or re.search(r"https?://", line):
                required.append(line.strip())
    description_lines = {line.strip() for line in english.description.splitlines()}
    missing = [line for line in dict.fromkeys(required) if line not in description_lines]
    if missing:
        raise ValueError(f"English description must keep source/credit lines verbatim: {missing}")
    return english


def audio_duration(path):
    info = probe(path)
    stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
    duration = float(stream.get("duration", info["format"]["duration"]))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"invalid audio duration: {path}")
    return duration


def ordered_timings(script, timings, duration):
    ids = [t.line_id for t in timings.lines]
    if len(set(ids)) != len(ids) or set(ids) != {line.id for line in script.lines}:
        raise ValueError("Japanese timings must cover every script line exactly once")
    ordered = sorted(timings.lines, key=lambda t: t.start)
    for i, timing in enumerate(ordered):
        limit = ordered[i+1].start if i+1 < len(ordered) else duration
        if not (math.isfinite(timing.start) and math.isfinite(timing.end)
                and 0 <= timing.start < timing.end <= limit + 1e-6):
            raise ValueError(f"{timing.line_id}: invalid/overlapping Japanese timings")
    return ordered


def captions(english, ordered):
    texts = {line.id: line.text for line in english.lines}
    result = []
    for timing in ordered:
        wrapped = textwrap.wrap(" ".join(texts[timing.line_id].split()), width=42,
                                break_long_words=True, break_on_hyphens=False)
        groups = ["\n".join(wrapped[i:i+2]) for i in range(0, len(wrapped), 2)]
        weights = [len(group.replace("\n", " ")) for group in groups]
        total, cursor = sum(weights), 0
        for group, weight in zip(groups, weights):
            start = timing.start + (timing.end-timing.start)*cursor/total
            cursor += weight
            end = timing.start + (timing.end-timing.start)*cursor/total
            if round(end*1000) <= round(start*1000):
                raise ValueError(f"{timing.line_id}: too much English subtitle text for its time span")
            result.append(Caption(start=start, end=end, text=group))
    return result


def word_rate(text, seconds):
    return len(re.findall(r"\b\w+(?:['’-]\w+)*\b", text)) / seconds


def synthesize(jobs, *, cpu=False):
    """One model load across all variants. Injectable at generate_many's boundary."""
    if not jobs:
        return
    spec = Path(jobs[0]["output"]).parent / "kokoro_jobs.json"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(json.dumps({"checkpoint": CHECKPOINT, "device": "cpu" if cpu else "cuda",
                                "jobs": [{**j, "output": assets.remote(j["output"])} for j in jobs]},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    narrator._run_on_lab_gpu("workers/tts_kokoro/.venv/bin/python workers/tts_kokoro/synth.py "
                           f"--spec {shlex.quote(assets.remote(spec))}", 1024 if cpu else 2048)


def fingerprint(directory, voice, *, english=None, legacy: bool = False):
    """Invalidate fitting after edits/rerenders; large media are identified by stat. The English title and
    description do not change the dub or captions (v2), so adding a credit to the description keeps the build
    fresh; `legacy` computes the v1 key of builds made before 2026-11-10, which hashed the whole en.json.
    `english`: an English spec to use instead of RUN/en.json (to verify an older build against its inputs)."""
    directory = Path(directory)
    digest = hashlib.sha256(b"english-v1" if legacy else b"english-v2")
    english = load(directory, "en") if english is None else english
    digest.update((english.model_dump_json() if legacy
                   else english.model_dump_json(exclude={"title", "description"})).encode())
    for name in ("script", "timings", "timeline"):
        digest.update(load(directory, name).model_dump_json().encode())
    digest.update(voice.model_dump_json().encode())
    timeline = load(directory, "timeline")
    paths = [directory / "final.mp4"] + [directory / a.src for a in timeline.audio]
    paths += [directory / c.props["src"] for c in timeline.clips
              if c.component == "video_clip" and c.props.get("muted") is False]
    for path in paths:
        stat = path.stat()
        digest.update(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def generate_many(directories, *, tts=None, cpu=False):
    """Resume content-addressed WAVs and rebuild only stale fitted dubs."""
    voices = registry.load_voices()
    voice = voices[registry.load_voice_defaults()["narrator_en"]]
    if (voice.lang != "en" or voice.engine != "kokoro" or voice.status != "active" or not voice.commercial_ok
            or voice.model not in (None, CHECKPOINT) or voice.speaker not in ("af_heart", "am_michael")
            or not math.isfinite(voice.speed) or voice.speed <= 0):
        raise ValueError("defaults.narrator_en must be an active commercial English Kokoro voice")
    jobs, entries = [], []
    for directory in map(lambda p: Path(p).resolve(), directories):
        english = validate(directory)
        duration = audio_duration(directory / "final.mp4")
        ordered = ordered_timings(load(directory, "script"), load(directory, "timings"), duration)
        output = directory / "en"
        output.mkdir(exist_ok=True)
        key = fingerprint(directory, voice)
        marker = output / "build.json"
        previous = json.loads(marker.read_text()) if marker.is_file() else {}
        if (previous.get("fingerprint") in (key, fingerprint(directory, voice, legacy=True))
                and (directory / "dub_en.m4a").is_file()
                and (directory / "final_en.srt").is_file() and (output / "timings.json").is_file()):
            continue
        marker.unlink(missing_ok=True)
        (directory / "dub_en.m4a").unlink(missing_ok=True)
        by_id = {}
        for line in english.lines:
            token = hashlib.sha256((voice.model_dump_json() + line.text).encode()).hexdigest()[:24]
            path = output / f"raw_{token}.wav"
            by_id[line.id] = path
            if not path.is_file() and not any(j["output"] == str(path) for j in jobs):
                jobs.append({"text": line.text, "voice": voice.speaker, "speed": voice.speed, "output": str(path)})
        entries.append((directory, english, ordered, by_id, duration, key))
    if jobs:
        (tts or synthesize)(jobs, cpu=cpu)
    for directory, english, ordered, by_id, duration, key in entries:
        timeline = load(directory, "timeline").model_copy(deep=True)
        japanese = [a for a in timeline.audio if a.kind == "voice"]
        if not japanese:
            raise ValueError("English dubbing requires Japanese voice clips in timeline.json")
        reference = measure_level([directory / a.src for a in japanese], [a.gain_db for a in japanese])["level_db"]
        fitted, clips, speeds = [], [], {}
        for i, timing in enumerate(ordered):
            source = by_id[timing.line_id]
            length = audio_duration(source)
            slot = (ordered[i+1].start if i+1 < len(ordered) else duration) - timing.start
            speed = max(1.0, length/slot)
            if speed > 1.15 + 1e-6:
                raise ValueError(f"{timing.line_id}: English dub needs {speed:.3f}x (max 1.15x); shorten this English line")
            speeds[timing.line_id] = speed
            dest = directory / "en" / f"fit_{i:03}.wav"
            run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(source), "-af",
                 f"atempo={speed:.9f}", "-ar", "48000", "-c:a", "pcm_s24le", str(dest)])
            actual = audio_duration(dest)
            if actual > slot + 1/48000:
                raise ValueError(f"{timing.line_id}: fitted audio exceeds its slot; shorten this English line")
            relative = str(dest.relative_to(directory))
            fitted.append(LineTiming(line_id=timing.line_id, audio=relative, start=timing.start,
                                     end=timing.start+actual))
            clips.append(AudioClip(start=timing.start, src=relative, kind="voice", duration=actual))
        level = measure_level([directory / a.src for a in clips])["level_db"]
        if reference is None or level is None:
            raise ValueError("cannot match silent/unmeasurable narration")
        for clip in clips:
            clip.gain_db = reference-level
        timeline.audio = clips + [a for a in timeline.audio if a.kind != "voice"]
        mixed = render_audio(directory, timeline, directory / "en" / "mix.wav", duration=duration)
        target = measure_level([directory / "final.mp4"])["integrated_lufs"]
        if target is None or not -70 < target < -5:
            raise ValueError("Japanese audio loudness is outside loudnorm's supported range")
        temporary = directory / "en" / "dub.m4a"
        loudnorm(mixed, temporary, target_i=target, audio_only=True)
        if abs(audio_duration(temporary)-duration) > 0.05:
            raise ValueError("English dub audio duration differs from Japanese by more than 50 ms")
        if abs(measure_level([temporary])["integrated_lufs"]-target) > 1:
            raise ValueError("English dub loudness differs from Japanese by more than 1 LU")
        save(directory / "en", "timings", Timings(sample_rate=48000, total_sec=duration, lines=fitted))
        write_srt(SimpleNamespace(captions=captions(english, ordered)), directory / "final_en.srt")
        manifest = load(directory, "manifest") if (directory / "assets/manifest.json").is_file() else Manifest()
        manifest.assets = [a for a in manifest.assets if not a.id.startswith("voice_en_")]
        for i, timing in enumerate(fitted):
            manifest.assets.append(Asset(id=f"voice_en_{i:03}", path=timing.audio, kind="voice", origin="generated",
                tool=f"kokoro:{CHECKPOINT}", params={"voice_id": voice.id, "lang": "en", "line_id": timing.line_id,
                    "text": next(l.text for l in english.lines if l.id == timing.line_id), "speed": voice.speed,
                    "atempo": speeds[timing.line_id]},
                license=voice.license, license_url=voice.license_url, commercial_ok=voice.commercial_ok, credit=voice.credit))
        save(directory, "manifest", manifest)
        temporary.replace(directory / "dub_en.m4a")
        (directory / "en" / "build.json").write_text(json.dumps({"fingerprint": key, "voice_id": voice.id}) + "\n")


def generate(directory, **kwargs):
    return generate_many([directory], **kwargs)
