import json
import os
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageFont

from paibot_video.cli import EXAMPLE, main
from paibot_video.lint import contrast, lint
from paibot_video.render import Renderer
from paibot_video.spec import json_schema, validate
from paibot_video.text import find_font, wrap, wrap_balanced
from paibot_video.timeline import plan, split_captions

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def mk(scenes, **top):
    return {"preset": "square", "width": 540, "height": 540, **top, "scenes": scenes} if top.pop("_small", True) else {**top, "scenes": scenes}


def renderer(data, base=""):
    spec, issues = validate(data, base)
    assert spec is not None, [str(i) for i in issues]
    tl, _ = plan(spec, synth=False)
    return Renderer(spec, tl), issues


def paths(issues, level=None):
    return [i.path for i in issues if level is None or i.level == level]


# -- spec ---------------------------------------------------------------------

def test_unknown_field_gets_a_suggestion():
    spec, issues = validate({"scenes": [{"elements": [{"type": "text", "txt": "hi"}]}]})
    assert spec is None
    by_path = {i.path: i for i in issues}
    assert "did you mean `text`" in by_path["$.scenes[0].elements[0].txt"].hint
    assert "$.scenes[0].elements[0].text" in by_path  # required and missing


def test_bad_values_are_all_reported():
    data = {"fps": "30", "scenes": [{"duration": -1, "elements": [
        {"type": "text", "text": "a", "color": "notacolour", "animate": "bounce"},
        {"type": "image", "src": "missing.png"},
        {"type": "sparkle"},
    ]}]}
    spec, issues = validate(data, "/nonexistent")
    got = paths(issues, "error")
    for p in ["$.fps", "$.scenes[0].duration", "$.scenes[0].elements[0].color",
              "$.scenes[0].elements[0].animate", "$.scenes[0].elements[1].src", "$.scenes[0].elements[2].type"]:
        assert p in got, p


def test_defaults_and_presets():
    spec, issues = validate({"preset": "landscape", "scenes": [{"elements": [{"type": "text", "text": "x"}]}]})
    assert not issues
    assert (spec.width, spec.height, spec.fps) == (1920, 1080, 30)
    el = spec.scenes[0]["elements"][0]
    assert el["size"] == 72 and el["animate"] == "pop" and el["weight"] == "bold"


def test_schema_is_json_and_covers_elements():
    s = json.loads(json.dumps(json_schema()))
    els = s["properties"]["scenes"]["items"]["properties"]["elements"]["items"]["oneOf"]
    assert {e["title"] for e in els} == {"text", "bullets", "image", "shape", "code", "counter", "bars"}


def test_example_spec_is_valid():
    spec, issues = validate(EXAMPLE)
    assert spec is not None and not [i for i in issues if i.level == "error"]


# -- text -----------------------------------------------------------------------

def test_wrap_japanese_kinsoku_and_english_words():
    f = ImageFont.truetype(find_font("regular"), 40)
    lines = wrap("これはテストの文章です。とても長い文章を折り返します。", f, 400)
    assert len(lines) > 1
    assert all(not ln.startswith(("。", "、")) for ln in lines)
    en = wrap("the quick brown fox jumps over the lazy dog", f, 300)
    assert all(not ln.startswith(" ") for ln in en)
    assert " ".join(en) == "the quick brown fox jumps over the lazy dog"


def test_balanced_wrap_has_no_orphan():
    f = ImageFont.truetype(find_font("bold"), 50)
    lines = wrap_balanced("JSONを書くだけで、動画ができます。", f, 820)
    assert len(lines) == 2 and min(len(l) for l in lines) >= 5


def test_caption_split_covers_the_voice():
    caps = split_captions("一つ目の文です。二つ目の文です！三。", 0.3, 4.0)
    assert caps[0].start == pytest.approx(0.3)
    assert caps[-1].end == pytest.approx(4.3)
    assert len(caps) == 2  # the short last piece was merged


# -- layout & lint ----------------------------------------------------------------

def test_stack_is_centered_and_ordered():
    r, _ = renderer(mk([{"elements": [{"type": "text", "text": "A", "size": 60}, {"type": "text", "text": "B", "size": 40}]}]))
    a, b = r.scenes[0]
    assert a.y < b.y
    assert abs((a.x + a.w / 2) - r.W / 2) < 1


def test_lint_overflow_contrast_and_offscreen():
    data = mk([
        {"duration": 2, "elements": [{"type": "text", "text": "長い文章 " * 30, "size": 90}]},
        {"background": "#ffffff", "elements": [{"type": "text", "text": "white on white", "color": "#f8f8f8"}]},
        {"elements": [{"type": "text", "text": "edge", "x": 530, "y": 270}]},
    ])
    r, _ = renderer(data)
    issues = lint(r)
    msgs = {(i.path, i.level) for i in issues}
    assert ("$.scenes[0].elements", "error") in msgs
    assert ("$.scenes[1].elements[0].color", "warning") in msgs
    assert ("$.scenes[2].elements[0]", "error") in msgs


def test_lint_overlap_and_reading_time():
    data = mk([{"duration": 1, "elements": [
        {"type": "text", "text": "読むのに時間がかかる文章をここに書いています", "x": "50%", "y": "50%", "size": 40},
        {"type": "text", "text": "重なる", "x": "50%", "y": "50%", "size": 40},
    ]}])
    r, _ = renderer(data)
    issues = lint(r)
    text = " ".join(i.message for i in issues)
    assert "overlaps" in text and "to read" in text


