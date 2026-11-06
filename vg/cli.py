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
  vg thumbnail RUN[:vN]            render cover options and crop previews
  vg publish auth youtube         authorize the optional official YouTube uploader
  vg publish youtube RUN:vN --confirm [--privacy private|unlisted|public] [--at ISO8601] [--dry-run]
  vg daily init | status [--check] | next | history [--limit N]   daily autopost (local/requests.md)
  vg daily add "<request>" --reason "..." | start "<request>" --run RUN | fail "<reason>" | skip "<reason>"
  vg daily publish RUN:vN --confirm [--dry-run]   schedule for the run's slot (private + publishAt)
  vg analyze VIDEO [--json PATH]    measure pixels/audio and write a six-frame contact sheet
  vg sfx build                     synthesize the original local sound-effect library
  vg feedback RUN "<text>" [--variant vN] | vg feedback RUN --show
  vg smoke                          fixed three-line run end to end (no GPU)
<run> is a run/variant path, a directory name under runs/, "latest", or "<run>:v2".
"""
from __future__ import annotations

import argparse
import json
import os
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


def cmd_yomi(run: Path, approve: bool = False) -> int:
    import os

    from .yomi import reading_table
    rows = reading_table(run)
    report = {"lines": rows, "reviewed": approve}
    path = run / "yomi_check.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    script_time = (run / "script.json").stat().st_mtime_ns
    if path.stat().st_mtime_ns <= script_time:
        # Shared filesystems can give consecutive writes the same clock tick.
        os.utime(path, ns=(path.stat().st_atime_ns, script_time + 1))
    print("line\tflags (*=needs yomi; D=digit L=Latin K=kanji N=name)\ttext\tengine reading")
    for row in rows:
        marks = "".join(mark for key, mark in (("has_digits", "D"), ("has_latin", "L"),
                         ("has_rare_kanji_run", "K"), ("has_proper_noun_hint", "N")) if row[key])
        marks = ("*" if row["flagged"] else "") + marks
        text = row["text"].replace("\n", " ").replace("\t", " ")
        print(f"{row['line_id']}\t{marks or '-'}\t{text}\t{row['engine_reading']}")
    print(f"reviewed: {str(approve).lower()}")
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
        Scene(id="C02", line_ids=["L02"], component="background", props={
            "from": "#14283c", "to": "#0b1520",
            "overlays": [{"component": "source_card", "props": {"text": "スモークテスト用の固定台本"}}]}),
        Scene(id="C03", line_ids=["L03"], component="background", props={"from": "#3c1f14", "to": "#1a0d08"})]))
    print(run)
    for name in PIPELINE:
        stage(run, name)
        if name == "tts":
            cmd_yomi(run, approve=True)  # fixed, known smoke script
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


def cmd_assets_all(run: Path) -> int:
    from .assets import generate_many
    if run.parent.name == "variants":
        raise ValueError("assets --all requires the parent run, not a variant")
    media = _media()
    variants = runs.variants(run) or [run]
    entries = [(v, media.load(v, "storyboard"),
                media.load(v, "manifest") if (v / "assets/manifest.json").is_file() else None)
               for v in variants]
    for variant in variants:
        runs.mark(variant, "assets", "running")
    try:
        manifests = generate_many(entries)
        for (variant, board, _), manifest in zip(entries, manifests):
            media.save(variant, "manifest", manifest)
            media.save(variant, "storyboard", board)
            runs.mark(variant, "assets", "done")
    except Exception as exc:
        for variant in variants:
            runs.mark(variant, "assets", "failed", f"{type(exc).__name__}: {exc}")
        raise
    return 0


def cmd_daily(a) -> int:
    from . import daily
    try:
        if a.daily_cmd == "init":
            print(daily.init(daily.config()))
        elif a.daily_cmd == "status":
            if a.check:
                return daily.check()
            print(json.dumps(daily.status(), ensure_ascii=False, indent=2))
        elif a.daily_cmd == "next":
            info = daily.status()
            out = info["resume"] or ({"request": info["next"], "run": None} if info["next"] else {"empty": True})
            if not out.get("empty"):
                out["failures"] = info["failures"]
            print(json.dumps(out, ensure_ascii=False))
        elif a.daily_cmd == "add":
            daily.add_auto(a.request, a.reason)
            print(json.dumps({"added": " ".join(a.request.split())}, ensure_ascii=False))
        elif a.daily_cmd == "start":
            print(json.dumps(daily.start(a.request, str(runs.resolve(a.run))), ensure_ascii=False, indent=2))
        elif a.daily_cmd == "fail":
            print(json.dumps(daily.fail(a.reason), ensure_ascii=False, indent=2))
        elif a.daily_cmd == "skip":
            print(json.dumps({"skipped": daily.skip(a.reason, a.request)}, ensure_ascii=False))
        elif a.daily_cmd == "history":
            print(json.dumps(daily.history(a.limit), ensure_ascii=False, indent=2))
        elif a.daily_cmd == "publish":
            # Unattended runs may only schedule through the daily runner, never publish directly.
            if os.environ.get("VG_HEADLESS") == "1" and os.environ.get("VG_AUTOPOST") != "1":
                print("daily publish in an unattended run requires the daily autopost runner (VG_AUTOPOST=1)",
                      file=sys.stderr)
                return 2
            result = daily.publish(runs.resolve(a.run), confirm=a.confirm, dry_run=a.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not a.confirm and not a.dry_run:
                print("Scheduling requires explicit --confirm", file=sys.stderr)
                return 1
        return 0
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"daily: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new")
    new.add_argument("request")
    new.add_argument("--slug")
    yomi = sub.add_parser("yomi", help="inspect VOICEVOX readings before QA")
    yomi.add_argument("run")
    yomi.add_argument("--approve", action="store_true", help="mark readings reviewed after fixing yomi and rerunning tts")
    for name in ["validate", "cast", "status", "sheet", *PIPELINE]:
        parser = sub.add_parser(name)
        parser.add_argument("run")
        if name == "assets":
            parser.add_argument("--all", action="store_true", help="batch all variants in one GPU job")
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
    sub.add_parser("thumbnail").add_argument("run")
    publish = sub.add_parser("publish").add_subparsers(dest="publish_cmd", required=True)
    publish.add_parser("auth").add_argument("service", choices=["youtube"])
    youtube = publish.add_parser("youtube")
    youtube.add_argument("run")
    youtube.add_argument("--confirm", action="store_true")
    youtube.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    youtube.add_argument("--at")
    youtube.add_argument("--dry-run", action="store_true")
    youtube.add_argument("--force", action="store_true")
    thumb = publish.add_parser("youtube-thumbnail")  # retry the cover of an uploaded variant
    thumb.add_argument("run")
    vis = publish.add_parser("youtube-privacy")  # e.g. private test upload -> public
    vis.add_argument("run")
    vis.add_argument("--privacy", choices=["private", "unlisted", "public"], required=True)
    vis.add_argument("--confirm", action="store_true")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("video")
    analyze.add_argument("--json", type=Path)
    sub.add_parser("sfx").add_subparsers(dest="sfx_cmd", required=True).add_parser("build")
    feedback = sub.add_parser("feedback")
    feedback.add_argument("run")
    feedback.add_argument("text", nargs="?")
    feedback.add_argument("--variant")
    feedback.add_argument("--show", action="store_true")
    sub.add_parser("smoke")
    daily = sub.add_parser("daily").add_subparsers(dest="daily_cmd", required=True)
    daily.add_parser("init")
    daily.add_parser("status").add_argument("--check", action="store_true",
                                            help="exit 0 when a video should be made now, 10-12 otherwise")
    daily.add_parser("next")
    daily_add = daily.add_parser("add")
    daily_add.add_argument("request")
    daily_add.add_argument("--reason", required=True)
    daily_start = daily.add_parser("start")
    daily_start.add_argument("request")
    daily_start.add_argument("--run", required=True)
    daily.add_parser("fail").add_argument("reason")
    daily_skip = daily.add_parser("skip")
    daily_skip.add_argument("reason")
    daily_skip.add_argument("--request", help="default: today's request")
    daily_publish = daily.add_parser("publish")
    daily_publish.add_argument("run")
    daily_publish.add_argument("--confirm", action="store_true")
    daily_publish.add_argument("--dry-run", action="store_true")
    daily.add_parser("history").add_argument("--limit", type=int, default=30)
    a = p.parse_args(argv)
    if a.cmd == "daily":
        return cmd_daily(a)

    if a.cmd == "publish":
        if os.environ.get("VG_HEADLESS") == "1":
            print("Publishing is refused in unattended runs (VG_HEADLESS=1): the user must choose and ask.",
                  file=sys.stderr)
            return 2
        from . import publish_youtube
        try:
            if a.publish_cmd == "auth":
                publish_youtube.authorize()
                print("YouTube OAuth authorized")
                return 0
            chosen = runs.resolve(a.run)
            if chosen.parent.name != "variants":
                raise ValueError("publishing requires a human-chosen variant: RUN:vN")
            if a.publish_cmd == "youtube-thumbnail":
                print(json.dumps(publish_youtube.set_thumbnail(chosen), ensure_ascii=False, indent=2))
                return 0
            if a.publish_cmd == "youtube-privacy":
                if not a.confirm:
                    print(f"Changing visibility to {a.privacy} requires explicit --confirm", file=sys.stderr)
                    return 1
                print(json.dumps(publish_youtube.set_privacy(chosen, a.privacy), ensure_ascii=False, indent=2))
                return 0
            if not a.confirm:
                print(json.dumps(publish_youtube.preview(chosen, a.privacy, a.at, force=a.force),
                                 ensure_ascii=False, indent=2))
                print("Publication requires explicit --confirm", file=sys.stderr)
                return 1
            result = publish_youtube.upload(chosen, privacy=a.privacy, publish_at=a.at,
                                            dry_run=a.dry_run, force=a.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, FileNotFoundError, ImportError) as exc:
            print(f"YouTube: {exc}", file=sys.stderr)
            return 1
    if a.cmd == "new":
        print(runs.new_run(a.request, a.slug))
        return 0
    if a.cmd == "smoke":
        return cmd_smoke()
    if a.cmd == "sfx":
        from .sfx import build
        for path in build():
            print(path)
        return 0
    if a.cmd == "analyze":
        from .analyze import analyze, japanese_summary
        video = Path(a.video)
        if not video.is_file() and ":" in a.video:
            parent, name = a.video.rsplit("/", 1)
            video = runs.resolve(parent) / name
        result = analyze(video)
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(japanese_summary(result))
        return 0
    if a.cmd == "feedback" and ((a.text is None and not a.show) or (a.text is not None and a.show)):
        p.error('feedback requires either "<text>" or --show')
    if a.cmd == "bgm" and a.bgm_cmd == "list":
        from .registry import load_bgm
        print("id\ttitle\tartist\tmood\tbpm\tlength_sec")
        for track in load_bgm().values():
            print(f"{track.id}\t{track.title}\t{track.artist}\t{', '.join(track.mood)}\t{track.bpm}\t{track.length_sec}")
        return 0
    run = runs.resolve(a.run)
    if a.cmd == "thumbnail":
        from .thumbnail import thumbnail
        print(thumbnail(run))
        return 0
    if a.cmd == "yomi":
        return cmd_yomi(run, a.approve)
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
    if a.cmd == "assets" and a.all:
        return cmd_assets_all(run)
    stage(run, a.cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
