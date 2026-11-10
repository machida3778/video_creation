"""Offline thumbnail layout, Remotion stills and crop/contact-sheet previews."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import warnings

from PIL import Image, ImageDraw

from .media import FFMPEG, ROOT, load, run, scrubbed_env
from .schemas import CoverJudgement, CoverOption, visible_chars
from .variants import local_file

PALETTES = {"yellow": "#ffd400", "red": "#ff3b30", "white": "#ffffff"}
SAFE = {"left": 108, "top": 480, "right": 864, "bottom": 1440}


def contrast(fill: str, background: str) -> float:
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))
    a, b = sorted((luminance(fill), luminance(background)))
    return (b + 0.05) / (a + 0.05)


def contained(box: dict) -> bool:
    return (SAFE["left"] <= box["x"] and SAFE["top"] <= box["y"]
            and box["x"] + box["width"] <= SAFE["right"]
            and box["y"] + box["height"] <= SAFE["bottom"])


def talent_ids(manifest) -> set[str]:
    """Pictures of real talents or characters: what `vg image add` brought in (stream stills, official images under
    a clip guideline) unless it was marked `--not-talent` (params.talent false; older records count as talent). A run
    that has them is about those people, so its covers must show them."""
    if manifest is None:
        return set()
    return {asset.id for asset in manifest.assets
            if asset.tool == "vg image add" and asset.kind in ("image", "character_art")
            and asset.params.get("talent", True) is not False}


def _talents(run_dir: Path) -> set[str]:
    try:
        return talent_ids(load(run_dir, "manifest"))
    except FileNotFoundError:
        return set()


def layout(option: CoverOption, low: bool = False) -> dict:
    """Headline placement. `low` puts it at the bottom of the safe region, so the faces in a talent cover (the
    upper part of the stills) stay visible."""
    # Fixed em cells prevent font-dependent wrapping and horizontal squeezing. Each SVG
    # glyph uses the same cell; 20 px padding includes outline and soft shadow.
    lines = [line[i:i + 5] for line in option.text.split("\n") for i in range(0, len(line), 5)]
    # Explicit line breaks are respected; a long explicit line may shrink to 126.
    if "\n" in option.text:
        lines = option.text.split("\n")
    size = min(140, (756 - 40) / max(map(len, lines)))
    line_height = size * 1.2
    width = max(map(len, lines)) * size + 40
    height = len(lines) * line_height + 40
    top = SAFE["bottom"] - 8 - height if low else 1050 - height / 2
    box = {"x": 486 - width / 2, "y": top, "width": width, "height": height}
    badge = None
    if option.badge is not None:
        badge = {"x": 128, "y": 510, "width": len(option.badge) * 140 + 40, "height": 208}
    return {"lines": lines, "font_size": size, "line_height": line_height, "padding": 20,
            "box": box, "badge": badge, "shade": "low" if low else None}


def option_report(option: CoverOption, placement: dict | None = None, talent: set[str] | None = None) -> dict:
    """Checks of one option. `talent` holds the run's talent pictures (talent_ids): when there are any, every
    picture on the cover must be one of them, since only the faces tell viewers who the video is about."""
    shows_talent = bool(talent) and all(source in talent for source in option.sources)
    placement = placement or layout(option, low=shows_talent)
    chars = visible_chars(option.text)
    ratios = {"white_outline": contrast("#ffffff", "#000000"),
              "emphasis_outline": contrast(PALETTES[option.palette], "#000000")}
    if option.badge:
        ratios["badge_panel"] = contrast("#000000", PALETTES[option.palette])
    checks = {"chars": chars <= 20, "lines": len(placement["lines"]) <= 3,
              "min_glyph_size": placement["font_size"] >= 126,
              "safe_region": contained(placement["box"]) and
              (placement["badge"] is None or contained(placement["badge"])),
              "contrast": all(ratio >= 4.5 for ratio in ratios.values()),
              "talent": not talent or shows_talent}
    return {"id": option.id, "option": option.model_dump(), "line_id": option.line_id, "chars": chars,
            "lines": len(placement["lines"]), "min_glyph_size": placement["font_size"],
            "contrast": ratios, "layout": placement, "checks": checks,
            "warnings": ["headline exceeds recommended 12 chars"] if chars > 12 else [],
            "ai_generated": option.ai_generated, "photorealistic": option.photorealistic,
            "passed": all(checks.values())}


def _same(stored, option) -> bool:
    """A report's option equals the current one (reports written before newer optional fields still match)."""
    try:
        return CoverOption.model_validate(stored) == option
    except (ValueError, TypeError):
        return False


