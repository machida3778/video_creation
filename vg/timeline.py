"""Timeline stage: script + timings + storyboard (+ assets manifest) -> timeline.json (renderer input).

Scene s covers its lines' audio: it starts at its first line (scene 0 at 0 s) and lasts until the next
scene starts (the last one until the end). Scene props may carry:
  overlays: [{component, props}]  drawn on layer 1 for the scene's span (e.g. a source_card)
  captions: false                 no captions for the scene's lines (e.g. when a title card shows them)
Asset ids (visual.ref, props.src_asset) resolve to paths through assets/manifest.json.
A storyboard with `format` gets that format's layout (local/formats/<id>/format.json); `header` fills the
layout's header band.
"""
from __future__ import annotations

from .captions import band_text, captions_for_line
from .registry import load_style, load_types
from .sfx import asset_id, events
from .schemas import (AudioClip, Caption, Clip, ClipPlan, ClipSegment, Layout, Line, LineTiming, Manifest, Script, Storyboard, Timeline,
                      Timings)

TAIL = 0.5  # seconds after the last line


def _character_props(component: str, props: dict, line_ids: list[str], lines: dict[str, Line],
                     by_line: dict[str, LineTiming], start: float) -> dict:
    """Fill clip-local speaking intervals without modifying the storyboard's props."""
    result = dict(props)
    if component != "character_pair":
        return result
    roles = result.get("roles")
    if (not isinstance(roles, dict) or set(roles) != {"a", "b"}
            or not all(isinstance(role, str) and role for role in roles.values())
            or roles["a"] == roles["b"]):
        raise ValueError("character_pair requires distinct script roles: {a: role, b: role}")
    speakers = {role: speaker for speaker, role in roles.items()}
    speaking = []
    for lid in line_ids:
        role = lines[lid].speaker
        if role not in speakers:
            raise ValueError(f"character_pair: line {lid} speaker '{role}' is missing from props.roles")
        timing = by_line[lid]
        speaking.append({"speaker": speakers[role], "start": round(timing.start - start, 6),
                         "end": round(timing.end - start, 6)})
    result["speaking"] = speaking
    return result


def resolve_layout(board: Storyboard, layout: Layout | None = None) -> Layout | None:
    """The format's layout with the header band's text taken from the storyboard."""
    if layout is None and board.format:
        from .reference import load_format
        layout = load_format(board.format).layout
    if layout is None:
        if board.header:
            raise ValueError("storyboard.header needs a format whose layout has a header band")
        return None
    if layout.header is not None:
        text = board.header or layout.header.text
        if not text or not text.strip():
            raise ValueError(f"format {board.format!r} has a header band: set storyboard.header")
        text = band_text(text.strip(), layout.header.size)
        layout = layout.model_copy(update={"header": layout.header.model_copy(update={"text": text})})
    elif board.header:
        raise ValueError(f"format {board.format!r} has no header band; remove storyboard.header")
    return layout


