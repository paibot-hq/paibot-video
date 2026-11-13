"""Draw frames. Everything is computed from the spec and the time, so any
frame can be rendered on its own (used by `frame` and `sheet`)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageOps

from .spec import Spec, parse_color
from .text import find_font, font, is_cjk, wrap, wrap_balanced
from .timeline import Timeline


# -- easing -----------------------------------------------------------------

def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_cubic(x: float) -> float:
    return 1 - (1 - x) ** 3


def ease_out_back(x: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_in_out(x: float) -> float:
    return 3 * x * x - 2 * x * x * x


# -- geometry ---------------------------------------------------------------

def safe_area(w: int, h: int, captions: bool) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) inside which content should stay."""
    portrait = h > w * 1.2
    mx = w * 0.06
    top = h * (0.08 if portrait else 0.07)
    bottom = h - h * (0.14 if portrait else 0.08)
    if captions:
        bottom -= min(w, h) * 0.05 * 1.35 * 2 + min(w, h) * 0.06
    return mx, top, w - mx, bottom


def even(n: float) -> int:
    return max(2, int(round(n)) // 2 * 2)


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    table = [int(v * alpha) for v in range(256)]
    out = img.copy()
    out.putalpha(img.getchannel("A").point(table))
    return out


def _composite(dst: Image.Image, src: Image.Image, x: float, y: float) -> None:
    """alpha_composite that accepts positions partly outside `dst`."""
    x, y = int(round(x)), int(round(y))
    cx, cy = max(0, -x), max(0, -y)
    if cx >= src.width or cy >= src.height or x >= dst.width or y >= dst.height:
        return
    if cx or cy:
        src = src.crop((cx, cy, src.width, src.height))
    dst.alpha_composite(src, (x + cx, y + cy))


def type_duration(el: dict, chars: int) -> float:
    """Typing runs at ~20 characters a second unless animDuration is longer."""
    return max(el["animDuration"], chars / 20.0)


def rounded_mask(size: tuple[int, int], radius: float) -> Image.Image:
    m = Image.new("L", size, 0)
    r = int(max(0, min(radius, size[0] / 2, size[1] / 2)))
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=r, fill=255)
    return m


@dataclass
class Placed:
    spec: dict
    x: float
    y: float
    w: float
    h: float
    data: dict = field(default_factory=dict)
    cache: Image.Image | None = None

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)


