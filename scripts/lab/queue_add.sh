#!/usr/bin/env bash
# Add one job to a scripts/lab/worker.sh queue: scripts/lab/queue_add.sh <queue_dir> <id> <command...>
# The command runs from the repo root on the worker's GPU. Ids sort the run order (prefix with a number).
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../site.sh"
Q=$1; ID=$2; shift 2
mkdir -p "$Q/jobs"
[ -e "$Q/jobs/$ID.sh" ] && { echo "job $ID exists" >&2; exit 1; }
printf '#!/usr/bin/env bash\nset -euo pipefail\n%s\n' "$*" > "$Q/jobs/$ID.sh.tmp"
mv "$Q/jobs/$ID.sh.tmp" "$Q/jobs/$ID.sh"
echo "queued $ID"