def selected_cover(run_dir: Path) -> str:
    """Use the first passing candidate, unless a human recorded chosen_id in the report."""
    covers = load(run_dir, "covers")
    report = json.loads((run_dir / "covers_report.json").read_text(encoding="utf-8"))
    passing = {item["id"]: item for item in report["options"] if item.get("passed") is True}
    talent = _talents(run_dir)
    valid = [option.id for option in covers.options if option.id in passing
             and _same(passing[option.id].get("option"), option) and option_report(option, talent=talent)["passed"]]
    chosen = report.get("chosen_id")
    if chosen is not None and chosen not in valid:
        raise ValueError("chosen cover did not pass thumbnail checks")
    if not valid:
        raise ValueError("no passing covers; run vg thumbnail and review covers_report.json")
    return chosen or valid[0]


def previews(jpeg: Path) -> None:
    for label, width, height in (("3x4", 1080, 1440), ("4x5", 1080, 1350), ("1x1", 1080, 1080)):
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(jpeg), "-vf",
             f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,setsar=1", "-frames:v", "1",
             "-threads", "1", "-q:v", "2", str(jpeg.with_stem(f"{jpeg.stem}_{label}"))])
    for width in (120, 180):
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(jpeg), "-vf",
             f"scale={width}:-1,setsar=1", "-frames:v", "1", "-threads", "1", "-q:v", "2",
             str(jpeg.with_stem(f"{jpeg.stem}_{width}"))])


