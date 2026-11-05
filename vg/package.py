"""Create publishing metadata and copy a QA-approved run to the local outbox."""
from pathlib import Path
import re
import shlex
import shutil
import unicodedata

from PIL import Image

from .media import FFMPEG, ROOT, load, run, save
from .schemas import Publish
from . import registry, run as runs
from .variants import metadata
from .disclosure import needs_disclosure
from .qa import check_metadata, write_report


def _hashtags(brief, script, type_name: str) -> list[str]:
    tags = list(dict.fromkeys(c for c in brief.constraints if c.startswith("#")))[:5]
    if len(tags) >= 3:
        return tags
    # Prefer the type and search phrase, then their individual words and type-specific fallbacks.
    candidates = [type_name, script.search_query or ""]
    candidates += re.split(r"[\W_]+", " ".join(candidates))
    candidates += [type_name + suffix for suffix in ("動画", "解説", "紹介")]
    for word in candidates:
        word = re.sub(r"[^\w]", "", word)
        tag = "#" + word
        if word and tag not in tags:
            tags.append(tag)
        if len(tags) >= 3:
            break
    return tags


def covers(run_dir: Path) -> None:
    """Extract a vertical cover at 0.5 s and a small preview for human review."""
    cover = run_dir / "cover.jpg"
    cover.unlink(missing_ok=True)
    (run_dir / "cover.png").unlink(missing_ok=True)
    if (run_dir / "covers.json").is_file():
        from .thumbnail import selected_cover
        chosen = selected_cover(run_dir)
        shutil.copy2(run_dir / "thumbs" / f"{chosen}.jpg", cover)
        png = run_dir / "thumbs" / f"{chosen}.png"
        if png.is_file():
            shutil.copy2(png, run_dir / "cover.png")
    else:
        run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-ss", "0.5", "-i",
             str(run_dir / "final.mp4"), "-vf", "scale=1080:1920,setsar=1", "-frames:v", "1",
             "-q:v", "2", str(cover)])
    with Image.open(cover) as image:
        image.resize((360, 640), Image.Resampling.LANCZOS).save(run_dir / "cover_small.jpg", quality=90)


def x_length(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in text)


def _truncate(text: str, limit: int, length=len) -> str:
    if length(text) <= limit:
        return text
    result = ""
    for char in text:
        if length(result + char + "…") > limit:
            break
        result += char
    return result + "…" if limit >= length("…") else result


def post_kit(destination: Path, publish: Publish) -> Path:
    kit = destination / "post_kit"
    kit.mkdir(exist_ok=True)
    # Keep tags valid and bounded even for independently supplied publish metadata.
    tags = list(dict.fromkeys("#" + re.sub(r"[^\w]", "", tag)[:24]
                              for tag in publish.hashtags if re.sub(r"[^\w]", "", tag)))[:5]
    for fallback in ("#ショート動画", "#解説", "#動画"):
        if len(tags) >= 3:
            break
        if fallback not in tags:
            tags.append(fallback)
    # At least three complete hashtags must fit alongside the X title.
    while x_length(" ".join(tags)) > 180:
        tags = ["#" + tag[1:13] for tag in tags[:3]]
    tag_text = " ".join(tags)
    sentence = re.split(r"(?<=[。！？.!?])|\n", publish.description.strip(), maxsplit=1)[0]
    title = _truncate(publish.title.replace("\n", " "), 100)
    tiktok = title + "\n" + _truncate(sentence, max(0, 2200 - len(title) - len(tag_text) - 2)) + "\n" + tag_text
    x = _truncate(title, 280 - x_length(tag_text) - 1, x_length) + "\n" + tag_text
    (kit / "tiktok.txt").write_text(tiktok, encoding="utf-8")
    (kit / "x.txt").write_text(x, encoding="utf-8")
    shutil.copy2(destination / "cover.jpg", kit / "cover_tiktok.jpg")
    run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(destination / "cover.jpg"),
         "-vf", "crop=1080:1440:(iw-ow)/2:(ih-oh)/2,setsar=1", "-frames:v", "1", "-threads", "1",
         "-q:v", "2", str(kit / "cover_tiktok_3x4.jpg")])
    instructions = ("1. 人が選んだ final.mp4 を TikTok / X の投稿画面で選びます。\n"
                    "2. tiktok.txt / x.txt を貼り、表紙を選び、切り取り見本を確認します。\n"
                    "3. publish.json の出典・必須クレジットも確認し、投稿先で表示してください。\n"
                    "4. 公開範囲と内容を人が確認して投稿します。\n")
    if publish.synthetic_media:
        instructions += "AI生成コンテンツを含みます。投稿先のAIラベル・合成メディア開示を有効にしてください。\n"
    (kit / "README.txt").write_text(instructions, encoding="utf-8")
    return kit


