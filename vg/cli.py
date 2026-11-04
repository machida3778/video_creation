"""vg: deterministic stages of the video harness.

  vg new "<request>" [--slug s]     create runs/<stamp>-<slug>/ and print its path
  vg validate <run>                 validate every stage JSON present in the run
  vg cast <run>                     print the allowed cast (role -> voice) for the run's type and request
  vg tts | timeline | render | qa | package <run>
  vg make <run> [--from STAGE]      tts -> timeline -> render -> qa -> package (stops at a failed QA)
  vg status <run>
  vg variant new RUN [--id vN] [--from vK] [--note ...]
  vg variant list RUN
  vg mix RUN --base v3 [--script vX] [--voice v1] [--visuals v3] [--bgm v2]
  vg bgm list | suggest RUN [--n 3]
  vg compare RUN
  vg feedback RUN "<text>" [--variant vN] | vg feedback RUN --show
  vg smoke                          fixed three-line run end to end (no GPU)
<run> is a run/variant path, a directory name under runs/, "latest", or "<run>:v2".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import run as runs
from .schemas import STAGE_MODELS

PIPELINE = ["tts", "assets", "timeline", "render", "qa", "package"]


def _media():
    from . import media  # written alongside (load/save helpers, ffmpeg paths)
    return media


def cmd_validate(run: Path) -> int:
    media, bad = _media(), 0
    for name in STAGE_MODELS:
        path = run / ("assets/manifest.json" if name == "manifest" else f"{name}.json")
        if path.is_file():
            try:
                media.load(run, name)
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001 - report every invalid file
                bad += 1
                print(f"BAD  {name}: {exc}")
    return 1 if bad else 0


def cmd_cast(run: Path) -> int:
    from .registry import cast_for_type, load_types, load_voice_defaults, load_voices, resolve_voice
    media = _media()
    brief, route = media.load(run, "brief"), media.load(run, "route")
    spec = load_types()[route.type_id]
    voices, defaults = load_voices(), load_voice_defaults()
    requested = {role: (vr.voice_id or resolve_voice(vr.raw, voices, role)) for role, vr in brief.cast.items()}
    if "narrator" in spec.voice and "narrator" not in requested:
        requested["narrator"] = brief.voice.voice_id or resolve_voice(brief.voice.raw, voices)
    cast = cast_for_type(spec, voices, defaults, requested)
    notes = {r: f"requested {v} not allowed (inactive or terms forbid {spec.content})"
             for r, v in requested.items() if v and cast.get(r) != v}
    print(json.dumps({"cast": cast, "notes": notes}, ensure_ascii=False, indent=2))
    return 0


def stage(run: Path, name: str) -> None:
    media = _media()
    runs.mark(run, name, "running")
    try:
        if name == "tts":
            from .tts import Voicevox, record_voices, synthesize
            script, engine = media.load(run, "script"), Voicevox()
            timings = synthesize(run, script, engine=engine)
            media.save(run, "timings", timings)
            manifest = media.load(run, "manifest") if (run / "assets/manifest.json").is_file() else None
            (run / "assets").mkdir(exist_ok=True)
            media.save(run, "manifest", record_voices(manifest, script, timings, engine_version=engine.version()))
        elif name == "assets":
            from .assets import generate
            board = media.load(run, "storyboard")
            manifest = media.load(run, "manifest") if (run / "assets/manifest.json").is_file() else None
            media.save(run, "manifest", generate(run, board, manifest))
            media.save(run, "storyboard", board)  # t2i scenes now carry visual.ref
        elif name == "timeline":
            from .timeline import build
            manifest = media.load(run, "manifest") if (run / "assets/manifest.json").is_file() else None
            media.save(run, "timeline", build(media.load(run, "script"), media.load(run, "timings"),
                                              media.load(run, "storyboard"), manifest))
        elif name == "render":
            from .render import render
            render(run)
        elif name == "qa":
            from . import qa
            report = qa.write_report(run, qa.run_checks(run))
            if not report.passed:
                failed = [c.id for c in report.checks if not c.passed and c.severity == "block"]
                runs.mark(run, name, "failed", f"failed checks: {failed}")
                raise SystemExit(f"QA failed: {failed} (see {run / 'qa.json'})")
        elif name == "package":
            from .package import package
            print(f"outbox: {package(run)}")
        else:
            raise ValueError(f"unknown stage {name}")
    except SystemExit:
        raise
    except Exception as exc:
        runs.mark(run, name, "failed", f"{type(exc).__name__}: {exc}")
        raise
    runs.mark(run, name, "done")


def cmd_sheet(run: Path, frames: int = 6) -> int:
    """RUN/qa/contact.jpg: `frames` evenly spaced frames of final.mp4, 3 per row, 360 px wide each."""
    media = _media()
    final = run / "final.mp4"
    duration = float(media.probe(final)["format"]["duration"])
    out = run / "qa" / "contact.jpg"
    out.parent.mkdir(exist_ok=True)
    rows = -(-frames // 3)
    media.run([str(media.FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(final), "-vf",
               f"fps={frames / duration:.6f},scale=360:-1,tile=3x{rows}", "-frames:v", "1", "-q:v", "4", str(out)])
    print(out)
    return 0


def cmd_smoke() -> int:
    from .schemas import Brief, Line, Scene, Script, Storyboard, VoiceRequest
    media = _media()
    run = runs.new_run("スモークテスト: 固定の3行台本", slug="smoke")
    media.save(run, "brief", Brief(request="スモークテスト", topic="ハチミツが腐らない理由", target_sec=12, max_sec=20,
                                   voice=VoiceRequest(raw="ひまりの声で", voice_id="vv_himari")))
    media.save(run, "script", Script(title="ハチミツはなぜ腐らない？", cast={"narrator": "vv_himari"}, lines=[
        Line(id="L01", text="ハチミツは、何千年たっても腐らないと言われています。", emphasis=["腐らない"]),
        Line(id="L02", text="理由は、水分が少なく、菌が増えにくいからです。", emphasis=["水分が少なく"]),
        Line(id="L03", text="ただし、1歳未満の赤ちゃんには与えないでください。", emphasis=["1歳未満"])]))
    media.save(run, "storyboard", Storyboard(type_id="trivia_explainer", scenes=[
        Scene(id="C01", line_ids=["L01"], component="title_card",
              props={"text": "ハチミツは\nなぜ腐らない？", "emphasis": ["腐らない"], "from": "#3a2a10", "to": "#120d05"}),
        Scene(id="C02", line_ids=["L02", "L03"], component="background", props={
            "from": "#14283c", "to": "#0b1520",
            "overlays": [{"component": "source_card", "props": {"text": "スモークテスト用の固定台本"}}]})]))
    print(run)
    for name in PIPELINE:
        stage(run, name)
    return 0


def cmd_variants(run: Path) -> int:
    from .variants import PARTS, summaries
    print("id\tnote\tparts\tduration\tQA passed")
    for row in summaries(run):
        parts = ", ".join(f"{key}={row['parts'].get(key) or row['id']}" for key in PARTS)
        duration = f"{row['duration']:.1f}s" if row["duration"] is not None else "-"
        qa = "-" if row["qa_passed"] is None else str(row["qa_passed"])
        note = row["note"].replace("\n", " / ").replace("\t", " ")
        print(f"{row['id']}\t{note}\t{parts}\t{duration}\t{qa}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new")
    new.add_argument("request")
    new.add_argument("--slug")
    for name in ["validate", "cast", "status", "sheet", *PIPELINE]:
        sub.add_parser(name).add_argument("run")
    make = sub.add_parser("make")
    make.add_argument("run")
    make.add_argument("--from", dest="start", choices=PIPELINE, default=PIPELINE[0])
    variant = sub.add_parser("variant").add_subparsers(dest="variant_cmd", required=True)
    variant_new = variant.add_parser("new")
    variant_new.add_argument("run")
    variant_new.add_argument("--id")
    variant_new.add_argument("--from", dest="source")
    variant_new.add_argument("--note", default="")
    variant.add_parser("list").add_argument("run")
    mixed = sub.add_parser("mix")
    mixed.add_argument("run")
    mixed.add_argument("--base", required=True)
    for part in ("script", "voice", "visuals", "bgm"):
        mixed.add_argument(f"--{part}")
    mixed.add_argument("--note", default="")
    bgm = sub.add_parser("bgm").add_subparsers(dest="bgm_cmd", required=True)
    bgm.add_parser("list")
    suggest = bgm.add_parser("suggest")
    suggest.add_argument("run")
    suggest.add_argument("--n", type=int, default=3)
    sub.add_parser("compare").add_argument("run")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("run")
    feedback.add_argument("text", nargs="?")
    feedback.add_argument("--variant")
    feedback.add_argument("--show", action="store_true")
    sub.add_parser("smoke")
    a = p.parse_args(argv)

    if a.cmd == "new":
        print(runs.new_run(a.request, a.slug))
        return 0
    if a.cmd == "smoke":
        return cmd_smoke()
    if a.cmd == "feedback" and ((a.text is None and not a.show) or (a.text is not None and a.show)):
        p.error('feedback requires either "<text>" or --show')
    if a.cmd == "bgm" and a.bgm_cmd == "list":
        from .registry import load_bgm
        print("id\ttitle\tartist\tmood\tbpm\tlength_sec")
        for track in load_bgm().values():
            print(f"{track.id}\t{track.title}\t{track.artist}\t{', '.join(track.mood)}\t{track.bpm}\t{track.length_sec}")
        return 0
    run = runs.resolve(a.run)
    if a.cmd == "variant":
        if a.variant_cmd == "list":
            return cmd_variants(run)
        if a.source:
            from .variants import clone
            print(clone(run, a.source, note=a.note, vid=a.id))
        else:
            print(runs.new_variant(run, a.id, a.note))
        return 0
    if a.cmd == "mix":
        from .variants import mix
        variant = mix(run, a.base, script=a.script, voice=a.voice, visuals=a.visuals, bgm=a.bgm, note=a.note)
        print(variant, flush=True)
        for name in ("tts", "assets", "timeline", "render", "qa"):
            stage(variant, name)
        return 0
    if a.cmd == "bgm":
        from .bgm import suggest
        for row in suggest(run, a.n):
            print(f"{row['id']}\t{row['title']} / {row['artist']}\t{row['score']}\t{' / '.join(row['reasons'])}")
        return 0
    if a.cmd == "compare":
        from .package import compare
        print(compare(run))
        return 0
    if a.cmd == "feedback":
        from .variants import feedback
        for row in feedback(run, a.text, a.variant):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.cmd == "validate":
        return cmd_validate(run)
    if a.cmd == "cast":
        return cmd_cast(run)
    if a.cmd == "status":
        print(json.dumps(runs.read_state(run), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "sheet":
        return cmd_sheet(run)
    if a.cmd == "make":
        for name in PIPELINE[PIPELINE.index(a.start):]:
            stage(run, name)
        return 0
    stage(run, a.cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
