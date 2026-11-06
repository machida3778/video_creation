# English dub worker

Separate environment; nothing is installed in the harness environment. Kokoro-82M
weights and voices: [Apache-2.0](https://huggingface.co/hexgrad/Kokoro-82M).
Misaki uses the MIT-licensed spaCy `en_core_web_sm` tagger. Unknown English words
use the external GPL-3.0 `espeak-ng` executable; the adapter replaces Misaki's
library fallback before importing Kokoro. Only US English is configured.

Setup commands to run **on a chosen lab GPU host**, after checking reachability,
`hostname` and `nvidia-smi` and finding an idle GPU. None was run during implementation.
Use the site's mapped absolute checkout path if it differs on that host.
Do not replace the Japanese/PyTorch environments.

```sh
hostname
nvidia-smi
bash $REPO/scripts/lab/gpu_free.sh 0
uv sync --project $REPO/workers/tts_kokoro --python 3.12
uv pip install --python $REPO/workers/tts_kokoro/.venv/bin/python 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
command -v espeak-ng
```

If `espeak-ng` is absent (true on all lab hosts as of 2026-11-06), Misaki's own
fallback loads the GPL-3.0 library bundled in the `espeakng_loader` wheel. It is
used privately and not redistributed; synthesized audio is not covered by the
GPL. On that same host, explicitly prepare the shared HF cache once (a model download, no inference):

```sh
$REPO/workers/tts_kokoro/.venv/bin/python -c 'from huggingface_hub import snapshot_download; snapshot_download("hexgrad/Kokoro-82M", allow_patterns=["config.json", "kokoro-v1_0.pth", "voices/af_heart.pt", "voices/am_michael.pt", "LICENSE*"])'
```

Inspect the downloaded LICENSE before production use. Setup commands assume the
host's default Torch wheel supports its CUDA driver; check that on the selected
host before inference. Runtime is offline and fails if weights, voices, the spaCy
model or espeak-ng are missing. GPU execution uses the existing host discovery,
shared lock, credential scrubbing and immediate `gpu_free.sh` check. `--cpu`
still selects and locks a free GPU on a lab GPU host; it never runs inference on
the login server.

Once the other agent has reviewed the worker and QA changes, on the file server:

```sh
uv run --project $REPO vg en RUN:v1
uv run --project $REPO vg en RUN
uv run --project $REPO vg en RUN --cpu
uv run --project $REPO vg qa RUN:v1
uv run --project $REPO vg package RUN:v1
```

`RUN` batches all variants containing `en.json` in one worker invocation. Japanese
`final.mp4`, `timings.json` and the rendered `timeline.json` must already exist.
The shared mixer in `vg/mix.py` replaces voice clips, retaining the Japanese
accompaniment, gains, fades and ducking. Voice levels match the Japanese source;
the shared two-pass loudnorm targets the final Japanese integrated loudness.
`en/timings.json` reuses the Timings schema for fitted voice durations; captions
always use Japanese timings. Raw TTS WAVs are keyed by text/voice, so edits only
resynthesize affected lines. `en/build.json` invalidates fitting on input edits.
Descriptions must preserve full source and credit lines; language, adaptation,
facts and the title hook still require editorial review.

Outputs: `final_en.srt` and `dub_en.m4a` (AAC 48 kHz). The original video and
Japanese audio are unchanged by `vg en`. Package adds both files, `en.json` and
`ENGLISH.txt`. The uploader inserts English metadata and captions on the same
video, keeping both default languages `ja`. For the dub:
**upload manually in YouTube Studio > Subtitles > Add language > Audio**.
There is no Studio automation.

Quota checked 2026-11-06: one [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
costs 1 unit in the separate Video Uploads bucket; one
[thumbnails.set](https://developers.google.com/youtube/v3/docs/thumbnails/set)
costs approximately 50 general units; each
[captions.insert](https://developers.google.com/youtube/v3/docs/captions/insert)
costs 400 general units. One JA+EN upload makes four method calls: 1 upload unit
plus about 850 general units (JA only: three calls, 1 +450). Localizations are
part of the video insert. Retries, OAuth and daily-runner discovery/analytics
calls are outside these per-upload totals.
