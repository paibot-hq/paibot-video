"""paibot-video: JSON in, MP4 out."""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import resources

from . import __version__
from .encode import bar_progress, contact_sheet, render_video
from .lint import lint
from .render import Renderer
from .spec import Issue, MUSIC_STYLES, json_schema, load
from .text import FontNotFound
from .timeline import ToolMissing, plan

EXAMPLE = {
    "title": "Paibot Video demo",
    "preset": "shorts",
    "captions": True,
    "background": {"gradient": ["#0b1024", "#1e1b4b"], "angle": 160},
    "accent": "#38bdf8",
    "scenes": [
        {
            "narration": "JSONを書くだけで、動画ができます。",
            "elements": [
                {"type": "text", "text": "JSON → 動画", "size": 140},
                {"type": "text", "text": "Paibot Video", "size": 64, "weight": "regular", "color": "#94a3b8", "start": 0.4},
            ],
        },
        {
            "narration": "文字の配置は自動です。読み上げの長さに合わせて、シーンの長さも決まります。",
            "elements": [
                {"type": "text", "text": "できること", "size": 96},
                {"type": "bullets", "items": ["自動レイアウト", "読み上げと字幕", "グラフとカウンター"], "start": 0.5},
            ],
        },
        {
            "narration": "数字もアニメーションで見せられます。",
            "elements": [
                {"type": "counter", "to": 12800, "suffix": " 再生", "size": 150},
                {"type": "bars", "items": [{"label": "月", "value": 3}, {"label": "火", "value": 7}, {"label": "水", "value": 12}], "start": 0.6},
            ],
        },
    ],
}


def _print_issues(issues: list[Issue], stream=None) -> None:
    stream = stream or sys.stderr
    color = stream.isatty() and not os.environ.get("NO_COLOR")
    for it in issues:
        tag = it.level.upper()
        if color:
            tag = ("\x1b[31m" if it.level == "error" else "\x1b[33m") + tag + "\x1b[0m"
        stream.write(f"{tag} {it.path}: {it.message}\n")
        if it.hint:
            stream.write(f"      ↳ {it.hint}\n")


def _prepare(path: str, *, synth: bool, tts: str | None, scale: float = 1.0):
    """Load + plan + layout + lint. Returns (renderer|None, issues, summary)."""
    spec, issues = load(path)
    if spec is None:
        return None, issues, None
    try:
        tl, tissues = plan(spec, tts_command=tts, synth=synth)
        issues += tissues
        r = Renderer(spec, tl, scale=scale)
    except (FontNotFound, ToolMissing) as e:
        return None, issues + [Issue("error", "$", str(e))], None
    issues += lint(Renderer(spec, tl) if scale != 1.0 else r)
    summary = {
        "size": [spec.width, spec.height], "fps": spec.fps, "duration": round(tl.total, 2), "voice": tl.voice,
        "language": tl.language,
        "music": (spec.top["music"]["generate"] or os.path.basename(spec.top["music"]["src"])) if spec.top["music"] else None,
        "scenes": [{"index": s.index + 1, "start": round(s.start, 2), "duration": round(s.duration, 2),
                    "voice": round(s.audio_len, 2) if s.audio else None} for s in tl.scenes],
    }
    return r, issues, summary


def _summary_text(summary: dict) -> str:
    w, h = summary["size"]
    lines = [f"  {w}x{h} @ {summary['fps']}fps · {len(summary['scenes'])} scenes · {summary['duration']:.1f}s"
             + (f" · language: {summary['language']}" if summary.get("language") else "")
             + (f" · voice: {summary['voice']}" if summary.get("voice") else "")
             + (f" · music: {summary['music']}" if summary.get("music") else "")]
    for s in summary["scenes"]:
        voice = f" · voice {s['voice']:.1f}s" if s["voice"] else ""
        lines.append(f"    #{s['index']:<2} {s['start']:6.1f}s  +{s['duration']:.1f}s{voice}")
    return "\n".join(lines)


