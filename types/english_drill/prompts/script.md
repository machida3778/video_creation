# 瞬間英作文: 台本

6〜8問（通常 `route.params.items: 7`）。日常の短い例文をオリジナルで作り、自然な英訳を確認する。
`vg cast RUN` の cast を使う: narrator は日本語、english は `lang: en` の Kokoro。
各問は日本語1行（`speaker: narrator`, `pause_after: 3` = think_sec）と
英語1行（`speaker: english`）。長い前置きなしで最初の問いを出し、考える3秒の後に英語を見せて話す。
英語はカタカナのyomiを付けず英語のまま合成する。日本語の難読語にはyomiを付ける。
英語の反復は任意。同じ英文を別行にすれば2回読めるが、全体が59秒を超えるなら省略する。
速度は役ごとの `voice_overrides`。必要なら反復用の別役に同じKokoro音声と遅い速度を設定する。
`vg tts RUN` の実測尺で調整する。思考時間を含め、末尾0.5秒の余韻込みで59秒以内。
日本語の読みと英語の発音を実音声で確認し、`vg yomi RUN --approve` を記録する。
この動画自体が日英混在教材なので、別の英語吹替 `en.json` は不要。
