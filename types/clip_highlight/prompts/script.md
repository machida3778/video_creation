# 切り抜き: 台本

元動画の字幕・文字起こしとフレームシートで見どころを選ぶ。文脈を歪めず20〜59秒に編集する。
先に `clips.json` を書く: `source` は元動画URL、`license_id`、必要なら `guideline`、
`segments: [{id, start, end, crop, focus_x}]` は元動画の秒数で、並び順が出力順。
各区間は0.6秒以上、合計59秒以下。`crop` は `fill`（顔に寄る）か `fit`（全体）、`focus_x` は0〜1。
ホロライブ公開配信は `hololive_clip`、他は `creator_clip` と商用利用を認める `guideline` URL必須。
スポーツ放送も権利者の明示的な許諾URLがなければ使わない。

`script.lines` は編集字幕。各行に `start` / `end` を**編集後の出力時計**で指定し、
全尺内・順番通り・重なりなしにする。`text` は実際の発言に沿い、大きな字幕で読める短さに。
cast と speaker は既定の空 narrator を保持し、読み上げ・yomiは不要。
`vg clips RUN` は元音声を保持して切り出し、出力時計の `clips/source_subs.json` を字幕の下書きにする。
下書きを見て字幕を直したら同じコマンドを再実行する。
