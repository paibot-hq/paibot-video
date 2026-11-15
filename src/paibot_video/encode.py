"""Pipe frames into FFmpeg and mix the audio."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Callable

from PIL import Image, ImageDraw

from .render import Renderer
from .text import font
import os

from .timeline import Timeline, need, sfx_file


def audio_inputs(r: Renderer, tl: Timeline) -> tuple[list[str], str | None]:
    """Extra ffmpeg inputs and a filter graph producing [aout]."""
    args: list[str] = []
    parts: list[str] = []
    labels: list[str] = []
    n = 1  # input 0 is the video pipe
    for st in tl.scenes:
        if not st.audio:
            continue
        args += ["-i", st.audio]
        ms = int(round((st.start + st.audio_start) * 1000))
        parts.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{n}]")
        labels.append(f"[v{n}]")
        n += 1
    cache = os.path.join(r.spec.base_dir or ".", ".paibot-video-cache")
    for at, kind in tl.sfx:
        args += ["-i", sfx_file(kind, cache)]
        ms = int(round(at * 1000))
        parts.append(f"[{n}:a]aformat=channel_layouts=stereo,volume=0.55,adelay={ms}|{ms}[s{n}]")
        labels.append(f"[s{n}]")
        n += 1
    music = r.spec.top["music"]
    if music:
        total = tl.total
        if music["generate"]:
            from .music import track

            args += ["-i", track(music["generate"], total, music["seed"], music["bpm"], cache)]
        else:
            args += ["-stream_loop", "-1", "-ss", f"{music['start']:g}", "-i", music["src"]]
        chain = f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},{duck_filter(tl, music['volume'], music['duck'])}"
        if music["fadeIn"] > 0:
            chain += f",afade=t=in:d={music['fadeIn']:g}"
        if music["fadeOut"] > 0:
            chain += f",afade=t=out:st={max(0.0, total - music['fadeOut']):.3f}:d={music['fadeOut']:g}"
        parts.append(chain + f"[m{n}]")
        labels.append(f"[m{n}]")
        n += 1
    if not labels:
        return args, None
    if len(labels) == 1:
        graph = ";".join(parts) + f";{labels[0]}apad[aout]"
    else:
        graph = ";".join(parts) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0,apad[aout]"
    return args, graph


def duck_filter(tl: Timeline, volume: float, duck: float, ramp: float = 0.35) -> str:
    """A volume filter that lowers the music while a voice is speaking."""
    spans = [(s.start + s.audio_start, s.start + s.audio_start + s.audio_len) for s in tl.scenes if s.audio]
    duck = min(max(duck, 0.0), 1.0)
    if not spans or duck == 0:
        return f"volume={volume:g}"
    terms = "+".join(f"clip(min((t-{a:.3f})/{ramp}+1,({b:.3f}-t)/{ramp}+1),0,1)" for a, b in spans)
    return f"volume=eval=frame:volume='{volume:g}*(1-{duck:g}*min(1,{terms}))'"


def render_video(r: Renderer, out: str, *, fps: int | None = None, crf: int = 20, preset: str = "medium",
                 progress: Callable[[int, int], None] | None = None) -> None:
    tl = r.tl
    fps = fps or r.spec.fps
    frames = max(1, int(round(tl.total * fps)))
    a_args, graph = audio_inputs(r, tl)
    cmd = [need("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{r.W}x{r.H}", "-r", str(fps), "-i", "-", *a_args]
    if graph:
        cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-map", "0:v"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", f"{frames / fps:.3f}", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for n in range(frames):
            img = r.frame(n / fps)
            proc.stdin.write(img.tobytes())
            if progress:
                progress(n + 1, frames)
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err.strip()}")


def contact_sheet(r: Renderer, cols: int | None = None, thumb: int = 360) -> Image.Image:
    """One image with a thumbnail of every scene, labelled with its number and time."""
    n = len(r.tl.scenes)
    portrait = r.H > r.W
    cols = cols or (min(n, 5) if portrait else min(n, 3))
    tw = thumb if portrait else int(thumb * 1.5)
    th = int(tw * r.H / r.W)
    rows = (n + cols - 1) // cols
    pad, label = 16, 34
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + label + pad) + pad), (24, 24, 27))
    dr = ImageDraw.Draw(sheet)
    f = font(r.fonts["bold"], 20)
    fr = font(r.fonts["regular"], 18)
    for i, st in enumerate(r.tl.scenes):
        t = st.start + r.settle_time(i)
        img = r.frame(t).resize((tw, th), Image.LANCZOS)
        x = pad + (i % cols) * (tw + pad)
        y = pad + (i // cols) * (th + label + pad)
        dr.text((x, y + 4), f"#{i + 1}", font=f, fill=(250, 250, 250))
        dr.text((x + 44, y + 6), f"{_mmss(st.start)}–{_mmss(st.end)}  ({st.duration:.1f}s)", font=fr, fill=(160, 160, 170))
        sheet.paste(img, (x, y + label))
    return sheet


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def bar_progress(stream=None) -> Callable[[int, int], None]:
    stream = stream or sys.stderr
    t0 = time.perf_counter()
    last = [0.0]

    def cb(done: int, total: int) -> None:
        now = time.perf_counter()
        if done != total and now - last[0] < 0.1:
            return
        last[0] = now
        frac = done / total
        fps = done / max(now - t0, 1e-6)
        eta = (total - done) / max(fps, 1e-6)
        filled = int(frac * 28)
        stream.write(f"\r  [{'█' * filled}{'·' * (28 - filled)}] {frac * 100:5.1f}%  {done}/{total} frames  {fps:5.1f} fps  ETA {eta:4.0f}s ")
        if done == total:
            stream.write("\n")
        stream.flush()

    return cb
