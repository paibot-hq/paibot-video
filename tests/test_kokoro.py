import hashlib
import io
import sys
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from paibot_video import kokoro, tts
from paibot_video.spec import validate
from paibot_video.timeline import plan


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIBOT_VIDEO_KOKORO_DIR", str(tmp_path / "models"))
    folder = kokoro.model_dir()
    folder.mkdir()
    for name in kokoro.FILES:
        (folder / name).write_bytes(b"model")
    calls = []
    class Engine:
        def __init__(self, *args):
            pass
        def get_voices(self):
            return ["af_heart", "am_michael", "bf_emma", "jf_alpha"]
        def create(self, text, **kwargs):
            calls.append((text, kwargs))
            return np.sin(np.arange(2400) * 0.1) * 0.2, 24000
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=Engine))
    monkeypatch.setattr(kokoro.importlib.metadata, "version", lambda _: "test-version")
    monkeypatch.setattr(kokoro.urllib.request, "urlopen", lambda *a, **k: pytest.fail("speech must be offline"))
    return calls


def test_offline_speech_cache_and_voice_selection(fake_engine, tmp_path):
    spec, issues = validate({"tts": "kokoro", "language": "en", "speed": 0.95, "scenes": [{"narration": "Hello."}]})
    assert not issues
    backend = tts.choose(spec.top, False, tts_command="ignored-command")
    out = backend.synth("Hello.", str(tmp_path / "audio"))
    assert backend.synth("Hello.", str(tmp_path / "audio")) == out
    assert len(fake_engine) == 1
    assert fake_engine[0][1] == {"voice": "af_heart", "speed": 0.95, "lang": "en-us"}
    with wave.open(out) as wav:
        assert (wav.getframerate(), wav.getnframes(), wav.getnchannels()) == (24000, 2400, 1)
    british = kokoro.KokoroTTS("bf_emma", 1, "en")
    assert british.synth("Hello.", str(tmp_path / "audio")) != out
    assert fake_engine[-1][1]["lang"] == "en-gb"
    faster = kokoro.KokoroTTS(None, 1.2, "en")
    assert faster.synth("Hello.", str(tmp_path / "audio")) != out


def test_unknown_voice_reports_english_choices(fake_engine):
    with pytest.raises(tts.TTSError) as error:
        kokoro.KokoroTTS("Samantha", 1, "en")
    assert "af_heart" in error.value.hint and "jf_alpha" not in error.value.hint


@pytest.mark.parametrize("language,speed", [("ja", 1), ("en", 2.1)])
def test_unsupported_settings_are_clear_errors(language, speed):
    with pytest.raises(tts.TTSError):
        kokoro.KokoroTTS(None, speed, language)


def test_missing_models_report_setup_command(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIBOT_VIDEO_KOKORO_DIR", str(tmp_path))
    with pytest.raises(tts.TTSError) as error:
        kokoro.KokoroTTS(None, 1, "en")
    assert "setup-kokoro" in error.value.hint


def test_missing_optional_dependency_has_install_hint(fake_engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    with pytest.raises(tts.TTSError) as error:
        kokoro.KokoroTTS(None, 1, "en")
    assert "--extra kokoro" in error.value.hint


def test_estimation_needs_neither_model_nor_dependency(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIBOT_VIDEO_KOKORO_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)
    spec, _ = validate({"tts": "kokoro", "language": "en", "scenes": [{"narration": "Hello there."}]})
    timeline, issues = plan(spec, synth=False)
    assert not issues and timeline.total > 0


def test_synthesis_failure_is_reported(fake_engine, monkeypatch, tmp_path):
    backend = kokoro.KokoroTTS(None, 1, "en")
    monkeypatch.setattr(backend.engine, "create", lambda *a, **k: (np.array([np.nan]), 24000))
    with pytest.raises(tts.TTSError, match="invalid audio"):
        backend.synth("Hello", str(tmp_path / "audio"))
    assert not (tmp_path / "audio").exists()


def test_setup_verifies_download_and_reuses_existing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIBOT_VIDEO_KOKORO_DIR", str(tmp_path))
    data = b"verified model"
    monkeypatch.setattr(kokoro, "FILES", {"model.onnx": hashlib.sha256(data).hexdigest()})
    calls = []
    def download(*args, **kwargs):
        calls.append(args)
        return io.BytesIO(data)
    monkeypatch.setattr(kokoro.urllib.request, "urlopen", download)
    kokoro.setup(progress=lambda _: None)
    kokoro.setup(progress=lambda _: None)
    assert len(calls) == 1
    assert (tmp_path / "model.onnx").read_bytes() == data


def test_failed_download_does_not_replace_existing_model(monkeypatch, tmp_path):
    monkeypatch.setenv("PAIBOT_VIDEO_KOKORO_DIR", str(tmp_path))
    monkeypatch.setattr(kokoro, "FILES", {"model.onnx": hashlib.sha256(b"expected").hexdigest()})
    target = tmp_path / "model.onnx"
    target.write_bytes(b"old model")
    monkeypatch.setattr(kokoro.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"incomplete"))
    with pytest.raises(tts.TTSError, match="checksum"):
        kokoro.setup(progress=lambda _: None)
    assert target.read_bytes() == b"old model"
    assert list(tmp_path.iterdir()) == [target]
