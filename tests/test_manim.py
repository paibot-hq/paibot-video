import shutil

import pytest

from paibot_video.lint import lint
from paibot_video.manim_scene import ALLOW_ENV, ManimError, available, default_size, pick_scene, preamble
from paibot_video.render import Renderer
from paibot_video.spec import json_schema, validate
from paibot_video.timeline import plan

SCENE = "class Main(Scene):\n    def construct(self):\n        self.play(Create(Circle(radius=1.5, color=PV_ACCENT)), run_time=1)\n"
CAN_RENDER = available() and shutil.which("ffmpeg") is not None


def mk(el, **scene):
    return {"preset": "square", "width": 320, "height": 320, "scenes": [{**scene, "elements": [{"type": "manim", **el}]}]}


def test_code_or_file_is_required():
    spec, issues = validate(mk({}))
    assert spec is None
    assert any(i.path == "$.scenes[0].elements[0]" and "one of the two" in i.message for i in issues)


def test_scene_class_is_found_or_asked_for():
    assert pick_scene(SCENE, None) == "Main"
    two = SCENE + "class Other(MovingCameraScene):\n    pass\n"
    with pytest.raises(ManimError) as e:
        pick_scene(two, None)
    assert e.value.field == "scene" and "Main, Other" in str(e.value)
    assert pick_scene(two, "Other") == "Other"
    with pytest.raises(ManimError):
        pick_scene("print('hi')", None)


def test_file_is_read_relative_to_the_spec(tmp_path):
    (tmp_path / "s.py").write_text(SCENE)
    spec, issues = validate(mk({"file": "s.py"}), str(tmp_path))
    assert spec is not None, [str(i) for i in issues]
    assert spec.scenes[0]["elements"][0]["code"] == SCENE


def test_preamble_gives_theme_and_aspect():
    head = preamble("#ffffff", "#22d3ee", "Hiragino Sans")
    assert "from manim import *" in head and "PV_ACCENT = '#22d3ee'" in head
    assert "config.frame_width = config.frame_height * config.pixel_width / config.pixel_height" in head
    assert "font=PV_FONT" in head
    assert "font=" not in preamble("#fff", "#000", None).split("PV_FONT")[-1]


def test_default_size():
    lx = lambda v, total=1080: float(v.rstrip("%")) / 100 * total if isinstance(v, str) else v
    ly = lambda v: lx(v, 1920)
    assert default_size(None, None, 1080, 1920, lx, ly) == (1080, 1080)
    assert default_size(None, 700, 1080, 1920, lx, ly) == (1080, 700)
    assert default_size("50%", None, 1080, 1920, lx, ly) == (540, 540)
    assert default_size(None, None, 1920, 1080, lx, ly) == (1920, 1080)


def test_code_needs_permission(monkeypatch):
    monkeypatch.delenv(ALLOW_ENV, raising=False)
    spec, _ = validate(mk({"code": SCENE}))
    tl, issues = plan(spec, synth=False)
    assert any(i.level == "error" and "--allow-code" in i.hint for i in issues)
    # Without permission the scene still lays out, with a placeholder.
    r = Renderer(spec, tl)
    assert r.frame(0.5).size == (320, 320)


def test_schema_lists_manim():
    titles = [e["title"] for e in json_schema()["properties"]["scenes"]["items"]["properties"]["elements"]["items"]["oneOf"]]
    assert "manim" in titles


@pytest.mark.skipif(not CAN_RENDER, reason="needs manim and ffmpeg")
def test_render_fits_scene_and_reports_errors(monkeypatch, tmp_path):
    monkeypatch.setenv(ALLOW_ENV, "1")
    spec, _ = validate(mk({"code": SCENE}))
    tl, issues = plan(spec, synth=False, cache_dir=str(tmp_path))
    assert not [i for i in issues if i.level == "error"], [str(i) for i in issues]
    clip = spec.scenes[0]["elements"][0]["_clip"]
    assert clip.width == 320 and abs(clip.duration - 1.0) < 0.1
    assert tl.scenes[0].duration == pytest.approx(clip.duration + 0.6)
    r = Renderer(spec, tl)
    early, late = r.frame(0.1), r.frame(0.95)
    assert early.tobytes() != late.tobytes()
    assert not [i for i in lint(r) if "cut off" in i.message]

    # Cached: a second plan reuses the frames.
    spec2, _ = validate(mk({"code": SCENE}))
    plan(spec2, synth=False, cache_dir=str(tmp_path))
    assert spec2.scenes[0]["elements"][0]["_clip"].frames == clip.frames

    # A typo is reported with the line in the user's code.
    bad = SCENE.replace("Circle(", "Circel(")
    spec3, _ = validate(mk({"code": bad}))
    _, issues = plan(spec3, synth=False, cache_dir=str(tmp_path))
    err = next(i for i in issues if i.level == "error")
    assert err.path == "$.scenes[0].elements[0].code" and "line 3" in err.message and "Circel" in err.message


@pytest.mark.skipif(not CAN_RENDER, reason="needs manim and ffmpeg")
def test_drawing_cut_off_at_the_edge_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv(ALLOW_ENV, "1")
    big = "class Main(Scene):\n    def construct(self):\n        self.play(FadeIn(Square(side_length=12, fill_opacity=1)), run_time=0.5)\n"
    spec, _ = validate(mk({"code": big}))
    tl, _ = plan(spec, synth=False, cache_dir=str(tmp_path))
    issues = lint(Renderer(spec, tl))
    assert any(i.path.endswith(".code") and "cut off" in i.message for i in issues)