def build(script: Script, timings: Timings, board: Storyboard | None = None, manifest: Manifest | None = None,
          fps: int = 30, layout: Layout | None = None, *, timing: str | None = None,
          clip_plan: ClipPlan | None = None, params: dict | None = None) -> Timeline:
    if board is None:
        if timing != "source":
            raise ValueError("narration and music timing require a storyboard")
        board = Storyboard(type_id="clip_highlight")
    if timing is None:
        spec = load_types().get(board.type_id)
        timing = spec.timing if spec else "narration"
    music = timing == "music"
    source = timing == "source"
    layout = resolve_layout(board, layout)
    by_line = {t.line_id: t for t in timings.lines}
    lines = {line.id: line for line in script.lines}
    assets = {a.id: a.path for a in (manifest.assets if manifest else [])}
    duration = round(timings.total_sec + (0 if music or source else TAIL), 6 if music or source else 3)
    roles = list(dict.fromkeys(line.speaker for line in script.lines))
    role_style = {r: ("speaker_a", "speaker_b")[i] for i, r in enumerate(roles[:2])} if len(roles) > 1 else {}

    missing = [lid for sc in board.scenes for lid in sc.line_ids if lid not in by_line]
    if missing:
        raise ValueError(f"storyboard refers to lines without audio: {missing}")
    covered = [lid for sc in board.scenes for lid in sc.line_ids]
    if not source and sorted(covered) != sorted(lines):
        raise ValueError("storyboard scenes must cover every script line exactly once")

    # Proportional variant remaps may retain scenes with no lines when the new script is shorter.
    scenes = [] if source else [sc for sc in board.scenes if sc.line_ids]
    starts = [0.0] + [by_line[sc.line_ids[0]].start for sc in scenes[1:]]
    clips, captions, effects = [], [], []
    source_audio = []
    if source:
        from .clips import source_timings
        if clip_plan is None:
            raise ValueError("source timing requires clips.json")
        if timings != source_timings(script, clip_plan):
            raise ValueError("source timings are stale; rerun vg clips")
        defaults = ClipSegment(id="defaults", start=0, end=1, **{
            k: v for k, v in (params or {}).items() if k in ("crop", "focus_x")})
        cursor = 0.0
        for segment in clip_plan.segments:
            src = f"clips/{segment.id}.mp4"
            if assets.get(segment.id) != src:
                raise ValueError(f"segment {segment.id}: not in manifest; run vg clips")
            end = round(cursor + segment.end - segment.start, 6)
            clips.append(Clip(start=cursor, end=end, component="video_clip", props={
                "src": src, "crop": segment.crop or defaults.crop or "fill",
                "focus_x": segment.focus_x if segment.focus_x is not None else (
                    defaults.focus_x if defaults.focus_x is not None else 0.5), "muted": True}))
            source_audio.append(AudioClip(start=cursor, src=f"clips/{segment.id}.wav", kind="voice",
                                          duration=end-cursor))
            cursor = end
        for line in script.lines:
            captions.extend(captions_for_line(line, by_line[line.id], "default", layout))
    mix_style = load_style()
    for i, sc in enumerate(scenes):
        start, end = starts[i], (starts[i + 1] if i + 1 < len(starts) else duration)
        props = {k: v for k, v in sc.props.items() if k not in ("overlays", "captions", "sfx")}
        if sc.transition is not None:
            props["transition"] = sc.transition
        # The label supplies the picture's escalation, without duplicating it in scene props.
        label = next((ov.get("props", {}) for ov in sc.props.get("overlays", [])
                      if ov["component"] == "rank_label"), None)
        if music and label is not None:
            props.update({k: label[k] for k in ("rank", "tier", "color", "reveal") if k in label})
        for effect_id, offset in events(sc.props.get("sfx")):
            key = asset_id(effect_id)
            if key not in assets or not any(a.id == key and a.kind == "sfx" for a in manifest.assets):
                raise ValueError(f"scene {sc.id}: SFX '{effect_id}' not in manifest")
            if start + offset >= end:
                raise ValueError(f"scene {sc.id}: SFX offset {offset} outside scene duration {end-start}")
            effects.append(AudioClip(start=start + offset, src=assets[key], kind="sfx"))
        ref = sc.visual.ref or props.pop("src_asset", None)
        if ref:
            if ref not in assets:
                raise ValueError(f"scene {sc.id}: asset '{ref}' not in manifest")
            props["src"] = assets[ref]
        props = _character_props(sc.component, props, sc.line_ids, lines, by_line, start)
        scene_clips = [Clip(start=start, end=end, layer=0, component=sc.component, props=props)]
        for ov in sc.props.get("overlays", []):
            overlay_props = _character_props(ov["component"], ov.get("props", {}), sc.line_ids, lines, by_line, start)
            scene_clips.append(Clip(start=start, end=end, layer=1, component=ov["component"], props=overlay_props))
        clips.extend(scene_clips)
        scene_role_style = dict(role_style)
        for clip in scene_clips:
            if clip.component == "character_pair":
                scene_role_style.update({role: f"speaker_{speaker}" for speaker, role in clip.props["roles"].items()})
        if not music and sc.props.get("captions", True):
            for lid in sc.line_ids:
                line = lines[lid]
                style = sc.caption_style or scene_role_style.get(line.speaker, "default")
                caption_timing = by_line[lid]
                if board.type_id == "english_drill" and line.pause_after > 0:
                    caption_timing = caption_timing.model_copy(update={
                        "end": min(end, caption_timing.end + line.pause_after), "spans": []})
                captions.extend(captions_for_line(line, caption_timing, style, layout))

    audio = (source_audio if source else [] if music else [
        AudioClip(start=t.start, src=t.audio, kind="voice") for t in timings.lines]) + effects
    if board.music and board.music.asset:
        if board.music.asset not in assets:
            raise ValueError(f"music asset '{board.music.asset}' not in manifest")
        audio.append(AudioClip(start=0.0, src=assets[board.music.asset], kind="music",
                               gain_db=board.music.gain_db, duck_under_voice=False,
                               fade_in=mix_style["bgm_fade_in_sec"], fade_out=mix_style["bgm_fade_out_sec"]))
    return Timeline(fps=fps, duration=duration, clips=clips,
                    captions=sorted(captions, key=lambda c: c.start), audio=audio, layout=layout)
