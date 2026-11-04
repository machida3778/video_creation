"""Self-contained variant composition and human feedback (no generation here)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from . import run as runs
from .media import load, save
from .schemas import Manifest, Script, Storyboard

PARTS = ("script", "voice", "visuals", "bgm")


def metadata(path: Path) -> dict:
    return json.loads((path / "variant.json").read_text(encoding="utf-8"))


def local_file(root: Path, relative: str) -> Path:
    """Reject absolute paths and references escaping the self-contained directory."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"asset path must be relative: {relative}")
    source = root / path
    if not source.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"asset path escapes {root}: {relative}")
    if not source.is_file():
        raise FileNotFoundError(f"missing asset file: {source}")
    return source


def _manifest(path: Path) -> Manifest:
    return load(path, "manifest") if (path / "assets/manifest.json").is_file() else Manifest()


def _refs(value) -> set[str]:
    if isinstance(value, dict):
        found = {value["src_asset"]} if value.get("src_asset") else set()
        for item in value.values():
            found.update(_refs(item))
        return found
    if isinstance(value, list):
        return set().union(*(_refs(item) for item in value)) if value else set()
    return set()


def clone(run: Path, source: str, *, vid: str | None = None, note: str = "") -> Path:
    """Branch even an unfinished variant, copying specifications and non-voice assets."""
    run = Path(run)
    original = runs.variant_dir(run, source)
    info = metadata(original)
    manifest = _manifest(original)
    entries = [a for a in manifest.assets if a.kind != "voice"]
    files = {a.path: local_file(original, a.path) for a in entries}
    destination = runs.new_variant(run, vid=vid, note=note)
    try:
        for name in ("script", "storyboard", "factcheck"):
            if (original / f"{name}.json").is_file():
                save(destination, name, load(original, name))
        for relative, file in files.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
        save(destination, "manifest", Manifest(assets=entries))
        data = metadata(destination)
        data["parts"] = {part: info.get("parts", {}).get(part) or source for part in PARTS}
        (destination / "variant.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def remap_lines(board: Storyboard, original: Script, target: Script) -> bool:
    """Use cumulative source-line fractions as boundaries, retaining even empty scenes."""
    old_ids = [line.id for line in original.lines]
    new_ids = [line.id for line in target.lines]
    covered = [lid for scene in board.scenes for lid in scene.line_ids]
    if covered != old_ids:
        raise ValueError("visuals storyboard must cover its script lines exactly once in order")
    if old_ids == new_ids:
        return False
    old_end, start = 0, 0
    for scene in board.scenes:
        old_end += len(scene.line_ids)
        end = old_end * len(new_ids) // len(old_ids)
        scene.line_ids = new_ids[start:end]
        start = end
    return True


def mix(run: Path, base: str, script: str | None = None, voice: str | None = None,
        visuals: str | None = None, bgm: str | None = None, note: str = "") -> Path:
    """Copy spec parts and licensed visual/music files, without stale generated outputs.

    Voice assets, timings, timeline, QA and final deliverables are deliberately regenerated.
    """
    run = Path(run)
    base_dir = runs.variant_dir(run, base)
    base_meta = metadata(base_dir)
    selected = dict(script=script, voice=voice, visuals=visuals, bgm=bgm)
    sources = {part: runs.variant_dir(run, source or base) for part, source in selected.items()}
    for path in sources.values():
        metadata(path)  # fail before creating a destination for an unknown variant

    result_script = load(base_dir, "script").model_dump()
    if script is not None:
        donor = load(sources["script"], "script")
        result_script.update(title=donor.title, lines=[line.model_dump() for line in donor.lines])
    if voice is not None:
        donor = load(sources["voice"], "script")
        result_script.update(cast=donor.cast, voice_overrides=donor.voice_overrides)
    result_script = Script.model_validate(result_script)

    board = load(base_dir, "storyboard")
    visual_board = load(sources["visuals"], "storyboard")
    board.scenes = visual_board.scenes
    remapped = remap_lines(board, load(sources["visuals"], "script"), result_script)
    if bgm is not None:
        board.music = load(sources["bgm"], "storyboard").music
    if remapped:
        detail = f"映像の台詞IDを{sources['visuals'].name}から新台本へ行数比率で再割当（全行を順番に1回ずつ）"
        note = f"{note}\n{detail}".strip()

    refs = {scene.visual.ref for scene in board.scenes if scene.visual.ref}
    refs.update(_refs([scene.props for scene in board.scenes]))
    visual_assets = _manifest(sources["visuals"])
    missing = refs - {a.id for a in visual_assets.assets}
    if missing:
        raise ValueError(f"visual assets missing from manifest: {sorted(missing)}")
    selected_assets = [(sources["visuals"], a) for a in visual_assets.assets
                       if a.kind != "voice" and (a.kind == "image" or a.id in refs)]
    if board.music and board.music.asset:
        music_assets = [a for a in _manifest(sources["bgm"]).assets if a.id == board.music.asset]
        if len(music_assets) != 1 or music_assets[0].kind != "music":
            raise ValueError(f"music asset {board.music.asset!r} missing from manifest")
        selected_assets.append((sources["bgm"], music_assets[0]))

    # Validate all sources and collisions before allocating a variant.
    files, assets, paths = {}, {}, {}
    for source, asset in selected_assets:
        file = local_file(source, asset.path)
        if asset.id in assets and assets[asset.id] != asset:
            raise ValueError(f"conflicting asset id {asset.id}")
        if asset.path in paths and paths[asset.path] != file:
            raise ValueError(f"conflicting asset path {asset.path}")
        assets[asset.id], paths[asset.path] = asset, file
        files[asset.path] = file
    # Preserve all generated image files, including files not referenced by this board.
    for source in sorted((sources["visuals"] / "assets/images").rglob("*")):
        if source.is_file():
            relative = str(source.relative_to(sources["visuals"]))
            files[relative] = local_file(sources["visuals"], relative)

    destination = runs.new_variant(run, note=note)
    try:
        save(destination, "script", result_script)
        save(destination, "storyboard", board)
        save(destination, "manifest", Manifest(assets=list(assets.values())))
        for relative, source in files.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        factcheck = sources["script"] / "factcheck.json"
        if factcheck.is_file():
            shutil.copy2(factcheck, destination / factcheck.name)
        data = metadata(destination)
        data["parts"] = {part: selected[part] or base_meta.get("parts", {}).get(part) or base
                         for part in PARTS}
        (destination / "variant.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8")
    except Exception:
        shutil.rmtree(destination)
        raise
    return destination


def feedback(run: Path, text: str | None = None, variant: str | None = None) -> list[dict]:
    """Append one feedback record, or return existing records when text is omitted."""
    run = Path(run)
    if run.parent.name == "variants":
        variant = variant or run.name
        run = run.parent.parent
    if variant is not None:
        metadata(runs.variant_dir(run, variant))
    path = run / "feedback.jsonl"
    if text is not None:
        if not text.strip():
            raise ValueError("feedback text cannot be empty")
        item = {"time": runs.now(), "variant": variant, "text": text}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        return [item]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def summaries(run: Path) -> list[dict]:
    rows = []
    for path in runs.variants(run):
        data = metadata(path)
        duration = None
        if (path / "timeline.json").is_file():
            duration = load(path, "timeline").duration
        elif (path / "timings.json").is_file():
            duration = load(path, "timings").total_sec
        passed = load(path, "qa").passed if (path / "qa.json").is_file() else None
        rows.append({**data, "duration": duration, "qa_passed": passed})
    return rows
