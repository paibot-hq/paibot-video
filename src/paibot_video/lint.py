"""Checks that catch what a human would notice when watching the video."""

from __future__ import annotations

from .render import Renderer, safe_area, type_duration
from .spec import Issue, parse_color
from .text import reading_seconds

TEXTY = ("text", "bullets", "code", "counter", "bars")


def _lum(c) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _blend_on(fg, bg):
    a = fg[3] / 255
    return tuple(int(fg[i] * a + bg[i] * (1 - a)) for i in range(3))


def _bg_colors(r: Renderer, i: int) -> list[tuple] | None:
    bg = r.spec.scenes[i]["background"] or r.spec.top["background"] or {"color": "#000000"}
    if "color" in bg:
        return [parse_color(bg["color"])]
    if "gradient" in bg:
        return [parse_color(c) for c in bg["gradient"]]
    if "orbs" in bg:
        base = parse_color(bg["base"])
        return [base] + [(*_blend_on((*parse_color(c)[:3], 150), base), 255) for c in bg["orbs"]]
    return None  # photo: unknown


def _text_items(el: dict) -> str:
    t = el["type"]
    if t == "text":
        return el["text"]
    if t == "bullets":
        return "\n".join(el["items"])
    if t == "code":
        return el["code"]
    if t == "bars":
        return " ".join(f"{it['label']} {it['value']}" for it in el["items"])
    if t == "counter":
        return f"{el['prefix']}{el['to']}{el['suffix']}"
    return ""


def _lint_manim(r: Renderer, p, issues: list[Issue], safe: tuple, texty: list) -> None:
    """Check what the Manim scene actually draws: cut off at its edge, outside the safe area, under text."""
    el, ep, k = p.spec, p.spec["_path"], r.k
    clip = el["_clip"]
    info = clip.content()
    if info["box"] is None:
        issues.append(Issue("warning", ep, "the Manim scene draws nothing visible",
                            "add mobjects with self.play(Create(...)) or self.add(...); colours must not be fully transparent"))
        return
    if info["cut"]:
        when, side = info["cut"]
        issues.append(Issue("warning", f"{ep}.code", f"at {when:.1f}s the drawing reaches the {side} edge of the Manim area, so it is cut off",
                            f"the area is {config_units(clip)} units; shrink it, e.g. mob.scale_to_fit_width(config.frame_width - 1), "
                            "or make the element bigger"))
    sx, sy = p.w / clip.width, p.h / clip.height
    bx0, by0, bx1, by1 = info["box"]
    x0, y0, x1, y1 = p.x + bx0 * sx, p.y + by0 * sy, p.x + bx1 * sx, p.y + by1 * sy
    left, top, right, bottom = safe
    if x0 < left - 1 or y0 < top - 1 or x1 > right + 1 or y1 > bottom + 1:
        issues.append(Issue("warning", ep,
                            "the drawing reaches outside the safe area, where phone apps draw their buttons or captions go"
                            f" (drawing {x0 / k:.0f},{y0 / k:.0f} – {x1 / k:.0f},{y1 / k:.0f}; safe area {left / k:.0f},{top / k:.0f} – {right / k:.0f},{bottom / k:.0f})",
                            "scale the mobjects down in the code, or make the element smaller"))
    dur_end = el["end"] if el["end"] is not None else float("inf")
    for q in texty:
        qe = q.spec
        q_end = qe["end"] if qe["end"] is not None else float("inf")
        if qe["start"] >= dur_end or el["start"] >= q_end:
            continue
        ix = min(x1, q.box[2]) - max(x0, q.box[0])
        iy = min(y1, q.box[3]) - max(y0, q.box[1])
        if ix > 2 and iy > 2:
            issues.append(Issue("warning", ep, f"the drawing overlaps {qe['_path']} by {ix / k:.0f}x{iy / k:.0f}px",
                                "move the mobjects in the code (e.g. .shift(DOWN)), or give the element its own place"))


def config_units(clip) -> str:
    return f"{8 * clip.width / clip.height:.1f} x 8"


