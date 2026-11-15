"""Narration (text to speech), audio lengths and scene timing."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

from .spec import Issue, Spec

DEFAULT_SCENE = 3.0
TAIL = 0.6  # silence after the narration before the scene ends


class ToolMissing(Exception):
    pass


def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise ToolMissing(f"`{tool}` was not found on PATH; install FFmpeg (https://ffmpeg.org/download.html)")
    return path


def probe_duration(path: str) -> float:
    out = subprocess.run(
        [need("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"could not read audio file {path}: {out.stderr.strip()}")
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError) as e:
        raise RuntimeError(f"could not read the length of {path}") from e


@dataclass
class Caption:
    text: str
    start: float  # seconds into the scene
    end: float


@dataclass
class SceneTime:
    index: int
    start: float
    duration: float
    audio: str | None = None
    audio_start: float = 0.0
    audio_len: float = 0.0
    captions: list[Caption] = field(default_factory=list)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Timeline:
    scenes: list[SceneTime]
    sfx: list[tuple[float, str]] = field(default_factory=list)  # (time, "whoosh" | "pop")
    voice: str | None = None  # which TTS was used, for the report
    language: str | None = None

    @property
    def total(self) -> float:
        return self.scenes[-1].end if self.scenes else 0.0

    def at(self, t: float) -> tuple[int, float]:
        for s in self.scenes:
            if t < s.end:
                return s.index, max(0.0, t - s.start)
        last = self.scenes[-1]
        return last.index, max(0.0, min(t - last.start, last.duration - 1e-6))


_SENT = re.compile(r".+?(?:[。！？!?]+|\.(?=\s|$)|\n|$)", re.DOTALL)


_CLAUSE = re.compile(r".+?(?:[、，]|,(?!\d)|$)", re.DOTALL)


def _chop(part: str, max_len: int) -> list[str]:
    """Split a sentence at commas, then evenly, so no piece is longer than max_len."""
    if len(part) <= max_len:
        return [part]
    out: list[str] = []
    for clause in (c.strip() for c in _CLAUSE.findall(part)):
        if not clause:
            continue
        if out and len(out[-1]) + len(clause) <= max_len:
            out[-1] = out[-1] + ("" if re.search(r"[\u3000-\u9fff]$", out[-1]) else " ") + clause
        elif len(clause) <= max_len:
            out.append(clause)
        else:
            n = -(-len(clause) // max_len)
            if " " in clause:  # Latin: split between words
                words, cur = clause.split(), ""
                for w in words:
                    if cur and len(cur) + 1 + len(w) > len(clause) / n + 4:
                        out.append(cur)
                        cur = w
                    else:
                        cur = f"{cur} {w}".strip()
                out.append(cur)
            else:
                step = -(-len(clause) // n)
                out += [clause[i : i + step] for i in range(0, len(clause), step)]
    return out


def split_captions(text: str, start: float, length: float, max_len: int | None = None) -> list[Caption]:
    parts = [p.strip() for p in _SENT.findall(text) if p.strip()]
    if max_len:
        parts = [c for p in parts for c in _chop(p, max_len)]
    # Merge very short pieces into their neighbour so captions don't flicker.
    merged: list[str] = []
    for p in parts:
        if merged and (len(merged[-1]) < 8 or len(p) < 4) and (not max_len or len(merged[-1]) + len(p) <= max_len + 2):
            merged[-1] = merged[-1] + ("" if re.search(r"[　-鿿]$", merged[-1]) else " ") + p
        else:
            merged.append(p)
    if not merged:
        return []
    weights = [max(len(p), 1) for p in merged]
    total = sum(weights)
    caps, t = [], start
    for p, w in zip(merged, weights):
        d = length * w / total
        caps.append(Caption(p, t, t + d))
        t += d
    return caps


def _manim_clips(spec: Spec, cache_dir: str, issues: list[Issue]) -> None:
    """Render every `manim` element (or reuse the cache) and store the clip on it as `_clip`."""
    from .manim_scene import ALLOW_ENV, ManimError, allowed, default_size, render_clip

    els = [(sc, el) for sc in spec.scenes for el in sc["elements"] if el["type"] == "manim"]
    if not els:
        return
    W, H = spec.width, spec.height
    lx = lambda v, total=W: float(v.strip().rstrip("%")) / 100 * total if isinstance(v, str) else float(v)
    ly = lambda v: lx(v, H)
    family = None
    try:
        from PIL import ImageFont

        from .text import find_font

        family = ImageFont.truetype(find_font("regular", spec.top["font"]), 20).getname()[0]
    except Exception:
        pass
    for sc, el in els:
        el["_size"] = default_size(el["width"], el["height"], W, H, lx, ly)
        el["_clip"] = None
        if not allowed():
            issues.append(Issue("error", el["_path"], "a manim element runs its Python code, which needs permission",
                                f"run with --allow-code (or set {ALLOW_ENV}=1) if you trust this spec"))
            continue
        try:
            el["_clip"] = render_clip(el["code"], el["scene"], *el["_size"], spec.fps, color=spec.top["color"],
                                      accent=spec.top["accent"], font=family, cache_dir=cache_dir)
        except ManimError as e:
            field_ = "file" if el["file"] and e.field == "code" else e.field
            issues.append(Issue("error", f"{el['_path']}.{field_}", str(e), e.hint))


def plan(spec: Spec, *, tts_command: str | None = None, cache_dir: str | None = None, synth: bool = True) -> tuple[Timeline, list[Issue]]:
    """Work out every scene's length. With synth=False narration length is estimated."""
    from .text import reading_seconds

    issues: list[Issue] = []
    top = spec.top
    cache_dir = cache_dir or os.path.join(spec.base_dir or ".", ".paibot-video-cache")
    from .text import is_cjk
    from .tts import TTSError, choose, narration_language

    _manim_clips(spec, cache_dir, issues)
    backend, backend_error = None, None
    has_narration = any(sc["narration"] for sc in spec.scenes)
    cjk = any(sc["narration"] and is_cjk(sc["narration"]) for sc in spec.scenes)
    language = narration_language(top, cjk) if has_narration else None
    if synth and has_narration:
        try:
            backend = choose(top, cjk, tts_command)
        except TTSError as e:
            backend_error = e
    scenes: list[SceneTime] = []
    t = 0.0
    for i, sc in enumerate(spec.scenes):
        audio, alen = None, 0.0
        if sc["narration"]:
            if synth:
                if backend_error is not None:
                    if not any(i.message == str(backend_error) for i in issues):
                        issues.append(Issue("error", "$.voice" if "voice" in str(backend_error) else f"{sc['_path']}.narration",
                                            str(backend_error), backend_error.hint))
                else:
                    try:
                        audio = backend.synth(sc["narration"], cache_dir)
                        alen = probe_duration(audio)
                    except Exception as e:  # TTSError, ToolMissing, RuntimeError
                        issues.append(Issue("error", f"{sc['_path']}.narration", str(e), getattr(e, "hint", "")))
            if not audio:
                alen = reading_seconds(sc["narration"]) * 1.1
        elif sc["audio"]:
            audio = sc["audio"]
            try:
                alen = probe_duration(audio)
            except (RuntimeError, ToolMissing) as e:
                issues.append(Issue("error", f"{sc['_path']}.audio", str(e)))

        d = sc["duration"]
        lead = sc["narrationStart"]
        clips = [(el, el["start"] + el["_clip"].duration) for el in sc["elements"] if el["type"] == "manim" and el.get("_clip")]
        anim_end = max((end for _, end in clips), default=0.0)
        if d in (None, "auto"):
            d = lead + alen + TAIL if alen else DEFAULT_SCENE
            if anim_end:
                d = max(d if alen else 0.0, anim_end + TAIL)
        elif alen and lead + alen > d:
            issues.append(Issue("error", f"{sc['_path']}.duration",
                                f"the voice is {alen:.1f}s and starts at {lead:g}s, so it would be cut off at {d:g}s",
                                f'use "duration": "auto" or at least {lead + alen + TAIL:.1f}'))
        for el, end in clips:
            if end > d + 0.05:
                issues.append(Issue("warning", el["_path"], f"the animation ends at {end:.1f}s but the scene ends at {d:g}s, so its end is cut off",
                                    'use "duration": "auto", or shorten run_time / self.wait in the code'))
            elif alen and end - (lead + alen) > 2.0:
                issues.append(Issue("warning", el["_path"],
                                    f"the animation runs {end - (lead + alen):.1f}s after the narration ends ({end:.1f}s vs {lead + alen:.1f}s)",
                                    "add narration, or shorten run_time / self.wait in the code"))
        st = SceneTime(i, t, float(d), audio, lead, alen)
        if sc["narration"] and top["captions"]:
            cjk = is_cjk(sc["narration"])
            if top["captionStyle"] == "karaoke":
                max_len = 18 if cjk else 36
            else:
                max_len = 32 if cjk else 80
            st.captions = split_captions(sc["narration"], lead, alen, max_len)
        scenes.append(st)
        t += float(d)
    tl = Timeline(scenes)
    tl.voice = backend.label if backend is not None else None
    tl.language = language
    if top["sfx"]:
        for st, sc in zip(scenes, spec.scenes):
            trans = sc["transition"] or top["transition"]
            if st.index > 0 and trans != "none":
                tl.sfx.append((max(0.0, st.start - 0.12), "whoosh"))
            first = min((e for e in sc["elements"] if e["animate"] in ("pop", "words")), key=lambda e: e["start"], default=None)
            if first is not None:
                tl.sfx.append((st.start + first["start"] + 0.02, "pop"))
    return tl, issues


SFX = {
    # Filtered pink noise swelling and fading: a soft whoosh.
    "whoosh": ["-f", "lavfi", "-i", "anoisesrc=d=0.7:c=pink:a=0.5:seed=7",
               "-af", "bandpass=f=1600:w=1400,afade=t=in:st=0:d=0.32:curve=exp,afade=t=out:st=0.32:d=0.38,volume=1.6"],
    # Rising sine blip with a fast decay: a pop.
    "pop": ["-f", "lavfi", "-i", "aevalsrc=0.45*sin(2*PI*t*(420+2200*t))*exp(-22*t):d=0.22"],
}


def sfx_file(kind: str, cache_dir: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"sfx-{kind}-v1.wav")
    if not os.path.exists(out):
        tmp = out + ".part.wav"
        r = subprocess.run([need("ffmpeg"), "-v", "error", "-y", *SFX[kind], "-ar", "48000", "-ac", "2", tmp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"could not make the {kind} sound: {r.stderr.strip()}")
        os.replace(tmp, out)
    return out
