---
name: revise-video
description: Apply the user's feedback to a produced video — recombine variants (「v2のBGMを用いてv3の動画に合わせ、合成音声はv1」), change voice/speed/BGM/images/script, and remember general preferences. Use when the user comments on a made video or its variants.
---

# revise-video

Run commands from the repository root with `uv run vg ...`. Read recipes from `types/*/type.yaml`; `local/VIDEO_TYPES.md` is optional when present. Never publish automatically.

1. Find the run (`outbox/<run>/README.md`, or the latest run). Record the feedback verbatim: `vg feedback RUN "<text>" [--variant vN]`.
2. Map the request to parts — script (構成・台本), voice (合成音声・声・速さ), visuals (動画・映像・画像), bgm (BGM・音楽):
   - Recombination → `vg mix RUN --base <video variant> [--voice vX] [--bgm vY] [--script vZ] --note "<the request>"`; "動画/映像" means visuals+script unless the user says otherwise, so use it as `--base`.
   - Changes inside one part (e.g. 「もっと速く」「この画像を変えて」「BGMを〇〇に」) → `vg variant new RUN --from vN --note ...`, edit that variant's script.json (`voice_overrides`, lines), storyboard.json (scenes, `music.track`), then rerun from the earliest changed stage (`vg make RUN:vM --from tts|assets|timeline`). New or changed factual lines need a fact check.
3. Render, QA, `vg sheet`, `vg package RUN:vM`, `vg compare RUN`.
4. If the feedback is general (applies to future videos, e.g. 「声が遅い」「BGMは必ず」), also add it to `local/preferences.yaml` (what, the user's words, date) and to private notes when it is type-specific. Keep personal feedback out of tracked files; do not commit unless asked.
5. Report which variant was made from which parts and where it is.
