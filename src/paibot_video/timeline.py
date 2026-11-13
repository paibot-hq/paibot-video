"""Narration (text to speech), audio lengths and scene timing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
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


def default_tts_command(voice: str | None, rate: int | None) -> str | None:
    if sys.platform == "darwin" and shutil.which("say"):
        cmd = "say"
        if voice:
            cmd += " -v {voice}"
        if rate:
            cmd += " -r {rate}"
        return cmd + " --file-format=WAVE --data-format=LEI16@24000 -o {out} -f {text_file}"
    if shutil.which("espeak-ng"):
        return "espeak-ng" + (" -v {voice}" if voice else "") + (" -s {rate}" if rate else "") + " -w {out} -f {text_file}"
    return None


def synthesize(text: str, cache_dir: str, command: str, voice: str | None, rate: int | None) -> str:
    """Run the TTS command once per distinct (command, voice, text); results are cached."""
    key = hashlib.sha256(json.dumps([command, voice, rate, text]).encode()).hexdigest()[:20]
    os.makedirs(cache_dir, exist_ok=True)
    out = os.path.join(cache_dir, f"tts-{key}.wav")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    text_file = os.path.join(cache_dir, f"tts-{key}.txt")
    with open(text_file, "w", encoding="utf-8") as fh:
        fh.write(text)
    tmp = out + ".part.wav"
    subs = {"{text}": text, "{text_file}": text_file, "{out}": tmp, "{voice}": voice or "", "{rate}": str(rate or "")}
    args = []
    for a in shlex.split(command):
        for k, v in subs.items():
            a = a.replace(k, v)
        args.append(a)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise RuntimeError(f"text-to-speech failed ({args[0]}): {(r.stderr or r.stdout).strip() or 'no output file'}")
    os.replace(tmp, out)
    return out


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

    @property
    def total(self) -> float:
        return self.scenes[-1].end if self.scenes else 0.0

    def at(self, t: float) -> tuple[int, float]:
        for s in self.scenes:
            if t < s.end:
                return s.index, max(0.0, t - s.start)
        last = self.scenes[-1]
        return last.index, max(0.0, min(t - last.start, last.duration - 1e-6))


_SENT = re.compile(r"[^。！？!?\.\n]+[。！？!?\.]*\s*|\n")


def split_captions(text: str, start: float, length: float) -> list[Caption]:
    parts = [p.strip() for p in _SENT.findall(text) if p.strip()]
    # Merge very short pieces into their neighbour so captions don't flicker.
    merged: list[str] = []
    for p in parts:
        if merged and (len(merged[-1]) < 8 or len(p) < 4):
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


def plan(spec: Spec, *, tts_command: str | None = None, cache_dir: str | None = None, synth: bool = True) -> tuple[Timeline, list[Issue]]:
    """Work out every scene's length. With synth=False narration length is estimated."""
    from .text import reading_seconds

    issues: list[Issue] = []
    top = spec.top
    cache_dir = cache_dir or os.path.join(spec.base_dir or ".", ".paibot-video-cache")
    from .text import is_cjk

    voice = top["voice"]
    if not voice and not tts_command and not os.environ.get("PAIBOT_VIDEO_TTS") and sys.platform == "darwin":
        # The default macOS voice reads Japanese badly; pick a Japanese one.
        if any(sc["narration"] and is_cjk(sc["narration"]) for sc in spec.scenes):
            voice = "Kyoko"
    command = tts_command or os.environ.get("PAIBOT_VIDEO_TTS") or default_tts_command(voice, top["rate"])
    scenes: list[SceneTime] = []
    t = 0.0
    for i, sc in enumerate(spec.scenes):
        audio, alen = None, 0.0
        if sc["narration"]:
            if synth:
                if not command:
                    issues.append(Issue("error", f"{sc['_path']}.narration", "no text-to-speech command is available",
                                        "on macOS `say` is used automatically; elsewhere install espeak-ng or set PAIBOT_VIDEO_TTS, "
                                        "e.g. 'mytts --out {out} --text-file {text_file}'"))
                else:
                    try:
                        audio = synthesize(sc["narration"], cache_dir, command, voice, top["rate"])
                        alen = probe_duration(audio)
                    except (RuntimeError, ToolMissing) as e:
                        issues.append(Issue("error", f"{sc['_path']}.narration", str(e)))
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
        if d in (None, "auto"):
            d = lead + alen + TAIL if alen else DEFAULT_SCENE
        elif alen and lead + alen > d:
            issues.append(Issue("error", f"{sc['_path']}.duration",
                                f"the voice is {alen:.1f}s and starts at {lead:g}s, so it would be cut off at {d:g}s",
                                f'use "duration": "auto" or at least {lead + alen + TAIL:.1f}'))
        st = SceneTime(i, t, float(d), audio, lead, alen)
        if sc["narration"] and top["captions"]:
            st.captions = split_captions(sc["narration"], lead, alen)
        scenes.append(st)
        t += float(d)
    return Timeline(scenes), issues
