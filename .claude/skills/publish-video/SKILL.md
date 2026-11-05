---
name: publish-video
description: 「v2を投稿して」「v3を公開して」「非公開でテスト投稿して」など、人が選んだ動画候補をYouTubeに投稿する明示的な依頼に使う。
---

# 動画の投稿

ユーザーの方針（2026-11-05）: 人が版を選んだら（例「v2で」）、**確認を挟まずに公開（public）で投稿**する。版の選択そのものは必ず人が行い、無人実行（VG_HEADLESS=1）では投稿しない。
サムネイルが権限エラーで失敗したら、チャンネルの電話番号認証後に `uv run vg publish youtube-thumbnail RUN:vN` で再設定する。公開範囲の変更は `uv run vg publish youtube-privacy RUN:vN --privacy public --confirm`。TikTok と X は自動投稿しない（TikTok は個人利用のアプリを審査で認めず、X の API は有料）。`post_kit/` の場所を伝える。

依頼された run と variant を特定する。依頼から特定できない場合だけ確認する。投稿依頼がないときや、無人の候補生成では実行しない。

1. 選択した版の `qa.json` が合格していること、`publish.json` のタイトル・説明・出典・クレジット・合成メディア開示を確認する。
2. 表紙と `covers_report.json`、縮小・切り取り見本を確認する。人が表紙を指定したら合格案のIDを `covers_report.json` の `chosen_id` に記録して `vg package RUN:vN` を再実行する。未指定なら最初の合格案を用いる。表紙が未生成なら `vg thumbnail RUN:vN` を実行する。`covers.json` がなければ既存の `cover.jpg` を使う。
3. 公開範囲は依頼を優先。「投稿」「公開」なら public、「非公開」「テスト」なら private。予約日時があれば private とタイムゾーン付きの `--at ISO8601` を使う。
4. OAuth 未設定なら `vg publish auth youtube` を案内する。認証情報は Git 管理外の `local/youtube/` だけに保存し、内容を表示しない。
5. `uv run vg publish youtube RUN:vN --confirm --privacy public` を実行する（公開範囲・日時は上記に合わせる）。依頼済みの投稿をもう一度確認し直さない。既投稿の版は停止し、ユーザーが再アップロードを明示した場合だけ `--force` を使う。
6. `published_youtube.json` のURLと公開範囲を報告する。表紙・字幕のエラーがあれば、動画自体の投稿結果と併せて伝える。TikTok / X は `outbox/<run>/<vid>/post_kit/` の手動投稿キットを案内する。自動投稿しない。
