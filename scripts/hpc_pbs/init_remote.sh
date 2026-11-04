#!/usr/bin/env bash
# PBS cluster helper. Configure local/site.env before use.
set -euo pipefail
LOCAL_REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$LOCAL_REPO/scripts/site.sh"
site_require PBS_HOST PBS_USER PBS_GROUP PBS_WORK_DIR PBS_BARE_REPO
HOST=$PBS_HOST
GROUP=$PBS_GROUP
REMOTE_USER=$PBS_USER
REMOTE_REPO=$PBS_WORK_DIR
GIT_BARE=$PBS_BARE_REPO
SSH_TARGET="$REMOTE_USER@$HOST"
# One-time bootstrap AFTER the first local commit. Safe to repeat; never resets code.
[[ $# == 0 ]] || { echo "usage: init_remote.sh" >&2; exit 2; }
git -C "$LOCAL_REPO" rev-parse --verify HEAD >/dev/null
git -C "$LOCAL_REPO" symbolic-ref --short HEAD >/dev/null
echo "[1/4] Create or verify remote bare repository: $HOST:$GIT_BARE"
printf -v remote_cmd 'bash -s -- %q %q %q' "$REMOTE_REPO" "$GIT_BARE" "$GROUP"
ssh "$SSH_TARGET" "$remote_cmd" <<'REMOTE'
set -euo pipefail
REPO=$1; BARE=$2; GROUP=$3
mkdir -p -- "$(dirname -- "$BARE")"
if [[ ! -e "$BARE" ]]; then git init --bare "$BARE"; fi
[[ $(git -C "$BARE" rev-parse --is-bare-repository) == true ]] || exit 1
chgrp "$GROUP" "$(dirname -- "$BARE")"
chmod g+s "$(dirname -- "$BARE")"
chgrp -R "$GROUP" "$BARE"
find "$BARE" -type d -exec chmod g+s {} +
echo "[2/4] Create or verify remote working copy: $REPO"
if [[ ! -e "$REPO" ]]; then git clone "$BARE" "$REPO"; fi
[[ $(git -C "$REPO" rev-parse --show-toplevel) == "$(cd -- "$REPO" && pwd -P)" ]] || exit 1
[[ $(git -C "$REPO" remote get-url origin) == "$BARE" ]] || {
  echo "Working copy origin differs; refusing to replace it" >&2; exit 1;
}
chgrp -R "$GROUP" "$REPO"
find "$REPO" -type d -exec chmod g+s {} +
REMOTE
echo "[3/4] Add or verify local git remote"
URL="$SSH_TARGET:$GIT_BARE"
if git -C "$LOCAL_REPO" remote get-url hpc_pbs >/dev/null 2>&1; then
  [[ $(git -C "$LOCAL_REPO" remote get-url hpc_pbs) == "$URL" ]] || {
    echo "Remote hpc_pbs differs; refusing to replace it" >&2; exit 1;
  }
else
  git -C "$LOCAL_REPO" remote add hpc_pbs "$URL"
fi
echo "[4/4] Synchronize committed code"
bash "$LOCAL_REPO/scripts/hpc_pbs/sync.sh" code
