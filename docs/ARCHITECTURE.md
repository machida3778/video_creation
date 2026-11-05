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
| Final selection and publication decision | Human, or the opt-in daily autopost (`vg daily`) | Selected candidate, or one scheduled upload per day |

Recipes in `types/<id>/type.yaml` define routing criteria, length limits, components, model capabilities, rights requirements and quality criteria. Planned recipes are not implemented backends. The initial implemented recipe is `trivia_explainer`.

Speech precedes storyboard timing so scene durations follow actual narration. Each run has a `state.json`; stages can resume from the earliest changed input. Specifications are validated by Pydantic models in `vg/schemas.py`, and stage storage uses atomic writes.

## QA gates

Implemented deterministic checks cover media format, duration, loudness and true peak, black frames, long silence, caption safe area and caption timing. `config/style.yaml` also sets hook timing, maximum visual holds, speech rate, caption/title coverage, character-3-gram script originality and SFX density. Originality compares other runs, excluding all variants of the current run. Realistic image/video assets require synthetic-media disclosure; packaging sets the flag from manifest metadata and prompts, and QA catches an existing false flag. Blocking failures stop packaging; warnings remain visible in comparison reports.

`vg analyze VIDEO --json PATH` measures arbitrary local video, including scene changes, visual hold intervals, EBU R128 loudness, audio activity above −35 dBFS and a six-frame JPEG contact sheet. Hook/tempo QA uses specification timing; pixel-based scene detection is supplementary. `vg sfx build` creates original procedural WAVs in ignored `assets/sfx/`; assets used by a storyboard are copied into its run and licensed in its manifest using `config/sfx.yaml`.

Agents also review facts, pronunciation, visual coherence, originality, licences and AI-content disclosure. ASR-based pronunciation checking and automated visual/factual review are extension points, not current machine checks. Passing technical QA does not replace human editorial review.

## Variants and recombination

A run shares its brief, route and research across independent `variants/vN/` directories. Each candidate owns its script, voice settings, storyboard, licensed assets and derived outputs. `vg compare` prepares a comparison for human selection.

`vg mix` combines the script, voice, visuals and BGM from selected variants, records provenance, preserves asset licence records, and regenerates dependent outputs. Personal feedback belongs in the private run or `local/preferences.yaml`.

## Daily autopost (opt-in)

`scripts/daily_post.sh` runs from cron. When `vg daily status --check` reports that today's slot still needs a video, it starts a headless Claude session with `/daily-post` (`VG_HEADLESS=1 VG_AUTOPOST=1`). The session takes the first unchecked item of the user's Markdown checklist (`autopost.list`, git-ignored), or adds one from the user's past items and the channel's statistics when the list is empty, and produces one variant with the normal stages and QA gates. `vg daily publish` is the only publishing command allowed in that session: it uploads privately with `publishAt` set to the day's slot (weekday and day-off times in `autopost`, Japanese holidays via `jpholiday`), then ticks the item and records the URL. Failures are noted under the item and the next attempt resumes the same run; a slot that cannot be met is skipped rather than posted late.

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
