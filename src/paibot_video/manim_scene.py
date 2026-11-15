"""Scenes drawn by Manim (https://www.manim.community/).

A `manim` element holds the Python code of a Manim scene. It is rendered once
with a transparent background at the element's size and the video's frame
rate, split into PNG frames and cached, so the renderer can lay one frame per
video frame over the theme's background. The code is run as a normal Python
program, so it only runs when the caller allows it (`--allow-code`).
"""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

ALLOW_ENV = "PAIBOT_VIDEO_ALLOW_CODE"
INSTALL_HINT = "install it with `pip install 'paibot-video[manim]'` (MathTex and Tex also need LaTeX)"

_SCENE_RE = re.compile(r"^class\s+([A-Za-z_]\w*)\s*\(\s*(?:\w+\.)*\w*Scene\s*\)\s*:", re.M)


class ManimError(Exception):
    def __init__(self, message: str, hint: str = "", field: str = "code"):
        super().__init__(message)
        self.hint = hint
        self.field = field


@dataclass
class Clip:
    frames: list[str]
    fps: int
    width: int
    height: int

    @property
    def duration(self) -> float:
        return len(self.frames) / self.fps

    def content(self, step: float = 0.2) -> dict:
        """Where the drawing is, from frames sampled every `step` seconds and the last one.

        Returns {"box": (x0, y0, x1, y1) | None, "cut": (seconds, side) | None}: the union of
        the drawn pixels, and the first moment something touches the edge of the clip.
        """
        if getattr(self, "_content", None) is not None:
            return self._content
        from PIL import Image

        n = len(self.frames)
        idxs = sorted(set(list(range(0, n, max(1, int(step * self.fps)))) + [n - 1]))
        box, cut = None, None
        for i in idxs:
            with Image.open(self.frames[i]) as im:
                a = im.getchannel("A").point(lambda v: 255 if v > 24 else 0)
            b = a.getbbox()
            if not b:
                continue
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
            if cut is None:
                side = ("left" if b[0] <= 0 else "top" if b[1] <= 0 else
                        "right" if b[2] >= self.width else "bottom" if b[3] >= self.height else None)
                if side:
                    cut = (i / self.fps, side)
        self._content = {"box": box, "cut": cut}
        return self._content


def allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "") not in ("", "0", "false", "no")


def available() -> bool:
    return importlib.util.find_spec("manim") is not None


def scene_names(code: str) -> list[str]:
    return _SCENE_RE.findall(code)


def pick_scene(code: str, scene: str | None) -> str:
    names = scene_names(code)
    if scene:
        if scene not in names and names:
            raise ManimError(f"no scene class named {scene!r} in the code", "scenes found: " + ", ".join(names), "scene")
        return scene
    if len(names) == 1:
        return names[0]
    if not names:
        raise ManimError("the code defines no scene class",
                         "write `class Main(Scene):` with a `def construct(self):` that calls self.play(...)")
    raise ManimError(f"the code defines {len(names)} scene classes ({', '.join(names)})",
                     'set "scene" to the one to show', "scene")


def preamble(color: str, accent: str, font: str | None) -> str:
    """Prepended to the code: Manim's names plus the video's colours and font.

    Text, MathTex and Tex default to the video's text colour, and Text to its
    font (so Japanese works without `font=`). The code can use PV_COLOR,
    PV_ACCENT and PV_FONT itself. The frame keeps Manim's height of 8 units and
    takes its width from the element's shape, so a square element is 8 x 8.
    """
    lines = [
        "from manim import *",
        "config.frame_width = config.frame_height * config.pixel_width / config.pixel_height",
        f"PV_COLOR = {color!r}",
        f"PV_ACCENT = {accent!r}",
        f"PV_FONT = {font!r}",
        "for _cls in (Text, MarkupText, Paragraph):",
        "    _cls.set_default(color=PV_COLOR" + (", font=PV_FONT)" if font else ")"),
        "for _cls in (MathTex, Tex):",
        "    _cls.set_default(color=PV_COLOR)",
        "del _cls",
    ]
    return "\n".join(lines) + "\n"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("manim")
    except Exception:
        return "?"


