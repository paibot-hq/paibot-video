import json
import shutil
import subprocess

import pytest

from paibot_video.cli import main
from paibot_video.lint import lint
from paibot_video.render import Renderer, parse_marks, words_duration
from paibot_video.spec import validate
from paibot_video.timeline import _chop, plan, split_captions

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def build(data, synth=False):
    spec, issues = validate(data)
    assert spec is not None, [str(i) for i in issues]
    tl, _ = plan(spec, synth=synth)
    return Renderer(spec, tl), spec, tl


def small(scenes, **top):
    return {"width": 360, "height": 640, "fps": 10, **top, "scenes": scenes}


def test_parse_marks():
    assert parse_marks("a **bc** d") == ("a bc d", {2, 3})
    assert parse_marks("no marks") == ("no marks", set())
    assert parse_marks("**全部**")[1] == {0, 1}


def test_theme_fills_defaults_but_user_wins():
    spec, _ = validate(small([{"elements": [{"type": "text", "text": "a"}, {"type": "text", "text": "b", "glow": "#ffffff", "animate": "fade"}]}],
                             theme="neon", grain=0))
    a, b = spec.scenes[0]["elements"]
    assert a["glow"] == "#d946ef" and a["animate"] == "words"
    assert b["glow"] == "#ffffff" and b["animate"] == "fade"
    assert spec.top["transition"] == "glitch" and spec.top["grain"] == 0
    assert "orbs" in spec.top["background"]


def test_unknown_theme_is_an_error():
    spec, issues = validate(small([{}], theme="neom"))
    assert spec is None and any("did you mean `neon`" in i.hint for i in issues)


def test_orbs_background_moves():
    r, *_ = build(small([{"duration": 4}], background={"orbs": ["#ff0000", "#0000ff"], "base": "#000000"}))
    assert r.frame(0).tobytes() != r.frame(2).tobytes()


def test_words_animation_builds_up():
    r, *_ = build(small([{"duration": 3, "elements": [{"type": "text", "text": "one two three four", "animate": "words", "size": 40}]}]))
    n = len(r._text_units(r.scenes[0][0]))
    assert n == 4
    early, done = r.frame(0.05), r.frame(words_duration(n) + 0.1)
    later = r.frame(2.5)
    assert early.tobytes() != done.tobytes()
    assert done.tobytes() == later.tobytes()  # settled and cached


@pytest.mark.parametrize("kind", ["flash", "glitch", "slide", "wipe", "zoom", "fade"])
def test_transitions_render(kind):
    r, _, tl = build(small([{"duration": 1, "background": "#ff0000"}, {"duration": 1, "background": "#0000ff", "transition": kind}],
                           transitionDuration=0.4))
    mid = r.frame(tl.scenes[1].start + 0.2)
    assert mid.size == (360, 640)
    assert mid.tobytes() != r.frame(tl.scenes[1].start + 0.6).tobytes()


@pytest.mark.parametrize("mode", ["push", "pull", "drift"])
def test_camera_moves(mode):
    r, *_ = build(small([{"duration": 2, "camera": mode, "elements": [{"type": "shape", "width": 100, "height": 100, "animate": "none"}]}]))
    assert r.frame(0.1).tobytes() != r.frame(1.9).tobytes()


def test_effects_render_without_errors():
    data = small([{"duration": 2, "elements": [
        {"type": "text", "text": "**glow** text", "glow": "#00ffff", "stroke": 4, "animate": "blur"},
        {"type": "counter", "to": 10, "stroke": 3, "shadow": True},
        {"type": "text", "text": "wipe", "animate": "wipe", "shadow": True},
    ]}], grain=0.5, vignette=0.7)
    r, *_ = build(data)
    for t in (0.1, 0.3, 1.0, 1.9):
        r.frame(t)


def test_karaoke_captions_are_short_and_light_up():
    text = "たとえば、1から5の二乗のリストを作るとき、ふつうはこう書きます。"
    caps = split_captions(text, 0.3, 5.0, 18)
    assert all(len(c.text) <= 18 for c in caps)
    assert "".join(c.text for c in caps) == text
    r, _, tl = build(small([{"narration": "hello there friends", "duration": 4}], captions=True, captionStyle="karaoke"))
    c = tl.scenes[0].captions[0]
    assert r.frame(c.start + 0.01).tobytes() != r.frame(c.end - 0.05).tobytes()


def test_chop_latin_and_cjk():
    assert _chop("short", 10) == ["short"]
    out = _chop("one two three four five six seven eight nine ten", 20)
    assert all(len(x) <= 24 for x in out) and " ".join(out).split() == "one two three four five six seven eight nine ten".split()


def test_sfx_events_follow_scenes():
    _, _, tl = build(small([{"elements": [{"type": "text", "text": "a", "start": 0.5}]}, {"transition": "fade"}, {"transition": "none"}], sfx=True))
    kinds = [(round(t, 2), k) for t, k in tl.sfx]
    assert (0.52, "pop") in kinds
    assert sum(1 for _, k in kinds if k == "whoosh") == 1  # the "none" transition gets no whoosh


def test_outline_skips_contrast_warning():
    data = small([{"background": "#ffff00", "elements": [{"type": "text", "text": "x", "color": "#ffffff", "stroke": 6, "strokeColor": "#000000"}]}])
    r, *_ = build(data)
    assert not [i for i in lint(r) if i.path.endswith(".color")]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_render_with_theme_and_sfx(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(small([{"duration": 1, "elements": [{"type": "text", "text": "hi"}]}, {"duration": 1}], theme="neon")))
    out = tmp_path / "o.mp4"
    assert main(["render", str(p), "-o", str(out), "-q"]) == 0
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(out)],
                          capture_output=True, text=True, check=True)
    assert {s["codec_type"] for s in json.loads(info.stdout)["streams"]} == {"video", "audio"}
