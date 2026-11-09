---
name: learn-format
description: Learn a reusable video format from a reference Short the user liked (URL or file) — fetch and measure it, read its layout, structure and pacing from frame sheets, write local/formats/<id>/format.json, render a preview and correct it until it matches. Use when the user shares a reference video 「この動画みたいなフォーマットで」「このフォーマットを覚えて」「参考動画」, or when a make-video request includes a video URL as a model.
---

# learn-format

A format copies **presentation and structure** only: layout (header band, picture frame, caption look), beat structure, pacing, script and visual rules. Never copy the reference's script lines, images, logos, catchphrases or audio into a run; videos made with the format are written and drawn anew (YouTube reused-content policy, originality QA).

Formats and references live in the ignored `local/formats/<id>/`; never commit them. Commands run from the repo root with `uv run vg ...`.

## 1. Fetch and measure
`vg reference add <URL|file> --id <format-id>` (ascii, e.g. `header-box-ranking`). Run it again with another URL of the same style to add `reference2/` — two or three references make a sturdier format. It prints a summary: duration, shot count and lengths, loudness, subtitles and the narration rate, the transcript, and the frame sheets.

## 2. Read the reference
Look at every sheet with the Read tool:
- `sheet_hook.jpg` — the first 3.5 s every 0.5 s: how the hook is shown (text first? face? question?), how fast the first cut comes.
- `sheet_shots.jpg` — the first frame of each shot: the beat structure and what kind of picture each beat uses.
- `sheet_even.jpg` — an even sample: what stays fixed (header band, frame, progress bar, logo position).
- `frame_25/50/75.jpg` (1080 px wide) — measure positions in pixels: header top/height and font size, picture frame top/height/margins, caption centre y and font size, colours (hex), outline vs box captions.
Read the transcript for the hook pattern, sentence length, how items are introduced and how it ends.

## 3. Write `local/formats/<id>/format.json`
Schema: `FormatProfile` in `vg/schemas.py` (layout = `Layout`). Fill:
- `summary`: what makes the format work, in one or two sentences.
- `types`: implemented types it fits (`ranking`, `trivia_explainer`, `dialogue_explainer`).
- `structure`: beats with `share` of the running time (sum 1) and what happens in each.
- `pacing`: measured `median_shot_sec`; `max_static_sec` for the storyboard (≤ 6, the QA limit); `speech_cps`; `narration_speed` (VOICEVOX speed; 1.4 ≈ 8 chars/s); `target_sec` 30–59 (the channel's target is 45 s).
- `layout`: the measured positions, sizes and colours. Text must stay in the Shorts safe area (x 120–900, y 240–1440); the schema refuses bands or captions under the bottom UI or overlapping each other. If the reference puts text where the Shorts UI covers it, move it into the safe area and say so in `avoid`.
- `script_rules`, `visual_rules`, `audio_rules`: concrete, checkable rules (e.g. 「1項目は『第N位、名前。数字。ひとこと』の3文」), not adjectives.
- `header_rule` when there is a header band; `avoid`: what must not be copied.
Then `vg format validate <id>`.

## 4. Preview and correct
`vg format preview <id> --header "<sample header>"` renders the layout over the reference's middle frame and writes `preview/compare.jpg` (reference left, preview right). Compare positions, sizes, colours and weight; fix `layout` and preview again (at most 3 rounds). Text breaks: give `\n` explicitly in headers when BudouX splits a compound word.

## 5. Report
Tell the user the format id and name, what was learned (structure, pacing, layout) and what differs from the reference on purpose, and show `preview/compare.jpg`'s path. To make it the default for the daily posts, the user can add `formats: {default: <id>}` to `local/preferences.yaml`; otherwise a request names it (「<id>のフォーマットで〇〇」).
