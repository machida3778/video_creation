# Architecture

The harness turns a one-line request into vertical video candidates. An LLM agent writes validated JSON specifications; deterministic tools synthesize speech, assemble media, render, and check the result. A human selects the final version and decides whether to publish, unless the opt-in daily autopost is enabled (see below).

## Constraints

- No API keys or paid model APIs. Orchestration uses agent subscription logins; generation uses open-weights models on the operator's own GPUs.
- Model outputs and all assets must be usable commercially. Record licences, source URLs, generation parameters and required credits in each run's `assets/manifest.json`.
- Voice requests resolve through `config/voices.yaml`, subject to the voice's content restrictions.
- Generated content, personal preferences and site settings remain outside the public tree.

## Stages and artifacts

| Stage | Owner | Artifact |
|---|---|---|
| Brief and recipe selection | Agent | `brief.json`, `route.json` |
| Research and script | Agent | `research.json`, `script.json` |
| Independent fact check | Reviewing agent | `factcheck.json` |
| Speech synthesis | `vg tts` | Audio and `timings.json` |
| Storyboard | Agent | `storyboard.json` |
| Asset generation and selection | `vg assets` | Images, music and licence manifest |
| Timeline assembly | `vg timeline` | `timeline.json` |
| Rendering | `vg render` | `final.mp4`, `final.srt` |
| Quality checks | `vg qa` and agent review | `qa.json`, contact sheet |
| Packaging | `vg package` | Candidate files and `publish.json` in `outbox/` |
| Final selection and publication decision | Human, or the opt-in daily autopost (`vg daily`) | Selected candidate, or scheduled uploads at the configured daily slots |

Recipes in `types/<id>/type.yaml` define routing criteria, length limits, components, model capabilities, rights requirements and quality criteria. Planned recipes are not implemented backends. The initial implemented recipe is `trivia_explainer`.

Speech precedes storyboard timing so scene durations follow actual narration. Each run has a `state.json`; stages can resume from the earliest changed input. Specifications are validated by Pydantic models in `vg/schemas.py`, and stage storage uses atomic writes.

`ranking_montage` is an experimental `music_motion` recipe without narration. Its storyboard precedes `vg tts`: one scene per script label with explicit `props.sec` produces cumulative display timings and empty audio paths, without contacting a speech engine. Timeline assembly omits voice clips, captions and the narration tail while retaining BGM mixing. Full-bleed stills carry serif `rank_label` overlays, a base-media `mirror` entrance and escalating tier effects; `section_card` draws an original procedural starfield. The assets stage records the system Noto Serif CJK JP font licence. Music QA counts label/card text for hook and coverage, skips speech/yomi checks and retains the normal tempo limit; factual rankings still require sources and editorial fact checking. The countdown starts at 10th place at most (`vg tts` rejects more ranked labels). Every picture keeps moving (an eased settle from a closer framing, sideways drift, a single glint, drifting lights; labels slide, wipe and shine), and the 1st place (`tier: top1`, `reveal` seconds, about 6 s in all) builds up on a darkened, blurred picture with a pulsing gold 「1位」 before a white flash, rotating rays, confetti and a gold frame; the procedural `riser`/`impact` effects mark it. Without narration, effects are levelled against the measured music (`sfx_vs_music_db`).

Rankings (`ranking`, `ranking_montage`) are stated with confidence: QA `confidence` blocks hedges such as 「独断」「根拠はない」「個人的」 in the title, lines, cards, covers and description. Covers of runs with talent pictures (assets added by `vg image add`) must show those pictures — one (`image`) or a 2–3 column collage (`images`) — with the headline at the bottom of the safe region (`vg thumbnail` check `talent`).

## QA gates

Implemented deterministic checks cover media format, duration, loudness and true peak, black frames, long silence, caption safe area and caption timing. `config/style.yaml` also sets hook timing, maximum visual holds, speech rate, caption/title coverage, character-3-gram script originality and SFX density. Originality compares other runs, excluding all variants of the current run. Realistic image/video assets require synthetic-media disclosure; packaging sets the flag from manifest metadata and prompts, and QA catches an existing false flag. Blocking failures stop packaging; warnings remain visible in comparison reports.

`vg analyze VIDEO --json PATH` measures arbitrary local video, including scene changes, visual hold intervals, EBU R128 loudness, audio activity above −35 dBFS and a six-frame JPEG contact sheet. Hook/tempo QA uses specification timing; pixel-based scene detection is supplementary. `vg sfx build` creates original procedural WAVs in ignored `assets/sfx/`; assets used by a storyboard are copied into its run and licensed in its manifest using `config/sfx.yaml`.

Agents also review facts, pronunciation, visual coherence, originality, licences and AI-content disclosure. ASR-based pronunciation checking and automated visual/factual review are extension points, not current machine checks. Passing technical QA does not replace human editorial review.

## Formats from reference videos

