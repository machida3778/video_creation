---
name: make-video
description: Produce a finished vertical short (YouTube Shorts / TikTok / X) from a one-line Japanese request, end to end without asking the user — brief, type choice, research, script, fact check, TTS, storyboard, assets, render, QA, outbox. Use when asked 「〜の動画を作って」, "make a video about …", or /make-video <request>.
---

# make-video

Prepare candidates for human selection without routine clarification. Never publish automatically. Every decision is recorded in the run directory; when something blocks (missing tool, no allowed voice, QA keeps failing), stop and report what is missing instead of asking. Rules from AGENTS.md apply (no API keys or paid APIs; open-weights models only; licences recorded).

Commands below run from the repo root: `uv run vg ...` (repo-relative paths). Schemas: `vg/schemas.py`. After writing any stage JSON run `vg validate <run>`.

## 1. Run and brief
1. `vg new "<request verbatim>" --slug <short-ascii-slug>` → prints RUN.
2. Write `RUN/brief.json` (Brief): `request` verbatim, `topic`, `platforms` (default `[youtube_shorts, tiktok, x]`), `tone`, and the voice wording copied verbatim into `voice.raw` (e.g. 「落ち着いた男性ナレーターで」); for dialogue types fill `cast` per role. Lengths come from the type (step 2).

## 2. Type
Read the candidate `types/*/type.yaml` (optionally `local/VIDEO_TYPES.md` when present) (`use_when`, `avoid_when`, `status`). Choose one type and its `params`; write `RUN/route.json` (Route) with a one-line reason.
- Only types with `status: experimental` or `stable` can be produced. Implemented now: `trivia_explainer`.
- If the request fits a planned type that is not implemented yet, produce the nearest implemented type when that still answers the request honestly; otherwise stop and report.
- Set `brief.target_sec`/`max_sec` from the type's `length` (use the strictest platform limits).

## 3. Research (when the type requires it)
Use web search/fetch (no paid APIs) to collect at least `research.min_sources` reliable sources: official, government, academic, encyclopedic or major-newspaper pages; record title, publisher, URL, published and accessed dates. Write `RUN/research.json`: one `Fact` per claim, citing source ids; mark `confirmed` only when a primary or two independent sources agree. Avoid topics you cannot source.

Before writing the script, list `outbox/*/publish.json` titles and `runs/*/script.json` titles: do not repeat a topic or angle already produced (YouTube inauthentic-content policy).

## 4. Script
Follow `types/<type>/prompts/script.md`. Get the voices with `vg cast RUN` and copy its `cast` into `script.json`; mention any `notes` (rejected voice requests) in the final report. Every factual line lists its `fact_ids`. Give `yomi` (katakana reading) for proper nouns, English words, abbreviations, numbers with units and any kanji with doubtful readings.

## 5. Fact check (independent)
Spawn a fresh general-purpose subagent with only `research.json` and `script.json` (paths) and ask it, without your reasoning, to judge every line: supported / unsupported / overstated / not_factual (opinion or framing), and to fetch the cited URLs when needed. Write `RUN/factcheck.json` (FactCheck, `checker: claude-subagent`). Fix the script and repeat until every factual line is supported (at most 2 rounds; then drop the unsupported lines or stop).

## 6. TTS
`vg tts RUN` → `audio/*.wav`, `timings.json`. Check `timings.total_sec` against the type length; if outside, shorten or extend the script and redo 4–6.

## 7. Storyboard and assets
Follow `types/<type>/prompts/storyboard.md`. Scenes must cover every line exactly once, in order, using only components the renderer knows (`render/src/components/registry.tsx` COMPONENTS). Scenes with `visual.kind: t2i` get images from `vg assets RUN` (Z-Image Turbo on an idle lab GPU, recorded in `assets/manifest.json` with prompt, seed and licence). If no lab GPU is idle, wait and retry later, or fall back to backgrounds and say so in the report.

## 8. Assets, render, QA, package
`vg make RUN --from assets` (assets → timeline → render → qa → package). When QA fails, read `RUN/qa.json`, go back to the failing check's `redo_stage`, fix, and rerun from there (at most 2 retries per check). After packaging, run `vg sheet RUN:vN` and `vg analyze RUN:vN/final.mp4` for each candidate; inspect the contact sheet and the timing/audio summary before reporting (use `RUN` directly for a run without variants).

## 8b. Variants (always; local/preferences.yaml `delivery`)
Produce **3 variants** of every video so the user can choose (the user decides and uploads; never publish).
Make the shared brief/route/research once in RUN, then `vg variant new RUN --id v1 --note "<what is different>"` (v2, v3) and give each its own script/storyboard. Vary across variants, not all in one: **BGM** (always a different track each; pick from `vg bgm suggest RUN --n 3`), **voice** (different cast or speed), **structure** (e.g. answer-first vs question-first vs surprise-first hook), **visuals** (different image style/prompts/seeds). Every new script gets its own fact check. Then `vg make RUN:vN` for each (`--from tts`), `vg sheet RUN:vN` to check, and `vg compare RUN` for the side-by-side README in outbox.

## 9. Report
Reply with: outbox path of the comparison README, per variant what differs (BGM, voice, structure, visuals), duration, type, voice(s) and any rejected voice request, number of sources, QA summary (warnings too), and what a human should know before publishing (e.g. disclosure flags). Update nothing else.
