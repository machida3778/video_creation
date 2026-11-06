# モデル台帳（v1, 2026-11-04）

この台帳はモデル選定の記録。**状態が「候補」のものは、ダウンロード時に重みに同梱された LICENSE を確認し、自分たちの GPU で実測してから「採用」にする。** 各実行の素材台帳にもモデル名とライセンス URL を記録する。

**出力を商用利用でき、配信地域の制限がないもの**だけを使う。Apache-2.0 / MIT を基本とし、条件付きのライセンスは理由を書いて例外扱いにする。タイプ定義（`types/*/type.yaml` の `models`）の能力キーと対応させる。

## 採用候補

| 能力キー | 第一候補 | 代替 | ライセンス | 動かす場所 | 状態 |
|---|---|---|---|---|---|
| `tts` | VOICEVOX ENGINE 0.26.0（キャラクター） | — | エンジン LGPL-3.0。声はキャラクターごとの規約（`config/voices.yaml`） | CPU server | 候補 |
| `tts` | Irodori-TTS v4.1 Small（ナレーター。文章で声を設計でき、声の複製も可） | Qwen3-TTS 1.7B（VoiceDesign → Base） | MIT / Apache-2.0 | lab GPU servers | 候補 |
| `tts_en` | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)（英語、af_heart / am_michael） | — | Apache-2.0 weights | lab GPU servers（GPU または CPU） | 実装済み・実機未検証 |
| `t2i` | Qwen-Image-2512（日本語の文字の描画に対応と記載） | Z-Image Turbo（16GB、高速）、FLUX.2 Klein 4B（13GB） | Apache-2.0 | HPC cluster (NQSV) / lab GPU servers (48GB) | 候補 |
| `image_edit` | Qwen-Image-Edit-2511（複数の参照画像でキャラクターを一貫させる） | FLUX.2 Klein 4B | Apache-2.0 | HPC cluster (NQSV) | 候補 |
| `t2v` / `i2v` | Wan2.2 A14B ＋ LightX2V の4ステップ蒸留 LoRA | Wan2.2 TI2V-5B（24GB、量産向け） | Apache-2.0 | HPC cluster (NQSV)（H100 80GB） | 候補 |
| `t2v`（音声つき） | Kandinsky 6 Pro-distill（5秒、音声44kHzつき。H100で480p 88秒） | Kandinsky 6 Lite-distill（31秒） | MIT | HPC cluster (NQSV) | 候補 |
| `s2v`（話す映像） | InfiniteTalk | Wan2.2-S2V、LongCat-Video-Avatar 1.5（MIT）、Kandinsky S2V（MIT） | Apache-2.0 | HPC cluster (NQSV) | 候補 |
| 長尺・一貫性・制御 | LongCat-Video（続きを生成）、VACE（参照・マスク・ポーズ） | Wan2.2-Animate-2 | MIT / Apache-2.0 | HPC cluster (NQSV) | 候補 |
| `upscale` | FlashVSR+、RIFE（中割り） | SeedVR2、Real-ESRGAN | Apache-2.0 / MIT / BSD-3 | HPC cluster (NQSV) | 候補 |
| `music` | ACE-Step 1.5 XL Turbo | YuE（日本語の歌） | MIT / Apache-2.0 | lab GPU servers（24GB以上） | 候補 |
| `foley`（効果音） | MOSS-SoundEffect v2（文章から効果音） | Kandinsky 6 の映像→音声 | Apache-2.0 / MIT | lab GPU servers | 候補 |
| `asr`（字幕の時刻合わせ） | Qwen3-ASR ＋ Qwen3-ForcedAligner | Whisper large-v3-turbo、ReazonSpeech k2（CPU） | Apache-2.0 / MIT | lab GPU servers | 候補 |
| `asr`（読みの確認） | kana-whisper（カタカナで書き起こす） | — | MIT | lab GPU servers | 候補 |
| `vlm`（画面の確認） | Qwen3-VL-8B | Qwen3.8-27B | Apache-2.0 | lab GPU servers / HPC cluster (PBS) | 候補 |

補足:
- VOICEVOX の音声合成では、モーラ（拍）ごとの長さが返るので字幕の時刻が作れる。Irodori・AivisSpeech は返さないので、ForcedAligner で時刻を合わせる。
- 生成画像の中には日本語の文字を描かせない。字幕・テロップは合成の段階でフォントを使って入れる。
- aarch64 向けは wheel が揃わないことがあるため、各 GPU アーキテクチャで動作確認する。

## 条件付き（原則使わない。使うときは理由を書く）

| モデル | 条件 |
|---|---|
| LTX-2 / 2.3 / 2.5（音声つき動画、4K） | 関連会社を含む年間売上が1,000万ドル未満なら無料で商用可。利用規約（AUP）と来歴表示の義務あり |
| FLUX 3 Image Klein | 年間売上500万ドル未満なら出力の商用可（Kommunity ライセンス） |
| Stability AI（SD3.5、Stable Audio） | 売上100万ドル前後の上限、商用登録、クレジット表示 |
| LatentSync 1.6 | 既定の顔検出が InsightFace の非商用モデルを使う。差し替えが必要 |
| FramePack、Ovi | 重みや依存モデルのライセンスが未確認 |

## 使わない

| モデル | 理由 |
|---|---|
| HunyuanVideo-1.5 / Avatar / Image 3.0 / Foley（Tencent） | EU・英国・韓国での出力の配布・表示を禁止。YouTube は世界に配信されるため使えない |
| MiniMax-H3 / Music3 | 米国などの地域制限、UI へのブランド表示の義務 |
| FLUX.1 dev / FLUX.2 dev / FLUX.2 Klein 9B | 出力の商用利用は可だが、本番での推論利用に制限がある |
| Qwen-Image-2.1 | 研究用ライセンス |
| YuE2、MusicGen、MMAudio、GIMM-VFI、ThinkSound | 非商用・研究用 |
| Sarashina2.2-TTS、F5-TTS の公式重み、Fish Speech S1-mini / S2-Pro | 非商用 |

## Implemented backends

- Text-to-image: [Z-Image Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo), Apache-2.0; see `vg/assets.py` and `workers/t2i/`.
- Speech: [VOICEVOX ENGINE](https://github.com/VOICEVOX/voicevox_engine), LGPL-3.0; individual voice terms and attribution URLs are recorded in `config/voices.yaml`.
- English speech: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), Apache-2.0 weights and `kokoro` package; Misaki English G2P. `espeak-ng` is GPL-3.0 and handles out-of-vocabulary words: the host executable when present, otherwise the library bundled in the `espeakng_loader` wheel (private use, not redistributed). See `workers/tts_kokoro/README.md`; inference verified on a lab GPU 2026-11-06.

The candidate table is a planning inventory, not a list of validated backends. Check the exact weights and all dependent models before use.
