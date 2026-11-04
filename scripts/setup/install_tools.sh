#!/usr/bin/env bash
# Install the local toolchain on a CPU server (x86_64 Linux). Idempotent; downloads are checksum-verified.
#   bash scripts/setup/install_tools.sh [ffmpeg|voicevox|python|render|all]   (default: all)
# ffmpeg   -> tools/ffmpeg/bin/{ffmpeg,ffprobe}   BtbN static GPL build (libass, x264, freetype, fribidi, harfbuzz)
# voicevox -> tools/voicevox/run                  VOICEVOX ENGINE Linux CPU x64 (start: scripts/setup/voicevox.sh)
# python   -> .venv (uv sync from pyproject.toml / uv.lock)
# render   -> render/node_modules + Chrome Headless Shell for Remotion
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
source "$ROOT/scripts/site.sh"
TOOLS=$ROOT/tools
VOICEVOX_VERSION=${VOICEVOX_VERSION:-0.26.0}
export PATH=$HOME/.local/opt/node/bin:$HOME/.local/bin:$PATH
mkdir -p "$TOOLS"

asset_digest() {  # asset_digest <owner/repo> <tag|latest> <asset name> -> sha256 hex from the GitHub API (empty if absent)
  local rel=$2; [[ $rel == latest ]] || rel="tags/$rel"
  curl -fsSL "https://api.github.com/repos/$1/releases/$rel" | python3 -c '
import json, sys
name = sys.argv[1]
for a in json.load(sys.stdin)["assets"]:
    if a["name"] == name:
        print((a.get("digest") or "").removeprefix("sha256:"))' "$3"
}

install_ffmpeg() {
  if [[ -x $TOOLS/ffmpeg/bin/ffmpeg ]]; then echo "ffmpeg: present"; return; fi
  local dl url name
  dl=$(mktemp -d "$TOOLS/.dl.XXXXXX")
  url=$(curl -fsSL https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest | python3 -c '
import json, re, sys
urls = [a["browser_download_url"] for a in json.load(sys.stdin)["assets"]]
print(sorted(u for u in urls if re.search(r"/ffmpeg-n[\d.]+-latest-linux64-gpl-[\d.]+\.tar\.xz$", u))[-1])')
  name=${url##*/}
  echo "ffmpeg: $name"
  curl -fL --retry 3 -o "$dl/$name" "$url"
  curl -fsSL -o "$dl/checksums.sha256" "${url%/*}/checksums.sha256"
  (builtin cd "$dl" && grep -E " \*?$name\$" checksums.sha256 | sha256sum -c -)
  mkdir -p "$TOOLS/ffmpeg"
  tar -C "$TOOLS/ffmpeg" -xJf "$dl/$name" --strip-components=1
  rm -rf "$dl"
  "$TOOLS/ffmpeg/bin/ffmpeg" -hide_banner -version | head -1
}

install_voicevox() {
  if [[ -x $TOOLS/voicevox/run ]]; then echo "voicevox: present ($(cat "$TOOLS/voicevox/VERSION" 2>/dev/null))"; return; fi
  local dl name sum run
  dl=$(mktemp -d "$TOOLS/.dl.XXXXXX")
  name=voicevox_engine-linux-cpu-x64-$VOICEVOX_VERSION.7z.001
  echo "voicevox: $name"
  curl -fL --retry 3 -o "$dl/$name" "https://github.com/VOICEVOX/voicevox_engine/releases/download/$VOICEVOX_VERSION/$name"
  sum=$(asset_digest VOICEVOX/voicevox_engine "$VOICEVOX_VERSION" "$name")
  if [[ -n $sum ]]; then echo "$sum  $dl/$name" | sha256sum -c -; else echo "voicevox: no published digest, size check only"; fi
  7z x -y -bd -o"$dl/x" "$dl/$name" > /dev/null
  run=$(find "$dl/x" -maxdepth 3 -type f -name run | head -1)
  [[ -n $run ]] || { echo "voicevox: 'run' not found in archive" >&2; exit 1; }
  mv "$(dirname "$run")" "$TOOLS/voicevox"
  echo "$VOICEVOX_VERSION" > "$TOOLS/voicevox/VERSION"
  chmod +x "$TOOLS/voicevox/run"
  rm -rf "$dl"
}

install_python() {
  (builtin cd "$ROOT" && uv sync --python /usr/bin/python3)
  "$ROOT/.venv/bin/python" -c 'import pydantic, yaml, budoux, httpx; print("python env ok")'
}

install_render() {
  local npm_cmd=install
  [[ -f $ROOT/render/package-lock.json ]] && npm_cmd=ci   # first run creates the lock file
  (builtin cd "$ROOT/render" && npm "$npm_cmd" --no-audit --no-fund && npx remotion browser ensure && npx tsc --noEmit)
}

case ${1:-all} in
  ffmpeg) install_ffmpeg ;;
  voicevox) install_voicevox ;;
  python) install_python ;;
  render) install_render ;;
  all) install_ffmpeg; install_voicevox; install_python; install_render ;;
  *) echo "usage: install_tools.sh [ffmpeg|voicevox|python|render|all]" >&2; exit 2 ;;
esac