class Renderer:
    def __init__(self, spec: Spec, timeline: Timeline, scale: float = 1.0) -> None:
        self.spec = spec
        self.tl = timeline
        self.W = even(spec.width * scale)
        self.H = even(spec.height * scale)
        self.k = self.W / spec.width
        top = spec.top
        self.fonts = {
            "regular": find_font("regular", top["font"]),
            "bold": find_font("bold", top["fontBold"] or top["font"]),
            "mono": find_font("mono", top["fontMono"]),
        }
        self.color = top["color"]
        self.accent = top["accent"]
        self._bg_cache: dict[int, Image.Image] = {}
        self._img_cache: dict[str, Image.Image] = {}
        self._cap_cache: dict[str, Image.Image] = {}
        self._last_frame: dict[int, Image.Image] = {}
        self.scenes: list[list[Placed]] = [self._layout(i) for i in range(len(spec.scenes))]

    # -- units ---------------------------------------------------------------

    def px(self, v: float) -> float:
        return v * self.k

    def lx(self, v, total=None) -> float:
        total = self.W if total is None else total
        if isinstance(v, str):
            return float(v.strip().rstrip("%")) / 100 * total
        return v * self.k

    def ly(self, v) -> float:
        return self.lx(v, self.H)

    def f(self, weight: str, size: float):
        return font(self.fonts[weight], round(self.px(size)))

    # -- measuring -----------------------------------------------------------

    def _measure(self, el: dict) -> tuple[float, float, dict]:
        t = el["type"]
        if t == "text":
            fnt = self.f(el["weight"], el["size"])
            pad = self.px(el["padding"]) if el["background"] else 0
            maxw = self.lx(el["maxWidth"]) - 2 * pad
            lines = wrap(el["text"], fnt, maxw)
            lh = self.px(el["size"]) * el["lineHeight"]
            tw = max((fnt.getlength(ln) for ln in lines), default=0)
            return tw + 2 * pad, lh * len(lines) + 2 * pad, {"font": fnt, "lines": lines, "lh": lh, "pad": pad, "tw": tw, "maxw": maxw}
        if t == "bullets":
            fnt = self.f(el["weight"], el["size"])
            size = self.px(el["size"])
            marker = size * 1.15
            maxw = self.lx(el["maxWidth"])
            lh = size * el["lineHeight"]
            items = [wrap(s, fnt, maxw - marker) for s in el["items"]]
            widths = [max((fnt.getlength(ln) for ln in it), default=0) for it in items]
            heights = [lh * len(it) for it in items]
            gap = self.px(el["gap"])
            return marker + max(widths), sum(heights) + gap * (len(items) - 1), {
                "font": fnt, "items": items, "lh": lh, "marker": marker, "heights": heights, "gap": gap, "maxw": maxw}
        if t == "image":
            src = self._load_image(el["src"])
            iw, ih = src.size
            tw = self.lx(el["width"]) if el["width"] is not None else None
            th = self.ly(el["height"]) if el["height"] is not None else None
            if tw is None and th is None:
                s = min(self.W * 0.8 / iw, self.H * 0.5 / ih, self.k * 1e9)
                tw, th = iw * s, ih * s
            elif th is None:
                th = tw * ih / iw
            elif tw is None:
                tw = th * iw / ih
            return tw, th, {}
        if t == "shape":
            return self.lx(el["width"]), self.ly(el["height"]), {}
        if t == "code":
            size = self.px(el["size"])
            lines = el["code"].expandtabs(4).rstrip("\n").split("\n")
            kind = "regular" if is_cjk(el["code"]) else "mono"
            pad = self.px(el["padding"])
            maxw = self.lx(el["maxWidth"])
            fnt = font(self.fonts[kind], round(size))
            widest = max((fnt.getlength(ln) for ln in lines), default=0)
            shrunk = 1.0
            avail = maxw - 2 * pad
            if widest > avail and widest > 0:
                full = size
                size = max(1, math.floor(size * avail / widest))
                fnt = font(self.fonts[kind], size)
                widest = max((fnt.getlength(ln) for ln in lines), default=0)
                while widest > avail and size > 1:
                    size -= 1
                    fnt = font(self.fonts[kind], size)
                    widest = max((fnt.getlength(ln) for ln in lines), default=0)
                shrunk = size / full
            lh = size * el["lineHeight"]
            return widest + 2 * pad, lh * len(lines) + 2 * pad, {"font": fnt, "lines": lines, "lh": lh, "pad": pad, "shrunk": shrunk}
        if t == "counter":
            fnt = self.f(el["weight"], el["size"])
            w = max(fnt.getlength(self._fmt_counter(el, v)) for v in (el["from"], el["to"]))
            return w, self.px(el["size"]) * 1.2, {"font": fnt}
        if t == "bars":
            fnt = self.f("bold", el["size"])
            width = self.lx(el["width"])
            row = max(self.px(el["barHeight"]), self.px(el["size"]) * 1.3)
            gap = self.px(el["gap"])
            label_w = max(fnt.getlength(it["label"]) for it in el["items"]) + self.px(24)
            value_w = max(fnt.getlength(self._fmt_bar(el, it["value"])) for it in el["items"]) + self.px(20)
            n = len(el["items"])
            return width, row * n + gap * (n - 1), {"font": fnt, "row": row, "gap": gap, "label_w": label_w, "value_w": value_w}
        raise ValueError(t)

    def _layout(self, i: int) -> list[Placed]:
        sc = self.spec.scenes[i]
        left, top, right, bottom = safe_area(self.W, self.H, bool(self.spec.top["captions"]))
        placed: list[Placed] = []
        stack: list[Placed] = []
        for el in sc["elements"]:
            w, h, data = self._measure(el)
            p = Placed(el, 0, 0, w, h, data)
            if el["x"] is None and el["y"] is None:
                stack.append(p)
            else:
                cx = self.lx(el["x"]) if el["x"] is not None else self.W / 2
                cy = self.ly(el["y"]) if el["y"] is not None else self.H / 2
                a = el["anchor"]
                fx = 0.0 if "left" in a else 1.0 if "right" in a else 0.5
                fy = 0.0 if a.startswith("top") else 1.0 if a.startswith("bottom") else 0.5
                p.x, p.y = cx - w * fx, cy - h * fy
            placed.append(p)
        if stack:
            gap = self.px(sc["gap"])
            total = sum(p.h for p in stack) + gap * (len(stack) - 1)
            avail = bottom - top
            y = {"top": top, "bottom": bottom - total}.get(sc["valign"], top + (avail - total) / 2)
            for p in stack:
                p.x, p.y = (self.W - p.w) / 2, y
                y += p.h + gap
        return placed

    # -- formatting ----------------------------------------------------------

    @staticmethod
    def _fmt_counter(el: dict, v: float) -> str:
        d = el["decimals"]
        s = f"{v:,.{d}f}" if el["separator"] else f"{v:.{d}f}"
        return f"{el['prefix']}{s}{el['suffix']}"

    @staticmethod
    def _fmt_bar(el: dict, v: float) -> str:
        return f"{v:,.{el['decimals']}f}{el['suffix']}"

    # -- assets ----------------------------------------------------------------

    def _load_image(self, path: str) -> Image.Image:
        img = self._img_cache.get(path)
        if img is None:
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
            self._img_cache[path] = img
        return img

    def background(self, i: int) -> Image.Image:
        bg = self._bg_cache.get(i)
        if bg is not None:
            return bg
        spec_bg = self.spec.scenes[i]["background"] or self.spec.top["background"] or {"color": "#000000"}
        W, H = self.W, self.H
        if "color" in spec_bg:
            bg = Image.new("RGB", (W, H), parse_color(spec_bg["color"])[:3])
        elif "gradient" in spec_bg:
            cols = [parse_color(c)[:3] for c in spec_bg["gradient"]]
            L = int(math.hypot(W, H)) + 2
            strip = Image.new("RGB", (L, 1))
            px = strip.load()
            n = len(cols) - 1
            for x in range(L):
                pos = x / (L - 1) * n
                j = min(int(pos), n - 1)
                f = pos - j
                a, b = cols[j], cols[j + 1]
                px[x, 0] = tuple(int(a[c] + (b[c] - a[c]) * f) for c in range(3))
            sq = strip.resize((L, L), Image.BILINEAR).rotate(90 - spec_bg["angle"], resample=Image.BILINEAR)
            bg = sq.crop(((L - W) // 2, (L - H) // 2, (L - W) // 2 + W, (L - H) // 2 + H))
        else:
            src = self._load_image(spec_bg["image"]).convert("RGB")
            bg = ImageOps.fit(src, (W, H), Image.LANCZOS)
            if spec_bg.get("dim"):
                bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), spec_bg["dim"])
        self._bg_cache[i] = bg
        return bg

    # -- element layers --------------------------------------------------------

    def _canvas(self, p: Placed) -> Image.Image:
        return Image.new("RGBA", (max(1, math.ceil(p.w)), max(1, math.ceil(p.h))), (0, 0, 0, 0))

    def _draw_text(self, p: Placed, chars: int | None = None) -> Image.Image:
        el, d = p.spec, p.data
        img = self._canvas(p)
        dr = ImageDraw.Draw(img)
        if el["background"]:
            dr.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(self.px(el["radius"])), fill=parse_color(el["background"]))
        fnt, lh, pad = d["font"], d["lh"], d["pad"]
        asc, desc = fnt.getmetrics()
        color = parse_color(el["color"] or self.color)
        left = chars
        for i, ln in enumerate(d["lines"]):
            if left is not None:
                if left <= 0:
                    break
                shown = ln[:left]
                left -= len(ln)
            else:
                shown = ln
            lw = fnt.getlength(ln)
            x = pad + {"left": 0, "right": d["tw"] - lw}.get(el["align"], (d["tw"] - lw) / 2)
            y = pad + i * lh + (lh - (asc + desc)) / 2 + asc
            dr.text((x, y), shown, font=fnt, fill=color, anchor="ls")
        return img

    def _draw_code(self, p: Placed, chars: int | None = None) -> Image.Image:
        el, d = p.spec, p.data
        img = self._canvas(p)
        dr = ImageDraw.Draw(img)
        dr.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(self.px(el["radius"])), fill=parse_color(el["background"]))
        fnt, lh, pad = d["font"], d["lh"], d["pad"]
        asc, desc = fnt.getmetrics()
        color = parse_color(el["color"])
        left = chars
        for i, ln in enumerate(d["lines"]):
            if left is not None:
                if left <= 0:
                    break
                shown = ln[:left]
                left -= len(ln) + 1
            else:
                shown = ln
            dr.text((pad, pad + i * lh + (lh - (asc + desc)) / 2 + asc), shown, font=fnt, fill=color, anchor="ls")
        return img

    def _bullet_item(self, p: Placed, idx: int) -> Image.Image:
        key = f"item{idx}"
        if key in p.data:
            return p.data[key]
        el, d = p.spec, p.data
        h = d["heights"][idx]
        img = Image.new("RGBA", (max(1, math.ceil(p.w)), max(1, math.ceil(h))), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        fnt, lh = d["font"], d["lh"]
        asc, desc = fnt.getmetrics()
        size = self.px(el["size"])
        r = size * 0.17
        cy = (lh) / 2
        dr.ellipse((size * 0.2, cy - r, size * 0.2 + 2 * r, cy + r), fill=parse_color(el["bulletColor"] or self.accent))
        color = parse_color(el["color"] or self.color)
        for i, ln in enumerate(d["items"][idx]):
            dr.text((d["marker"], i * lh + (lh - (asc + desc)) / 2 + asc), ln, font=fnt, fill=color, anchor="ls")
        p.data[key] = img
        return img

    def _draw_image(self, p: Placed) -> Image.Image:
        el = p.spec
        src = self._load_image(el["src"])
        size = (max(1, round(p.w)), max(1, round(p.h)))
        if el["width"] is not None and el["height"] is not None and el["fit"] == "cover":
            img = ImageOps.fit(src, size, Image.LANCZOS)
        elif el["width"] is not None and el["height"] is not None:
            img = ImageOps.pad(src, size, Image.LANCZOS, color=(0, 0, 0, 0))
        else:
            img = src.resize(size, Image.LANCZOS)
        if el["radius"]:
            mask = rounded_mask(size, self.px(el["radius"]))
            a = Image.new("L", size, 0)
            a.paste(img.getchannel("A"), (0, 0), mask)
            img.putalpha(a)
        return img

    def _draw_shape(self, p: Placed) -> Image.Image:
        el = p.spec
        img = self._canvas(p)
        dr = ImageDraw.Draw(img)
        fill = parse_color(el["color"] or self.accent)
        if el["shape"] == "circle":
            dr.ellipse((0, 0, img.width - 1, img.height - 1), fill=fill)
        else:
            dr.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(self.px(el["radius"])), fill=fill)
        return img

    def _draw_counter(self, p: Placed, v: float) -> Image.Image:
        el = p.spec
        img = self._canvas(p)
        dr = ImageDraw.Draw(img)
        dr.text((img.width / 2, img.height / 2), self._fmt_counter(el, v), font=p.data["font"],
                fill=parse_color(el["color"] or self.accent), anchor="mm")
        return img

    def _draw_bars(self, p: Placed, u: float) -> tuple[Image.Image, bool]:
        el, d = p.spec, p.data
        img = self._canvas(p)
        dr = ImageDraw.Draw(img)
        fnt, row, gap, lw, vw = d["font"], d["row"], d["gap"], d["label_w"], d["value_w"]
        vmax = el["max"] or max((it["value"] for it in el["items"]), default=1) or 1
        track = max(1.0, p.w - lw - vw)
        bh = self.px(el["barHeight"])
        txt = parse_color(el["textColor"] or self.color)
        moving = False
        for i, it in enumerate(el["items"]):
            prog = clamp01((u - el["start"] - i * el["stagger"]) / el["duration"]) if el["duration"] else 1.0
            if prog < 1:
                moving = True
            e = ease_out_cubic(prog)
            y = i * (row + gap)
            cy = y + row / 2
            dr.text((lw - self.px(24), cy), it["label"], font=fnt, fill=txt, anchor="rm")
            bw = max(0.0, track * max(0.0, it["value"]) / vmax * e)
            col = parse_color(it["color"] or el["color"] or self.accent)
            if bw >= 2:
                dr.rounded_rectangle((lw, cy - bh / 2, lw + bw, cy + bh / 2), radius=int(min(bh / 2, self.px(12))), fill=col)
            dr.text((lw + bw + self.px(16), cy), self._fmt_bar(el, it["value"] * e), font=fnt, fill=txt, anchor="lm")
        return img, moving

    # -- compositing -------------------------------------------------------------

    def _element(self, frame: Image.Image, p: Placed, u: float, scene_dur: float) -> None:
        el = p.spec
        start = el["start"]
        end = el["end"] if el["end"] is not None else scene_dur + 1
        if u < start or u >= end:
            return
        anim, ad = el["animate"], el["animDuration"]
        t = el["type"]
        if anim == "type":
            ad = type_duration(el, sum(len(x) for x in p.data["lines"]))
        prog = clamp01((u - start) / ad) if ad > 0 else 1.0
        alpha, dx, dy, scale = el["opacity"], 0.0, 0.0, 1.0
        img: Image.Image | None = None
        dynamic = False

        if t == "bullets":
            # Bullets animate item by item; the block itself does not move.
            img = self._canvas(p)
            y = 0.0
            dynamic = False
            for i in range(len(el["items"])):
                ip = clamp01((u - start - i * el["stagger"]) / ad) if ad > 0 else 1.0
                item = self._bullet_item(p, i)
                if ip > 0:
                    e = ease_out_cubic(ip)
                    off_x = off_y = 0.0
                    if anim == "slide-up":
                        off_y = (1 - e) * self.px(40)
                    elif anim == "slide-down":
                        off_y = -(1 - e) * self.px(40)
                    elif anim == "slide-left":
                        off_x = (1 - e) * self.px(60)
                    elif anim == "slide-right":
                        off_x = -(1 - e) * self.px(60)
                    a = 1.0 if anim == "none" else e
                    _composite(img, _with_alpha(item, a), off_x, y + off_y)
                if ip < 1:
                    dynamic = True
                y += p.data["heights"][i] + p.data["gap"]
            anim = "none"
        elif t == "counter":
            cp = clamp01((u - start) / el["duration"]) if el["duration"] else 1.0
            v = el["from"] + (el["to"] - el["from"]) * ease_out_cubic(cp)
            if cp >= 1 and p.cache is not None:
                img = p.cache
            else:
                img = self._draw_counter(p, v)
                dynamic = cp < 1
        elif t == "bars":
            if p.cache is not None:
                img = p.cache
            else:
                img, dynamic = self._draw_bars(p, u)
        elif anim == "type" and prog < 1:
            total = sum(len(x) for x in p.data["lines"])
            n = int(total * prog)
            img = self._draw_text(p, n) if t == "text" else self._draw_code(p, n)
            dynamic = True
            anim = "none"
        else:
            if p.cache is None:
                img = {"text": self._draw_text, "code": self._draw_code, "image": self._draw_image, "shape": self._draw_shape}[t](p)
            else:
                img = p.cache

        if not dynamic and p.cache is None:
            p.cache = img

        if anim == "fade" or anim == "type":
            alpha *= ease_out_cubic(prog)
        elif anim == "pop":
            alpha *= clamp01(prog * 2.5)
            scale = 0.6 + 0.4 * ease_out_back(prog)
        elif anim.startswith("slide-"):
            e = ease_out_cubic(prog)
            alpha *= e
            dist = self.px(80)
            dx = {"slide-left": dist, "slide-right": -dist}.get(anim, 0.0) * (1 - e)
            dy = {"slide-up": dist, "slide-down": -dist}.get(anim, 0.0) * (1 - e)
        elif anim == "zoom":
            alpha *= ease_out_cubic(clamp01((u - start) / max(ad, 1e-6)))
            life = max(min(end, scene_dur) - start, 1e-6)
            scale = 1.0 + 0.08 * clamp01((u - start) / life)

        if el["out"] == "fade":
            stop = min(end, scene_dur)
            alpha *= clamp01((stop - u) / 0.35)

        if alpha <= 0.003:
            return
        if abs(scale - 1) > 1e-3:
            sw, sh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
            img = img.resize((sw, sh), Image.BILINEAR)
            dx += (p.w - sw) / 2
            dy += (p.h - sh) / 2
        img = _with_alpha(img, alpha)
        frame.paste(img, (int(round(p.x + dx)), int(round(p.y + dy))), img)

    def _caption(self, frame: Image.Image, text: str) -> None:
        cap = self._cap_cache.get(text)
        if cap is None:
            base = min(self.W, self.H)
            size = base * 0.05
            fnt = font(self.fonts["bold"], round(size))
            lines = wrap_balanced(text, fnt, self.W * 0.82)
            lh = size * 1.35
            tw = max(fnt.getlength(ln) for ln in lines)
            padx, pady = size * 0.6, size * 0.35
            cap = Image.new("RGBA", (int(tw + 2 * padx), int(lh * len(lines) + 2 * pady)), (0, 0, 0, 0))
            dr = ImageDraw.Draw(cap)
            dr.rounded_rectangle((0, 0, cap.width - 1, cap.height - 1), radius=int(size * 0.4), fill=(0, 0, 0, 170))
            asc, desc = fnt.getmetrics()
            for i, ln in enumerate(lines):
                dr.text((cap.width / 2, pady + i * lh + (lh - (asc + desc)) / 2 + asc), ln, font=fnt, fill=(255, 255, 255, 255), anchor="ms")
            self._cap_cache[text] = cap
        portrait = self.H > self.W * 1.2
        bottom = self.H - self.H * (0.14 if portrait else 0.08)
        frame.paste(cap, (int((self.W - cap.width) / 2), int(bottom - cap.height)), cap)

    def scene_frame(self, i: int, u: float) -> Image.Image:
        frame = self.background(i).copy()
        dur = self.tl.scenes[i].duration
        for p in self.scenes[i]:
            self._element(frame, p, u, dur)
        for c in self.tl.scenes[i].captions:
            if c.start <= u < c.end:
                self._caption(frame, c.text)
                break
        return frame

    def frame(self, t: float) -> Image.Image:
        i, u = self.tl.at(t)
        cur = self.scene_frame(i, u)
        if i == 0:
            return cur
        sc = self.spec.scenes[i]
        kind = sc["transition"] or self.spec.top["transition"]
        td = min(self.spec.top["transitionDuration"], self.tl.scenes[i].duration / 2, self.tl.scenes[i - 1].duration / 2)
        if kind == "none" or td <= 0 or u >= td:
            return cur
        prev = self._last_frame.get(i - 1)
        if prev is None:
            prev = self.scene_frame(i - 1, self.tl.scenes[i - 1].duration - 1e-4)
            self._last_frame[i - 1] = prev
        e = ease_in_out(clamp01(u / td))
        W, H = self.W, self.H
        if kind == "fade":
            return Image.blend(prev, cur, e)
        if kind == "slide":
            out = Image.new("RGB", (W, H))
            off = int(round(e * W))
            out.paste(prev, (-off, 0))
            out.paste(cur, (W - off, 0))
            return out
        if kind == "wipe":
            out = prev.copy()
            x = int(round(e * W))
            if x > 0:
                out.paste(cur.crop((0, 0, x, H)), (0, 0))
            return out
        if kind == "zoom":
            s = 1 + 0.12 * e
            big = prev.resize((int(W * s), int(H * s)), Image.BILINEAR)
            ox, oy = (big.width - W) // 2, (big.height - H) // 2
            return Image.blend(big.crop((ox, oy, ox + W, oy + H)), cur, e)
        return cur

    def settle_time(self, i: int) -> float:
        """A moment in scene i when entrances are done (used for thumbnails)."""
        dur = self.tl.scenes[i].duration
        t = 0.0
        for p in self.scenes[i]:
            el = p.spec
            done = el["start"] + el["animDuration"]
            if el["type"] == "bullets":
                done += el["stagger"] * (len(el["items"]) - 1)
            if el["type"] in ("counter", "bars"):
                done = el["start"] + el["duration"] + (el.get("stagger", 0) * (len(el.get("items") or []) - 1) if el["type"] == "bars" else 0)
            if el["animate"] == "type":
                done = el["start"] + type_duration(el, sum(len(x) for x in p.data["lines"]))
            if el["end"] is None or done < el["end"]:
                t = max(t, done)
        return min(max(t + 0.1, dur * 0.5), dur - 0.05)
