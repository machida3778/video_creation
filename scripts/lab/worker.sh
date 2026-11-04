#!/usr/bin/env bash
# One GPU worker for a shared-disk queue (lab or allocated supercomputer node).
# CUDA_VISIBLE_DEVICES=<gpu> bash scripts/lab/worker.sh <queue_dir>
# Jobs: jobs/<id>.sh; atomic mkdir claims/<id>; exit code + worker/time: done/<id>;
# output: logs/<id>.log. Remove BOTH claims/<id> and done/<id> to rerun.
# closed exits once no unclaimed jobs remain (other workers may still be running).
# WORKER_IDLE_EXIT: seconds without available jobs; 0/unset waits forever.
# WORKER_REPO: job working directory (default: this repository).
# WORKER_SKIP_GPU_CHECK=1: only for scheduler-allocated HPC GPUs.
# GPU availability is checked before every claim; running jobs are never stopped.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../site.sh"
[[ $# == 1 ]] || { echo "usage: worker.sh <queue_dir>" >&2; exit 2; }
SCRIPT_REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
REPO=$(cd -- "${WORKER_REPO:-$SCRIPT_REPO}" && pwd)
IDLE_EXIT=${WORKER_IDLE_EXIT:-0}
[[ "$IDLE_EXIT" =~ ^[0-9]+$ ]] || { echo "WORKER_IDLE_EXIT must be nonnegative integer seconds" >&2; exit 2; }
IDLE_EXIT=$((10#$IDLE_EXIT))
if [[ "${WORKER_SKIP_GPU_CHECK:-0}" == 1 ]]; then
  GPU=${CUDA_VISIBLE_DEVICES:-0}  # scheduler-allocated node: the scheduler exposes its allocated GPU without CUDA_VISIBLE_DEVICES
else
  GPU=${CUDA_VISIBLE_DEVICES:?select one GPU}
fi
[[ "$GPU" != *,* ]] || { echo "select exactly one GPU" >&2; exit 2; }
WORKER="$(hostname -s):$GPU"
export CUDA_DEVICE_ORDER=${CUDA_DEVICE_ORDER:-PCI_BUS_ID}
mkdir -p -- "$1"
Q=$(cd -- "$1" && pwd)
mkdir -p "$Q/jobs" "$Q/claims" "$Q/done" "$Q/logs"
cd -- "$REPO"
shopt -s nullglob
export LC_ALL=C
unclaimed() {
  local job id
  for job in "$Q/jobs/"*.sh; do
    id=${job##*/}; id=${id%.sh}
    if [[ ! -e "$Q/claims/$id" && ! -e "$Q/done/$id" ]]; then return 0; fi
  done
  return 1
}
idle_since=$SECONDS
waiting=0
while true; do
  ran=0
  busy_gpu=0
  for job in "$Q/jobs/"*.sh; do
    id=${job##*/}; id=${id%.sh}
    [[ ! -e "$Q/claims/$id" && ! -e "$Q/done/$id" ]] || continue
    idle_since=$SECONDS
    if [[ "${WORKER_SKIP_GPU_CHECK:-0}" != 1 ]] && ! busy=$(bash "$SCRIPT_REPO/scripts/lab/gpu_free.sh" 2>&1); then
      if [[ $waiting == 0 ]]; then echo "[worker $WORKER] GPU busy, waiting: $busy $(date -Is)"; fi
      waiting=1
      busy_gpu=1
      break
    fi
    if [[ $waiting == 1 ]]; then echo "[worker $WORKER] GPU free again $(date -Is)"; fi
    waiting=0
    mkdir "$Q/claims/$id" 2>/dev/null || continue
    echo "$WORKER $(date -Is)" > "$Q/claims/$id/by"
    echo "[worker $WORKER] start $id $(date -Is)"
    code=0
    bash "$job" > "$Q/logs/$id.log" 2>&1 || code=$?
    echo "$code $WORKER $(date -Is)" > "$Q/done/$id"
    echo "[worker $WORKER] done $id exit=$code $(date -Is)"
    idle_since=$SECONDS
    ran=1
    break  # Rescan in order, including newly added lower ids.
  done
  if [[ $busy_gpu == 1 ]]; then sleep 300; continue; fi
  if [[ $ran == 0 ]]; then
    # Recheck: a producer may have published a job during this scan.
    if unclaimed; then continue; fi
    if [[ -e "$Q/closed" ]]; then echo "[worker $WORKER] queue closed, exiting"; break; fi
    delay=60
    if (( IDLE_EXIT > 0 )); then
      remaining=$((IDLE_EXIT - (SECONDS - idle_since)))
      if (( remaining <= 0 )); then echo "[worker $WORKER] idle timeout, exiting"; break; fi
      if (( remaining < delay )); then delay=$remaining; fi
    fi
    sleep "$delay"
  fi
done
