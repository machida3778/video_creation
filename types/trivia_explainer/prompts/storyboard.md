# 根拠つき雑学: 構成（絵コンテ）の書き方

`timings.json` で行ごとの時間が決まってから書く。シーンは台本の行を順番に、重複なく全部カバーする。

## シーンの割り方

- **C01 フック**: 1行目だけ。0秒目から印象の強い画像（`ken_burns_image`、ズームは速めに `[1.0, 1.2]`）に、`overlays` で問いの `title_card`（`background: false`）を重ねる。字幕と重複するので `captions: false`。
- **本編**: 1〜2 行ごとに 1 シーン（1 シーン 2〜4 秒）。**2〜3 秒ごとに画面が変わる**ようにする（画像・構図・ズーム方向を変える）。同じ見た目が 4 秒以上続かないようにする。
  - 画像が使えるとき: `ken_burns_image`（`visual.kind: t2i`、`visual.prompt` は英語で具体的に。写実的な実在の人物・ロゴ・文字を描かせない）。`zoom` と `pan` をシーンごとに変える。
  - 画像がないとき: `background`（シーンごとに色を変える）。
  - 出典を示す行があるシーンには `overlays` に `source_card`（「出典: 〇〇」）を付ける。
- **締め**: 最後の 1〜2 行。`title_card` に持ち帰る一言。`captions: false`。

## 決まり

- 1 シーンの props は renderer の部品の props だけにする（`render/src/components/registry.tsx`）。
- 生成画像を使う場合は、資産の記録（モデル、プロンプト、シード、ライセンス）を `assets/manifest.json` に残す。
- 写実的な生成映像を使うときは、`publish.json` の `synthetic_media` を true にする前提で `params.realistic: true` を付ける。

## BGM

- `storyboard.music.track` に `config/bgm.yaml` の曲 id を入れる（`vg bgm suggest RUN` の候補から、タイプと口調に合うもの）。
- 解説・ランキング系は落ち着いたポップ、面白系は軽快な曲。歌もの（ボーカル入り）はナレーションとぶつかるので、ナレーション系では避ける。
- バリエーションごとに別の曲にする。音量は `gain_db` の既定（-20 dB、発話中はさらに下げる）のまま。

## 効果音

- `vg sfx build` で自作ライブラリを準備する。シーンの `props.sfx: whoosh`、または `props.sfx: [{id: ding, at: 0.5}]`（`at` はシーン開始からの秒数）で指定する。
- whoosh は大きな話題転換だけに使う。pop / ding は答えの提示や重要な強調に絞る。
- `config/style.yaml` の上限（既定では任意の10秒に3回）を超えない。重要なナレーションの単語には効果音を重ねない。
