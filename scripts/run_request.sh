#!/usr/bin/env bash
# Subscription-only, unattended /make-video entry point. Dry runs have no side effects.
set -euo pipefail

usage() { echo 'usage: scripts/run_request.sh "<request>" [--slug s] (RUN_REQUEST_DRY=1 prints the command)'; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
if [[ $# -ne 1 && $# -ne 3 ]]; then usage >&2; exit 2; fi
request=$1
slug=video
if [[ $# -eq 3 ]]; then
  [[ $2 == --slug ]] || { usage >&2; exit 2; }
  slug=$3
fi
if [[ -z ${request//[[:space:]]/} || ! $slug =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$ ]]; then
  usage >&2; exit 2
fi
# Remove inherited credentials before any subprocess, and again after site settings.
# OAuth subscription login stays available through Claude's normal account storage.
scrub=(-u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY -u ARK_API_KEY
       -u ANTHROPIC_AUTH_TOKEN -u AWS_BEARER_TOKEN_BEDROCK)
scrub_keys() {
  local key
  for key in $(compgen -e); do
    case ${key^^} in
      *API_KEY*|*API_TOKEN*|ANTHROPIC_AUTH_TOKEN|AWS_BEARER_TOKEN_BEDROCK)
        scrub+=(-u "$key")
        unset "$key"
        ;;
    esac
  done
}
scrub_keys
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$REPO"
if [[ ${RUN_REQUEST_DRY:-0} != 1 && -f scripts/site.sh ]]; then source scripts/site.sh; fi
scrub_keys
# Include the local Node installation while preserving caller-selected tools.
export PATH="$PATH:$HOME/.local/opt/node/bin:$HOME/.local/bin"

settings_text=$("$REPO/.venv/bin/python" - "$REPO" <<'PY'
from pathlib import Path
import sys
import yaml

repo = sys.argv[1]
config = yaml.safe_load((Path(repo) / "config/headless.yaml").read_text(encoding="utf-8"))
minutes = config.get("max_minutes", 180)
model = config.get("model") or ""
tools = config.get("allowed_tools")
if type(minutes) is not int or minutes <= 0:
    raise SystemExit("headless.max_minutes must be a positive integer")
if not isinstance(model, str) or "\n" in model or "\r" in model:
    raise SystemExit("headless.model must be a single-line string")
if not isinstance(tools, list) or not tools or any(
    not isinstance(tool, str) or not tool or "\n" in tool or "\r" in tool for tool in tools
):
    raise SystemExit("headless.allowed_tools must be a nonempty list of single-line strings")
print(minutes)
print(model)
for tool in tools:
    print(tool.replace("$REPO", repo))
PY
)
mapfile -t settings <<< "$settings_text"
command=(timeout "${settings[0]}m" claude -p "/make-video $request"
         --permission-mode acceptEdits --allowedTools "${settings[@]:2}" --output-format text)
if [[ -n ${settings[1]} ]]; then command+=(--model "${settings[1]}"); fi

if [[ ${RUN_REQUEST_DRY:-0} == 1 ]]; then
  # Use the same scrubbed invocation as production; print arguments without eval.
  printf '%q ' env "${scrub[@]}" "${command[@]}"
  printf '\n'
  exit 0
fi

mkdir -p local/logs
stamp=$(date +%Y%m%d-%H%M%S)
log="$REPO/local/logs/$stamp-$slug-$$.log"
finish() {
  local status=$?
  trap - EXIT
  printf 'log: %s\n' "$log"
  "$REPO/.venv/bin/python" - "$REPO" <<'PY'
from pathlib import Path
import sys
paths = list((Path(sys.argv[1]) / "outbox").glob("*/README.md"))
print("newest outbox README: " + (str(max(paths, key=lambda p: p.stat().st_mtime_ns)) if paths else "(none)"))
PY
  exit "$status"
}
trap finish EXIT
{
  env "${scrub[@]}" bash scripts/setup/voicevox.sh start
  command -v node >/dev/null || { echo 'node is missing from PATH' >&2; exit 127; }
  env "${scrub[@]}" "${command[@]}"
} 2>&1 | tee "$log"
