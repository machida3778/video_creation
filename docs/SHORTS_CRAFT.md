# ショート動画の作り方（ハーネスに入れるルール）

記事・プラットフォーム公式・クリエイターの話（2024〜2026年）から、ハーネスに入れたルールをまとめる。数値は出典の推奨か、検証用の暫定値（**暫定**）。機械で守らせるものは QA（`vg/qa.py`、しきい値は `config/style.yaml`）に、判断が要るものは台本・構成の指示（`types/*/prompts/`）に入れる。最終的な好みの判断は人が行う。

## 冒頭（最初の1〜3秒）

- 0秒目から「主役になる被写体1つ＋読める主張1つ」を出す。真っ白・ロゴだけの始まりにしない。QA `hook`: 0.3秒以内に文字と声。
- 価値は3秒以内に示す（[TikTok 公式](https://ads.tiktok.com/resources/help/article/creative-best-practices?lang=en)、[YouTube 公式のクリエイター対談](https://blog.youtube/creator-and-artist-stories/youtube-shorts-deep-dive/)）。
- 台本ではフックを **4案以上、3種類以上の型**（問い・意外な主張・結論先出し・誤解の指摘・数字 など）で出してから1つを選ぶ（**暫定**、[フックの型の分析](https://www.overseeros.com/blog/youtube-shorts-hooks-study)）。挨拶・自己紹介・「今回は〜」は入れない。

## 構成とテンポ

- 冒頭で投げた問い（オープンループ）は、必ず本編で回収する。回収しない問いは置かない（[CINRA](https://www.cinra.net/index.php/article/202603-tiktok02_ienkmktcl)）。
- 50秒以上の動画では、**30〜40秒あたりに1回、引き直し**を入れる（新しい疑問か証拠を出す。**暫定**）。
- 1文は **40字以内**。言い換えだけの行、つなぎだけの行を重ねない。
- 台本1〜2行、約3〜5秒ごとに **新しい画像** に切り替えてテンポを作る。意味のある変化がないまま **6秒を超えて** 続けない（QA `tempo` は新しいベース画像・部品への切り替え間隔を6秒で判定）。場面と関係のない飾りの映像は使わない（[PaperTok, CHI 2026](https://faculty.washington.edu/garyhs/docs/cristobal-CHI2026-papertok.pdf)）。
- 画像内のズーム・パンは全尺を通じて滑らかに加減速する。ズームは1.0〜1.15倍、パンは40px以内。同じ画像の構図をカットで切り替えず、急な寄りも入れない（2026-11-05の視聴フィードバック）。
- ランキングは基準を1つ明示し、約束した数だけ、重複も抜けもなく並べる。

## テロップ（字幕）

- 1080×1920 で本文 56〜72 px、強調 72〜96 px（[Omniweb](https://omniweb.jp/m25/)、制作会社の推奨）。1行 10〜15字、最大2行。
- 強調は **1行に1語まで**。縁取りか背景を必ず付ける。話者ごとに色を固定する（[Sentarys](https://sentarys.com/blog/animated-captions-shorts)）。
- 字幕・タイトルは各プラットフォームの UI と重ねない（x 120〜900、y 240〜1440 に収める）。

## 音

- 効果音にはそれぞれ目的を持たせる（場面転換・文字の出現・確認・答えの提示）。30〜60秒で **3〜8個**、間隔は1秒以上。声より 6〜12 dB 下げる（[StoryCut](https://www.storycut.com/blog/how-to-add-sound-effects-to-tiktok)、制作ツール側の目安）。大事な言葉に効果音をかぶせない。
- 音量は声を台本順に連結して実測し、BGMを声より10 dB下、効果音を6 dB下に合わせる。0.4秒未満の短い音はRMSで測る。BGMに発話中の追加ダッキングはせず、冒頭0.3秒・末尾1秒をフェードする。測定値とゲインは `render/mix.json` に保存し、QA `mix` はBGM ±2 dB・効果音 ±3 dBを確認する（`config/style.yaml`、2026-11-05の視聴フィードバック）。
- 答えの直前に、BGM を 0.2〜0.4秒だけ止める演出は「入れる版・入れない版」で比べる（**暫定**）。
- VOICEVOX の速さは 1.15〜1.3 が目安（[Shorty](https://shorty.mazenture.com/articles/zundamon-douga-tsukurikata)）。固有名詞・数字の読み間違いは0にする。

## 終わり方

- 答えを出しきってから終える。ループ（終わりが冒頭につながる）にするか、答えで切るかは版を分けて比べる（**暫定**）。
- 呼びかけ（CTA）は1つまで。
- 尺は 30〜59 秒、目安 45 秒（YouTube Shorts だけに出すため TikTok の61秒以上の条件は不要。Shorts の収益は再生数の割合で配分され、尺の条件はない: [YouTube ヘルプ](https://support.google.com/youtube/answer/12504220)）。尺を満たすためだけの水増しはしない。

## タイトル・説明・表紙

- 検索されそうな言葉（主要な検索語）を1つ決め、説明文と画面の文字に自然に入れる。TikTok のハッシュタグは関連するもの3〜5個（[Sprout Social](https://sproutsocial.com/insights/tiktok-seo/)）。
- YouTube のタイトルは100字、説明は5,000字まで（[YouTube ヘルプ](https://support.google.com/youtube/answer/57407)）。
- 表紙（サムネイル）の候補を1枚作り、小さく表示しても読めるか確かめる。

## AI生成コンテンツ

- 生成した素材は、写実的かどうかを全部記録し、写実的なものがあれば開示フラグを付ける（[YouTube](https://support.google.com/youtube/answer/14328491)、[TikTok](https://support.tiktok.com/en/using-tiktok/creating-videos/ai-generated-content)）。
- 1本ごとの固有の価値（独自の問い・説明の筋道）を人が確認できる形で残す。量産・使い回しに見える動画はプラットフォームの方針で評価されない（[YouTube の方針](https://support.google.com/youtube/answer/1311392)）。

## 効果測定（今後）

- 版ごとに ID・尺・指標の定義を記録し、2つの版を同じ7日間で比べる。1,000回程度の視聴に届くまでは傾向として扱い、勝ち負けを自動で決めない（**暫定**）。
