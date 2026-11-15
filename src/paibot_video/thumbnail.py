"""The cover picture: a settled moment of the video, or a picture laid out like a scene of its own."""

from __future__ import annotations

import os

from PIL import Image

from .render import Renderer
from .spec import Issue
from .timeline import plan


def prepare(r: Renderer, *, scale: float = 1.0) -> tuple[Renderer | None, list[Issue]]:
    """Plan and lay out the thumbnail's own picture, if the spec has one (`thumbnail.elements`)."""
    th = r.spec.thumbnail
    if not th or th["spec"] is None:
        return None, []
    tl, issues = plan(th["spec"], synth=False)
    return Renderer(th["spec"], tl, scale=scale), issues


def moment(r: Renderer) -> float:
    """The time in the video the thumbnail shows (when it is not a picture of its own)."""
    th = r.spec.thumbnail
    st = r.tl.scenes[th["scene"]]
    at = th["at"] if th["at"] is not None else r.settle_time(th["scene"])
    return st.start + min(at, st.duration - 1e-3)


def image(r: Renderer, own: Renderer | None) -> Image.Image | None:
    if not r.spec.thumbnail:
        return None
    if own is not None:
        return own.frame(own.settle_time(0))
    return r.frame(min(moment(r), r.tl.total - 1e-3))


def default_path(video_or_spec: str) -> str:
    return os.path.splitext(video_or_spec)[0] + ".thumb.png"
