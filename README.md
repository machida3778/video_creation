# video_creation

An agentic harness that turns a one-line request into a finished vertical short video using open-weights models on your own GPUs. Agents write structured specifications; deterministic tools render and check the result. The harness prepares multiple candidates so a human can select the final version.

The initial implemented recipe is a Japanese trivia explainer. Additional recipes describe planned formats, including dialogue, data comparisons and short fiction.

## How it works

```text
Request → Brief / recipe → Research → Script / fact check
        → Speech → Storyboard → Assets → Timeline → Render → QA
        → Candidate comparison → Human selection
```

- **Validated specifications:** Python and Pydantic define the JSON contracts between stages.
- **Repeatable stages:** run state and intermediate artifacts support resuming and selective regeneration.
- **QA gates:** format, duration, loudness, black frames and caption checks block invalid packages; editorial review covers facts, pronunciation, originality and rights.
- **Licence tracking:** every asset carries its licence, provenance and required credits into the final package.
- **Variants and recombination:** compare different scripts, voices, visuals and music, then combine selected parts while preserving provenance.

The stack includes Python, Pydantic, Remotion/React, ffmpeg, VOICEVOX and diffusers. Agent orchestration uses subscription logins; the project uses no model API keys or paid model APIs.

## Getting started

Use Linux with Python 3.12+, uv, Node.js/npm, Bash, curl and 7z. Run these commands from the repository root. The installer downloads the CPU tools and installs the pinned Python and renderer dependencies.

```sh
bash scripts/setup/install_tools.sh
bash scripts/setup/voicevox.sh start
uv run vg smoke
```

`vg smoke` runs a fixed, small example through speech synthesis, rendering, QA and packaging without a GPU. It creates ignored artifacts under `runs/` and `outbox/`. Inspect the output before using it elsewhere.

For GPU generation, configure your own hosts and mount mapping:

```sh
mkdir -p local
cp config/site.example.env local/site.env
# Edit local/site.env for your environment.
```

The site file contains literal shell `KEY=VALUE` assignments, with quoted values for spaces or regexes. Environment variables override the file. Cluster scripts fail clearly when required settings are missing. Install GPU worker dependencies and model weights separately on an available GPU host; see [model requirements](docs/MODELS.md) and `workers/t2i/`. Check shared GPUs immediately before use with `scripts/lab/gpu_free.sh`.

With an agent session, use the recipe workflow in [.claude/skills/make-video/SKILL.md](.claude/skills/make-video/SKILL.md). The CLI operates on the specifications the agent writes:

```sh
uv run vg new "Explain a surprising science fact" --slug example
uv run vg --help
```

Offline tests:

```sh
.venv/bin/python -m unittest tests.test_registry tests.test_media_qa tests.test_package tests.test_variants
python3 .claude/hooks/test_check.py
```

## Repository layout

| Path | Purpose |
|---|---|
| `vg/` | CLI, schemas, deterministic stages, QA and packaging |
| `render/` | Remotion templates and components |
| `types/` | Video recipes and prompts |
| `config/` | Voice/music licence registries and example site settings |
| `workers/` | Isolated GPU worker code and environments |
| `scripts/setup/`, `scripts/lab/` | Tool installation and shared GPU helpers |
| `scripts/hpc_nqsv/`, `scripts/hpc_pbs/` | NQSV cluster and PBS cluster synchronization helpers |
| `tests/` | Tests using synthetic fixtures |
| `local/` | Ignored site settings, preferences and private notes |
| `runs/`, `outbox/`, `assets/media/` | Ignored production artifacts and licensed media |

See [architecture](docs/ARCHITECTURE.md), [models and licences](docs/MODELS.md), and [agent rules](AGENTS.md).

## Responsible use

Use only models and assets whose licences permit your intended use, including commercial use of outputs. Check the exact weights, voice terms, fonts, music and dependent models; retain required credits. Model candidates in the registry require verification before adoption.

Disclose AI-generated content when required by the platform or context. Review factual claims, permissions and sensitive depictions. QA prepares candidates for review: a human must choose the final version and authorize any publication. The harness does not publish automatically.

Generated content is not stored in this repository. Production scripts, research, images, audio and video remain in ignored local directories.

## 日本語

一行の依頼から縦型ショート動画の候補を作るエージェント用ハーネスです。LLM が JSON 仕様を作り、VOICEVOX・Remotion・ffmpeg などが音声合成、描画、品質検査を行います。生成モデルは自前 GPU 上のオープンウェイトのみを使用し、有料モデル API は使いません。

複数案の比較や、台本・声・映像・BGM の組み替えに対応します。最終版は人が選び、公開を判断します。素材のライセンスと必要なクレジットを記録し、生成物や個人設定は Git 管理外に保存します。導入手順は上記「Getting started」を参照してください。
