from PIL import Image

from paibot_video.render import Renderer
from paibot_video.spec import validate
from paibot_video.timeline import plan


def build(data, base=""):
    spec, issues = validate(data, base)
    assert spec is not None, [str(i) for i in issues]
    tl, _ = plan(spec, synth=False)
    return Renderer(spec, tl)


def scenes(**scene):
    return [{"narration": "明るいテーマの字幕です。", **scene, "elements": [{"type": "text", "text": "Hi"}]}]


def test_light_theme_is_light_with_dark_text_and_lofi():
    spec, _ = validate({"theme": "light", "scenes": scenes()})
    assert spec.top["color"] == "#1e293b" and spec.top["music"]["generate"] == "lofi"


def test_captions_follow_the_background_brightness():
    light = build({"theme": "light", "music": False, "captions": True, "width": 540, "height": 960, "scenes": scenes()})
    dark = build({"captions": True, "width": 540, "height": 960, "scenes": scenes()})
    assert light._caption_light() and not dark._caption_light()


def test_watermark_needs_image_or_text(tmp_path):
    spec, issues = validate({"watermark": {"position": "top-left"}, "scenes": scenes()})
    assert spec is None and any(i.path == "$.watermark" for i in issues)
    spec, issues = validate({"watermark": {"src": "nope.png"}, "scenes": scenes()}, str(tmp_path))
    assert spec is None and any(i.path == "$.watermark.src" for i in issues)


def test_watermark_is_drawn_in_its_corner_outside_the_safe_area(tmp_path):
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(tmp_path / "m.png")
    r = build({"width": 540, "height": 960, "background": "#000000",
               "watermark": {"src": "m.png", "text": "PAIBOT", "opacity": 1}, "scenes": scenes()}, str(tmp_path))
    mark = r._watermark()
    x, y = r._watermark_pos(mark)
    assert x < 540 * 0.1 and y + mark.height < 960 * 0.08 + 1  # top-left, above the safe area
    px = r.frame(0.5).getpixel((x + 5, y + mark.height // 2))
    assert px[0] > 200 and px[1] < 60
    right = build({"width": 540, "height": 960, "watermark": {"text": "PAIBOT", "position": "top-right"}, "scenes": scenes()})
    m2 = right._watermark()
    assert right._watermark_pos(m2)[0] + m2.width <= 540


def test_scene_can_hide_the_watermark(tmp_path):
    r = build({"width": 540, "height": 960, "background": "#000000",
               "watermark": {"text": "PAIBOT", "opacity": 1, "color": "#ff0000"},
               "scenes": [{"duration": 1, "elements": []}, {"duration": 1, "watermark": False, "elements": []}]})
    mark = r._watermark()
    x, y = r._watermark_pos(mark)
    box = (x, y, x + mark.width, y + mark.height)
    red = lambda img: max(p[0] for p in img.crop(box).getdata())
    assert red(r.frame(0.5)) > 150 and red(r.frame(1.9)) < 30
