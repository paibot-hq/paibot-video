"""Optional, offline English neural speech. Models are downloaded only by setup."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import os
from pathlib import Path
import tempfile
import urllib.request
import wave

import numpy as np

from .tts import TTSError, _cached, _key

# Use the original v1.0 export (tokens + float speed). The v1.1 input_ids
# export is incompatible with kokoro-onnx 0.4.9's int32 speed input.
BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
SETUP_HINT = "run: uv sync --extra kokoro && uv run --extra kokoro paibot-video setup-kokoro"


def model_dir() -> Path:
    return Path(os.environ.get("PAIBOT_VIDEO_KOKORO_DIR", "~/.cache/paibot-video/kokoro-v1.0")).expanduser()


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def setup(progress=print) -> Path:
    """Fetch pinned model files, verify them, then atomically install each file."""
    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for name, digest in FILES.items():
        target = folder / name
        if target.is_file() and _digest(target) == digest:
            progress(f"Already installed: {name}")
            continue
        progress(f"Downloading {name}…")
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as f:
                tmp = Path(f.name)
                with urllib.request.urlopen(BASE_URL + name, timeout=60) as response:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        f.write(chunk)
            if _digest(tmp) != digest:
                raise TTSError(f"checksum mismatch for {name}", "retry setup-kokoro")
            os.replace(tmp, target)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    return folder


class KokoroTTS:
    def __init__(self, voice: str | None, speed: float, language: str):
        if language != "en":
            raise TTSError("Kokoro currently supports English in paibot-video", 'use "language": "en", or another TTS engine for Japanese')
        if not 0.5 <= speed <= 2.0:
            raise TTSError("Kokoro speed must be between 0.5 and 2.0")
        self.voice, self.speed = voice or "af_heart", speed
        self.lang = "en-gb" if self.voice.startswith("b") else "en-us"
        paths = [model_dir() / name for name in FILES]
        if not all(p.is_file() and p.stat().st_size > 0 for p in paths):
            raise TTSError("Kokoro model files are not installed", SETUP_HINT)
        try:
            from kokoro_onnx import Kokoro
        except ImportError as e:
            raise TTSError("Kokoro optional dependencies are not installed", SETUP_HINT) from e
        try:
            self.engine = Kokoro(*(str(p) for p in paths))
            voices = sorted(v for v in self.engine.get_voices() if v.startswith(("af_", "am_", "bf_", "bm_")))
            if self.voice not in voices:
                raise TTSError(f'unknown English Kokoro voice "{self.voice}"', "available: " + ", ".join(voices))
            self.identity = [importlib.metadata.version("kokoro-onnx"),
                             [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]]
        except TTSError:
            raise
        except Exception as e:
            raise TTSError(f"could not load Kokoro: {e}", SETUP_HINT) from e

    @property
    def label(self) -> str:
        return f"Kokoro v1.0 · {self.voice}"

    def synth(self, text: str, cache_dir: str) -> str:
        key = _key(["kokoro", self.identity, self.voice, self.speed, self.lang, text])
        out = Path(cache_dir) / f"tts-{key}.wav"
        if _cached(str(out)):
            return str(out)
        try:
            samples, sample_rate = self.engine.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
            samples = np.asarray(samples)
            if samples.size == 0 or not np.isfinite(samples).all():
                raise ValueError("empty or invalid audio")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile(dir=out.parent, suffix=".wav", delete=False) as f:
                    tmp = Path(f.name)
                    f.write(buf.getvalue())
                os.replace(tmp, out)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        except Exception as e:
            raise TTSError(f"Kokoro failed to read the text: {e}") from e
        return str(out)
