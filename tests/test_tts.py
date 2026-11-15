import shlex
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from paibot_video import tts
from paibot_video.spec import json_schema, validate
from paibot_video.timeline import plan, split_captions

SPEAKERS = [
    {"name": "まお", "styles": [{"name": "ノーマル", "id": 10}, {"name": "おちつき", "id": 13}]},
    {"name": "コハク", "styles": [{"name": "ノーマル", "id": 20}]},
]


def test_resolve_voice_names_styles_and_ids():
    r = tts.HttpEngine._resolve
    assert r(SPEAKERS, None) == (10, "まお/ノーマル")
    assert r(SPEAKERS, "コハク") == (20, "コハク/ノーマル")
    assert r(SPEAKERS, "まお/おちつき") == (13, "まお/おちつき")
    assert r(SPEAKERS, "13") == (13, "まお/おちつき")


def test_unknown_voice_lists_the_choices():
    with pytest.raises(tts.TTSError) as e:
        tts.HttpEngine._resolve(SPEAKERS, "Kyoko")
    assert '"まお/おちつき"' in e.value.hint and '"コハク/ノーマル"' in e.value.hint
    with pytest.raises(tts.TTSError) as e:
        tts.HttpEngine._resolve(SPEAKERS, "まお/ねむい")
    assert '"まお/おちつき"' in e.value.hint


def test_engine_not_running_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("PAIBOT_VIDEO_AIVIS_URL", "http://127.0.0.1:9")  # nothing listens there
    spec, _ = validate({"tts": "aivisspeech", "scenes": [{"narration": "こんにちは"}]})
    tl, issues = plan(spec)
    assert any("not running" in i.message and "PAIBOT_VIDEO_AIVIS_URL" in i.hint for i in issues)


def test_command_backend_is_used_when_given(tmp_path):
    spec, _ = validate({"scenes": [{"narration": "hi"}]})
    script = tmp_path / "fake.sh"
    script.write_text('#!/bin/sh\nffmpeg -v error -f lavfi -i "sine=d=0.5" "$1"\n')
    script.chmod(0o755)
    tl, issues = plan(spec, tts_command=f"{script} {{out}}", cache_dir=str(tmp_path / "c"))
    assert not issues, issues
    assert tl.voice.startswith(str(script))
    assert tl.scenes[0].audio_len == pytest.approx(0.5, abs=0.05)


def system_backend(monkeypatch, platform="darwin", available=("say",)):
    monkeypatch.delenv("PAIBOT_VIDEO_TTS", raising=False)
    monkeypatch.setattr(tts.sys, "platform", platform)
    monkeypatch.setattr(tts.shutil, "which", lambda name: f"/usr/bin/{name}" if name in available else None)


@pytest.mark.parametrize("language,cjk", [("auto", False), ("en", False), ("en", True)])
def test_english_auto_uses_english_voice_even_when_japanese_engines_are_running(monkeypatch, language, cjk):
    system_backend(monkeypatch)
    def unexpected(name):
        pytest.fail(f"English auto selection queried Japanese engine {name}")
    monkeypatch.setattr(tts, "engine_alive", unexpected)
    spec, _ = validate({"language": language, "scenes": [{"narration": "Hello from Tokyo."}]})
    backend = tts.choose(spec.top, cjk)
    assert isinstance(backend, tts.CommandTTS)
    assert backend.voice == "Samantha" and backend.language == "en"
    assert backend.command.startswith("say ")


@pytest.mark.parametrize("language,cjk", [("auto", True), ("ja", False)])
def test_japanese_auto_still_prefers_local_engine(monkeypatch, language, cjk):
    monkeypatch.delenv("PAIBOT_VIDEO_TTS", raising=False)
    monkeypatch.setattr(tts, "engine_alive", lambda name: "1.2.0")
    monkeypatch.setattr(tts, "HttpEngine", lambda *args: args)
    spec, _ = validate({"language": language, "scenes": [{"narration": "こんにちは"}]})
    assert tts.choose(spec.top, cjk)[0] == "aivisspeech"


def test_english_explicit_voice_and_rate_are_kept(monkeypatch):
    system_backend(monkeypatch)
    spec, _ = validate({"language": "en", "voice": "Daniel", "rate": 155, "scenes": [{}]})
    backend = tts.choose(spec.top, False)
    assert backend.voice == "Daniel" and backend.rate == 155
    assert "-r {rate}" in backend.command