def thumbnail(run_dir: Path) -> Path:
    run_dir = Path(run_dir).resolve()
    covers, script, manifest = (load(run_dir, stage) for stage in ("covers", "script", "manifest"))
    assets = {asset.id: asset for asset in manifest.assets}
    talent = talent_ids(manifest)
    output = run_dir / "thumbs"
    output.mkdir(exist_ok=True)
    # Invalidate any previous successful report before attempting a new render; the judge's record
    # (chosen_id, scores) is set aside and carried over below for options that did not change.
    report_path = run_dir / "covers_report.json"
    earlier = _set_aside(run_dir)
    reports = []
    env = scrubbed_env()
    env["PATH"] = str(Path.home() / ".local/opt/node/bin") + os.pathsep + env.get("PATH", "")
    for option in covers.options:
        for previous in output.glob(f"{option.id}.*"):
            previous.unlink()
        for previous in output.glob(f"{option.id}_*.jpg"):
            previous.unlink()
        item = option_report(option, talent=talent)
        reports.append(item)
        if option.line_id not in {line.id for line in script.lines}:
            raise ValueError(f"{option.id}: payoff line is missing: {option.line_id}")
        paths = []
        for source in option.sources:
            asset = assets.get(source)
            if asset is None or asset.kind not in ("image", "character_art") or not asset.commercial_ok:
                raise ValueError(f"{option.id}: focal image requires a commercially licensed image asset")
            local_file(run_dir, asset.path)
            paths.append(asset.path)
        if not item["checks"]["talent"]:
            warnings.warn(f"{option.id}: this run shows real talents or characters, so the cover must use their "
                          f"pictures (image/images from: {', '.join(sorted(talent))}), not other or generated "
                          "images", stacklevel=2)
        if not item["passed"]:
            continue
        props = output / f"{option.id}.props.json"
        props.write_text(json.dumps({"option": option.model_dump(), "src": paths[0], "srcs": paths,
                                     "layout": item["layout"], "width": 1080, "height": 1920},
                                    ensure_ascii=False), encoding="utf-8")
        for scale, extension in ((1, "jpg"), (2, "png")):
            target = output / f"{option.id}.{extension}"
            run(["npx", "--no-install", "remotion", "still", "src/index.ts", "Thumbnail", str(target),
                 f"--props={props}", f"--public-dir={run_dir}", f"--scale={scale}",
                 f"--image-format={'jpeg' if extension == 'jpg' else 'png'}", "--jpeg-quality=90",
                 "--log=error"], cwd=ROOT / "render", env=env)
            with Image.open(target) as image:
                if image.size != (1080 * scale, 1920 * scale):
                    raise ValueError(f"{option.id}: unexpected still dimensions: {image.size}")
        jpeg = output / f"{option.id}.jpg"
        # Re-encode locally only if required, preserving the 1080x1920 dimensions.
        for quality in (85, 75, 60, 45):
            if jpeg.stat().st_size <= 2_000_000:
                break
            with Image.open(jpeg) as image:
                image.convert("RGB").save(jpeg, quality=quality, optimize=True)
        if jpeg.stat().st_size > 2_000_000:
            raise ValueError(f"{option.id}: JPEG exceeds 2 MB")
        previews(jpeg)
    sheet = Image.new("RGB", (660, 680 * len(reports)), "#222222")
    draw = ImageDraw.Draw(sheet)
    for index, item in enumerate(reports):
        y = index * 680
        draw.text((10, y + 5), f"{item['id']} | payoff: {item['line_id']} | pass: {item['passed']}", fill="white")
        if item["passed"]:
            for x, suffix, width in ((10, "", 300), (330, "_120", 120), (470, "_180", 180)):
                with Image.open(output / f"{item['id']}{suffix}.jpg") as image:
                    image = image.convert("RGB")
                    image.thumbnail((width, 620))
                    sheet.paste(image, (x, y + 40))
    sheet.save(output / "sheet.jpg", quality=90)
    report = {"options": reports} | _kept(earlier, covers.options, reports)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (run_dir / PREVIOUS_REPORT).unlink(missing_ok=True)
    if not any(item["passed"] for item in reports):
        raise ValueError("no passing covers (see covers_report.json); shorten the headline")
    return report_path


# ---------------------------------------------------------------- the judge's record
# covers_report.json keeps, next to the checks above, the independent judge's verdict (docs/THUMBNAILS.md
# 「採点と選び方」): "chosen_id", "chosen_by" ("judge" or "choose") and
# "scores": {"judge", "round", "scored_at", "options": {id: {"items": [① .. ⑧], "total", "note"}}}.
PREVIOUS_REPORT = "covers_report.previous.json"   # the last report while vg thumbnail renders again
VETO = (1, 3, 5, 6)   # ② ④ ⑥ ⑦: an option with 0 on any of them is not used
STRONG = (3, 6)       # ④ ⑦: the rubric asks for an option with 2 on both
BAR = 12              # below this best total the rubric asks for one redo


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _set_aside(run_dir: Path) -> dict:
    """Move the current report aside (selected_cover no longer trusts it) and return it. A report a failed
    render left aside earlier is used when there is no current one, so a retry still finds the judge's record."""
    report, aside = run_dir / "covers_report.json", run_dir / PREVIOUS_REPORT
    if report.is_file():
        previous = _read(report)
        report.replace(aside)
        return previous
    return _read(aside)


def scores(raw) -> dict | None:
    """The judge's scores in the stored shape. Reports written by hand before `--score` kept the per-option
    entries at the top level next to "judge"; those are read as the same shape."""
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("options"), dict):
        return raw
    options = {key: value for key, value in raw.items() if isinstance(value, dict)}
    meta = {key: value for key, value in raw.items() if not isinstance(value, dict)}
    return meta | {"options": options} if options else None