A format is a reusable presentation and structure learned from a Short the user liked (`.claude/skills/learn-format`). `vg reference add SOURCE --id ID` (`vg/reference.py`) fetches a URL with yt-dlp (through `uvx`, with subtitles when the platform has them) or copies a local file into the ignored `local/formats/<id>/reference/`, measures shots, loudness, audio activity and narration rate, and writes frame sheets (hook, first frame of each shot, even sample, three full-size frames). The agent reads them and writes `format.json` (`FormatProfile`): beat structure, pacing, script/visual/audio rules and a `Layout`. `vg format preview` renders the layout as a still beside a reference frame for correction.

A run uses a format through `route.format` and `storyboard.format`; `storyboard.header` fills the layout's header band. `vg timeline` copies the layout into `timeline.json`; the renderer places the base picture in the frame (full bleed with optional dimming, or a box over a blurred or flat fill), draws the header/footer bands and progress bar, and styles captions and title cards. Caption breaking and the `safe_area` check use the layout's caption size and width, and also check the header text; the schema keeps every band and caption block inside the Shorts safe area. Only presentation and structure are reused; scripts and media are always new.

Outside pictures of real talents come from `vg image add` (`vg/library.py`) under licences listed in `config/licenses.yaml`, e.g. a still from a talent's public stream under a published clip guideline. The manifest records the licence URL, the source video and the credit; credits marked `credit_first` lead the packaged description.

## Variants and recombination

A run shares its brief, route and research across independent `variants/vN/` directories. Each candidate owns its script, voice settings, storyboard, licensed assets and derived outputs. `vg compare` prepares a comparison for human selection.

`vg mix` combines the script, voice, visuals and BGM from selected variants, records provenance, preserves asset licence records, and regenerates dependent outputs. Personal feedback belongs in the private run or `local/preferences.yaml`.

## Daily autopost (opt-in)

Every unattended run (`scripts/run_request.sh`, used by the daily runner and the queue) first runs `vg usage --check` (`vg/usage.py`): a short interactive Claude session in a pseudo-terminal reports the subscription's five-hour and seven-day usage through the status line `rate_limits`, and the run starts only when each is at or under its limit in `max_usage_percent` in `config/headless.yaml` (five hours 70%, seven days 90%; one number applies to both). Over the limit or unknown, it exits 75 without starting; the next cron or queue attempt checks again.

`scripts/daily_post.sh` runs from cron. When `vg daily status --check` reports that one of today's slots still needs a video, it starts a headless Claude session with `/daily-post` (`VG_HEADLESS=1 VG_AUTOPOST=1`). The session takes the first unchecked item of the user's Markdown checklist (`autopost.list`, git-ignored), or adds one from the user's past items and the channel's statistics when the list is empty, and produces one variant with the normal stages and QA gates. `vg daily publish` is the only publishing command allowed in that session: it uploads privately with `publishAt` set to the slot the run was started for, then ticks the item and records the URL. `autopost.weekday` and `autopost.holiday` (Japanese holidays via `jpholiday`) hold one time or a list for several videos a day; each session fills the earliest open slot that still leaves `min_work_minutes`, and a slot within `min_gap_minutes` of a video already scheduled that day counts as filled. Cron runs the script hourly; state is kept per slot. Failures are noted under the item and the next attempt resumes the same run for the next open slot; a slot that cannot be met is skipped rather than posted late.

Once a day, the first daily session runs a performance review, whether or not the list has items. `vg daily review` (`vg/review.py`) joins each upload's YouTube statistics and analytics (retention, shares, subscribers gained) with the production choices recorded in its run: type, hook, length, voice, BGM, cover, slot and whether the request came from the user or was added automatically. An independent subagent (`.claude/skills/daily-post/review.md`) compares the videos that grew with those that did not and rewrites the ignored `local/insights.md` with evidence-backed production rules, one or two experiments and suggestions for the user. `vg daily review --done` marks the day as reviewed. make-video reads the file before choosing the type, hook, script and cover; the lessons never override the hard rules, QA gates, the fact check or the user's preferences.

## Compute and site configuration

| Work | Compute |
|---|---|
| Agent orchestration, VOICEVOX, Remotion, ffmpeg and machine QA | CPU server |
| Image generation and other GPU workers | lab GPU servers |
| Batch workloads | HPC cluster (NQSV) or HPC cluster (PBS) |

Shared lab GPUs are checked immediately before work with `scripts/lab/gpu_free.sh`. The asset backend tries the smallest idle eligible GPU with enough memory and retries another candidate if it becomes busy. Long-lived queue workers can amortize model loading.

Configure host lists, path mappings and cluster accounts in ignored `local/site.env`, using `config/site.example.env`. Missing required settings fail before remote work. Python and shell loaders allow environment overrides. Site-specific access procedures belong in `local/AGENTS.local.md`.

## Repository layout

```text
.claude/          agent skills and guard hooks
config/           voice/BGM registries and example site configuration
docs/             architecture and model selection
types/<id>/       recipe and stage prompts
vg/               schemas, deterministic stages, CLI and QA
render/           Remotion templates and shared components
workers/<tool>/   GPU implementations and isolated environments
scripts/          setup/, lab/, hpc_nqsv/, hpc_pbs/
tests/            synthetic fixtures and offline tests
local/            private settings, notes and research (ignored)
runs/             intermediate production artifacts (ignored)
outbox/           QA-approved candidates for human selection (ignored)
assets/media/     licensed media library (ignored)
```
