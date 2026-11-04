#!/usr/bin/env bash
# Install Irodori-TTS (narrator voices; MIT, https://github.com/Aratako/Irodori-TTS) into tools/irodori
# (gitignored) with its CUDA 12.8 environment, pinned to a reviewed commit.
#   bash scripts/setup/install_irodori.sh
# Weights used at run time (downloaded into the Hugging Face cache on first use):
#   Aratako/Irodori-TTS-v4.1-Small (MIT) and the codec Aratako/Semantic-DACVAE-Japanese-32dim (MIT,
#   derived from facebook/dacvae-watermarked, Apache-2.0).
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
DIR=$ROOT/tools/irodori
COMMIT=${IRODORI_COMMIT:-7ed4374}
export PATH=$HOME/.local/bin:$PATH

[[ -d $DIR/.git ]] || git clone -q https://github.com/Aratako/Irodori-TTS "$DIR"
if ! git -C "$DIR" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
  git -C "$DIR" fetch -q --unshallow origin 2>/dev/null || git -C "$DIR" fetch -q origin
fi
git -C "$DIR" checkout -q "$COMMIT"
echo "irodori: $(git -C "$DIR" log --oneline -1)"
(builtin cd "$DIR" && uv sync --extra cu128 --python /usr/bin/python3)
"$DIR/.venv/bin/python" -c 'import irodori_tts, torch; print("irodori ok, torch", torch.__version__)'
# Weights for offline GPU jobs (shared Hugging Face cache).
"$DIR/.venv/bin/python" -c '
from huggingface_hub import snapshot_download
for repo in ("Aratako/Irodori-TTS-v4.1-Small", "Aratako/Semantic-DACVAE-Japanese-32dim"):
    print(repo, snapshot_download(repo))'