def _explain(stderr: str, offset: int) -> tuple[str, str]:
    """The last error of a Manim run, with line numbers counted in the user's code."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", stderr)
    # Rich draws tracebacks in boxes; strip the box characters.
    text = re.sub(r"[│╭╮╰╯─]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    err = next((ln for ln in reversed(lines) if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", ln)), None)
    if err is None:
        err = lines[-1] if lines else "Manim failed without a message"
    where = ""
    if "no play calls" in text:
        return ("the scene has no self.play(...) calls, so there is no animation",
                "animate with self.play(...); to show a still drawing, self.add(...) it and then self.wait(2)")
    for m in re.finditer(r"pv_scene\.py:(\d+)|pv_scene\.py\", line (\d+)", text):
        n = int(m.group(1) or m.group(2)) - offset
        if n >= 1:
            where = f"line {n} of the code: "
    hint = ""
    if "latex" in err.lower() or "LaTeX" in text and "not" in err.lower():
        hint = "MathTex and Tex need LaTeX (TeX Live / MacTeX); use Text for plain words"
    elif "NameError" in err:
        hint = "everything from `from manim import *` is available; PV_COLOR, PV_ACCENT and PV_FONT are defined too"
    return where + err, hint


def render_clip(code: str, scene: str | None, width: int, height: int, fps: int, *,
                color: str, accent: str, font: str | None, cache_dir: str, timeout: float = 900) -> Clip:
    """Render (or reuse) the scene as transparent PNG frames."""
    name = pick_scene(code, scene)
    head = preamble(color, accent, font)
    source = head + code
    key = hashlib.sha256("\0".join([source, name, f"{width}x{height}@{fps}", _version()]).encode()).hexdigest()[:20]
    out = os.path.join(cache_dir, "manim", key)
    done = os.path.join(out, "done")
    if os.path.exists(done):
        frames = sorted(glob.glob(os.path.join(out, "f*.png")))
        if frames:
            return Clip(frames, fps, width, height)
    if not available():
        raise ManimError("Manim is not installed", INSTALL_HINT)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ManimError("FFmpeg is not on PATH", "install FFmpeg")
    shutil.rmtree(out, ignore_errors=True)
    work = os.path.join(out, "work")
    os.makedirs(work)
    with open(os.path.join(work, "pv_scene.py"), "w", encoding="utf-8") as fh:
        fh.write(source)
    cmd = [sys.executable, "-W", "ignore", "-m", "manim", "render", "pv_scene.py", name,
           "--format", "mov", "--transparent", "-r", f"{width},{height}", "--fps", str(fps),
           "--media_dir", "media", "--disable_caching", "--progress_bar", "none", "-v", "WARNING"]
    try:
        res = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ManimError(f"Manim took longer than {timeout:.0f}s", "shorten the animation or simplify it") from None
    movs = [p for p in glob.glob(os.path.join(work, "media", "videos", "**", f"{name}.mov"), recursive=True)
            if "partial_movie_files" not in p]
    if res.returncode != 0 or not movs:
        if res.returncode == 0:
            raise ManimError("the scene shows nothing over time",
                             "animate with self.play(...); to show a still drawing, self.add(...) it and then self.wait(2)")
        msg, hint = _explain(res.stderr + res.stdout, head.count("\n"))
        raise ManimError(msg, hint)
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", movs[0], os.path.join(out, "f%05d.png")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ManimError("could not read Manim's video: " + r.stderr.strip()[-300:])
    shutil.rmtree(work, ignore_errors=True)
    frames = sorted(glob.glob(os.path.join(out, "f*.png")))
    if not frames:
        raise ManimError("Manim produced no frames", "call self.play(...) or self.wait(...) in construct()")
    open(done, "w").close()
    return Clip(frames, fps, width, height)


def default_size(el_w, el_h, W: int, H: int, lx, ly) -> tuple[int, int]:
    """The element's size in output pixels: full frame on landscape, a square of the full width otherwise."""
    if el_w is None and el_h is None:
        w, h = (W, H) if W > H else (W, W)
    elif el_h is None:
        w = lx(el_w)
        h = w * (H / W if W > H else 1.0)
    elif el_w is None:
        w, h = W, ly(el_h)
    else:
        w, h = lx(el_w), ly(el_h)
    even = lambda v: max(2, int(round(v / 2)) * 2)
    return even(min(w, W)), even(min(h, H))
