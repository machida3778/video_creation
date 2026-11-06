# 掛け合い解説: 構成（絵コンテ）の書き方

`script.json`、`timings.json`、`config/characters.yaml` を読んでから `Storyboard` を書く。`type_id: dialogue_explainer`。全行を順番に、重複なくシーンに割り当てる。

## 表紙の候補

`docs/THUMBNAILS.md` に従い、`covers.json` に主張または構図の異なる3案を `Covers.options` として書く。各案に id・template（question / reveal / ranking）・text・emphasis・badge（rankingだけ）・image（manifestの画像ID）または prompt（表紙専用の画像。`vg assets` が `img_cover_<id>` として生成する）・line_id（約束を回収する台詞）・palette（yellow / red / white）・ai_generated・photorealistic を記録する。見出しは8〜12字、最大20字・3行。長い場合は文字を削る。見出しは `docs/THUMBNAILS.md` の「惹きつける見出し」の型で作り、答えを書かず、主語と意外性を入れる。3案のうち2案以上は表紙専用の画像にし、プロンプトは `docs/THUMBNAILS.md` の「表紙専用の画像」に従う（見出しの「おかしな点」を大きく写す寄りの構図、見出しの余白、画像に文字を入れない）。`covers.json` は `vg assets` より前に書く（表紙の画像も同じGPUジョブで作る）。並び順は仮でよく、最終の選択は独立した採点で決める。主役の画像にキャラクターの肖像を使ってもよい。

## 背景と二人

- **全シーン**（フック・締めを含む）のベースは `ken_burns_image`。1〜2行ごと、約3〜5秒で話題に合う新しい背景画像へ切り替えてテンポを作る。画像内は全尺を通じて滑らかに加減速するズーム・パンだけ（1.0〜1.15倍、40px以内）。同じ画像の構図だけを切り替えるカットは禁止。意味のある画面変化なしに6秒を超えない。
- **全シーン** の `props.overlays` に `character_pair` を1つ置く。左のaを聞き役、右のbを解説役に固定する。背景は説明の対象、手前は二人の丸い肖像という構図にする。
- `props.roles` は **`{a: questioner, b: explainer}`**。`a` と `b` の `name`、`color`、肖像の元ファイルは `config/characters.yaml` の同じ役割から取得する。任意の既存キャラクターの絵に差し替えない。
- オーバーレイの形式（肖像をラン内にコピー済みの場合）:

```json
{
  "component": "character_pair",
  "props": {
    "roles": {"a": "questioner", "b": "explainer"},
    "a": {"name": "ポンカ", "src": "assets/characters/ponka.png", "color": "#2f7d32"},
    "b": {"name": "リリモ", "src": "assets/characters/ririmo.png", "color": "#b0306a"}
  }
}
```

- `speaking` を手書きしない。`vg timeline` がシーンの `line_ids`、話者、`timings.json` から、各クリップ開始を0秒とする発話区間を埋める。話している側が1.08倍になり、色のリングと小さなバウンスが付き、相手は薄くなる。台詞の時間を推測で指定しない。

## 字幕と文字の位置

- 字幕は有効のままにし、`caption_style` は省略する。役割に合わせて `speaker_a` / `speaker_b` が付く。aは緑、bはピンクで固定し、肖像の `color` と対応させる。
- 肖像は左右のy約760〜1020、名前はその下、字幕はy約1215。タイトル・出典は上部に置き、x 120〜900、y 240〜1440を守る。背景に文字を生成させない。
- フックは0秒から主役の背景と疑問の字幕。任意の短い `hook_title`（`background: false`）は上部に置く。二人や字幕と文字を重複させない。根拠を出すシーンには短い `source_card` を上部に加える。
- 最後の答えも背景＋二人＋字幕で出し、納得の一言のあと長い余韻を入れない。

## 素材と音

- 肖像は、レジストリの英語 `prompt` と `seed` を使い、自前GPUのZ-Image Turboで事前に生成する。レジストリの `portrait` はリポジトリ基準の保存先であり、ランから直接読めるパスではない。未生成なら素材準備が必要と報告し、架空の既存ファイル扱いにしない。
- 使用前に肖像をランの `assets/characters/<id>.png` にコピーし、そのラン内相対パスを `src` にする。各肖像は `assets/manifest.json` に `kind: character_art`、`origin: generated`、モデル名・プロンプト・シード・`generated (Z-Image Turbo, Apache-2.0)`・ライセンスURL・商用可を記録する。VOICEVOX音声の条件とクレジットは肖像とは別に記録する。
- 背景は `visual.kind: t2i`、英語で具体的に指示する。写実的な実在人物・ロゴ・文字を含めない。背景・肖像・音声・フォント・BGM・効果音すべての権利をmanifestに残す。
- BGMは `storyboard.music.track` に `config/bgm.yaml` の歌なし曲を指定する。BGMは声より10 dB下、効果音は6 dB下に実測調整する（`config/style.yaml`）。発話中の追加ダッキングはせず、BGMの冒頭0.3秒・末尾1秒をフェードする。効果音は疑問の転換や答えの提示だけに `props.sfx` で指定し、発話ごとには鳴らさない。1秒以上離し、任意の10秒に3回以内。重要語に重ねない。
- `caption` や `bgm` はシーン部品名にしない。字幕は自動生成、音楽は `music` フィールドを使う。