def lint(r: Renderer) -> list[Issue]:
    issues: list[Issue] = []
    W, H, k = r.W, r.H, r.k
    left, top, right, bottom = safe_area(W, H, r.cap_style)
    min_size = min(r.spec.width, r.spec.height) * 0.035

    for i, (sc, placed) in enumerate(zip(r.spec.scenes, r.scenes)):
        st = r.tl.scenes[i]
        dur = st.duration
        sp = sc["_path"]
        if not placed and not sc["narration"] and not sc["audio"]:
            issues.append(Issue("warning", sp, "scene has nothing on screen and no voice"))

        stacked = [p for p in placed if p.spec["x"] is None and p.spec["y"] is None]
        if stacked:
            gap = r.px(sc["gap"])
            need = sum(p.h for p in stacked) + gap * (len(stacked) - 1)
            avail = bottom - top
            if need > avail + 1:
                issues.append(Issue("error", f"{sp}.elements",
                                    f"stacked elements are {need / k:.0f}px tall but only {avail / k:.0f}px fit inside the safe area",
                                    "make text smaller or shorter, lower `gap`, or split this into two scenes"))

        bg_cols = _bg_colors(r, i)
        texts_for_reading = []
        for p in placed:
            el, ep, t = p.spec, p.spec["_path"], p.spec["type"]
            x0, y0, x1, y1 = p.box
            if el["start"] >= dur:
                issues.append(Issue("error", f"{ep}.start", f"starts at {el['start']:g}s but the scene is only {dur:.1f}s long",
                                    "lower `start` or lengthen the scene"))
                continue
            off = max(-x0, -y0, x1 - W, y1 - H, 0)
            if off > 1 and t != "shape":
                issues.append(Issue("error" if t in TEXTY else "warning", ep,
                                    f"goes {off / k:.0f}px off the screen (box {x0 / k:.0f},{y0 / k:.0f} – {x1 / k:.0f},{y1 / k:.0f}; screen {r.spec.width}x{r.spec.height})",
                                    "move it, make it smaller, or shorten the text"))
            elif t in TEXTY and (x0 < left - 1 or y0 < top - 1 or x1 > right + 1 or y1 > bottom + 1):
                issues.append(Issue("warning", ep,
                                    "sits outside the safe area, where phone apps draw their buttons or captions go"
                                    f" (safe area {left / k:.0f},{top / k:.0f} – {right / k:.0f},{bottom / k:.0f})",
                                    "move it toward the centre"))

            if t == "manim" and el.get("_clip"):
                _lint_manim(r, p, issues, (left, top, right, bottom), [q for q in placed if q.spec["type"] in TEXTY])

            if t in ("text", "bullets", "code", "bars") and el["size"] < min_size:
                issues.append(Issue("warning", f"{ep}.size", f"{el['size']:g}px text is hard to read on a phone",
                                    f"use at least {min_size:.0f}"))
            if t == "text" and len(p.data["lines"]) > 5:
                issues.append(Issue("warning", f"{ep}.text", f"wraps to {len(p.data['lines'])} lines",
                                    "keep on-screen text short; put the rest in `narration`"))
            if t == "code" and p.data["shrunk"] < 0.999:
                eff = el["size"] * p.data["shrunk"]
                longest = max(p.data["lines"], key=len)
                if eff < min_size or p.data["shrunk"] < 0.8:
                    issues.append(Issue("warning", f"{ep}.code",
                                        f"the longest line ({len(longest)} characters) does not fit, so the code was shrunk from {el['size']:g}px to {eff:.0f}px",
                                        f"code is never wrapped: split lines to about {int(len(longest) * p.data['shrunk'] * el['size'] / max(el['size'], min_size * 1.2))} characters, or use a landscape preset"))

            # contrast
            if t in ("text", "bullets", "counter", "bars"):
                fg = parse_color((el.get("color") or (r.accent if t == "counter" else r.color)) if t != "bars" else (el["textColor"] or r.color))
                if t == "text" and el["background"]:
                    behind = [parse_color(el["background"])]
                    if behind[0][3] < 255 and bg_cols:
                        behind = [(*_blend_on(behind[0], b), 255) for b in bg_cols]
                    elif behind[0][3] < 255:
                        behind = None
                else:
                    behind = bg_cols
                outlined = el.get("stroke", 0) >= 3 and contrast(fg, parse_color(el["strokeColor"])) >= 4.5
                if outlined:
                    pass  # an outline keeps text readable on any background
                elif behind:
                    worst = min(contrast(fg, b) for b in behind)
                    if worst < 3:
                        issues.append(Issue("warning", f"{ep}.color", f"low contrast with the background ({worst:.1f}:1)",
                                            "aim for 4.5:1 or more: light text on dark backgrounds or the reverse"))
                elif bg_cols is None and not (t == "text" and el["background"]) and not el.get("glow"):
                    issues.append(Issue("warning", ep, "text over a photo can be unreadable",
                                        "give the text a `background` such as \"#000000aa\", or set `dim` on the background image"))

            if t in ("text", "bullets", "code"):
                shown = (min(el["end"], dur) if el["end"] is not None else dur) - el["start"]
                if el["animate"] == "type":
                    shown -= type_duration(el, sum(len(x) for x in p.data["lines"]))
                texts_for_reading.append((ep, _text_items(el), shown))

        for ep, txt, shown in texts_for_reading:
            need = reading_seconds(txt)
            if shown + 0.05 < need and not sc["narration"]:
                issues.append(Issue("warning", ep, f"needs about {need:.1f}s to read but is shown for {shown:.1f}s",
                                    "lengthen the scene (`duration`) or shorten the text"))

        # overlapping text-like elements visible at the same time
        texty = [p for p in placed if p.spec["type"] in TEXTY]
        for a in range(len(texty)):
            for b in range(a + 1, len(texty)):
                pa, pb = texty[a], texty[b]
                ea, eb = pa.spec, pb.spec
                a_end = ea["end"] if ea["end"] is not None else dur
                b_end = eb["end"] if eb["end"] is not None else dur
                if ea["start"] >= b_end or eb["start"] >= a_end:
                    continue
                ix = min(pa.box[2], pb.box[2]) - max(pa.box[0], pb.box[0])
                iy = min(pa.box[3], pb.box[3]) - max(pa.box[1], pb.box[1])
                if ix > 2 and iy > 2:
                    issues.append(Issue("warning", eb["_path"], f"overlaps {ea['_path']} by {ix / k:.0f}x{iy / k:.0f}px",
                                        "remove x/y from both to let them stack automatically, or move one"))
    return issues
