#!/usr/bin/env bash
# Cron entry point for the daily autopost (vg/daily.py, .claude/skills/daily-post/SKILL.md).
# Cheap when nothing is due: it exits before starting Claude unless today's slot still needs a video.
set -euo pipefail
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$REPO"
export PATH="$PATH:$HOME/.local/opt/node/bin:$HOME/.local/bin"
mkdir -p local/daily local/logs
# One daily run at a time; the lock is held (and inherited) until this script exits.
exec 9>>local/daily/run.lock
if ! flock -n 9; then echo "$(date -Is) daily: another run is active"; exit 0; fi
status=0
.venv/bin/vg daily status --check >/dev/null || status=$?
case $status in
  0) ;;
  10) echo "$(date -Is) daily: autopost disabled"; exit 0 ;;
  11) echo "$(date -Is) daily: today's video is already scheduled"; exit 0 ;;
  12) echo "$(date -Is) daily: not enough time before today's slot"; exit 0 ;;
  *) echo "$(date -Is) daily: status check failed ($status)" >&2; exit "$status" ;;
esac
# Stop Claude in time: scheduling is refused later than min_lead_minutes before the slot.
# Three minutes are kept for the VOICEVOX start before Claude's own timeout begins.
minutes=$(.venv/bin/vg daily status | .venv/bin/python -c 'import json, sys; print(max(1, json.load(sys.stdin)["minutes_left"] - 3))')
echo "$(date -Is) daily: starting (up to $minutes min)"
# 9>&-: daemons started by the run (VOICEVOX) must not inherit, and so keep holding, the lock.
bash scripts/run_request.sh --daily --max-minutes "$minutes" 9>&-
