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
  "voice": "Kyoko",
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

- `narration` is spoken with the system voice (`say` on macOS, `espeak-ng` on
  Linux) or any command in `PAIBOT_VIDEO_TTS`, e.g. `mytts --text-file {text_file} --out {out}`.
  Set `voice` (e.g. `Kyoko` for Japanese, `Samantha` for English) and `rate`.
- `audio` plays a file you already have instead.
- `"music": {"src": "bgm.mp3", "volume": 0.2}` loops background music and fades it out at the end.
