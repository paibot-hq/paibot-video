import json
import shutil
import subprocess

import pytest
from PIL import Image

from paibot_video import thumbnail as thumb
from paibot_video.cli import main
from paibot_video.encode import audio_inputs
from paibot_video.render import Renderer
from paibot_video.spec import json_schema, validate
from paibot_video.timeline import plan

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def spec_data(**top):
    return {"width": 270, "height": 480, "background": "#000000", **top, "scenes": [
        {"elements": [{"type": "text", "text": "one", "color": "#ff0000"}]},
        {"elements": [{"type": "text", "text": "two", "color": "#00ff00"}]},
    ]}


def build(data):
    spec, issues = validate(data)
    assert spec is not None, [str(i) for i in issues]
    tl, _ = plan(spec, synth=False)
    r = Renderer(spec, tl)
    r.thumb_own, _ = thumb.prepare(r)
    return r


def dominant(img):
    px = [p for p in img.convert("RGB").getdata() if max(p) > 128]
    return max(((sum(p[i] for p in px), i) for i in range(3)))[1] if px else None


def test_default_is_scene_one_and_false_turns_it_off():
    assert validate(spec_data())[0].thumbnail == {"scene": 0, "at": None, "intro": 0.0, "spec": None}
    assert validate(spec_data(thumbnail=False))[0].thumbnail is None
    assert dominant(thumb.image(build(spec_data()), None)) == 0  # red: scene 1


def test_a_chosen_scene():
    r = build(spec_data(thumbnail={"scene": 2}))
    assert dominant(thumb.image(r, r.thumb_own)) == 1  # green


def test_bad_thumbnails_are_reported():
    _, issues = validate(spec_data(thumbnail={"scene": 3}))
    assert any(i.path == "$.thumbnail.scene" for i in issues)
    _, issues = validate(spec_data(thumbnail={"scene": 1, "elements": [{"type": "text", "text": "x"}]}))
    assert any(i.path == "$.thumbnail" and i.level == "error" for i in issues)
    _, issues = validate(spec_data(thumbnail={"elements": [{"type": "txt"}]}))
    assert any(i.path == "$.thumbnail.elements[0].type" for i in issues)
    _, issues = validate(spec_data(thumbnail="yes"))
    assert any(i.path == "$.thumbnail" for i in issues)


def test_a_picture_of_its_own_uses_the_videos_look():
    r = build(spec_data(watermark={"text": "MARK"}, thumbnail={"elements": [{"type": "text", "text": "cover", "color": "#0000ff", "size": 80}]}))
    img = thumb.image(r, r.thumb_own)
    assert img.size == (270, 480) and dominant(img) == 2  # blue
    assert r.thumb_own.spec.top["captions"] is False and r.thumb_own.spec.top["music"] is None


def test_intro_delays_every_sound():
    r = build(spec_data(music="lofi"))
    _, graph = audio_inputs(r, r.tl, 0.5)
    assert "adelay=500:all=1,apad[aout]" in graph
    _, graph = audio_inputs(r, r.tl)
    assert "adelay=500:all=1" not in graph


def test_schema_has_thumbnail():
    sch = json_schema()["properties"]["thumbnail"]
    assert {"type": "boolean"} in sch["oneOf"]


def test_cli_thumbnail_and_its_checks(tmp_path, capsys):
    data = spec_data(thumbnail={"elements": [{"type": "text", "text": "x" * 40, "size": 200}]})
    p = tmp_path / "v.json"
    p.write_text(json.dumps(data))
    assert main(["check", str(p), "--no-tts"]) == 1
    assert "$.thumbnail.elements" in capsys.readouterr().err
    data["thumbnail"] = {"scene": 2}
    p.write_text(json.dumps(data))
    assert main(["thumbnail", str(p), "--no-tts"]) == 0
    assert Image.open(tmp_path / "v.thumb.png").size == (270, 480)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_render_writes_the_picture_and_holds_it_first(tmp_path):
    p = tmp_path / "v.json"
    p.write_text(json.dumps(spec_data(thumbnail={"scene": 2, "intro": 1})))
    out = tmp_path / "v.mp4"
    assert main(["render", str(p), "-o", str(out), "--quiet", "--fps", "10"]) == 0
    assert (tmp_path / "v.thumb.png").exists()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True, check=True).stdout)
    assert abs(dur - 7.0) < 0.15  # 1 s picture + 2 scenes of 3 s
    first = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(out), "-frames:v", "1", str(first)], check=True)
    assert dominant(Image.open(first)) == 1  # scene 2's green, not scene 1's red