def cmd_check(a) -> int:
    r, issues, summary = _prepare(a.spec, synth=not a.no_tts, tts=a.tts)
    errors = [i for i in issues if i.level == "error"]
    sheet_path = None
    if r is not None and a.sheet:
        sheet_path = os.path.abspath(a.sheet)
        contact_sheet(r).save(sheet_path)
    if a.json:
        print(json.dumps({"ok": not errors, "errors": len(errors), "warnings": len(issues) - len(errors),
                          "summary": summary, "sheet": sheet_path, "issues": [i.as_dict() for i in issues]},
                         ensure_ascii=False, indent=2))
    else:
        _print_issues(issues)
        if summary:
            print(_summary_text(summary), file=sys.stderr)
        if sheet_path:
            print(f"  sheet: {os.path.relpath(sheet_path)}", file=sys.stderr)
        print(f"  {'✗' if errors else '✓'} {len(errors)} errors, {len(issues) - len(errors)} warnings", file=sys.stderr)
    return 1 if errors else 0


def cmd_render(a) -> int:
    scale = a.scale if a.scale else (0.5 if a.preview else 1.0)
    r, issues, summary = _prepare(a.spec, synth=True, tts=a.tts, scale=scale)
    errors = [i for i in issues if i.level == "error"]
    if not a.json:
        _print_issues(issues)
    if r is None or (errors and not a.force):
        if a.json:
            print(json.dumps({"ok": False, "issues": [i.as_dict() for i in issues]}, ensure_ascii=False, indent=2))
        else:
            print("  not rendering because of the errors above (use --force to render anyway)", file=sys.stderr)
        return 1
    out = os.path.abspath(a.output or os.path.splitext(a.spec)[0] + (".preview.mp4" if a.preview else ".mp4"))
    fps = a.fps or (min(r.spec.fps, 15) if a.preview else None)
    if not a.json:
        print(_summary_text(summary), file=sys.stderr)
    try:
        render_video(r, out, fps=fps, preset="veryfast" if a.preview else "medium",
                     progress=None if (a.json or a.quiet) else bar_progress())
    except (RuntimeError, ToolMissing) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps({"ok": True, "output": out, "summary": summary, "issues": [i.as_dict() for i in issues]}, ensure_ascii=False, indent=2))
    else:
        print(f"  ✓ {os.path.relpath(out)}", file=sys.stderr)
    return 0


def cmd_frame(a) -> int:
    r, issues, _ = _prepare(a.spec, synth=not a.no_tts, tts=a.tts)
    if r is None:
        _print_issues(issues)
        return 1
    if a.scene is not None:
        if not 1 <= a.scene <= len(r.tl.scenes):
            print(f"error: --scene must be 1..{len(r.tl.scenes)}", file=sys.stderr)
            return 2
        st = r.tl.scenes[a.scene - 1]
        t = st.start + (a.at if a.at is not None else r.settle_time(a.scene - 1))
    else:
        t = a.at if a.at is not None else 0.0
    out = os.path.abspath(a.output or "frame.png")
    r.frame(min(t, r.tl.total - 1e-3)).save(out)
    print(out)
    return 0


def cmd_sheet(a) -> int:
    r, issues, _ = _prepare(a.spec, synth=not a.no_tts, tts=a.tts)
    if r is None:
        _print_issues(issues)
        return 1
    out = os.path.abspath(a.output or "sheet.png")
    contact_sheet(r, cols=a.cols).save(out)
    print(out)
    return 0


def cmd_music(a) -> int:
    from .music import STYLES, generate, write_wav

    if a.style is None:
        for name, st in STYLES.items():
            print(f"{name:10} {st.bpm:g} bpm  {st.describe}")
        return 0
    out = os.path.abspath(a.output or f"{a.style}.wav")
    try:
        write_wav(out, generate(a.style, a.length, a.seed, a.bpm))
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


