"""Text-to-speech backends.

- AivisSpeech / VOICEVOX: local engines with the same HTTP API. Natural Japanese.
- A command line (`say` on macOS, `espeak-ng`, or anything in PAIBOT_VIDEO_TTS).

`"tts": "auto"` (the default) uses the first engine that answers, in the order
AivisSpeech, VOICEVOX, command line. Which one was used is reported by `check`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ENGINES = {
    "aivisspeech": ("AivisSpeech", "PAIBOT_VIDEO_AIVIS_URL", "http://127.0.0.1:10101"),
    "voicevox": ("VOICEVOX", "PAIBOT_VIDEO_VOICEVOX_URL", "http://127.0.0.1:50021"),
}


class TTSError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _post(url: str, body: bytes | None, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body if body is not None else b"", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def engine_url(name: str) -> str:
    _, env, default = ENGINES[name]
    return os.environ.get(env, default).rstrip("/")


def engine_alive(name: str) -> str | None:
    """The engine's version string if it answers, else None."""
    try:
        return json.loads(_get(engine_url(name) + "/version", 0.8))
    except (OSError, ValueError, urllib.error.URLError):
        return None


class HttpEngine:
    def __init__(self, name: str, voice: str | None, speed: float, intonation: float):
        self.name = name
        self.url = engine_url(name)
        self.version = engine_alive(name)
        if self.version is None:
            raise TTSError(f"{ENGINES[name][0]} is not running at {self.url}",
                           f"start it, or set {ENGINES[name][1]} to its address")
        try:
            speakers = json.loads(_get(self.url + "/speakers", 10))
        except (OSError, ValueError) as e:
            raise TTSError(f"could not list voices from {ENGINES[name][0]}: {e}") from e
        self.speaker_id, self.voice_label = self._resolve(speakers, voice)
        self.speed, self.intonation = speed, intonation

    @staticmethod
    def _resolve(speakers: list, voice: str | None) -> tuple[int, str]:
        choices = [f"{s['name']}/{st['name']}" for s in speakers for st in s["styles"]]
        if not speakers:
            raise TTSError("the engine has no voices installed")
        if not voice:
            s = speakers[0]
            return s["styles"][0]["id"], f"{s['name']}/{s['styles'][0]['name']}"
        if voice.isdigit():
            for s in speakers:
                for st in s["styles"]:
                    if st["id"] == int(voice):
                        return st["id"], f"{s['name']}/{st['name']}"
        name, _, style = voice.partition("/")
        for s in speakers:
            if s["name"] == name:
                for st in s["styles"]:
                    if not style or st["name"] == style:
                        return st["id"], f"{s['name']}/{st['name']}"
                raise TTSError(f'voice "{name}" has no style "{style}"',
                               "styles: " + ", ".join(f'"{name}/{st["name"]}"' for st in s["styles"]))
        raise TTSError(f'no voice named "{voice}"', "available: " + ", ".join(f'"{c}"' for c in choices))

    @property
    def label(self) -> str:
        return f"{ENGINES[self.name][0]} {self.version} · {self.voice_label}"

    def synth(self, text: str, cache_dir: str) -> str:
        key = _key([self.name, self.version, self.speaker_id, self.speed, self.intonation, text])
        out = os.path.join(cache_dir, f"tts-{key}.wav")
        if _cached(out):
            return out
        q = urllib.parse.urlencode({"text": text, "speaker": self.speaker_id})
        try:
            query = json.loads(_post(f"{self.url}/audio_query?{q}", None, 60))
            query["speedScale"] = self.speed
            query["intonationScale"] = self.intonation
            query["prePhonemeLength"] = 0.05
            query["postPhonemeLength"] = 0.1
            wav = _post(f"{self.url}/synthesis?speaker={self.speaker_id}", json.dumps(query).encode(), 300)
        except (OSError, ValueError) as e:
            raise TTSError(f"{ENGINES[self.name][0]} failed to read the text: {e}") from e
        _write(out, wav)
        return out


class CommandTTS:
    def __init__(self, command: str, voice: str | None, rate: int | None):
        self.command, self.voice, self.rate = command, voice, rate

    @property
    def label(self) -> str:
        prog = shlex.split(self.command)[0]
        return f"{prog}" + (f" · {self.voice}" if self.voice else "")

    def synth(self, text: str, cache_dir: str) -> str:
        key = _key([self.command, self.voice, self.rate, text])
        out = os.path.join(cache_dir, f"tts-{key}.wav")
        if _cached(out):
            return out
        os.makedirs(cache_dir, exist_ok=True)
        text_file = os.path.join(cache_dir, f"tts-{key}.txt")
        with open(text_file, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp = out + ".part.wav"
        subs = {"{text}": text, "{text_file}": text_file, "{out}": tmp, "{voice}": self.voice or "", "{rate}": str(self.rate or "")}
        args = []
        for a in shlex.split(self.command):
            for k, v in subs.items():
                a = a.replace(k, v)
            args.append(a)
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(tmp):
            raise TTSError(f"text-to-speech failed ({args[0]}): {(r.stderr or r.stdout).strip() or 'no output file'}")
        os.replace(tmp, out)
        return out


def system_command(voice: str | None, rate: int | None) -> str | None:
    if sys.platform == "darwin" and shutil.which("say"):
        cmd = "say"
        if voice:
            cmd += " -v {voice}"
        if rate:
            cmd += " -r {rate}"
        return cmd + " --file-format=WAVE --data-format=LEI16@24000 -o {out} -f {text_file}"
    if shutil.which("espeak-ng"):
        return "espeak-ng" + (" -v {voice}" if voice else "") + (" -s {rate}" if rate else "") + " -w {out} -f {text_file}"
    return None


def choose(top: dict, cjk: bool, tts_command: str | None = None):
    """Pick a backend for the spec. Raises TTSError with a hint when none fits."""
    pref = top["tts"]
    voice, rate = top["voice"], top["rate"]
    command = tts_command or os.environ.get("PAIBOT_VIDEO_TTS")
    if command and pref in ("auto", "command"):
        return CommandTTS(command, voice, rate)
    if pref in ("aivisspeech", "voicevox"):
        return HttpEngine(pref, voice, top["speed"], top["intonation"])
    if pref == "auto":
        for name in ENGINES:
            if engine_alive(name) is not None:
                return HttpEngine(name, voice, top["speed"], top["intonation"])
    if pref == "command":
        raise TTSError('"tts": "command" needs --tts or PAIBOT_VIDEO_TTS')
    if not voice and cjk and sys.platform == "darwin":
        voice = "Kyoko"  # the default macOS voice cannot read Japanese
    cmd = system_command(voice, rate)
    if not cmd:
        raise TTSError("no text-to-speech is available",
                       "start AivisSpeech or VOICEVOX, install espeak-ng, or set PAIBOT_VIDEO_TTS "
                       "to a command using {text_file} and {out}")
    return CommandTTS(cmd, voice, rate)


def _key(parts: list) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:20]


def _cached(path: str) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > 0


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
