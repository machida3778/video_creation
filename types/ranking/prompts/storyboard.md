# データで見るランキング: 構成（絵コンテ）の書き方

`script.json`、`research.json`、`timings.json` を読んでから `Storyboard` を書く。`type_id: ranking`。シーンの `line_ids` は台本の全行を順番に、重複なくカバーする。数値・順位を台本と必ず一致させる。

## 表紙の候補

`docs/THUMBNAILS.md` に従い、`covers.json` に主張または構図の異なる3案を `Covers.options` として書く。各案に id・template（question / reveal / ranking）・text・emphasis・badge（rankingだけ）・image（manifestの画像ID）・line_id（約束を回収する台詞）・palette（yellow / red / white）・ai_generated・photorealistic を記録する。見出しは8〜12字、最大20字・3行。長い場合は文字を削る。

## シーンの割り方

- **フック**: 0秒目から主役の `ken_burns_image` と、指標・件数の短い `hook_title` オーバーレイ（`background: false`）。タイトルが字幕を兼ねるときだけ `captions: false`。比較条件は早めに表示する。
- **各順位**: 項目に対応する `ken_burns_image` をベースにし、`props.overlays` に `rank_card` を重ねる。props は `{rank: 5, label: "項目名", value: "120 人/km²", bar: 0.6, color: "#ffd400"}` の形式。`rank` は整数、`label` は短い項目名、`value` は出典と一致する単位つき数値。
- **棒の長さ**: 同じ単位の非負の原数値を、全項目の最大値で割った値を `bar`（0〜1）に入れる。全項目が0なら0。順位や表示用文字列から長さを作らない。負値や比率の表現が不適切な指標では棒を省略し、値を文字で明記する。0.6秒で指定長まで伸びる。
- 順位が切り替わるシーンだけ、任意で `countdown` を追加する（例: `{component: "countdown", props: {from: 3, to: 2}}`）。シーン冒頭の0.6秒だけ表示されるので、フックや引き直しの文字と重ねない。
- 各順位の数値を示すシーンには `source_card`（`text: "出典: 資料名・年次"`）を付ける。URLと詳しい比較条件は `research.json` と動画説明に残す。
- **第1位の直前**: 台本の引き直しを独立した短いシーンにする。第1位を先に表示しない。
- **任意の振り返り**: 最後の1シーンだけ、ベースを `rank_scroll` にできる。props は `{items: [{rank: 5, label: "項目名", src: "assets/images/item5.png"}], speed: 140}` の形式で全項目を渡す。`src` は省略可。画像は `staticFile(src)` で読めるラン内のパス。`speed` はpx/秒、既定140、負値で逆方向。項目を増やさず、最後の発見を語る行を割り当てる。

## テンポ・文字・素材

- 原則1〜2行、2〜4秒で1シーン。1項目が長い場合は、同じ順位のまま背景・構図を変えてシーンを分け、数値と理由を読み切れるようにする。ズームやパンは2〜3秒ごとに変え、意味のある変化なしに6秒以上続けない。
- `rank_card` は各順位のシーンに置くが、順位公開の効果音・`countdown` は **初登場だけ**。ラベルは12字程度以内、長ければ意味の切れ目で改行して最大2行。値も短くし、重要な文字はx 120〜900、y 240〜1440に収める。字幕は `default`。
- `visual.kind: t2i` の背景は英語の具体的なプロンプト。文字・ロゴ・写実的な実在人物を生成させない。既存画像なら `visual.ref` でmanifestの資産を参照し、出典・権利を記録する。
- 使用する生成画像・音声・効果音・音楽・フォントはランの `assets/manifest.json` にライセンスを記録し、必要なクレジットを説明文に入れる。

## 音

- 各順位の公開で `props.sfx: pop`。第1位は `props.sfx: [{id: pop, at: 0}, {id: ding, at: 1.1}]` として確認の音を加える。`at` はシーン開始からの秒数。短いシーンではdingを次の第1位解説シーンに置き、音声の重要語を避ける。
- 効果音は1秒以上離し、任意の10秒に3回以内、30〜60秒で3〜8個を目安にする。第1位のpopとdingが収まり、最終行を切らないシーン尺を実際のtimingsから確保する。
- `storyboard.music.track` は `config/bgm.yaml` の曲ID。落ち着いたポップ、歌なし。`gain_db` は既定の-20 dB。`caption` や `bgm` をシーン部品名にせず、字幕は自動生成、BGMは `music` で指定する。
