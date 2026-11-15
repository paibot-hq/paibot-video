# Paibot Video

[English](README.md) | **日本語**

動画を JSON で書くと、読み上げ・字幕・動く文字・コード・カウンター・グラフの入った MP4 になります。**AI エージェントが JSON を書き、自分で確かめ、自分で直せる**ように作っています。人が下書きを毎回見なくても進められます。

<p align="center"><img src="docs/demo.gif" width="300" alt="Paibot Video で作った解説ショート"></p>

```sh
pip install paibot-video        # FFmpeg が必要です
paibot-video init video.json    # 見本の台本を作る
paibot-video check video.json --sheet sheet.png
paibot-video render video.json -o video.mp4
```

```json
{
  "preset": "shorts",
  "captions": true,
  "background": {"gradient": ["#0b1024", "#312e81"], "angle": 160},
  "scenes": [
    {
      "narration": "内包表記なら、たった1行です。",
      "elements": [
        {"type": "text", "text": "1行で書ける", "size": 100},
        {"type": "code", "code": "squares = [n * n for n in range(1, 6)]", "animate": "type"}
      ]
    }
  ]
}
```

座標も秒数も書かなくて構いません。要素はセーフエリア（スマホのアプリのボタンなどに隠れない範囲）の中で自動で縦に並び、シーンの長さは読み上げの長さで決まります。

`"theme": "neon"`（ほかに `pop`、`cinematic`）を 1 行足すだけで、仕上がった見た目になります。動く光の背景、光る文字が 1 語ずつ跳ねて出る演出、グリッチの切り替え、カラオケ字幕、フィルムの粒子、効果音が付きます。`**…**` で囲んだ語は色が変わります。

## AI エージェント向けの作り

たいていの動画ツールは、人がプレビューを見る前提で作られています。Paibot Video は、エージェントが一人で作業を進めるのに要るものをそろえています。

- **`paibot-video guide`**：言語モデル向けの短い書き方ガイドを出します。エージェントのプロンプトにそのまま入れられます。
- **`paibot-video schema`**：JSON Schema を出します。構造化出力を使うツール向けです。
- **`paibot-video check --json`**：問題を一つ残らず、JSON 上の場所と具体的な直し方付きで返します。「不正です」だけでは終わりません。

  ```text
  ERROR   $.scenes[0].elements[0].txt: unknown field
          ↳ did you mean `text`?
  ERROR   $.scenes[1].duration: the voice is 5.3s and starts at 0.3s, so it would be cut off at 4s
          ↳ use "duration": "auto" or at least 6.2
  WARNING $.scenes[3].elements[1].code: the longest line (39 characters) does not fit, so the code was shrunk from 44px to 37px
          ↳ code is never wrapped: split lines to about 31 characters, or use a landscape preset
  ```

  見る人が気づくことを調べます。たとえば、文字が画面の外やスマホのアプリが重なる場所に出る、要素が縦に入りきらない、文字同士が重なる、背景とのコントラストが低い、スマホでは小さすぎる、読み切れないうちに消える、読み上げが途中で切れる、などです。
- **`--sheet sheet.png`**（または `paibot-video sheet`）：全シーンを 1 枚の画像にまとめます。画像を見られるモデルなら、一目で動画全体を確かめられます。

  ![全シーンの一覧](docs/sheet.jpg)

- **`paibot-video frame --scene 3`**：1 シーンを PNG で出します。
- 結果はいつも同じです。同じ JSON からは必ず同じ動画ができます。

エージェントの作業は「JSON を書く → `check --json --sheet` → 直す → 繰り返す → `render`」の流れになります。[examples/](examples/) の台本はこの流れで作りました。

## 要素

| type | 中身 |
| --- | --- |
| `text` | 見出しやラベル。日本語も英語も正しく折り返します（行頭禁則あり）。後ろに箱も付けられます。 |
| `bullets` | 1 項目ずつ現れる箇条書き。 |
| `image` | PNG・JPEG。`fit`、角丸、ゆっくり寄る `zoom` が使えます。 |
| `shape` | 四角と円。 |
| `code` | 1 文字ずつ打ち込む演出ができるコード。折り返さず、幅が足りないと縮めて `check` で知らせます。 |
| `counter` | カウントアップする数字。前後に文字を付けられます。 |
| `bars` | 伸びながら現れる横棒グラフ。 |

