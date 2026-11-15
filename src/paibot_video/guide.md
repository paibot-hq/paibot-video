# Writing a Paibot Video spec

You describe a video as JSON. `paibot-video` turns it into an MP4. This guide
is written so that a language model can follow it without other context.

## Workflow

1. Write `video.json`.
2. `paibot-video check video.json --sheet sheet.png` lists every problem with a
   JSON path and a hint, and saves a picture of all scenes. Look at the picture.
3. Fix the issues until `check` reports no errors (warnings are worth fixing too).
4. `paibot-video render video.json -o video.mp4`.

`paibot-video schema` prints the full JSON Schema. `paibot-video frame video.json --scene 3`
renders one scene as a PNG.

## Shape of a spec

```json
{
  "preset": "shorts",
  "language": "ja",
  "captions": true,
  "background": {"gradient": ["#0f172a", "#1e3a8a"], "angle": 160},
  "scenes": [
    {
      "narration": "Pythonで動画が作れます。",
      "elements": [
        {"type": "text", "text": "Pythonで動画", "size": 120},
        {"type": "text", "text": "JSONを書くだけ", "size": 60, "weight": "regular", "start": 0.4}
      ]
    }
  ]
}
```

- `preset`: `shorts` (1080x1920, default), `landscape` (1920x1080), `square`, `portrait`.
- Sizes are pixels in that resolution. Positions can be pixels or percentages (`"50%"`).
- Colours are CSS-style: `"#38bdf8"`, `"#00000099"`, `"white"`.

## Look: start from a theme

Set `"theme"` and you get a finished look without tuning anything:

- `neon`: drifting colour orbs, glowing text, words that bounce in, glitch cuts,
  karaoke captions, film grain, sound effects.
- `pop`: bright orbs, outlined text with shadows, slide cuts, karaoke captions, sound effects.
- `cinematic`: dark, grainy, vignetted; text fades in from a blur; slow camera push.
- `clean` (default): plain and quiet.

Anything you set yourself overrides the theme. Mark key words with `**double asterisks**`
in `text` to colour them with the accent ("リスト**内包表記**").

## Layout: let the stack do it

