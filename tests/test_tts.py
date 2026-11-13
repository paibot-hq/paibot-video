import pytest

from paibot_video import tts
from paibot_video.spec import validate
from paibot_video.timeline import plan

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
