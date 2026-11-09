# 音楽で見せるランキング: 絵コンテ

`type_id: ranking_montage`。**`vg tts RUN` より先に** `storyboard.json` を書く。
台本の全行を順番通りに、重複なく1行1シーンに割り当て、各 `props.sec` に正の秒数を指定する。
`vg tts` は累積秒数から `timings.json` を作り、音声合成を呼ばない。字幕も自動生成しない。

- 各項目: `ken_burns_image`、`props.sec: 3.4`、`transition: mirror`。
  顔を上半分に収めた全画面の静止画を `visual: {kind: library, ref: <画像ID>}` で指定する。
  人物は `vg image add` で権利を記録した写真・配信スチルを使う。架空・抽象的な項目には `visual.kind: t2i` も可。
  `props.overlays: [{component: rank_label, props: {rank: 16, name: "ナツミート（現）", tier: normal}}]`。
  `name` は行の `text` と同じにする。ラベルは下半分、顔は上半分に残す。
- 上位: 5・4位は `tier: top5`、3・2位は `top3`、1位は `top1`。
  順位で青・緑・橙・白金・金に変わり発光する。ベース画像にも同じtierが渡りRGB分離、1〜3位には火花が付く。
  色の上書きは `color`。文字や画像の効果を焼き込んだコピー素材は使わない。
- 5位直前のカード: `component: section_card`、`props: {sec: 1.5, text: "Top"}`。
  このシーンの行の `text` もカード文字と一致させる。`top_card` で変更可。黒い星空・紫の発光文字は手続き生成。
- 任意の冒頭タイトル: `section_card`、`props.sec: 0.8`〜`1.2`。行の `text` とカード文字を一致させる。

`vg bgm suggest RUN` で勢いの合うBGMを選び、`music.track` に指定する。カードを含めて30〜59秒以内、
文字は1行に収める。画像・音楽・フォントのライセンスはランのmanifestに記録する。テンポのQA上限は通常と同じ。
