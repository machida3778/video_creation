"""Timeline stage: script + timings + storyboard (+ assets manifest) -> timeline.json (renderer input).

Scene s covers its lines' audio: it starts at its first line (scene 0 at 0 s) and lasts until the next
scene starts (the last one until the end). Scene props may carry:
  overlays: [{component, props}]  drawn on layer 1 for the scene's span (e.g. a source_card)
  captions: false                 no captions for the scene's lines (e.g. when a title card shows them)
Asset ids (visual.ref, props.src_asset) resolve to paths through assets/manifest.json.
"""
from __future__ import annotations

from .captions import captions_for_line
from .registry import load_style
from .sfx import asset_id, events
from .schemas import AudioClip, Caption, Clip, Line, LineTiming, Manifest, Script, Storyboard, Timeline, Timings

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


def build(script: Script, timings: Timings, board: Storyboard, manifest: Manifest | None = None,
          fps: int = 30) -> Timeline:
    by_line = {t.line_id: t for t in timings.lines}
    lines = {line.id: line for line in script.lines}
    assets = {a.id: a.path for a in (manifest.assets if manifest else [])}
    duration = round(timings.total_sec + TAIL, 3)
    roles = list(dict.fromkeys(line.speaker for line in script.lines))
    role_style = {r: ("speaker_a", "speaker_b")[i] for i, r in enumerate(roles[:2])} if len(roles) > 1 else {}

    missing = [lid for sc in board.scenes for lid in sc.line_ids if lid not in by_line]
    if missing:
        raise ValueError(f"storyboard refers to lines without audio: {missing}")
    covered = [lid for sc in board.scenes for lid in sc.line_ids]
    if sorted(covered) != sorted(lines):
        raise ValueError("storyboard scenes must cover every script line exactly once")

    # Proportional variant remaps may retain scenes with no lines when the new script is shorter.
    scenes = [sc for sc in board.scenes if sc.line_ids]
    starts = [0.0] + [by_line[sc.line_ids[0]].start for sc in scenes[1:]]
    clips, captions, effects = [], [], []
    mix_style = load_style()
    for i, sc in enumerate(scenes):
        start, end = starts[i], (starts[i + 1] if i + 1 < len(starts) else duration)
        props = {k: v for k, v in sc.props.items() if k not in ("overlays", "captions", "sfx")}
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
        if sc.props.get("captions", True):
            for lid in sc.line_ids:
                line = lines[lid]
                style = sc.caption_style or scene_role_style.get(line.speaker, "default")
                captions.extend(captions_for_line(line, by_line[lid], style))

    audio = [AudioClip(start=t.start, src=t.audio, kind="voice") for t in timings.lines] + effects
    if board.music and board.music.asset:
        if board.music.asset not in assets:
            raise ValueError(f"music asset '{board.music.asset}' not in manifest")
        audio.append(AudioClip(start=0.0, src=assets[board.music.asset], kind="music",
                               gain_db=board.music.gain_db, duck_under_voice=False,
                               fade_in=mix_style["bgm_fade_in_sec"], fade_out=mix_style["bgm_fade_out_sec"]))
    return Timeline(fps=fps, duration=duration, clips=clips,
                    captions=sorted(captions, key=lambda c: c.start), audio=audio)
