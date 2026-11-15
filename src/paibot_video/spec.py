"""Loading, validating and normalising a video spec.

The spec is plain JSON so that people and language models can both write it.
Validation never stops at the first problem: it collects every issue with a
JSON path and, where possible, a concrete fix.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import ImageColor

PRESETS = {
    "shorts": (1080, 1920),
    "landscape": (1920, 1080),
    "square": (1080, 1080),
    "portrait": (1080, 1350),
}
ANCHORS = ("center", "top", "bottom", "left", "right", "top-left", "top-right", "bottom-left", "bottom-right")
ANIMS_IN = ("fade", "pop", "words", "wipe", "blur", "slide-up", "slide-down", "slide-left", "slide-right", "zoom", "type", "none")
TRANSITIONS = ("fade", "slide", "wipe", "zoom", "flash", "glitch", "none")
CAMERAS = ("none", "push", "pull", "drift")
MUSIC_STYLES = ("synthwave", "pop", "lofi", "ambient")


@dataclass
class Issue:
    level: str  # "error" | "warning"
    path: str
    message: str
    hint: str = ""

    def as_dict(self) -> dict:
        d = {"level": self.level, "path": self.path, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d

    def __str__(self) -> str:
        s = f"{self.level}: {self.path}: {self.message}"
        return s + (f"\n  hint: {self.hint}" if self.hint else "")


class SpecError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


# -- field definitions ------------------------------------------------------

@dataclass
class F:
    kind: str  # str num int bool color len path choice strlist time any
    default: Any = None
    required: bool = False
    choices: tuple = ()
    doc: str = ""
    min: float | None = None
    max: float | None = None


COMMON = {
    "type": F("str", required=True, doc="Element type"),
    "id": F("str", doc="Optional name, used in messages"),
    "x": F("len", doc="Horizontal position in px or '50%'. Setting x or y takes the element out of the automatic stack."),
    "y": F("len", doc="Vertical position in px or '40%'"),
    "anchor": F("choice", "center", choices=ANCHORS, doc="Which point of the element sits at (x, y)"),
    "start": F("num", 0.0, min=0, doc="Seconds after the scene starts when the element appears"),
    "end": F("num", None, min=0, doc="Seconds after the scene starts when it disappears (default: scene end)"),
    "animate": F("choice", None, choices=ANIMS_IN, doc="Entrance animation"),
    "animDuration": F("num", 0.5, min=0, doc="Length of the entrance animation in seconds"),
    "out": F("choice", "none", choices=("fade", "none"), doc="Exit animation before `end`"),
    "opacity": F("num", 1.0, min=0, doc="0..1"),
    "glow": F("color", None, doc="Neon glow colour around the element"),
    "shadow": F("bool", False, doc="Soft drop shadow"),
}

ELEMENTS: dict[str, dict[str, F]] = {
    "text": {
        "text": F("str", required=True, doc="The text. Use \\n for a line break; long lines wrap automatically."),
        "size": F("num", 72, min=1, doc="Font size in px"),
        "color": F("color", None, doc="Text colour (default: spec `color`)"),
        "weight": F("choice", "bold", choices=("regular", "bold")),
        "align": F("choice", "center", choices=("left", "center", "right")),
        "maxWidth": F("len", "88%", doc="Wrap width"),
        "lineHeight": F("num", 1.3, min=0.5),
        "background": F("color", None, doc="Box behind the text"),
        "padding": F("num", 28, min=0),
        "radius": F("num", 28, min=0),
        "stroke": F("num", 0, min=0, doc="Outline width in px"),
        "strokeColor": F("color", "#000000"),
        "highlightColor": F("color", None, doc="Colour for **marked** words (default: spec `accent`)"),
    },
    "bullets": {
        "items": F("strlist", required=True, doc="One string per bullet"),
        "size": F("num", 56, min=1),
        "color": F("color", None),
        "bulletColor": F("color", None, doc="Default: spec `accent`"),
        "weight": F("choice", "regular", choices=("regular", "bold")),
        "maxWidth": F("len", "84%"),
        "gap": F("num", 28, min=0, doc="Space between bullets in px"),
        "stagger": F("num", 0.4, min=0, doc="Seconds between bullets appearing"),
        "lineHeight": F("num", 1.3, min=0.5),
    },
    "image": {
        "src": F("path", required=True, doc="Image file, relative to the spec file"),
        "width": F("len", None, doc="Width in px or %; height follows the aspect ratio unless also given"),
        "height": F("len", None),
        "fit": F("choice", "contain", choices=("contain", "cover"), doc="When both width and height are set"),
        "radius": F("num", 0, min=0),
    },
    "shape": {
        "shape": F("choice", "rect", choices=("rect", "circle")),
        "width": F("len", required=True),
        "height": F("len", required=True),
        "color": F("color", None, doc="Fill (default: spec `accent`)"),
        "radius": F("num", 0, min=0),
    },
    "code": {
        "code": F("str", required=True),
        "size": F("num", 44, min=1),
        "color": F("color", "#e5e7eb"),
        "background": F("color", "#0b1020"),
        "padding": F("num", 36, min=0),
        "radius": F("num", 24, min=0),
        "maxWidth": F("len", "88%", doc="Lines are not wrapped; a wider block is shrunk to fit"),
        "lineHeight": F("num", 1.45, min=0.5),
    },
    "counter": {
        "to": F("num", required=True, doc="Final number"),
        "from": F("num", 0),
        "decimals": F("int", 0, min=0),
        "prefix": F("str", ""),
        "suffix": F("str", ""),
        "separator": F("bool", True, doc="Thousands separator"),
        "duration": F("num", 1.2, min=0, doc="Seconds to count"),
        "size": F("num", 160, min=1),
        "color": F("color", None, doc="Default: spec `accent`"),
        "weight": F("choice", "bold", choices=("regular", "bold")),
        "stroke": F("num", 0, min=0),
        "strokeColor": F("color", "#000000"),
    },
    "bars": {
        "items": F("any", required=True, doc='[{"label": "A", "value": 3, "color": "#..."}]'),
        "width": F("len", "84%"),
        "barHeight": F("num", 56, min=4),
        "gap": F("num", 30, min=0),
        "size": F("num", 40, min=1, doc="Label font size"),
        "color": F("color", None, doc="Bar colour (default: spec `accent`)"),
        "textColor": F("color", None),
        "max": F("num", None, doc="Value of a full-width bar (default: largest value)"),
        "decimals": F("int", 0, min=0),
        "suffix": F("str", ""),
        "duration": F("num", 1.0, min=0, doc="Seconds for the bars to grow"),
        "stagger": F("num", 0.12, min=0),
    },
}

DEFAULT_ANIM = {"text": "pop", "bullets": "slide-up", "image": "fade", "shape": "fade", "code": "fade", "counter": "fade", "bars": "fade"}

SCENE = {
    "duration": F("any", None, doc='Seconds, or "auto" (default) to fit the narration; 3 s without narration'),
    "background": F("any", None, doc='Colour, {"gradient": [c1, c2], "angle": 90}, or {"image": "bg.jpg"}'),
    "elements": F("any", [], doc="Things on screen"),
    "narration": F("str", None, doc="Text read aloud by the TTS voice (also used for captions)"),
    "audio": F("path", None, doc="Pre-recorded voice/sound for this scene, played from narrationStart"),
    "narrationStart": F("num", 0.3, min=0, doc="Seconds into the scene before the voice starts"),
    "transition": F("choice", None, choices=TRANSITIONS, doc="How this scene enters"),
    "gap": F("num", 48, min=0, doc="Space between stacked elements in px"),
    "valign": F("choice", "center", choices=("top", "center", "bottom"), doc="Where the stack sits"),
    "camera": F("choice", None, choices=CAMERAS, doc="Slow camera move during the scene (default: spec `camera`)"),
    "note": F("str", None, doc="Free text, ignored by the renderer"),
}

TOP = {
    "$schema": F("str", None),
    "title": F("str", None),
    "theme": F("choice", "clean", choices=("clean", "neon", "pop", "cinematic"), doc="A ready-made look; any field you set yourself wins"),
    "preset": F("choice", "shorts", choices=tuple(PRESETS), doc="shorts 1080x1920, landscape 1920x1080, square, portrait 1080x1350"),
    "width": F("int", None, min=16),
    "height": F("int", None, min=16),
    "fps": F("int", 30, min=1),
    "background": F("any", "#0f172a"),
    "color": F("color", "#ffffff", doc="Default text colour"),
    "accent": F("color", "#38bdf8", doc="Default colour for bullets, counters, bars and shapes"),
    "font": F("path", None, doc="Regular font file (default: a system CJK font)"),
    "fontBold": F("path", None),
    "fontMono": F("path", None),
    "tts": F("choice", "auto", choices=("auto", "aivisspeech", "voicevox", "kokoro", "system", "command"),
             doc="Speech engine. auto: Japanese uses AivisSpeech, VOICEVOX, then the system voice; English uses say / espeak-ng"),
    "language": F("choice", "auto", choices=("auto", "ja", "en"),
                  doc="Narration language. auto: Japanese if the narration contains CJK characters, otherwise English. Applies to the whole video."),
    "voice": F("str", None, doc='Voice. Kokoro: "af_heart" (default), "am_michael", "bf_emma". AivisSpeech/VOICEVOX: "まお", "まお/おちつき" or a style id. say: "Kyoko", "Samantha"'),
    "speed": F("num", 1.0, min=0.5, doc="Speaking speed for AivisSpeech/VOICEVOX/Kokoro (1.0 = normal; Kokoro maximum 2.0)"),
    "intonation": F("num", 1.0, min=0, doc="Intonation strength for AivisSpeech/VOICEVOX (1.0 = normal)"),
    "rate": F("int", None, min=50, doc="Speaking rate for the system voice (words per minute for `say`)"),
    "captions": F("bool", False, doc="Show the narration as captions"),
    "captionStyle": F("choice", "box", choices=("box", "karaoke"), doc="box: subtitles on a dark box; karaoke: big outlined words lit up as they are spoken"),
    "captionColor": F("color", None, doc="Highlight colour for karaoke captions (default: accent)"),
    "camera": F("choice", "none", choices=CAMERAS, doc="Default slow camera move for every scene"),
    "grain": F("num", 0, min=0, doc="Film grain strength, 0..1"),
    "vignette": F("num", 0, min=0, doc="Darkened corners, 0..1"),
    "sfx": F("bool", False, doc="Whoosh on transitions and a pop when a scene's first element appears"),
    "music": F("any", None, doc='Generated: "synthwave" | "pop" | "lofi" | "ambient", or {"generate": "lofi", "seed": 2, "volume": 0.3}. '
                                 'A file: {"src": "bgm.mp3"}. false turns a theme\'s music off.'),
    "transition": F("choice", "fade", choices=TRANSITIONS, doc="Default scene transition"),
    "transitionDuration": F("num", 0.4, min=0),
    "scenes": F("any", required=True),
    "note": F("str", None),
}


# -- validation helpers -----------------------------------------------------

_LEN_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*%\s*$")


def _suggest(key: str, options) -> str:
    m = difflib.get_close_matches(key, list(options), n=1, cutoff=0.6)
    return f"did you mean `{m[0]}`?" if m else "allowed: " + ", ".join(sorted(options))


def parse_color(v: Any) -> tuple[int, int, int, int]:
    rgb = ImageColor.getrgb(v)
    return rgb if len(rgb) == 4 else (*rgb, 255)


def _check_value(v: Any, f: F, path: str, issues: list[Issue], base_dir: str) -> Any:
    def bad(msg, hint=""):
        issues.append(Issue("error", path, msg, hint))
        return f.default

    k = f.kind
    if k == "str":
        if not isinstance(v, str):
            return bad(f"expected a string, got {type(v).__name__}")
    elif k in ("num", "int", "time"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return bad(f"expected a number, got {json.dumps(v)}")
        if not math.isfinite(v):
            return bad("must be a finite number")
        if k == "int" and not float(v).is_integer():
            return bad(f"expected a whole number, got {v}")
        if f.min is not None and v < f.min:
            return bad(f"must be at least {f.min:g}, got {v}")
        if f.max is not None and v > f.max:
            return bad(f"must be at most {f.max:g}, got {v}")
        if k == "int":
            v = int(v)
    elif k == "bool":
        if not isinstance(v, bool):
            return bad(f"expected true or false, got {json.dumps(v)}")
    elif k == "color":
        if v is None:
            return None
        try:
            parse_color(v)
        except (ValueError, AttributeError, TypeError):
            return bad(f"not a colour: {json.dumps(v)}", 'use "#rrggbb", "#rrggbbaa", "rgb(…)" or a CSS name like "white"')
    elif k == "len":
        if isinstance(v, bool) or not (isinstance(v, (int, float)) or (isinstance(v, str) and _LEN_RE.match(v))):
            return bad(f"expected px (number) or a percentage like \"50%\", got {json.dumps(v)}")
    elif k == "choice":
        if v not in f.choices:
            return bad(f"{json.dumps(v)} is not allowed", _suggest(str(v), f.choices))
    elif k == "path":
        if not isinstance(v, str):
            return bad("expected a file path")
        full = v if os.path.isabs(v) else os.path.join(base_dir, v)
        if not os.path.exists(full):
            return bad(f"file not found: {v}", f"paths are relative to the spec file's folder ({base_dir or '.'})")
        return full
    elif k == "strlist":
        if not isinstance(v, list) or not v or not all(isinstance(s, str) for s in v):
            return bad("expected a non-empty list of strings")
    return v


def _check_obj(obj: Any, fields: dict[str, F], path: str, issues: list[Issue], base_dir: str) -> dict:
    if not isinstance(obj, dict):
        issues.append(Issue("error", path, f"expected an object, got {type(obj).__name__}"))
        return {k: f.default for k, f in fields.items()}
    out: dict[str, Any] = {}
    for key, val in obj.items():
        if key not in fields:
            issues.append(Issue("error", f"{path}.{key}", "unknown field", _suggest(key, fields)))
    for key, f in fields.items():
        if key in obj and obj[key] is not None:
            out[key] = _check_value(obj[key], f, f"{path}.{key}", issues, base_dir)
        else:
            if f.required:
                issues.append(Issue("error", f"{path}.{key}", "required field is missing", f.doc))
            out[key] = f.default
    return out


def _check_background(v: Any, path: str, issues: list[Issue], base_dir: str) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return {"color": _check_value(v, F("color"), path, issues, base_dir) or "#000000"}
    if isinstance(v, dict):
        if "gradient" in v:
            g = v["gradient"]
            extra = set(v) - {"gradient", "angle"}
            if extra:
                issues.append(Issue("error", f"{path}.{sorted(extra)[0]}", "unknown field", "a gradient takes `gradient` and `angle`"))
            if not (isinstance(g, list) and len(g) >= 2):
                issues.append(Issue("error", f"{path}.gradient", "expected a list of 2 or more colours"))
                return {"color": "#000000"}
            cols = [_check_value(c, F("color"), f"{path}.gradient[{i}]", issues, base_dir) or "#000000" for i, c in enumerate(g)]
            ang = v.get("angle", 90)
            if isinstance(ang, bool) or not isinstance(ang, (int, float)):
                issues.append(Issue("error", f"{path}.angle", "expected a number of degrees"))
                ang = 90
            return {"gradient": cols, "angle": ang}
        if "orbs" in v:
            extra = set(v) - {"orbs", "base", "speed"}
            if extra:
                issues.append(Issue("error", f"{path}.{sorted(extra)[0]}", "unknown field", "an orbs background takes `orbs`, `base` and `speed`"))
            o = v["orbs"]
            if not (isinstance(o, list) and 1 <= len(o) <= 8):
                issues.append(Issue("error", f"{path}.orbs", "expected a list of 1 to 8 colours"))
                return {"color": "#000000"}
            cols = [_check_value(c, F("color"), f"{path}.orbs[{i}]", issues, base_dir) or "#000000" for i, c in enumerate(o)]
            base = _check_value(v.get("base", "#05010f"), F("color"), f"{path}.base", issues, base_dir) or "#000000"
            speed = v.get("speed", 1.0)
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed < 0:
                issues.append(Issue("error", f"{path}.speed", "expected a number >= 0"))
                speed = 1.0
            return {"orbs": cols, "base": base, "speed": speed}
        if "image" in v:
            extra = set(v) - {"image", "dim"}
            if extra:
                issues.append(Issue("error", f"{path}.{sorted(extra)[0]}", "unknown field", "an image background takes `image` and `dim`"))
            src = _check_value(v["image"], F("path"), f"{path}.image", issues, base_dir)
            dim = v.get("dim", 0.0)
            if isinstance(dim, bool) or not isinstance(dim, (int, float)) or not 0 <= dim <= 1:
                issues.append(Issue("error", f"{path}.dim", "expected a number from 0 to 1"))
                dim = 0.0
            return {"image": src, "dim": dim}
    issues.append(Issue("error", path, "expected a colour, {\"gradient\": [...]}, {\"orbs\": [...]} or {\"image\": ...}"))
    return {"color": "#000000"}


MUSIC = {
    "generate": F("choice", None, choices=MUSIC_STYLES, doc="Compose a track in this style, exactly as long as the video"),
    "seed": F("int", 1, min=0, doc="Another seed gives another variation (key, chord order, melody)"),
    "bpm": F("num", None, min=40, max=300, doc="Tempo in BPM (default depends on the style)"),
    "src": F("path", None, doc="Use this audio file instead (looped)"),
    "volume": F("num", 0.3, min=0),
    "duck": F("num", 0.5, min=0, max=1, doc="How much the music drops while the voice speaks: 0 = not at all, 1 = silent"),
    "fadeOut": F("num", 1.5, min=0),
    "fadeIn": F("num", 0.0, min=0),
    "start": F("num", 0.0, min=0, doc="Skip this many seconds of a `src` file"),
}

THEMES: dict[str, dict] = {
    "clean": {"top": {}, "el": {}},
    "neon": {
        "top": {"background": {"orbs": ["#c026d3", "#0891b2", "#6d28d9", "#e11d48"], "base": "#05010f"},
                "accent": "#22d3ee", "color": "#ffffff", "transition": "glitch", "captionStyle": "karaoke",
                "grain": 0.35, "vignette": 0.6, "sfx": True, "camera": "push", "music": "synthwave"},
        "el": {"text": {"glow": "#d946ef", "animate": "words"}, "counter": {"glow": "#22d3ee"},
               "bars": {"glow": "#22d3ee"}, "code": {"background": "#0b0820e6", "glow": "#7c3aed"},
               "bullets": {"bulletColor": "#f0abfc"}},
    },
    "pop": {
        "top": {"background": {"orbs": ["#f97316", "#facc15", "#ec4899", "#8b5cf6"], "base": "#1e1b4b"},
                "accent": "#facc15", "color": "#ffffff", "transition": "slide", "captionStyle": "karaoke",
                "sfx": True, "camera": "drift", "music": "pop"},
        "el": {"text": {"stroke": 10, "strokeColor": "#111827", "animate": "words", "shadow": True},
               "counter": {"stroke": 10, "strokeColor": "#111827", "shadow": True},
               "bullets": {"shadow": True}, "code": {"shadow": True}, "image": {"shadow": True}},
    },
    "cinematic": {
        "top": {"background": {"gradient": ["#0c0a09", "#1c1917", "#0c0a09"], "angle": 180},
                "accent": "#fbbf24", "color": "#f5f5f4", "transition": "fade", "transitionDuration": 0.8,
                "grain": 0.6, "vignette": 0.85, "camera": "push", "music": "ambient"},
        "el": {"text": {"animate": "blur", "weight": "regular", "animDuration": 1.1},
               "bullets": {"animate": "slide-up", "animDuration": 0.8}, "image": {"animate": "zoom"},
               "counter": {"weight": "regular"}},
    },
}


@dataclass
class Spec:
    width: int
    height: int
    fps: int
    base_dir: str
    top: dict
    scenes: list[dict]
    path: str = ""
    raw: dict = field(default_factory=dict)


def validate(data: Any, base_dir: str = "", path: str = "") -> tuple[Spec | None, list[Issue]]:
    issues: list[Issue] = []
    theme = THEMES.get(data.get("theme", "clean"), THEMES["clean"]) if isinstance(data, dict) else THEMES["clean"]
    if isinstance(data, dict):
        data = {**theme["top"], **data}
    top = _check_obj(data, TOP, "$", issues, base_dir)
    if not isinstance(data, dict):
        return None, issues

    w, h = PRESETS.get(top["preset"], PRESETS["shorts"])
    if top["width"] or top["height"]:
        if not (top["width"] and top["height"]):
            issues.append(Issue("error", "$.width" if not top["width"] else "$.height", "set both width and height, or neither"))
        else:
            w, h = top["width"], top["height"]
    if w % 2 or h % 2:
        issues.append(Issue("error", "$.width", f"{w}x{h}: width and height must be even for MP4 encoding"))
    top["background"] = _check_background(top["background"], "$.background", issues, base_dir)

    music = top["music"]
    if music is False:
        top["music"] = None
    elif music is not None:
        if isinstance(music, str):
            music = {"generate": music} if music in MUSIC_STYLES else {"src": music}
        top["music"] = m = _check_obj(music, MUSIC, "$.music", issues, base_dir)
        if isinstance(music, dict) and (
            ("src" in music) == ("generate" in music) or not (m["src"] or m["generate"])
        ):
            issues.append(Issue("error", "$.music", "give either `generate` (a style) or `src` (a file)",
                                "styles: " + ", ".join(MUSIC_STYLES)))
        if m["src"] and not os.path.isfile(m["src"]):
            issues.append(Issue("error", "$.music.src", "expected an audio file, not a directory"))

    scenes_raw = data.get("scenes")
    scenes: list[dict] = []
    if not isinstance(scenes_raw, list) or not scenes_raw:
        if "scenes" in data:
            issues.append(Issue("error", "$.scenes", "expected a non-empty list of scenes"))
        return None, issues
    for i, s in enumerate(scenes_raw):
        sp = f"$.scenes[{i}]"
        sc = _check_obj(s, SCENE, sp, issues, base_dir)
        d = sc["duration"]
        if d is not None and d != "auto" and (isinstance(d, bool) or not isinstance(d, (int, float)) or d <= 0):
            issues.append(Issue("error", f"{sp}.duration", f'expected seconds (> 0) or "auto", got {json.dumps(d)}'))
            sc["duration"] = None
        if sc["narration"] is not None and sc["audio"] is not None:
            issues.append(Issue("error", sp, "use either `narration` (text to speech) or `audio` (a file), not both"))
        sc["background"] = _check_background(sc["background"], f"{sp}.background", issues, base_dir)
        els = sc["elements"]
        if not isinstance(els, list):
            issues.append(Issue("error", f"{sp}.elements", "expected a list"))
            els = []
        norm_els = []
        for j, e in enumerate(els):
            ep = f"{sp}.elements[{j}]"
            t = e.get("type") if isinstance(e, dict) else None
            if t not in ELEMENTS:
                if isinstance(e, dict) and t is None:
                    issues.append(Issue("error", f"{ep}.type", "required field is missing", "one of: " + ", ".join(ELEMENTS)))
                elif isinstance(e, dict):
                    issues.append(Issue("error", f"{ep}.type", f"unknown element type {json.dumps(t)}", _suggest(str(t), ELEMENTS)))
                else:
                    issues.append(Issue("error", ep, "expected an object"))
                continue
            fields = {**COMMON, **ELEMENTS[t]}
            themed = {k: v for k, v in theme["el"].get(t, {}).items() if k in fields}
            el = _check_obj({**themed, **e}, fields, ep, issues, base_dir)
            el["_path"] = ep
            if el["animate"] is None:
                el["animate"] = DEFAULT_ANIM[t]
            if el["animate"] == "words" and t not in ("text", "bullets"):
                el["animate"] = "pop"
            if el["animate"] == "type" and t not in ("text", "code"):
                issues.append(Issue("error", f"{ep}.animate", '"type" only works on text and code'))
                el["animate"] = "fade"
            if el["end"] is not None and el["end"] <= el["start"]:
                issues.append(Issue("error", f"{ep}.end", f"end ({el['end']}) must be after start ({el['start']})"))
                el["end"] = None
            if t == "bars":
                el["items"] = _check_bars(el["items"], f"{ep}.items", issues)
            norm_els.append(el)
        sc["elements"] = norm_els
        sc["_path"] = sp
        scenes.append(sc)

    if any(i.level == "error" for i in issues):
        return None, issues
    return Spec(w, h, top["fps"], base_dir, top, scenes, path, data), issues


def _check_bars(items: Any, path: str, issues: list[Issue]) -> list[dict]:
    if not isinstance(items, list) or not items:
        issues.append(Issue("error", path, 'expected a list like [{"label": "A", "value": 3}]'))
        return []
    out = []
    for i, it in enumerate(items):
        o = _check_obj(it, {"label": F("str", required=True), "value": F("num", required=True), "color": F("color", None)}, f"{path}[{i}]", issues, "")
        out.append(o)
    return out


def load(path: str) -> tuple[Spec | None, list[Issue]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, [Issue("error", "$", f"spec file not found: {path}")]
    except json.JSONDecodeError as e:
        return None, [Issue("error", "$", f"invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}",
                            "JSON does not allow comments or trailing commas; use a `note` field for comments")]
    return validate(data, os.path.dirname(os.path.abspath(path)), os.path.abspath(path))


# -- JSON Schema ------------------------------------------------------------

def _schema_for(f: F) -> dict:
    k = f.kind
    s: dict[str, Any]
    if k == "str":
        s = {"type": "string"}
    elif k in ("num", "time"):
        s = {"type": "number"}
    elif k == "int":
        s = {"type": "integer"}
    elif k == "bool":
        s = {"type": "boolean"}
    elif k == "color":
        s = {"type": "string", "description": "CSS-style colour"}
    elif k == "len":
        s = {"oneOf": [{"type": "number"}, {"type": "string", "pattern": r"^-?\d+(\.\d+)?%$"}]}
    elif k == "choice":
        s = {"enum": list(f.choices)}
    elif k == "path":
        s = {"type": "string", "description": "File path relative to the spec"}
    elif k == "strlist":
        s = {"type": "array", "items": {"type": "string"}, "minItems": 1}
    else:
        s = {}
    if f.min is not None and k in ("num", "int"):
        s["minimum"] = f.min
    if f.max is not None and k in ("num", "int"):
        s["maximum"] = f.max
    if f.default is not None and k != "any":
        s["default"] = f.default
    if f.doc:
        s["description"] = (s.get("description", "") + ". " if "description" in s else "") + f.doc
    return s


def _obj_schema(fields: dict[str, F], extra: dict | None = None) -> dict:
    props = {k: _schema_for(f) for k, f in fields.items()}
    props.update(extra or {})
    req = [k for k, f in fields.items() if f.required]
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


def json_schema() -> dict:
    color = {"type": "string"}
    bg = {"oneOf": [
        color,
        {"type": "object", "properties": {"gradient": {"type": "array", "items": color, "minItems": 2}, "angle": {"type": "number", "default": 90}}, "required": ["gradient"], "additionalProperties": False},
        {"type": "object", "properties": {"image": {"type": "string"}, "dim": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["image"], "additionalProperties": False},
        {"type": "object", "description": "Blurred colour orbs drifting slowly", "properties": {"orbs": {"type": "array", "items": color, "minItems": 1, "maxItems": 8}, "base": color, "speed": {"type": "number", "minimum": 0, "default": 1}}, "required": ["orbs"], "additionalProperties": False},
    ]}
    elements = []
    for t, fields in ELEMENTS.items():
        extra = {"type": {"const": t}}
        if t == "bars":
            extra["items"] = {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"label": {"type": "string"}, "value": {"type": "number"}, "color": color}, "required": ["label", "value"], "additionalProperties": False}}
        sch = _obj_schema({**COMMON, **fields}, extra)
        sch["title"] = t
        elements.append(sch)
    scene = _obj_schema(SCENE, {
        "duration": {"oneOf": [{"type": "number", "exclusiveMinimum": 0}, {"const": "auto"}], "description": SCENE["duration"].doc},
        "background": bg,
        "elements": {"type": "array", "items": {"oneOf": elements}},
    })
    music_obj = _obj_schema(MUSIC)
    music_obj["properties"]["src"]["minLength"] = 1
    music_obj["oneOf"] = [{"required": ["generate"]}, {"required": ["src"]}]
    music = {"oneOf": [
        {"type": "string", "minLength": 1,
         "description": "Generated style (" + ", ".join(MUSIC_STYLES) + ") or an audio file path"},
        {"const": False}, {"type": "null"}, music_obj,
    ]}
    top = _obj_schema(TOP, {"background": bg, "music": music, "scenes": {"type": "array", "minItems": 1, "items": scene}})
    top["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    top["title"] = "Paibot Video spec"
    top["properties"]["$schema"] = {"type": "string"}
    return top
