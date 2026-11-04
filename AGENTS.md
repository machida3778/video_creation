# video_creation: working guide (Claude and Codex)

Site-specific rules: local/AGENTS.local.md (not in the repository).

Design: `docs/ARCHITECTURE.md`. Video recipes: `types/*/type.yaml`.
Model licences: `docs/MODELS.md`. Voice registry: `config/voices.yaml`.
Read `local/HANDOFF.md` when present for private continuation notes.

## Hard rules

- **No API keys or paid model APIs.** Use the agents' subscription logins for orchestration. Strip model API credentials from subprocess environments.
- **Open-weights models on our own GPUs only, with licences allowing commercial use of outputs.** Record the model and licence URL in `docs/MODELS.md` before use. Non-commercial or research-only weights are excluded.
- **Every asset has a licence record** in the run's `assets/manifest.json`: voice, image, clip, music, font and character art. Include required attribution in the video description.
- **Produced content never goes into Git or GitHub.** Scripts, storyboards, research notes, generated media and run topics stay in ignored `runs/`, `outbox/` and `assets/media/`. Use small synthetic fixtures in tests. Check that these directories and media files are untracked before any push.
- **Shared GPUs must be checked free immediately before use.** Run `bash scripts/lab/gpu_free.sh <gpu>` in the same command before starting work and choose another GPU if busy. Never stop or interfere with another user's jobs.
- A human selects the final version and decides whether to publish. The harness prepares candidates and never publishes automatically.

## Division of labour

- Claude: design decisions, interpreting results, user decisions, reviewing diffs and commits.
- Codex: mechanical edits, script ports, boilerplate, broad code searches and research. Claude reviews Codex's diff before it is committed.
- Code executed on GPUs or HPC clusters, and code deciding QA pass/fail, receives review by the other agent before a run is launched.
- Codex follows this guide independently of `.claude/` hooks, does not open generated or licensed material under `runs/`, `outbox/` or `assets/`, and does not commit unless asked.

## Conventions

- One video uses `runs/<YYYYMMDD-HHMM>-<slug>/` with `state.json` and intermediate artifacts. Stages are idempotent and resumable. QA-approved candidates are copied to `outbox/` for human selection.
- Stage outputs are JSON validated against `vg/schemas.py`. LLM steps write specifications; deterministic `vg` tools render and check them.
- Python uses a project-local uv environment: `uv run vg ...`. GPU environments and architecture-specific lock files belong under `workers/<tool>/`.
- Site configuration belongs in ignored `local/site.env`; use `config/site.example.env` as a template. Keep personal preferences in `local/preferences.yaml`.
- Keep private continuation notes in `local/HANDOFF.md` under 30 KB, with results and one next task.
