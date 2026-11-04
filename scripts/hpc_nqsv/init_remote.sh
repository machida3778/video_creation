#!/usr/bin/env bash
# NQSV cluster helper. Configure local/site.env before use.
set -euo pipefail
LOCAL_REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$LOCAL_REPO/scripts/site.sh"
site_require NQSV_HOST NQSV_USER NQSV_GROUP NQSV_WORK_DIR NQSV_BARE_REPO
HOST=$NQSV_HOST
GROUP=$NQSV_GROUP
REMOTE_USER=$NQSV_USER
REMOTE_REPO=$NQSV_WORK_DIR
GIT_BARE=$NQSV_BARE_REPO
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
echo "[2/4] Create or verify remote working copy: $REPO"
if [[ ! -e "$REPO" ]]; then git clone "$BARE" "$REPO"; fi
[[ $(git -C "$REPO" rev-parse --show-toplevel) == "$(cd -- "$REPO" && pwd -P)" ]] || exit 1
[[ $(git -C "$REPO" remote get-url origin) == "$BARE" ]] || {
  echo "Working copy origin differs; refusing to replace it" >&2; exit 1;
}
REMOTE
echo "[3/4] Add or verify local git remote"
URL="$SSH_TARGET:$GIT_BARE"
if git -C "$LOCAL_REPO" remote get-url hpc_nqsv >/dev/null 2>&1; then
  [[ $(git -C "$LOCAL_REPO" remote get-url hpc_nqsv) == "$URL" ]] || {
    echo "Remote hpc_nqsv differs; refusing to replace it" >&2; exit 1;
  }
else
  git -C "$LOCAL_REPO" remote add hpc_nqsv "$URL"
fi
echo "[4/4] Synchronize committed code"
bash "$LOCAL_REPO/scripts/hpc_nqsv/sync.sh" code