def cmd_schema(a) -> int:
    print(json.dumps(json_schema(), ensure_ascii=False, indent=2))
    return 0


def cmd_guide(a) -> int:
    print(resources.files("paibot_video").joinpath("guide.md").read_text(encoding="utf-8"))
    return 0


def cmd_init(a) -> int:
    path = a.path or "video.json"
    if os.path.exists(path) and not a.force:
        print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(EXAMPLE, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"wrote {path}. Next: paibot-video check {path} --sheet sheet.png", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paibot-video", description="Turn a JSON spec into an MP4. Made to be written by people and by AI.")
    p.add_argument("-V", "--version", action="version", version=f"paibot-video {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def common(sp, tts=True):
        sp.add_argument("spec", help="the JSON spec")
        if tts:
            sp.add_argument("--tts", metavar="CMD", help="text-to-speech command with {text_file} and {out} (default: say / espeak-ng)")
            sp.add_argument("--no-tts", action="store_true", help="estimate narration length instead of generating speech")
        allow_code(sp)

    def allow_code(sp):
        sp.add_argument("--allow-code", action="store_true",
                        help="run the Python code of `manim` elements (only for specs you trust)")

    sp = sub.add_parser("check", help="find problems; exit 1 if there are errors")
    common(sp)
    sp.add_argument("--sheet", metavar="PNG", help="also save a picture of every scene")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("render", help="write the MP4")
    sp.add_argument("spec")
    sp.add_argument("-o", "--output", metavar="MP4")
    sp.add_argument("--tts", metavar="CMD")
    sp.add_argument("--preview", action="store_true", help="half size, 15 fps, fast encode")
    sp.add_argument("--scale", type=float, help="resolution multiplier, e.g. 0.5")
    sp.add_argument("--fps", type=int)
    sp.add_argument("--force", action="store_true", help="render even if check finds errors")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("-q", "--quiet", action="store_true")
    allow_code(sp)
    sp.set_defaults(fn=cmd_render)

    sp = sub.add_parser("frame", help="save one frame as PNG")
    common(sp)
    sp.add_argument("--scene", type=int, help="scene number (1-based); --at is then relative to it")
    sp.add_argument("--at", type=float, help="time in seconds")
    sp.add_argument("-o", "--output", metavar="PNG")
    sp.set_defaults(fn=cmd_frame)

    sp = sub.add_parser("sheet", help="save a picture of every scene")
    common(sp)
    sp.add_argument("-o", "--output", metavar="PNG")
    sp.add_argument("--cols", type=int)
    sp.set_defaults(fn=cmd_sheet)

    sp = sub.add_parser("music", help="list the music styles, or write one as a WAV to listen to")
    sp.add_argument("style", nargs="?", choices=MUSIC_STYLES)
    sp.add_argument("--length", type=float, default=20.0, help="seconds (default 20)")
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--bpm", type=float)
    sp.add_argument("-o", "--output", metavar="WAV")
    sp.set_defaults(fn=cmd_music)

    sp = sub.add_parser("setup-kokoro", help="download local English neural voice models (~354 MB; once)")
    sp.set_defaults(fn=cmd_setup_kokoro)

    sp = sub.add_parser("schema", help="print the JSON Schema")
    sp.set_defaults(fn=cmd_schema)
    sp = sub.add_parser("guide", help="print the guide for writing specs (good to give to an AI)")
    sp.set_defaults(fn=cmd_guide)
    sp = sub.add_parser("init", help="write an example spec")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_init)
    return p


def cmd_setup_kokoro(a) -> int:
    from .kokoro import setup
    from .tts import TTSError
    try:
        folder = setup(progress=lambda s: print(s, file=sys.stderr))
    except (OSError, TTSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Kokoro models ready: {folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if getattr(a, "allow_code", False):
        from .manim_scene import ALLOW_ENV

        os.environ[ALLOW_ENV] = "1"
    try:
        return a.fn(a)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
