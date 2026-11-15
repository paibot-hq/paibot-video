# Paibot Video

**English** | [日本語](README.ja.md)

Write a video as JSON and get an MP4 with narration, captions, animated text, code, counters and charts. It is built so that **an AI agent can write the JSON, check its own work and fix it** without a person watching every draft.

<p align="center"><img src="docs/demo.gif" width="300" alt="A short explainer video made with Paibot Video"></p>

```sh
pip install paibot-video        # needs FFmpeg on PATH
paibot-video init video.json    # an example to start from
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

No coordinates and no timings are needed. Elements stack themselves inside the safe area, and each scene lasts as long as its narration.

Add `"theme": "neon"` (or `pop`, `cinematic`) for a finished look in one line: drifting light, glowing text that bounces in word by word, glitch cuts, karaoke captions, film grain and sound effects. Mark words with `**…**` to colour them.

## Made for AI agents

Most video tools assume a person is watching the preview. Paibot Video gives an agent what it needs to work alone:

- **`paibot-video guide`** prints a short authoring guide written for a language model. Put it in the agent's prompt.
- **`paibot-video schema`** prints the JSON Schema, for tools that use structured output.
- **`paibot-video check --json`** reports every problem with a JSON path and a concrete fix, not just "invalid":

  ```text
  ERROR   $.scenes[0].elements[0].txt: unknown field
          ↳ did you mean `text`?
  ERROR   $.scenes[1].duration: the voice is 5.3s and starts at 0.3s, so it would be cut off at 4s
          ↳ use "duration": "auto" or at least 6.2
  WARNING $.scenes[3].elements[1].code: the longest line (39 characters) does not fit, so the code was shrunk from 44px to 37px
          ↳ code is never wrapped: split lines to about 31 characters, or use a landscape preset
  ```

  It checks what a viewer would notice: text going off screen or into the area that phone apps cover, stacks that don't fit, overlapping text, low contrast, text too small for a phone, text shown too briefly to read, and narration that would be cut off.
- **`--sheet sheet.png`** (or `paibot-video sheet`) saves every scene in one image, so a model that can see images checks the whole video with one look:

  ![Contact sheet of all scenes](docs/sheet.jpg)

- **`paibot-video frame --scene 3`** renders a single scene as PNG.
- Everything is deterministic: the same JSON always gives the same video.

A typical agent loop is *write JSON → `check --json --sheet` → fix → repeat → `render`*. The examples in [examples/](examples/) were made that way.

## Elements

| type | what it is |
| --- | --- |
| `text` | Headlines and labels. Japanese and English wrap correctly (line-break rules included). Optional box behind it. |
| `bullets` | A list whose items appear one by one. |
| `image` | PNG/JPEG, with `fit`, rounded corners and a slow `zoom`. |
| `shape` | Rectangles and circles. |
| `code` | A code block that can be typed out. Lines are never wrapped; a wide block is shrunk and `check` tells you. |
| `counter` | A number counting up, with prefix and suffix. |
| `bars` | A horizontal bar chart whose bars grow in. |
| `manim` | A [Manim](https://www.manim.community/) scene for moving maths: equations written and rearranged, curves drawn, shapes transformed. See below. |

Every element has `start`, `end`, `animate` (`fade`, `pop`, `words`, `wipe`, `blur`, `slide-up/down/left/right`, `zoom`, `type`, `none`), `out: "fade"`, `opacity`, `glow` and `shadow`; text can have an outline (`stroke`). Scenes enter with `fade`, `slide`, `wipe`, `zoom`, `flash`, `glitch` or `none`, and the `camera` can push in, pull out or drift. Backgrounds can be a colour, a gradient, a photo (with `dim`) or moving colour orbs. `grain` and `vignette` give a film look.

## Manim scenes

Paibot Video does the narration, captions, layout and checks; [Manim](https://www.manim.community/) does what it is best at, moving maths. Put a scene's code in a `manim` element:

```json
{
  "narration": "Expanding the square gives three terms.",
  "elements": [
    {"type": "text", "text": "Square of a sum", "size": 90},
    {"type": "manim", "height": 700, "code": "class Main(Scene):\n    def construct(self):\n        eq = MathTex(r'(a+b)^2', '=', 'a^2+2ab+b^2')\n        self.play(Write(eq))\n        self.play(eq[2].animate.set_color(PV_ACCENT))"}
  ]
}
```

```sh
pip install 'paibot-video[manim]'      # MathTex/Tex also need LaTeX
paibot-video check video.json --allow-code --sheet sheet.png
paibot-video render video.json --allow-code
```

- The scene is rendered with a transparent background over the theme, at the element's size and the video's frame rate, and cached until the code changes.
- `from manim import *` is done for you, and `PV_COLOR`, `PV_ACCENT` and `PV_FONT` carry the video's colours and font (Japanese text works without `font=`).
- The scene lasts as long as the animation or the narration, whichever is longer; the last frame is held.
- `check` reports Manim's errors with the line in your code, a drawing cut off at the edge of its area, outside the safe area or over text, and an animation that runs long after the narration.
- The code is run as Python, so nothing runs without `--allow-code`. Use it only for specs you trust.

## Themes

| theme | look |
| --- | --- |
| `clean` | Default. Plain and quiet. |
| `light` | White to pale blue with dark text and a calm blue accent, lofi music, white caption boxes. Good for lessons. |
| `neon` | Drifting colour orbs, glowing text, word-by-word entrances, glitch cuts, karaoke captions, grain, sound effects. |
| `pop` | Bright orbs, outlined text with shadows, slide cuts, karaoke captions, sound effects. |
| `cinematic` | Dark and grainy with a vignette, text emerging from a blur, slow camera push. |

A theme only fills in defaults; anything you write yourself wins.

Run `paibot-video guide` for the full list of fields.

## A mark in the corner

```json
"watermark": {"src": "logo.png", "text": "PAIBOT", "position": "top-left", "size": 56, "opacity": 0.75}
```

A small logo and/or text on every frame, in the margin outside the safe area so it never covers the content, and fixed while the camera moves.

## Sound

- **`narration`**: text that is spoken. With `"language": "auto"` (default), narration containing CJK characters selects Japanese; otherwise it selects English. You can set `"language": "ja"` or `"language": "en"` for the whole video. For Japanese, Paibot Video uses the first engine that is running:
  1. [AivisSpeech](https://aivis-project.com/) — natural Japanese, free, runs locally (port 10101).
  2. [VOICEVOX](https://voicevox.hiroshiba.jp/) — free, many character voices (port 50021).
  3. The system voice: `say` on macOS, `espeak-ng` on Linux.

  Pick a voice with `"voice": "まお"` or `"まお/おちつき"` (a wrong name gets you the list of voices), tune it with `speed` and `intonation`, or force an engine with `"tts"`. Any other engine plugs in with `PAIBOT_VIDEO_TTS="mytts --text-file {text_file} --out {out}"`. Results are cached in `.paibot-video-cache/`. Check each voice's license for credit rules (VOICEVOX voices need a credit such as "VOICEVOX:ずんだもん").

  **English narration** automatically uses macOS `say` with `Samantha`, or `espeak-ng` with the `en` voice. This works even while a Japanese engine is running. A custom command still takes precedence, and an explicitly selected `tts` engine is respected. `check` reports the language, engine and voice.
- **`audio`**: a recorded file for the scene instead.
- **`captions: true`** shows the narration as subtitles, split by sentence and timed to the voice. `captionStyle: "karaoke"` shows big outlined words that light up as they are spoken.
- **`sfx: true`** adds a whoosh on transitions and a pop when things land.
- **`music`**: generate a background track locally or loop an audio file. Its volume drops during narration and fades out at the end.

### English narration

Write your narration in English and set `language` if you want to make the choice explicit:

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

On macOS, use `"voice": "Daniel"` for a different installed voice, and `"rate": 165` to set words per minute. Run `say -v '?'` to list the available voices. With `espeak-ng`, voice names such as `en-us` or `en-gb` can be supplied through `voice`. English captions split between words and keep numbers such as `3.14` and `1,200` together.

```sh
paibot-video check examples/english.json --sheet english-sheet.png
paibot-video render examples/english.json --preview -o english.mp4
```

[examples/english.json](examples/english.json) includes narration, captions and generated BGM. Custom TTS commands can use `{language}` to receive the resolved `en` or `ja` value, for example `PAIBOT_VIDEO_TTS="mytts --lang {language} --text-file {text_file} --out {out}"`.

### Local neural English narration

Use [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) for neural English voices. Install the optional dependencies and download the models (~354 MB) once. Speech generation then runs offline, without sending scripts to a service or requiring an API key.

```sh
uv sync --extra kokoro
uv run --extra kokoro paibot-video setup-kokoro
uv run --extra kokoro paibot-video render examples/english-kokoro.json --preview
```

Set these top-level fields:

```json
"tts": "kokoro",
"language": "en",
"voice": "af_heart",
"speed": 0.95
```

Try `af_heart` (American female), `am_michael` (American male), or `bf_emma` (British female). Use `speed` from 0.5 to 2.0 (normal: 1.0); the system voice's `rate` and the Japanese engines' `intonation` do not apply. This integration currently supports English only. Select Kokoro explicitly; `tts: auto` continues to use system English voices.

Models live in `~/.cache/paibot-video/kokoro-v1.0`; override this with `PAIBOT_VIDEO_KOKORO_DIR`. Model files are not included in Git. Upstream licenses: MIT for kokoro-onnx, Apache-2.0 for the model. [examples/english-kokoro.json](examples/english-kokoro.json) uses the same script as the system-voice example for comparison.

### Generate background music

Set `"music": "lofi"` to compose a stereo track for the video's duration. No external API or music file is needed. The same settings and seed produce the same track; generated tracks are cached.

| Style | Sound | Default tempo |
| --- | --- | --- |
| `synthwave` | Synth bass, arpeggios and drums | 100 BPM |
| `pop` | Bright melody and four-on-the-floor drums | 118 BPM |
| `lofi` | Relaxed keys and swung drums | 78 BPM |
| `ambient` | Wide pads and bells, no drums | 64 BPM |

The `neon`, `pop` and `cinematic` themes include `synthwave`, `pop` and `ambient` respectively. Set `"music": false` to disable it. The `clean` theme has no music by default; `light` has `lofi`.

```json
"music": {"generate": "lofi", "seed": 2, "bpm": 85, "volume": 0.3, "duck": 0.6, "fadeOut": 1.5}
```

`seed` is a non-negative integer selecting a variation. `bpm` accepts 40–300. `volume` sets the level; `duck` sets how much it drops during the voice (0 means no reduction, 0.6 leaves 40% of the level, 1 mutes it). The level ramps smoothly before and after each voice segment.

To use your own file, set `{"src": "bgm.mp3", "volume": 0.2}`. Choose either `generate` or `src`. `start` skips seconds at the start of a file; `fadeIn` and `fadeOut` are in seconds.

```sh
paibot-video music                                  # list styles
paibot-video music lofi --length 20 --seed 2 -o bgm.wav
paibot-video render examples/music.json --preview -o music.mp4
```

[examples/music.json](examples/music.json) combines Japanese narration with generated music.

## Commands

| command | |
| --- | --- |
| `render SPEC [-o out.mp4] [--preview] [--scale 0.5] [--fps N] [--force] [--json]` | Write the MP4. Refuses if `check` finds errors unless `--force`. `--preview` is half size at 15 fps. |
| `check SPEC [--json] [--sheet PNG] [--no-tts] [--allow-code]` | Validate and lint. Exit code 1 if there are errors. |
| `frame SPEC (--scene N \| --at SEC) [-o PNG]` | One frame. |
| `sheet SPEC [-o PNG] [--cols N]` | All scenes in one image. |
| `music [STYLE] [--length SEC] [--seed N] [--bpm N] [-o WAV]` | List styles or write a standalone WAV to listen to. |
| `schema` / `guide` / `init [PATH]` | JSON Schema, the authoring guide, an example spec. |

Presets: `shorts` 1080×1920 (default), `landscape` 1920×1080, `square` 1080×1080, `portrait` 1080×1350, or any `width`/`height`.

## Requirements

- Python 3.10+, Pillow and NumPy (installed automatically).
- FFmpeg (`ffmpeg` and `ffprobe` on PATH) for rendering and audio.
- Optional: Manim (`pip install 'paibot-video[manim]'`) for `manim` elements, and LaTeX for its MathTex/Tex.
- A font that covers your language. A system Japanese font is found automatically on macOS, Windows and most Linux distributions (Noto Sans CJK). Otherwise set `font` in the spec or `PAIBOT_VIDEO_FONT` / `PAIBOT_VIDEO_FONT_BOLD`.

## Development

```sh
uv sync
uv run pytest
cd examples && uv run paibot-video render comprehension.json
```

## License

MIT
