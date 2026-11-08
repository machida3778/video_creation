---
name: make-video
description: Produce a finished vertical YouTube Short from a one-line Japanese request, end to end without asking the user — brief, type choice, research, script, fact check, TTS, storyboard, assets, render, QA, outbox. Use when asked 「〜の動画を作って」, "make a video about …", or /make-video <request>.
---

# make-video

Headless runs start from `scripts/run_request.sh` and must never ask questions; make reasonable assumptions or report a blocker. In headless runs use the absolute `<primary working directory>/.venv/bin/vg` and absolute file paths: the Bash shell may start in another directory, so relative paths fail.

Prepare candidates without routine clarification. This skill never publishes: publishing happens only through `publish-video` (the user's choice) or `daily-post` (the scheduled daily upload, which runs this skill in daily mode). Every decision is recorded in the run directory; when something blocks (missing tool, no allowed voice, QA keeps failing), stop and report what is missing instead of asking. Rules from AGENTS.md apply (no API keys or paid APIs; open-weights models only; licences recorded).

**Usage gate (user rules 2026-11-06, 2026-11-08):** start a new video only while each Claude subscription window is at or under its `max_usage_percent` in `config/headless.yaml` (5-hour 70%, weekly 90%). Headless runs are checked by `scripts/run_request.sh` before they start. In an interactive session run `uv run vg usage --check` first; if it exits non-zero (over the limit or unknown), do not start: report its line and stop.

**Lessons from analytics:** if `local/insights.md` exists (rewritten once a day by the daily review, `.claude/skills/daily-post/review.md`), read it before step 2 and apply its 制作ルール (type, hook, title, pacing, length, cover, BGM) as defaults wherever they fit the request; follow the current 実験 in daily mode. They never override the hard rules, QA gates, the fact check, title accuracy, `local/preferences.yaml` or what the user asked for. Mention in the report which rules were applied.

Commands below run from the repo root: `uv run vg ...` (repo-relative paths). Schemas: `vg/schemas.py`. After writing any stage JSON run `vg validate <run>`.

## 1. Run and brief
1. `vg new "<request verbatim>" --slug <short-ascii-slug>` → prints RUN.
2. Write `RUN/brief.json` (Brief): `request` verbatim, `topic`, `platforms`: `[youtube_shorts]` (the channel is YouTube-only since 2026-11-05; the TikTok/X length limits in the types are dormant and conflict with the Shorts limits, so never add them), `tone`, and the voice wording copied verbatim into `voice.raw` (e.g. 「落ち着いた男性ナレーターで」); for dialogue types fill `cast` per role. Lengths come from the type (step 2).

## 2. Type
Read the candidate `types/*/type.yaml` (optionally `local/VIDEO_TYPES.md` when present) (`use_when`, `avoid_when`, `status`). Choose one type and its `params`; write `RUN/route.json` (Route) with a one-line reason.
- Only types with `status: experimental` or `stable` can be produced. Implemented now: `trivia_explainer`, `ranking`, `dialogue_explainer`.
- If the request fits a planned type that is not implemented yet, produce the nearest implemented type when that still answers the request honestly; otherwise stop and report.
- Set `brief.target_sec`/`max_sec` from the type's `length` (use the strictest platform limits).

## 3. Research (when the type requires it)
Use web search/fetch (no paid APIs) to collect at least `research.min_sources` reliable sources: official, government, academic, encyclopedic or major-newspaper pages; record title, publisher, URL, published and accessed dates. Write `RUN/research.json`: one `Fact` per claim, citing source ids; mark `confirmed` only when a primary or two independent sources agree. Avoid topics you cannot source.

Before writing the script, list the titles in `outbox/*/publish.json`, `outbox/*/*/publish.json`, `runs/*/script.json` and `runs/*/variants/*/script.json`: do not repeat a topic or angle already produced (YouTube inauthentic-content policy).

## 4. Script
Follow `types/<type>/prompts/script.md`. Get the voices with `vg cast RUN` and copy its `cast` into `script.json`; mention any `notes` (rejected voice requests) in the final report. Every factual line lists its `fact_ids`. Give `yomi` (katakana reading) for proper nouns, English words, abbreviations, numbers with units and any kanji with doubtful readings.

Set `script.search_query` to one main search phrase and include it naturally in on-screen text. Set `script.hook_options` to at least four alternative opening lines spanning at least three hook types, then select one as the first script line (see `docs/SHORTS_CRAFT.md`). Put relevant hashtags in brief constraints starting with `#`; packaging keeps three to five and fills missing tags from the type and search phrase.

## 5. Fact check (independent)
Spawn a fresh general-purpose subagent with only `research.json` and `script.json` (paths) and ask it, without your reasoning, to judge every line: supported / unsupported / overstated / not_factual (opinion or framing), and to fetch the cited URLs when needed. Write `RUN/factcheck.json` (FactCheck, `checker: claude-subagent`). Fix the script and repeat until every factual line is supported (at most 2 rounds; then drop the unsupported lines or stop).

## 6. TTS
`vg tts RUN` → `audio/*.wav`, `timings.json`. Check `timings.total_sec` against the type length; if outside, shorten or extend the script and redo 4–6.

After each TTS (including variants), run `vg yomi RUN:vN`, fix readings (add `yomi`) for wrong or flagged lines, rerun `vg tts RUN:vN`, then `vg yomi RUN:vN --approve` (use `RUN` for a run without variants).

### 6b. English on the same video
After yomi approval, write `en.json` per variant (`English` in `vg/schemas.py`): English title/description and `{id, text}` for every script line, no extras. Adapt natural spoken English for international viewers; keep all facts identical to the fact-checked Japanese, preserve source/credit lines verbatim, and retain the title's withheld-answer hook. Aim for 2.6 words/sec within each Japanese line's time. Keep the Japanese picture/telop unchanged. In step 8, run assets → timeline → render explicitly, then `vg en RUN:vN` before `vg qa` / `vg package`; for variants render all first and use `vg en RUN` once to batch one model load. Shorten lines that cannot fit at <=1.15x; QA blocks above 3.3 words/sec, warns above 3.0. `dub_en.m4a` is for manual Studio audio upload only.

## 7. Storyboard and assets
Follow `types/<type>/prompts/storyboard.md`. Scenes must cover every line exactly once, in order, using only components the renderer knows (`render/src/components/registry.tsx` COMPONENTS). Scenes with `visual.kind: t2i` get images from `vg assets RUN` (Z-Image Turbo on an idle lab GPU, recorded in `assets/manifest.json` with prompt, seed and licence). If no lab GPU is idle, wait and retry later, or fall back to backgrounds and say so in the report.

## 8. Assets, render, QA, package
With `en.json`, use step 6b's explicit stage order; render all variants before the single batched `vg en RUN`, then QA/package each variant.
`vg make RUN --from assets` (assets → timeline → render → qa → package). When QA fails, read `RUN/qa.json`, go back to the failing check's `redo_stage`, fix, and rerun from there (at most 2 retries per check). After packaging, run `vg sheet RUN:vN` and `vg analyze RUN:vN/final.mp4` for each candidate; inspect the contact sheet, the outbox package's `cover_small.jpg`, and the timing/audio summary before reporting (use `RUN` directly for a run without variants). Check that the cover remains readable at preview size; `cover.jpg` is the full-size candidate. Covers follow `docs/THUMBNAILS.md` (「惹きつける見出し」, 「表紙専用の画像」, 「採点と選び方」): at least two of the three options use cover-only images (`prompt` in `covers.json`, written before `vg assets` so they are drawn in the same GPU job), not narration scenes reused as stills. Never score your own covers: a fresh subagent sees only each option's `thumbs/<id>_180.jpg` and `<id>.jpg`, the title and the payoff line, and scores the eight rubric items strictly (16 points). Under 12, or no option with 2 on ④ and ⑦: redo the headline or prompt/seed once (`vg assets`, `vg thumbnail`) and re-score. Set `chosen_id` to the best, record the judge's scores in `covers_report.json`, rerun `vg package`, and report the scores.

Record the cover judge's answer with `vg thumbnail RUN:vN --score RUN/variants/vN/cover_judge.json` (CoverJudgement in `vg/schemas.py`: `judge`, `round`, and per option `id`, the eight rubric `items` 0–2 and a `note`); it stores the scores and sets `chosen_id` in `covers_report.json`, and prints `bar_met`. Never edit `covers_report.json` by hand; a person's pick is `vg thumbnail RUN:vN --choose <id>`. A later `vg thumbnail` keeps the scores of unchanged options and the choice while it passes.

## 8b. Variants (local/preferences.yaml `delivery`)
**Daily mode** (invoked from `daily-post`): skip the comparison set. Make exactly one variant `v1` (`vg variant new RUN --id v1 --note daily`) with the preferred voice (`voice.main_narrator` at `voice.narrator_speed`), one BGM from `vg bgm suggest RUN --n 1`, then continue below for v1 only and return to `daily-post`.

Otherwise produce **3 variants** of every video so the user can choose (the user decides and uploads; this skill never publishes).
Make the shared brief/route/research once in RUN, then `vg variant new RUN --id v1 --note "<what is different>"` (v2, v3) and give each its own script/storyboard. Vary across variants, not all in one: **BGM** (always a different track each; pick from `vg bgm suggest RUN --n 3`), **voice** (different cast or speed), **structure** (e.g. answer-first vs question-first vs surprise-first hook), **visuals** (different image style/prompts/seeds). Every new script gets its own fact check. Run `vg tts RUN:vN` and finish each storyboard first, then `vg assets RUN --all` once for all variants (one GPU job and model load). Continue with `vg make RUN:vN --from timeline` for each, `vg sheet RUN:vN` and inspect each packaged `cover_small.jpg`, then `vg compare RUN` for the side-by-side README in outbox.

## 9. Report
Reply with: outbox path of the comparison README, per variant what differs (BGM, voice, structure, visuals), duration, type, voice(s) and any rejected voice request, number of sources, QA summary (warnings too), and what a human should know before publishing (e.g. disclosure flags). Update nothing else.
