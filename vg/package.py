"""Create publishing metadata and copy a QA-approved run to the local outbox."""
from pathlib import Path
import re
import shlex
import shutil

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
    run([str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-ss", "0.5", "-i",
         str(run_dir / "final.mp4"), "-vf", "scale=1080:1920,setsar=1", "-frames:v", "1",
         "-q:v", "2", str(cover)])
    with Image.open(cover) as image:
        image.resize((360, 640), Image.Resampling.LANCZOS).save(run_dir / "cover_small.jpg", quality=90)


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
                      synthetic_media=needs_disclosure(manifest),
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
    sections.append("## 組み合わせの頼み方\n\n"
                    "例: 「v2のBGMを用いてv3の動画に合わせ、合成音声はv1」\n\n"
                    f"```sh\nvg mix {shlex.quote(str(run))} --base v3 --bgm v2 --voice v1\n```\n\n"
                    "新しい候補はTTS・素材・タイムライン・レンダリング・QAを実行します。"
                    "合格後に `vg package` で出力し、`vg compare` でこの比較を更新してください。")
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "README.md"
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    return path
