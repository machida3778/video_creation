# 瞬間英作文: 絵コンテ

オリジナルのシンプルなキャラクターが教室に立つt2i画像を1枚だけ生成し全問で再利用する。
最初のシーンだけ `visual.kind: t2i` で `vg assets RUN`、以降はその画像IDを
`visual: {kind: library, ref: img_<最初のシーンID>}` にする（追加生成しない）。
各台本行を順番に1行1シーンへ割り当て、`component: ken_burns_image`。
format の caption を黄色い `box`、黒い `fill`、`stroke: null` に設定する。
日本語の字幕はpause_afterの間も表示され、英語のシーンで答えに切り替わる。

日本語シーンに `props.overlays: [{component: countdown_ring, props: {seconds: 3, start: <秒>}}]`。
`start` は**そのシーン開始から**リング開始までの秒数。
`vg tts` 後、`timings` の日本語行の `end` からシーン開始を引く（最初のシーン開始は0、他はその行のstart）。
読み上げ終了からpause_afterの3秒間だけリングを出す。専用の空行・空シーンは作らない。
リングはキャラクターの上 (540,760)、字幕は下。静止画のパン・ズームは控えめにする。
