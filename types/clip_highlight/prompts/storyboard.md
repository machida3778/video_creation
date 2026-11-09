# 切り抜き: 絵コンテ

`storyboard.json` は任意。使う場合は `type_id: clip_highlight`、`format`、`header`、任意の `music` のみでよい。
scenes は省略可。ベース映像と音声は `clips.json` から作る。別ナレーターは付けない。
配信では `crop: fill` で顔を見せ、2行の固定ヘッダーにタイトル、大きな発言字幕を置く。
スポーツなど16:9の全体は `crop: fit`、format の `media.mode: box` とヘッダー・フッターを使う。
元音声のみなら music は指定しない。BGMを足す場合も元音声の下で既存ミックスを使う。
`vg clips RUN` → 必要なBGMは `vg assets RUN` → `vg make RUN --from timeline`。