どの要素にも `start`、`end`、`animate`（`fade`、`pop`、`words`、`wipe`、`blur`、`slide-up/down/left/right`、`zoom`、`type`、`none`）、`out: "fade"`、`opacity`、`glow`（発光）、`shadow`（影）があり、文字には縁取り（`stroke`）も付けられます。シーンの入り方は `fade`、`slide`、`wipe`、`zoom`、`flash`、`glitch`、`none` から選べ、`camera` で寄る・引く・流れる動きを付けられます。背景は単色・グラデーション・写真（`dim` で暗くできる）・動く光の玉が使えます。`grain`（粒子）と `vignette`（周辺を暗く）でフィルムのような質感になります。

## テーマ

| テーマ | 見た目 |
| --- | --- |
| `clean` | 既定。素直で落ち着いた見た目。 |
| `neon` | 動く光の玉、光る文字、1 語ずつ出る演出、グリッチの切り替え、カラオケ字幕、粒子、効果音。 |
| `pop` | 明るい光の玉、縁取りと影の付いた文字、スライドの切り替え、カラオケ字幕、効果音。 |
| `cinematic` | 暗い画面に粒子と周辺の暗さ、ぼかしから浮かぶ文字、ゆっくり寄るカメラ。 |

テーマは既定値を決めるだけです。自分で書いた項目が優先されます。

すべての項目は `paibot-video guide` で見られます。

## 音

