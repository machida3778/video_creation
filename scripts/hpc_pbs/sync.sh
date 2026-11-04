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
# code sends committed HEAD's branch; push/pull transfer repo-relative run data.
usage() { echo "usage: sync.sh code | push <dir> | pull <dir>" >&2; exit 2; }
[[ $# -ge 1 ]] || usage
MODE=$1
case "$MODE" in
  code)
    [[ $# == 1 ]] || usage
    BRANCH=$(git -C "$LOCAL_REPO" symbolic-ref --short HEAD)
    git -C "$LOCAL_REPO" push hpc_pbs "$BRANCH"
    printf -v remote_cmd 'bash -s -- %q %q %q %q' "$REMOTE_REPO" "$GIT_BARE" "$BRANCH" "$GROUP"
    ssh "$SSH_TARGET" "$remote_cmd" <<'REMOTE'
set -euo pipefail
REPO=$1; BARE=$2; BRANCH=$3; GROUP=$4
chgrp -R "$GROUP" "$BARE"
find "$BARE" -type d -exec chmod g+s {} +
git -C "$REPO" fetch -q origin
if git -C "$REPO" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git -C "$REPO" checkout -q "$BRANCH"
else
  git -C "$REPO" checkout -q -b "$BRANCH" "origin/$BRANCH"
fi
git -C "$REPO" pull -q --ff-only origin "$BRANCH"
git -C "$REPO" log --oneline -1
# Media trees are fixed on data push, not traversed on every code sync.
find "$REPO" \( -path "$REPO/runs" -o -path "$REPO/outbox" -o -path "$REPO/assets" \) -prune -o ! -group "$GROUP" -exec chgrp "$GROUP" {} +
find "$REPO" \( -path "$REPO/runs" -o -path "$REPO/outbox" -o -path "$REPO/assets" \) -prune -o -type d ! -perm -g+s -exec chmod g+s {} +
REMOTE
    ;;
  push|pull)
    [[ $# == 2 ]] || usage
    DIR=${2%/}
    # Reject absolute paths, parent traversal and shell metacharacters before ssh/rsync.
    [[ -n "$DIR" && "$DIR" != /* && "$DIR" =~ ^[a-zA-Z0-9_./-]+$ ]] || usage
    case "/$DIR/" in */../*) usage ;; esac
    if [[ "$MODE" == push ]]; then
      rsync -rlt --mkpath --exclude 'debug/' "$LOCAL_REPO/$DIR/" "$SSH_TARGET:$REMOTE_REPO/$DIR/"
      printf -v remote_cmd 'bash -s -- %q %q' "$REMOTE_REPO/$DIR" "$GROUP"
      ssh "$SSH_TARGET" "$remote_cmd" <<'REMOTE'
set -euo pipefail
chgrp -R "$2" "$1"
find "$1" -type d -exec chmod g+s {} +
REMOTE
    else
      rsync -rlt --mkpath "$SSH_TARGET:$REMOTE_REPO/$DIR/" "$LOCAL_REPO/$DIR/"
    fi
    ;;
  *) usage ;;
esac