def _kept(previous: dict, options: list[CoverOption], reports: list[dict]) -> dict:
    """What the new report keeps from the previous one: chosen_id while that option still passes, and the scores
    of options unchanged since they were judged (a new headline or image needs a new judgement)."""
    old = {item.get("id"): item.get("option") for item in previous.get("options", []) if isinstance(item, dict)}
    unchanged = {option.id for option in options if option.id in old and _same(old[option.id], option)}
    passed = {item["id"] for item in reports if item["passed"]}
    kept: dict = {}
    chosen = previous.get("chosen_id")
    if chosen in passed:
        kept["chosen_id"] = chosen
        if previous.get("chosen_by"):
            kept["chosen_by"] = previous["chosen_by"]
    elif chosen is not None:
        warnings.warn(f"chosen cover {chosen!r} no longer passes the checks; choose again with "
                      "vg thumbnail RUN:vN --score FILE or --choose ID", stacklevel=2)
    judged = scores(previous.get("scores"))
    if judged:
        entries = {key: value for key, value in judged["options"].items() if key in unchanged}
        if entries:
            kept["scores"] = judged | {"options": entries}
    return kept


def record(run_dir: Path, judgement: CoverJudgement | None = None, choose: str | None = None) -> dict:
    """Store the judge's scores and the chosen cover in covers_report.json. Without `choose` the best usable option
    is chosen: the highest total among options with no 0 on ②④⑥⑦ (earlier in covers.json on a tie), or the
    highest total when none is usable. `bar_met` is false when the rubric asks for a redo."""
    if judgement is None and choose is None:
        raise ValueError("give the judge's scores (--score FILE) or a cover id (--choose ID)")
    run_dir = Path(run_dir).resolve()
    path = run_dir / "covers_report.json"
    if not path.is_file():
        raise ValueError("no covers_report.json; run vg thumbnail first")
    original = path.read_text(encoding="utf-8")
    report = json.loads(original)
    rendered = {item.get("id"): item for item in report.get("options", []) if item.get("passed") is True}
    order = [option.id for option in load(run_dir, "covers").options
             if option.id in rendered and _same(rendered[option.id].get("option"), option)]
    if not order:
        raise ValueError("no passing covers; run vg thumbnail first")
    result: dict = {}
    if judgement is not None:
        unknown = [item.id for item in judgement.options if item.id not in order]
        if unknown:
            raise ValueError(f"not rendered by the last vg thumbnail, failed its checks or changed since: "
                             f"{', '.join(unknown)}")
        judged = sorted(judgement.options, key=lambda item: order.index(item.id))
        report["scores"] = {"judge": judgement.judge, "round": judgement.round,
                            "scored_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "options": {item.id: {"items": item.items, "total": item.total, "note": item.note}
                                        for item in judged}}
        usable = [item for item in judged if all(item.items[i] for i in VETO)]
        best = max(usable or judged, key=lambda item: (item.total, -order.index(item.id)))
        strong = [item.id for item in usable if all(item.items[i] == 2 for i in STRONG)]
        result = {"best": best.id, "total": best.total, "usable": [item.id for item in usable],
                  "strong_image": strong, "bar_met": bool(usable) and best.total >= BAR and bool(strong)}
        report["chosen_id"], report["chosen_by"] = best.id, "judge"
    if choose is not None:
        if choose not in order:
            raise ValueError(f"{choose}: not a cover that passed the last vg thumbnail")
        report["chosen_id"], report["chosen_by"] = choose, "choose"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        selected = selected_cover(run_dir)  # the same checks vg package applies
    except Exception:
        path.write_text(original, encoding="utf-8")
        raise
    return result | {"chosen_id": selected, "chosen_by": report["chosen_by"], "report": str(path)}