- **`narration`**：読み上げる文章です。`"language": "auto"`（既定）では、原稿に日本語などの CJK 文字があれば日本語、それ以外は英語を選びます。動画全体に `"language": "ja"` / `"language": "en"` を指定することもできます。日本語では、動いている読み上げソフトを次の順で使います。
  1. [AivisSpeech](https://aivis-project.com/)：自然な日本語。無料で手元で動きます（ポート 10101）。
  2. [VOICEVOX](https://voicevox.hiroshiba.jp/)：無料。キャラクターの声が多くあります（ポート 50021）。
  3. OS の読み上げ：macOS は `say`、Linux は `espeak-ng`。

  声は `"voice": "まお"` や `"まお/おちつき"` で選びます（名前を間違えると使える声の一覧が返ります）。`speed` と `intonation` で話す速さと抑揚を変えられ、`"tts"` で使うソフトを固定できます。ほかの読み上げソフトも `PAIBOT_VIDEO_TTS="mytts --text-file {text_file} --out {out}"` でつなげます。作った音声は `.paibot-video-cache/` に取っておきます。クレジット表記の要否は声ごとのライセンスに従ってください（VOICEVOX の声は「VOICEVOX:ずんだもん」のような表記が必要です）。

  **英語の原稿**には、macOS の `say`（Samantha）、または `espeak-ng`（`en`）を自動で選びます。日本語用のエンジンが起動していても英語音声に切り替わります。自分で指定した TTS コマンドや `tts` エンジンは優先されます。使った言語・エンジン・声は `check` の結果に表示されます。
- **`audio`**：録音済みのファイルをシーンで流します。
- **`captions: true`**：読み上げを字幕にします。文ごとに区切り、声に合わせて切り替えます。`captionStyle: "karaoke"` にすると、大きな縁取り文字が話している所から色づきます。
- **`sfx: true`**：切り替えに「シュッ」、登場に「ポン」という効果音を付けます。
- **`music`**：BGM をその場で生成するか、手元の音声ファイルを繰り返し流します。声の区間では音量を下げ、最後はフェードアウトします。

### 英語ナレーション

英語の原稿を書き、必要なら `language` で英語を明示します。

```json
{
  "language": "en",
  "captions": true,
  "scenes": [
    {
      "narration": "Turn a JSON script into a video with English narration.",
      "elements": [{"type": "text", "text": "English narration", "size": 90}]
    }
  ]
}
```

macOS では `"voice": "Daniel"` などでインストール済みの声を選び、`"rate": 165` で 1 分あたりの単語数を指定できます。声の一覧は `say -v '?'` で確認できます。`espeak-ng` では `voice` に `en-us` や `en-gb` を指定できます。英語字幕は単語の間で区切り、`3.14` や `1,200` のような数値を途中で分けません。

```sh
paibot-video check examples/english.json --sheet english-sheet.png
paibot-video render examples/english.json --preview -o english.mp4
```

[examples/english.json](examples/english.json) は英語音声・字幕・自動 BGM のサンプルです。独自の読み上げコマンドには `{language}` で判定後の `en` / `ja` を渡せます（例：`PAIBOT_VIDEO_TTS="mytts --lang {language} --text-file {text_file} --out {out}"`）。

### 自然な英語音声を無料・ローカルで使う

[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) のニューラル音声を選べます。初回のみ依存パッケージとモデル（約354 MB）をダウンロードし、その後の音声生成はオフラインです。原稿を外部へ送信せず、API キーも不要です。

```sh
uv sync --extra kokoro
uv run --extra kokoro paibot-video setup-kokoro
uv run --extra kokoro paibot-video render examples/english-kokoro.json --preview
```

JSON のトップレベルで次のように設定します。

```json
"tts": "kokoro",
"language": "en",
"voice": "af_heart",
"speed": 0.95
```

`af_heart` は米国英語の女性声、`am_michael` は米国英語の男性声、`bf_emma` は英国英語の女性声です。速度は `speed`（0.5〜2.0、標準 1.0）で調整します。OS 音声用の `rate` と、日本語エンジン用の `intonation` は Kokoro には適用されません。現在このアプリでの Kokoro 対応は英語のみです。`tts: auto` は従来どおり OS の英語音声を使うため、Kokoro を使う場合は明示してください。

モデルの保存先は `~/.cache/paibot-video/kokoro-v1.0` です。`PAIBOT_VIDEO_KOKORO_DIR` で変更できます。モデルは Git に含めません。配布元によるライセンスは kokoro-onnx が MIT、モデルが Apache-2.0 です。[同じ原稿の比較用 JSON](examples/english-kokoro.json) を用意しています。

### BGM を生成する

`"music": "lofi"` と書くだけで、動画の長さに合わせたステレオの BGM を手元で生成します。外部 API や音楽ファイルは不要です。同じ設定と seed なら同じ曲になり、生成結果をキャッシュします。

| スタイル | 音 | 既定のテンポ |
| --- | --- | --- |
| `synthwave` | シンセ、アルペジオ、ドラム | 100 BPM |
| `pop` | 明るいメロディと四つ打ち | 118 BPM |
| `lofi` | 落ち着いた鍵盤と揺れるリズム | 78 BPM |
| `ambient` | 広がるパッドとベル、ドラムなし | 64 BPM |

`neon` テーマには `synthwave`、`pop` には `pop`、`cinematic` には `ambient` が自動で付きます。`"music": false` で止められます。`clean` テーマには BGM が付きません。

```json
"music": {"generate": "lofi", "seed": 2, "bpm": 85, "volume": 0.3, "duck": 0.6, "fadeOut": 1.5}
```

`seed` は 0 以上の整数で、変えると曲のバリエーションが変わります。`bpm` は 40〜300。`volume` は音量、`duck` は声の区間で下げる割合です（0 は下げない、0.6 は通常の 40% にする、1 は消音）。声の前後ではなめらかに音量が変わります。

音声ファイルを使う場合は `{"src": "bgm.mp3", "volume": 0.2}` と書きます。`generate` と `src` はどちらか一方です。`start` はファイルの読み始める秒数、`fadeIn` / `fadeOut` はフェードの秒数です。

```sh
paibot-video music                                  # スタイル一覧
paibot-video music lofi --length 20 --seed 2 -o bgm.wav
paibot-video render examples/music.json --preview -o music.mp4
```

[examples/music.json](examples/music.json) は、日本語ナレーションと自動 BGM を組み合わせた短いサンプルです。

## コマンド

| コマンド | |
| --- | --- |
| `render SPEC [-o out.mp4] [--preview] [--scale 0.5] [--fps N] [--force] [--json]` | MP4 を書き出します。`check` でエラーがあると書き出しません（`--force` で強制）。`--preview` は半分の大きさ・15fps です。 |
| `check SPEC [--json] [--sheet PNG] [--no-tts]` | 検査します。エラーがあれば終了コード 1 です。 |
| `frame SPEC (--scene N \| --at 秒) [-o PNG]` | 1 コマを出します。 |
| `sheet SPEC [-o PNG] [--cols N]` | 全シーンを 1 枚にまとめます。 |
| `music [STYLE] [--length 秒] [--seed N] [--bpm N] [-o WAV]` | スタイル一覧、または試聴用の BGM を WAV に書き出します。 |
| `schema` / `guide` / `init [PATH]` | JSON Schema、書き方ガイド、見本の台本を出します。 |

大きさは `shorts` 1080×1920（既定）、`landscape` 1920×1080、`square` 1080×1080、`portrait` 1080×1350 から選ぶか、`width`/`height` で好きに決められます。

## 必要なもの

- Python 3.10 以降と Pillow・NumPy（自動で入ります）。
- FFmpeg（`ffmpeg` と `ffprobe`）。書き出しと音声に使います。
- 使う言語の文字が入ったフォント。macOS・Windows・主な Linux（Noto Sans CJK）では日本語フォントを自動で見つけます。見つからないときは、台本の `font` か、`PAIBOT_VIDEO_FONT` / `PAIBOT_VIDEO_FONT_BOLD` で指定してください。

## 開発

```sh
uv sync
uv run pytest
cd examples && uv run paibot-video render comprehension.json
```

## ライセンス

MIT
