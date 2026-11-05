---
name: daily-post
description: Scheduled daily upload, run by scripts/daily_post.sh (cron) — take the next unchecked item of the user's request list (local/requests.md), or add one from past trends when the list is empty, make one YouTube Short and schedule it for today's slot (weekdays 18:00, weekends and Japanese holidays 10:00 JST). Use only when invoked as /daily-post.
---

# daily-post

Unattended: never ask questions. The user opted in to one automatic upload per day (2026-11-05; `local/preferences.yaml` `autopost`). `vg daily publish` is the only publishing command allowed here: it uploads as private with `publishAt` = today's slot, ticks the item in the list and records the URL. `vg publish ...` stays refused.

**Paths:** the argument of `/daily-post` is the repository path REPO (also this session's primary working directory). The Bash shell may start in another directory (the user's shell profile changes it), so run every command with absolute paths: `REPO/.venv/bin/vg ...` (written `vg` below), and read or write files as `REPO/...`. Relative paths such as `.venv/bin/vg` fail.

## 1. Is anything due?
`vg daily status`. Stop with a one-line report if `enabled` is false, `scheduled` is true or `lead_ok` is false.

## 2. Pick the request
- `vg daily next`. If `failures` is 2 or more, or the request cannot be made honestly with an implemented type (`trivia_explainer`, `ranking`, `dialogue_explainer`) or breaks the rules (news, medical/financial advice, real private persons), run `vg daily skip "<short Japanese reason>" --request "<request>"` and call `vg daily next` again.
- If it returns `run`, resume that run for the same request: `vg daily start "<request>" --run <run>`, then `vg status <run>` / `vg status <run>:v1` and continue from the first unfinished or failed stage (skip step 3.1).
- If it returns a `request` without `run`, use it verbatim (`vg daily start` refuses text that is not an unchecked list item).
- If it returns `{"empty": true}`, add one item yourself:
  1. `vg daily history` — the user's past list items (their interests and phrasing) and the channel's uploads with views, likes and views per day.
  2. Choose one new request that fits the channel concept in `REPO/local/preferences.yaml` (`channel`) and follows what worked: the subject areas, video types and question styles of the better-performing videos and of the user's own items. With little data (fewer than five videos with views), stay close to the user's items.
  3. It must be a new question: not a topic or angle already produced (titles in `outbox/*/publish.json`, `outbox/*/*/publish.json`, `runs/*/script.json` and `runs/*/variants/*/script.json`). Low risk only: no news or current events, no medical, health, legal or financial advice, no real private persons, nothing needing realistic depictions of real people.
  4. Write it like the user's items (one Japanese line), then `vg daily add "<request>" --reason "<which trend it follows, one line>"` and use it.

## 3. Make the video
1. `vg new "<request>" --slug <short-ascii-slug>` → RUN, then at once `vg daily start "<request>" --run RUN`.
2. Follow `.claude/skills/make-video/SKILL.md` in **daily mode** on this RUN: skip its `vg new` (step 1.1; the registered RUN is the only run for today), do step 1.2 (brief) to step 3 in RUN, then `vg variant new RUN --id v1 --note daily` (when resuming, only if `RUN/variants/v1` does not exist yet), and steps 4–8 on `RUN:v1` (`vg assets RUN --all` serves the single variant). YouTube only (`platforms: [youtube_shorts]`), the type's length (30–59 s for most types), the preferred narrator and speed from `local/preferences.yaml`. Keep every QA gate and the independent fact check; never waive a check here.

## 4. Self-review (nobody checks before the slot)
- `vg sheet RUN:v1` and look at the contact sheet, `cover_small.jpg` and the timing/audio summary.
- Every image must match its narration line: the right object and situation, no garbled letters or meaningless numbers, no realistic real person. Fix a mismatch by changing that scene's prompt or seed and rerunning from `assets` (then `vg make RUN:v1 --from timeline`).
- Re-read `publish.json`: the title and description must be accurate and match the video; sources and credits present.
- If a problem cannot be fixed in two attempts, do not publish (step 6).

## 5. Schedule
`vg daily publish RUN:v1 --confirm`. It refuses when the slot is too close, the run is not today's, QA has not passed or a video is already scheduled today; report the refusal instead of working around it. If it reports that the request was already uploaded for an earlier day (recovered after an interrupted upload), the item is done and today's slot is still open: `vg daily next` and start again from step 2 with a new RUN.

## 6. Blockers
On a temporary blocker (no idle GPU after waiting, QA or fact check keeps failing, missing tool, refused publish): `vg daily fail "<short Japanese reason>"` and stop. The item stays unchecked; the next cron attempt resumes the same run. When the item itself is the problem (cannot be sourced, unsafe, unsupported), use `vg daily skip "<reason>"` instead; the next run moves to the following item.

## 7. Report
Final message (goes to `local/logs/`): request (and whether it was added automatically), title, type, duration, voice, BGM, number of sources, QA warnings, URL and slot, or the failure reason.
