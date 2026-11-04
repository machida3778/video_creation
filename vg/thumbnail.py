"""Offline thumbnail layout, Remotion stills and crop/contact-sheet previews."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image, ImageDraw

from .media import FFMPEG, ROOT, load, run, scrubbed_env
from .schemas import CoverOption, visible_chars
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


def layout(option: CoverOption) -> dict:
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
    box = {"x": 486 - width / 2, "y": 1050 - height / 2, "width": width, "height": height}
    badge = None
    if option.badge is not None:
        badge = {"x": 128, "y": 510, "width": len(option.badge) * 140 + 40, "height": 208}
    return {"lines": lines, "font_size": size, "line_height": line_height, "padding": 20,
            "box": box, "badge": badge}


def option_report(option: CoverOption, placement: dict | None = None) -> dict:
    placement = placement or layout(option)
    chars = visible_chars(option.text)
    ratios = {"white_outline": contrast("#ffffff", "#000000"),
              "emphasis_outline": contrast(PALETTES[option.palette], "#000000")}
    if option.badge:
        ratios["badge_panel"] = contrast("#000000", PALETTES[option.palette])
    checks = {"chars": chars <= 20, "lines": len(placement["lines"]) <= 3,
              "min_glyph_size": placement["font_size"] >= 126,
              "safe_region": contained(placement["box"]) and
              (placement["badge"] is None or contained(placement["badge"])),
              "contrast": all(ratio >= 4.5 for ratio in ratios.values())}
    return {"id": option.id, "option": option.model_dump(), "line_id": option.line_id, "chars": chars,
            "lines": len(placement["lines"]), "min_glyph_size": placement["font_size"],
            "contrast": ratios, "layout": placement, "checks": checks,
            "warnings": ["headline exceeds recommended 12 chars"] if chars > 12 else [],
            "ai_generated": option.ai_generated, "photorealistic": option.photorealistic,
            "passed": all(checks.values())}


def selected_cover(run_dir: Path) -> str:
    """Use the first passing candidate, unless a human recorded chosen_id in the report."""
    covers = load(run_dir, "covers")
    report = json.loads((run_dir / "covers_report.json").read_text(encoding="utf-8"))
    passing = {item["id"]: item for item in report["options"] if item.get("passed") is True}
    valid = [option.id for option in covers.options if option.id in passing
             and passing[option.id].get("option") == option.model_dump() and option_report(option)["passed"]]
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
    output = run_dir / "thumbs"
    output.mkdir(exist_ok=True)
    # Invalidate any previous successful report before attempting a new render.
    report_path = run_dir / "covers_report.json"
    report_path.unlink(missing_ok=True)
    reports = []
    env = scrubbed_env()
    env["PATH"] = str(Path.home() / ".local/opt/node/bin") + os.pathsep + env.get("PATH", "")
    for option in covers.options:
        for previous in output.glob(f"{option.id}.*"):
            previous.unlink()
        for previous in output.glob(f"{option.id}_*.jpg"):
            previous.unlink()
        item = option_report(option)
        reports.append(item)
        if option.line_id not in {line.id for line in script.lines}:
            raise ValueError(f"{option.id}: payoff line is missing: {option.line_id}")
        asset = assets.get(option.image)
        if asset is None or asset.kind not in ("image", "character_art") or not asset.commercial_ok:
            raise ValueError(f"{option.id}: focal image requires a commercially licensed image asset")
        local_file(run_dir, asset.path)
        if not item["passed"]:
            continue
        props = output / f"{option.id}.props.json"
        props.write_text(json.dumps({"option": option.model_dump(), "src": asset.path,
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
    report_path.write_text(json.dumps({"options": reports}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not any(item["passed"] for item in reports):
        raise ValueError("no passing covers (see covers_report.json); shorten the headline")
    return report_path
