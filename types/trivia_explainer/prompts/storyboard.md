# 根拠つき雑学: 構成（絵コンテ）の書き方

`timings.json` で行ごとの時間が決まってから書く。シーンは台本の行を順番に、重複なく全部カバーする。

## 表紙の候補

`docs/THUMBNAILS.md` に従い、`covers.json` に主張または構図の異なる3案を `Covers.options` として書く。各案に id・template（question / reveal / ranking）・text・emphasis・badge（rankingだけ）・image（manifestの画像ID）または prompt（表紙専用の画像。`vg assets` が `cover_<id>` として生成する）・line_id（約束を回収する台詞）・palette（yellow / red / white）・ai_generated・photorealistic を記録する。見出しは8〜12字、最大20字・3行。長い場合は文字を削る。見出しは `docs/THUMBNAILS.md` の「惹きつける見出し」の型で作り、答えを書かず、主語と意外性を入れる。3案のうち2案以上は表紙専用の画像にし、プロンプトは `docs/THUMBNAILS.md` の「表紙専用の画像」に従う（見出しの「おかしな点」を大きく写す寄りの構図、見出しの余白、画像に文字を入れない）。`covers.json` は `vg assets` より前に書く（表紙の画像も同じGPUジョブで作る）。並び順は仮でよく、最終の選択は独立した採点で決める。

## シーンの割り方

- **C01 フック**: 1行目だけ。0秒目から印象の強い画像（`ken_burns_image`、ゆっくり `[1.0, 1.12]` にズーム）に、`overlays` で問いの `title_card`（`background: false`）を重ねる。字幕と重複するので `captions: false`。
- **本編**: 1〜2行ごと、約3〜5秒で新しい画像のシーンに切り替えてテンポを作る。意味のある画面変化なしに6秒を超えない。同じ画像の構図だけを切り替えるカットは禁止。
  - 画像が使えるとき: `ken_burns_image`（`visual.kind: t2i`、`visual.prompt` は英語で具体的に。写実的な実在の人物・ロゴ・文字を描かせない）。画像内は全尺を通じて滑らかに加減速するズーム・パンだけ。`zoom` は1.0〜1.15倍、パンは40px以内。
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
- バリエーションごとに別の曲にする。レンダリング時に実測し、声より10 dB下に調整する（`config/style.yaml`）。発話中の追加ダッキングはしない。冒頭0.3秒・末尾1秒をフェードする。

## 効果音

- `vg sfx build` で自作ライブラリを準備する。シーンの `props.sfx: whoosh`、または `props.sfx: [{id: ding, at: 0.5}]`（`at` はシーン開始からの秒数）で指定する。
- whoosh は大きな話題転換だけに使う。pop / ding は答えの提示や重要な強調に絞る。
- `config/style.yaml` の上限（既定では任意の10秒に3回）を超えない。重要なナレーションの単語には効果音を重ねない。
