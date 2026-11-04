#!/usr/bin/env bash
# Check that one lab GPU is free before starting a job on it (shared lab GPU servers).
#   bash scripts/lab/gpu_free.sh [gpu]          # gpu defaults to $CUDA_VISIBLE_DEVICES; index (PCI bus order) or UUID
# Exit 0: no compute process on the GPU and memory.used <= GPU_FREE_MAX_MB (default 1024).
# Exit 1: busy (owners and PIDs are printed to stderr). Exit 2: no such GPU or nvidia-smi failed.
# Only call it before starting work: a running job is never stopped when someone else starts later.
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../site.sh"
GPU=${1:-${CUDA_VISIBLE_DEVICES:-}}
[ -n "$GPU" ] && [[ "$GPU" != *,* ]] || { echo "gpu_free: give one GPU" >&2; exit 2; }
MAX_MB=${GPU_FREE_MAX_MB:-1024}
info=$(nvidia-smi -i "$GPU" --query-gpu=uuid,memory.used --format=csv,noheader,nounits 2>&1) \
  || { echo "gpu_free: nvidia-smi -i $GPU failed: $info" >&2; exit 2; }
uuid=$(echo "${info%%,*}" | xargs); used=$(echo "${info##*,}" | xargs)
busy=0
while IFS=, read -r u pid mem; do
  [ "$(echo "$u" | xargs)" = "$uuid" ] || continue
  pid=$(echo "$pid" | xargs)
  echo "gpu_free: $(hostname -s) GPU $GPU used by pid $pid ($(ps -o user= -p "$pid" 2>/dev/null || echo ?), $(echo "$mem" | xargs))" >&2
  busy=1
done < <(nvidia-smi --query-compute-apps=gpu_uuid,pid,used_memory --format=csv,noheader 2>/dev/null)
if [ "$used" -gt "$MAX_MB" ]; then
  echo "gpu_free: $(hostname -s) GPU $GPU has ${used} MiB in use (> $MAX_MB)" >&2
  busy=1
fi
exit $busy