Leave out `x` and `y`. Elements without them are stacked top to bottom,
centred, `gap` px apart (default 48), and the whole stack is centred in the
safe area (use the scene's `valign` to move it). This is the easiest way to get
a clean layout. Only give `x`/`y` for things that must sit somewhere specific;
they are the centre of the element unless you change `anchor`.

## Timing

- A scene with `narration` lasts as long as the voice plus a short pause
  (`"duration": "auto"`, the default). Without narration it lasts 3 s unless you set `duration`.
- `start` / `end` on an element are seconds from the start of its scene.
- Stagger entrances with `start` (0, 0.4, 0.8 …) so the viewer reads in order.
- `transition` on a scene says how it enters: `fade` (default), `slide`, `wipe`, `zoom`, `none`.

## Elements

| type | required | useful fields |
| --- | --- | --- |
| `text` | `text` | `size`, `weight` (`bold`/`regular`), `color`, `background`, `align`, `maxWidth` |
| `bullets` | `items` | `size`, `stagger`, `bulletColor` |
| `image` | `src` | `width`, `height`, `fit`, `radius`; `animate: "zoom"` for a slow push-in |
| `shape` | `width`, `height` | `shape` (`rect`/`circle`), `color`, `radius` |
| `code` | `code` | `size`; `animate: "type"` types it out |
| `counter` | `to` | `from`, `prefix`, `suffix`, `decimals`, `duration` |
| `bars` | `items` (`[{"label", "value"}]`) | `suffix`, `max`, `color` |

Every element also takes `start`, `end`, `animate`, `animDuration`, `out: "fade"`,
`opacity`, `glow` (a colour) and `shadow` (true/false). `text` and `counter` take
`stroke` (outline width) and `strokeColor`.

Entrance animations: `fade`, `pop`, `words` (word by word / character by character,
text only), `wipe`, `blur`, `slide-up`, `slide-down`, `slide-left`, `slide-right`,
`zoom`, `type` (text and code), `none`.

## Motion and finish

- Backgrounds: a colour, `{"gradient": [...], "angle": 160}`, `{"image": "photo.jpg", "dim": 0.4}`,
  or moving light `{"orbs": ["#c026d3", "#0891b2"], "base": "#05010f", "speed": 1}`.
- `camera`: `push` (slow zoom in), `pull`, `drift`, `none`, for the whole video or per scene.
- Transitions: `fade`, `slide`, `wipe`, `zoom`, `flash`, `glitch`, `none`.
- `grain` and `vignette` (0..1) add film grain and dark corners.
- `captionStyle`: `box` (subtitles on a dark box) or `karaoke` (big outlined words
  that light up as they are spoken). Long sentences are split so captions stay at two lines.
- `sfx: true` adds a whoosh on transitions and a pop when a scene's first element lands.

## Good videos

- One idea per scene. Put the explanation in `narration` and only the key words on screen.
- Big text: 100–140 px for headlines and at least 48 px for anything else on `shorts`.
- Keep strong contrast: light text on dark backgrounds or the reverse.
- 5–12 scenes is a good length for a short.
- Use `note` fields for your own comments; JSON has no comments.

## Sound

- `language`: `auto` (default), `ja`, or `en`, for the whole video's narration.
  Auto selects Japanese if the narration contains CJK characters, English otherwise.
- `narration` is spoken by a text-to-speech engine. With `"tts": "auto"` (default),
  Japanese uses the first available engine: **AivisSpeech** (port 10101),
  **VOICEVOX** (port 50021), then the system voice (`say` / `espeak-ng`).
  English goes straight to the system's English voice, even if a Japanese engine is running:
  **Samantha** on macOS, **en** with espeak-ng. An explicit `tts` engine is respected.
  `check` prints the resolved language, engine and voice.
- `voice`: for AivisSpeech/VOICEVOX a name like `"まお"` or name/style like `"まお/おちつき"`;
  a wrong name returns the list of available voices. For `say`, e.g. `"Kyoko"` or `"Samantha"`.
- `speed` (1.0 = normal) and `intonation` tune AivisSpeech/VOICEVOX;
  `rate` sets words per minute for `say` / `espeak-ng` (for example 165).
- For an English video, write English `narration`, set `"language": "en"`, and enable `captions`.
  macOS `voice` can be `Samantha` or `Daniel`; list installed voices with `say -v '?'`.
  With espeak-ng, use voices such as `en-us` or `en-gb`.
- For local neural English narration, use `"tts": "kokoro", "language": "en", "voice": "af_heart", "speed": 0.95`.
  First run `uv sync --extra kokoro` and `uv run --extra kokoro paibot-video setup-kokoro` to download ~354 MB of models.
  Render with `uv run --extra kokoro paibot-video render SPEC`. After setup, speech generation is offline.
  Voices include `af_heart` (US female), `am_michael` (US male), `bf_emma` (UK female).
  Kokoro supports English here and uses `speed` (0.5–2.0), not `rate` or `intonation`.
  `tts: auto` still selects system English voices; explicitly select Kokoro to use it.
- Any other engine: `PAIBOT_VIDEO_TTS="mytts --lang {language} --text-file {text_file} --out {out}"`.
  Custom commands take precedence in auto mode; `{language}` is the resolved `en` or `ja`.
- `audio` plays a file you already have instead.
- `"music": "lofi"` generates a stereo background track locally, for the video's duration.
  Styles: `synthwave` (100 BPM), `pop` (118), `lofi` (78), `ambient` (64, no drums).
  `neon`, `pop`, `cinematic` themes include `synthwave`, `pop`, `ambient` music respectively.
  Set `"music": false` to disable a theme's music; `clean` has none by default.
- Tune it with `"music": {"generate": "lofi", "seed": 2, "bpm": 85, "volume": 0.3, "duck": 0.6}`.
  `seed` is a non-negative integer; `bpm` is 40–300. The same settings give the same track.
- `"music": {"src": "bgm.mp3", "volume": 0.2}` loops an existing audio file instead.
  Use exactly one of `generate` and `src`. `start` skips seconds at the beginning of a file.
- Music fades out at the end (`fadeOut`, default 1.5 s; `fadeIn`, default 0 s).
  `duck` smoothly lowers the music during voice segments: 0 = no reduction, 1 = mute,
  default 0.5 = half volume. This works for both generated music and files.
- `paibot-video music` lists the styles; `paibot-video music lofi --length 20 -o bgm.wav`
  writes a standalone track for listening.