@pytest.mark.parametrize("platform,language,voice,available", [
    ("linux", "en", "en", ("espeak-ng",)),
    ("linux", "ja", "ja", ("espeak-ng",)),
    ("darwin", "ja", "Kyoko", ("say",)),
    ("darwin", "en", "en", ("espeak-ng",)),
])
def test_system_fallback_uses_the_requested_language(monkeypatch, platform, language, voice, available):
    system_backend(monkeypatch, platform, available)
    spec, _ = validate({"tts": "system", "language": language, "scenes": [{}]})
    backend = tts.choose(spec.top, language == "ja")
    assert backend.voice == voice and backend.language == language


def test_english_missing_engine_error_explains_english_setup(monkeypatch):
    system_backend(monkeypatch, "linux", ())
    spec, _ = validate({"language": "en", "scenes": [{}]})
    with pytest.raises(tts.TTSError) as error:
        tts.choose(spec.top, False)
    assert "for en" in str(error.value)
    assert "espeak-ng" in error.value.hint and "{language}" in error.value.hint
    assert "start AivisSpeech" not in error.value.hint


def test_explicit_engine_is_not_overridden_by_language(monkeypatch):
    monkeypatch.setattr(tts, "HttpEngine", lambda *args: args)
    spec, _ = validate({"language": "en", "tts": "aivisspeech", "voice": "custom", "scenes": [{}]})
    assert tts.choose(spec.top, False)[:2] == ("aivisspeech", "custom")


def test_custom_command_gets_language_and_caches_it_separately(tmp_path, monkeypatch):
    script = tmp_path / "speak.py"
    script.write_text(
        "import sys, wave\n"
        "from pathlib import Path\n"
        "Path(sys.argv[2] + '.language').write_text(sys.argv[1])\n"
        "with wave.open(sys.argv[2], 'wb') as out:\n"
        "    out.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))\n"
        "    out.writeframes(b'\\0\\0' * 2400)\n"
    )
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{language}} {{out}}"
    monkeypatch.setenv("PAIBOT_VIDEO_TTS", command)
    outputs = []
    for language in ("ja", "en"):
        spec, _ = validate({"language": language, "scenes": [{"narration": "same text"}]})
        backend = tts.choose(spec.top, False)
        out = backend.synth("same text", str(tmp_path / "cache"))
        assert Path(out + ".part.wav.language").read_text() == language
        assert backend.synth("same text", str(tmp_path / "cache")) == out
        outputs.append(out)
    assert outputs[0] != outputs[1]


@pytest.mark.parametrize("language,narration,expected", [
    ("auto", "Hello, world.", "en"),
    ("auto", "こんにちは。", "ja"),
    ("en", "A guide to 東京.", "en"),
    ("ja", "Python and JSON", "ja"),
])
def test_planning_reports_language_without_starting_tts(language, narration, expected, monkeypatch):
    monkeypatch.setattr(tts, "choose", lambda *args: pytest.fail("synth=False started TTS"))
    data = {"language": language, "captions": True, "scenes": [{"narration": narration}]}
    Draft202012Validator(json_schema()).validate(data)
    spec, _ = validate(data)
    tl, issues = plan(spec, synth=False)
    assert not issues and tl.language == expected and tl.voice is None


def test_invalid_narration_language_is_reported_in_schema_and_check():
    data = {"language": "english", "scenes": [{}]}
    assert not Draft202012Validator(json_schema()).is_valid(data)
    spec, issues = validate(data)
    assert spec is None and any(i.path == "$.language" for i in issues)


def test_english_captions_preserve_decimal_numbers_and_word_boundaries():
    text = "Python 3.14 processes 1,200 records. Generate clear English narration and captions for your videos."
    caps = split_captions(text, 0.3, 10, max_len=36)
    assert " ".join(c.text for c in caps) == text
    assert caps[0].start == 0.3 and caps[-1].end == pytest.approx(10.3)
    assert all(a.end == pytest.approx(b.start) for a, b in zip(caps, caps[1:]))


def test_caption_punctuation_and_japanese_are_preserved():
    text = "こんにちは！Python 3.14です。次へ。"
    caps = split_captions(text, 0, 6)
    assert "".join(c.text for c in caps).replace(" ", "") == text.replace(" ", "")
