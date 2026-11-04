#!/usr/bin/env bash
# Process a snapshot sequentially, keeping failed requests for the next attempt.
set -euo pipefail

usage() { echo 'usage: scripts/run_queue.sh (reads local/requests.txt; RUN_REQUEST_DRY=1 previews without consuming)'; }
if [[ ${1:-} == --help || ${1:-} == -h ]]; then usage; exit 0; fi
if [[ $# -ne 0 ]]; then usage >&2; exit 2; fi
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$REPO"
queue="$REPO/local/requests.txt"
[[ -f $queue ]] || { echo "missing queue: $queue" >&2; exit 1; }
snapshot=$(mktemp)
trap 'rm -f -- "$snapshot"' EXIT
cp -- "$queue" "$snapshot"
while IFS= read -r line || [[ -n $line ]]; do
  [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
  status=0
  bash "$REPO/scripts/run_request.sh" "$line" < /dev/null || status=$?
  if [[ ${RUN_REQUEST_DRY:-0} == 1 ]]; then
    [[ $status -eq 0 ]] || exit "$status"
    continue
  fi
  printf '%s %s %s\n' "$(date -Iseconds)" "$status" "$line" >> "$REPO/local/requests.done"
  [[ $status -eq 0 ]] || exit "$status"
  # Remove just this successful occurrence, retaining comments, duplicates and
  # requests appended since the snapshot. Replace atomically in the same folder.
  "$REPO/.venv/bin/python" - "$queue" "$line" <<'PY'
import os
from pathlib import Path
import sys
import tempfile

queue, request = Path(sys.argv[1]), sys.argv[2]
lines = queue.read_text(encoding="utf-8").splitlines(keepends=True)
for i, line in enumerate(lines):
    if line.rstrip("\r\n") == request:
        del lines[i]
        break
else:
    raise SystemExit("successful request no longer present in queue")
temporary = None
try:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=queue.parent,
                                     prefix=".requests.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.writelines(lines)
    temporary.chmod(queue.stat().st_mode & 0o777)
    os.replace(temporary, queue)
finally:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
PY
done < "$snapshot"
