#!/usr/bin/env bash
# Local VOICEVOX ENGINE (CPU build in tools/voicevox) on 127.0.0.1 only.
#   bash scripts/setup/voicevox.sh start|stop|status
# VOICEVOX_PORT (default 50021), VOICEVOX_THREADS (default 8; the CPU server is shared, keep it modest).
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/site.sh"
DIR=$ROOT/tools/voicevox
PORT=${VOICEVOX_PORT:-50021}
THREADS=${VOICEVOX_THREADS:-8}
PIDFILE=$ROOT/tools/voicevox.pid
LOG=$ROOT/tools/voicevox.log

up() { curl -fsS --max-time 2 "http://127.0.0.1:$PORT/version" 2>/dev/null; }

case ${1:-status} in
  start)
    if v=$(up); then echo "voicevox: already running on $PORT (version $v)"; exit 0; fi
    [[ -x $DIR/run ]] || { echo "voicevox: not installed (bash scripts/setup/install_tools.sh voicevox)" >&2; exit 1; }
    # API keys are never needed by the engine; keep them out of its environment.
    setsid nohup env -u OPENAI_API_KEY -u GEMINI_API_KEY -u ARK_API_KEY \
      "$DIR/run" --host 127.0.0.1 --port "$PORT" --cpu_num_threads "$THREADS" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    for _ in $(seq 120); do
      if v=$(up); then echo "voicevox: started on $PORT (version $v, pid $(cat "$PIDFILE"))"; exit 0; fi
      sleep 1
    done
    echo "voicevox: did not answer within 120 s; see $LOG" >&2; exit 1 ;;
  stop)
    if [[ -f $PIDFILE ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then echo "voicevox: stopped"; else echo "voicevox: not running"; fi
    rm -f "$PIDFILE" ;;
  status)
    if v=$(up); then echo "voicevox: running on $PORT (version $v)"; else echo "voicevox: not running"; exit 1; fi ;;
  *) echo "usage: voicevox.sh start|stop|status" >&2; exit 2 ;;
esac