def package(run_dir) -> Path:
    run_dir = Path(run_dir)
    if not (run_dir / "qa.json").is_file():
        raise ValueError("Packaging requires qa.json")
    if any(not c.passed and c.severity == "block" and c.id != "metadata"
           for c in load(run_dir, "qa").checks):
        raise ValueError("Packaging requires a passed QA report")
    # Check deliverables before writing metadata or creating an outbox directory.
    for name in ("final.mp4", "final.srt"):
        if not (run_dir / name).is_file():
            raise FileNotFoundError(run_dir / name)
    brief = load(run_dir, "brief")
    script = load(run_dir, "script")
    manifest = load(run_dir, "manifest")
    parts = [script.lines[0].text]
    if (run_dir / "research.json").exists():
        research = load(run_dir, "research")
        if research.sources:
            parts.append("出典\n" + "\n".join(f"- {s.title} {s.url}" for s in research.sources))
    credits = list(dict.fromkeys(a.credit.strip() for a in manifest.assets if a.credit and a.credit.strip()))
    if credits:
        parts.append("\n".join(credits))
    if script.search_query and script.search_query not in "\n\n".join(parts):
        parts.insert(1, f"{script.search_query}について解説します。")
    board = load(run_dir, "storyboard") if (run_dir / "storyboard.json").is_file() else None
    type_id = (load(run_dir, "route").type_id if (run_dir / "route.json").is_file()
               else board.type_id if board else "short_video")
    spec = registry.load_types().get(type_id)
    type_name = spec.name if spec else type_id
    publish = Publish(title=script.title, description="\n\n".join(parts),
                      search_query=script.search_query, hashtags=_hashtags(brief, script, type_name),
                      platforms=brief.platforms,
                      synthetic_media=needs_disclosure(manifest) or (
                          (run_dir / "covers.json").is_file() and any(
                              c.ai_generated and c.photorealistic for c in load(run_dir, "covers").options)),
                      music_track=board.music.track if board and board.music else None)
    save(run_dir, "publish", publish)
    report = write_report(run_dir, [check_metadata(run_dir)])
    if not report.passed:
        raise ValueError("Packaging requires passed metadata QA (see qa.json)")
    covers(run_dir)
    destination = (ROOT / "outbox" / run_dir.parent.parent.name / run_dir.name
                   if run_dir.parent.name == "variants" else ROOT / "outbox" / run_dir.name)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("final.mp4", "final.srt", "publish.json", "cover.jpg", "cover_small.jpg"):
        shutil.copy2(run_dir / name, destination / name)
    (destination / "cover.png").unlink(missing_ok=True)
    if (run_dir / "covers.json").is_file():
        # Replace stale candidates when packaging the same variant again.
        shutil.rmtree(destination / "thumbs", ignore_errors=True)
        shutil.copytree(run_dir / "thumbs", destination / "thumbs")
        for name in ("covers.json", "covers_report.json", "cover.png"):
            if (run_dir / name).is_file():
                shutil.copy2(run_dir / name, destination / name)
    else:
        shutil.rmtree(destination / "thumbs", ignore_errors=True)
        for name in ("covers.json", "covers_report.json"):
            (destination / name).unlink(missing_ok=True)
    # Manual TikTok/X kits only when the brief targets those platforms (YouTube-only since 2026-11-05).
    if {"tiktok", "x"} & set(publish.platforms):
        post_kit(destination, publish)
    else:
        shutil.rmtree(destination / "post_kit", ignore_errors=True)
    return destination


