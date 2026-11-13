"""Fonts and line wrapping that work for both Japanese and English."""

from __future__ import annotations

import glob
import os
import re
from functools import lru_cache

from PIL import ImageFont

_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/YuGothB.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ],
}
_ENV = {"regular": "PAIBOT_VIDEO_FONT", "bold": "PAIBOT_VIDEO_FONT_BOLD", "mono": "PAIBOT_VIDEO_FONT_MONO"}


class FontNotFound(Exception):
    pass


def find_font(kind: str, override: str | None = None) -> str:
    for p in (override, os.environ.get(_ENV[kind])):
        if p:
            if os.path.exists(p):
                return p
            raise FontNotFound(f"font file not found: {p}")
    for p in _CANDIDATES[kind]:
        if os.path.exists(p):
            return p
    hits = glob.glob("/usr/share/fonts/**/NotoSansCJK*", recursive=True)
    if hits and kind != "mono":
        return sorted(hits)[0]
    if kind == "bold":
        return find_font("regular")
    if kind == "mono":
        return find_font("regular")
    raise FontNotFound(
        "no usable font found; set PAIBOT_VIDEO_FONT (and PAIBOT_VIDEO_FONT_BOLD) or `font` in the spec "
        "to a .ttf/.otf/.ttc file that covers your language"
    )


@lru_cache(maxsize=256)
def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(1, int(round(size))))


# Characters that must not start a line (kinsoku shori), and those that must
# not end one.
_NO_START = set("、。，．,.!?！？)）]］}｝」』】〕〉》〟’”ゝゞーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ・:;：；…‥%％")
_NO_END = set("(（[［{｛「『【〔〈《〝‘“")
_TOKEN = re.compile(r"[A-Za-z0-9\u00C0-\u024F_'’\-.,!?:;%$#@&/+=*<>~^|\\]+\s*|\s+|.", re.S)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """Greedy wrap. Latin words stay together, CJK breaks between characters."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for tok in tokens(para):
            cand = cur + tok
            if not cur or f.getlength(cand.rstrip()) <= max_w:
                cur = cand
                continue
            if tok.isspace():
                lines.append(cur.rstrip())
                cur = ""
                continue
            # Kinsoku: pull the forbidden char onto this line instead.
            if tok[0] in _NO_START:
                cur = cand
                continue
            if cur and cur[-1] in _NO_END:
                carry = cur[-1]
                lines.append(cur[:-1].rstrip())
                cur = carry + tok
                continue
            lines.append(cur.rstrip())
            # A single word wider than the line: break it by character.
            if f.getlength(tok.rstrip()) > max_w and len(tok.strip()) > 1:
                piece = ""
                for ch in tok:
                    if piece and f.getlength((piece + ch).rstrip()) > max_w:
                        lines.append(piece)
                        piece = ""
                    piece += ch
                cur = piece
            else:
                cur = tok
        lines.append(cur.rstrip())
    return lines


def wrap_balanced(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """Like wrap(), but makes the lines similar in length (no lone last word)."""
    lines = wrap(text, f, max_w)
    if len(lines) < 2 or "\n" in text:
        return lines
    lo, hi = max_w / len(lines) * 0.8, max_w
    best = lines
    for _ in range(14):
        mid = (lo + hi) / 2
        cand = wrap(text, f, mid)
        if len(cand) <= len(lines):
            best, hi = cand, mid
        else:
            lo = mid
    return best


def is_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]", text))


def reading_seconds(text: str) -> float:
    """How long a viewer needs to read `text` comfortably."""
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk / 8.0 + words / 3.0 + 0.4