def test_contrast_ratio():
    assert contrast((255, 255, 255, 255), (0, 0, 0, 255)) == pytest.approx(21, rel=0.01)


def test_narration_too_long_for_fixed_duration():
    spec, _ = validate(mk([{"duration": 1, "narration": "これはとても長いナレーションなので一秒には入りません。"}]))
    _, issues = plan(spec, synth=False)
    assert any(i.path == "$.scenes[0].duration" and "cut off" in i.message for i in issues)


def test_auto_duration_from_estimated_narration():
    spec, _ = validate(mk([{"narration": "短い"}, {}]))
    tl, _ = plan(spec, synth=False)
    assert tl.scenes[0].duration > 0.3 and tl.scenes[1].duration == 3.0
    assert tl.scenes[1].start == pytest.approx(tl.scenes[0].duration)


# -- rendering ----------------------------------------------------------------------

def test_every_element_type_renders(tmp_path):
    img = tmp_path / "pic.png"
    Image.new("RGB", (64, 32), (200, 40, 40)).save(img)
    data = mk([
        {"background": {"gradient": ["#000000", "#ffffff"], "angle": 180}, "elements": [
            {"type": "text", "text": "Hello", "background": "#00000088"},
            {"type": "bullets", "items": ["one", "two"]},
            {"type": "image", "src": "pic.png", "width": 100, "radius": 8, "animate": "zoom"},
        ]},
        {"transition": "slide", "background": {"image": "pic.png", "dim": 0.5}, "elements": [
            {"type": "shape", "shape": "circle", "width": 80, "height": 80, "x": 60, "y": 60},
            {"type": "code", "code": "print('hi')", "animate": "type"},
            {"type": "counter", "to": 99},
            {"type": "bars", "items": [{"label": "a", "value": 1}, {"label": "b", "value": 2}]},
        ]},
    ])
    r, _ = renderer(data, str(tmp_path))
    top = r.frame(0.0).getpixel((5, 5))
    bottom = r.frame(0.0).getpixel((5, r.H - 5))
    assert sum(top) < sum(bottom)  # gradient goes dark -> light downwards
    for t in [0.05, 0.3, 1.5, 2.9, 3.1, 3.5, 5.9]:
        f = r.frame(t)
        assert f.size == (540, 540) and f.mode == "RGB"
    assert r.frame(1.5).tobytes() != r.frame(0.0).tobytes()


def test_scale_renders_smaller():
    spec, _ = validate(mk([{"elements": [{"type": "text", "text": "x"}]}]))
    tl, _ = plan(spec, synth=False)
    assert Renderer(spec, tl, scale=0.5).frame(1).size == (270, 270)


# -- CLI ------------------------------------------------------------------------------

def write(tmp_path, data):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(data, ensure_ascii=False))
    return str(p)


def test_cli_check_json(tmp_path, capsys):
    p = write(tmp_path, mk([{"elements": [{"type": "text", "text": "ok"}]}, {"elements": [{"type": "txt"}]}]))
    assert main(["check", p, "--json", "--no-tts"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["issues"][0]["path"] == "$.scenes[1].elements[0].type"


def test_cli_check_ok_with_sheet(tmp_path, capsys):
    p = write(tmp_path, mk([{"elements": [{"type": "text", "text": "one"}]}, {"elements": [{"type": "text", "text": "two"}]}]))
    sheet = tmp_path / "s.png"
    assert main(["check", p, "--json", "--no-tts", "--sheet", str(sheet)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["summary"]["duration"] == 6.0
    assert Image.open(sheet).width > 0


def test_cli_invalid_json_message(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text('{"scenes": [], }')
    assert main(["check", str(p)]) == 1
    assert "trailing commas" in capsys.readouterr().err


def test_cli_schema_guide_init(tmp_path, capsys, monkeypatch):
    assert main(["schema"]) == 0
    json.loads(capsys.readouterr().out)
    assert main(["guide"]) == 0
    assert "paibot-video check" in capsys.readouterr().out
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert json.loads((tmp_path / "video.json").read_text())["scenes"]
    assert main(["init"]) == 1  # does not overwrite


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_cli_render_with_audio(tmp_path):
    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", str(tone)], check=True)
    p = write(tmp_path, mk([
        {"audio": "tone.wav", "elements": [{"type": "text", "text": "beep"}]},
        {"duration": 1, "elements": [{"type": "counter", "to": 5}]},
    ], music={"src": "tone.wav", "volume": 0.1}))
    out = tmp_path / "out.mp4"
    assert main(["render", p, "-o", str(out), "--quiet", "--fps", "10"]) == 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-show_entries",
                            "format=duration", "-of", "json", str(out)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    kinds = {s["codec_type"] for s in info["streams"]}
    assert kinds == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(0.3 + 1.2 + 0.6 + 1.0, abs=0.15)


def test_cli_render_refuses_on_errors(tmp_path, capsys):
    p = write(tmp_path, mk([{"elements": [{"type": "text", "text": "x", "x": 2000}]}]))
    assert main(["render", p, "-o", str(tmp_path / "o.mp4"), "--quiet"]) == 1
    assert "not rendering" in capsys.readouterr().err