def compare(run: Path, *, outbox: Path | None = None) -> Path:
    """Write a Japanese comparison of variants that have actually been packaged."""
    run = Path(run)
    destination = (Path(outbox) if outbox is not None else ROOT / "outbox") / run.name
    voices = registry.load_voices()
    tracks = registry.load_bgm()
    sections = [f"# {run.name} の動画候補", "完成した候補から選ぶか、台本・合成音声・映像・BGMを組み合わせられます。"]
    labels = {"script": "台本", "voice": "合成音声", "visuals": "映像", "bgm": "BGM"}
    for variant in runs.variants(run):
        packaged = destination / variant.name
        if not all((packaged / name).is_file() for name in ("publish.json", "final.mp4", "final.srt")):
            continue
        info = metadata(variant)
        script, board = load(variant, "script"), load(variant, "storyboard")
        report = load(variant, "qa")
        duration = load(variant, "timeline").duration
        cast = "、".join(f"{role}: {voices[vid].name if vid in voices else vid}" for role, vid in script.cast.items())
        music = "なし"
        if board.music:
            track = tracks.get(board.music.track)
            if track:
                music = f"{track.title} / {track.artist}（{track.id}）"
            elif board.music.asset:
                asset = next((a for a in load(variant, "manifest").assets if a.id == board.music.asset), None)
                music = (f"{asset.params.get('title', asset.id)} / {asset.params.get('artist', '作者未記録')}"
                         if asset else board.music.asset)
            elif board.music.track:
                music = board.music.track
        provenance = "、".join(f"{labels[key]}={info['parts'].get(key) or variant.name}" for key in labels)
        checks = "、".join(f"{c.id}: {'合格' if c.passed else '警告' if c.severity == 'warn' else '不合格'}"
                           + (f"（{c.detail}）" if c.detail else "") for c in report.checks)
        sections.append(f"## {variant.name}\n\n"
                        f"[動画]({variant.name}/final.mp4) / [字幕]({variant.name}/final.srt)\n\n"
                        f"- 違い: {info['note'] or '未記入'}\n"
                        f"- パーツの由来: {provenance}\n"
                        f"- 尺: {duration:.1f}秒\n"
                        f"- 合成音声: {cast}\n"
                        f"- BGM: {music}\n"
                        f"- シーン数: {len(board.scenes)}\n"
                        f"- QA: {'合格' if report.passed else '不合格'} — {checks}")
        if (packaged / "covers.json").is_file():
            from .thumbnail import selected_cover
            chosen = selected_cover(packaged)
            options = load(packaged, "covers").options
            sections.append("表紙候補（最終選択は人が行います）: " + " / ".join(
                (f"[{c.id}: {c.text.replace(chr(10), ' ')}]({variant.name}/thumbs/{c.id}.jpg)"
                 if (packaged / "thumbs" / f"{c.id}.jpg").is_file()
                 else f"{c.id}: {c.text.replace(chr(10), ' ')}（未出力・要修正）") +
                f"（回収: {c.line_id}{'・現在の表紙' if c.id == chosen else ''}）" for c in options) +
                f"\n\n[一覧]({variant.name}/thumbs/sheet.jpg)" + (
                    f" / [手動投稿キット]({variant.name}/post_kit/README.txt)"
                    if (packaged / "post_kit" / "README.txt").is_file() else ""))
    sections.append("## 組み合わせの頼み方\n\n"
                    "例: 「v2のBGMを用いてv3の動画に合わせ、合成音声はv1」\n\n"
                    f"```sh\nvg mix {shlex.quote(str(run))} --base v3 --bgm v2 --voice v1\n```\n\n"
                    "新しい候補はTTS・素材・タイムライン・レンダリング・QAを実行します。"
                    "合格後に `vg package` で出力し、`vg compare` でこの比較を更新してください。")
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "README.md"
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    return path
