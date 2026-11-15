import json
import shutil
import subprocess
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from paibot_video.cli import main
from paibot_video.encode import audio_inputs
from paibot_video.music import SR, STYLES, generate, track, write_wav
from paibot_video.render import Renderer
from paibot_video.spec import MUSIC_STYLES, json_schema, validate
from paibot_video.timeline import plan


def video(music):
    return {"width": 320, "height": 320, "fps": 12, "music": music,
            "scenes": [{"duration": 2, "elements": [{"type": "text", "text": "Music", "size": 30}]}]}


@pytest.mark.parametrize("music", [
    *MUSIC_STYLES, False, None, "tone.wav", {"src": "tone.wav"},
    {"generate": "lofi", "seed": 2, "bpm": 90, "duck": 1},
])
def test_music_schema_and_runtime_accept_the_same_forms(tmp_path, music):
    write_wav(tmp_path / "tone.wav", np.zeros((2, 100)))
    data = video(music)
    schema = json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(data)
    spec, issues = validate(data, str(tmp_path))
    assert spec is not None, issues


@pytest.mark.parametrize("music", [
    {}, True, [], "", {"generate": None}, {"src": None}, {"generate": "unknown"},
    {"generate": "pop", "src": "tone.wav"}, {"generate": "pop", "src": None},
    {"generate": "lofi", "bpm": 0}, {"generate": "lofi", "bpm": 301},
    {"generate": "pop", "duck": 1.1}, {"generate": "pop", "seed": -1},
])
def test_music_schema_and_runtime_reject_invalid_forms(tmp_path, music):
    write_wav(tmp_path / "tone.wav", np.zeros((2, 100)))
    data = video(music)
    assert not Draft202012Validator(json_schema()).is_valid(data)
    spec, issues = validate(data, str(tmp_path))
    assert spec is None
    assert any(i.level == "error" and i.path.startswith("$.music") for i in issues)


@pytest.mark.parametrize("field,value", [("bpm", float("inf")), ("duck", float("nan"))])
def test_non_finite_music_numbers_are_reported(field, value):
    spec, issues = validate(video({"generate": "lofi", field: value}))
    assert spec is None
    assert any(i.path == f"$.music.{field}" and "finite" in i.message for i in issues)


def test_music_file_must_not_be_a_directory(tmp_path):
    spec, issues = validate(video({"src": str(tmp_path)}))
    assert spec is None and any(i.path == "$.music.src" for i in issues)


def test_themes_supply_music_and_allow_overrides():
    for theme, expected in [("neon", "synthwave"), ("pop", "pop"), ("cinematic", "ambient")]:
        data = {"theme": theme, "scenes": [{}]}
        spec, _ = validate(data)
        assert spec.top["music"]["generate"] == expected
        for override in [False, None]:
            spec, _ = validate({**data, "music": override})
            assert spec.top["music"] is None
        spec, _ = validate({**data, "music": "lofi"})
        assert spec.top["music"]["generate"] == "lofi"
    spec, _ = validate({"scenes": [{}]})
    assert spec.top["music"] is None


@pytest.mark.parametrize("style", MUSIC_STYLES)
def test_generated_tracks_have_exact_length_and_reproducible_nonclipping_audio(style, tmp_path):
    mix = generate(style, 0.401, seed=3)
    assert mix.shape == (2, round(0.401 * SR))
    assert np.isfinite(mix).all()
    assert 0.0001 < np.sqrt(np.mean(mix ** 2)) < 0.8
    assert np.max(np.abs(mix)) <= 0.89 + 1e-12
    assert np.max(np.abs(mix[:, -1])) == 0
    np.testing.assert_array_equal(mix, generate(style, 0.401, seed=3))
    assert not np.array_equal(mix, generate(style, 0.401, seed=4))
    path = tmp_path / style / "music.wav"
    write_wav(path, mix)
    with wave.open(str(path)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (2, 2, SR, mix.shape[1])


def test_cache_distinguishes_close_lengths_and_reuses_equivalent_tempos(tmp_path, monkeypatch):
    a = track("pop", 0.201, 1, None, str(tmp_path))
    b = track("pop", 0.204, 1, None, str(tmp_path))
    assert a != b
    with wave.open(a) as wav:
        assert wav.getnframes() == round(0.201 * SR)
    with wave.open(b) as wav:
        assert wav.getnframes() == round(0.204 * SR)
    def unexpected(*args, **kwargs):
        pytest.fail("cached track was regenerated")
    monkeypatch.setattr("paibot_video.music.generate", unexpected)
    assert track("pop", 0.201, 1, STYLES["pop"].bpm, str(tmp_path)) == a


def test_concurrent_wav_writes_are_atomic(tmp_path):
    path = tmp_path / "shared.wav"
    mix = np.full((2, 4410), 0.1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: write_wav(path, mix), range(8)))
    with wave.open(str(path)) as wav:
        assert wav.getnframes() == 4410
        assert len(wav.readframes(4410)) == 4410 * 4
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("args", [
    ["--length", "0"], ["--length", "-2"], ["--length", "nan"], ["--length", "inf"],
    ["--seed", "-1"], ["--bpm", "0"], ["--bpm", "nan"], ["--bpm", "301"],
])
def test_music_cli_rejects_bad_values_without_traceback(args, capsys):
    assert main(["music", "lofi", *args]) == 1
    result = capsys.readouterr()
    assert "error:" in result.err and "Traceback" not in result.err


def test_music_cli_lists_and_writes_styles(tmp_path, capsys):
    assert main(["music"]) == 0
    listing = capsys.readouterr().out
    assert all(style in listing for style in MUSIC_STYLES)
    out = tmp_path / "nested" / "sample.wav"
    assert main(["music", "lofi", "--length", "0.25", "-o", str(out)]) == 0
    assert str(out) in capsys.readouterr().out
    with wave.open(str(out)) as wav:
        assert wav.getnframes() == round(0.25 * SR)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ffmpeg_ducks_music_during_voice_and_restores_it_afterwards(tmp_path):
    # A silent narration isolates the BGM in the mixed output, so its level is measurable.
    write_wav(tmp_path / "voice.wav", np.zeros((2, SR)))
    sine = 0.2 * np.sin(2 * np.pi * 440 * np.arange(4 * SR) / SR)
    write_wav(tmp_path / "music.wav", np.vstack([sine, sine]))
    data = video({"src": "music.wav", "volume": 0.5, "duck": 0.8, "fadeOut": 0})
    data["scenes"] = [{"duration": 4, "audio": "voice.wav", "narrationStart": 1.25}]
    spec, issues = validate(data, str(tmp_path))
    assert spec is not None, issues
    tl, issues = plan(spec, synth=False)
    assert not [i for i in issues if i.level == "error"], issues
    args, graph = audio_inputs(Renderer(spec, tl), tl)
    out = subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=16x16:d=4",
        *args, "-filter_complex", graph, "-map", "[aout]", "-t", "4", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1",
    ], check=True, capture_output=True)
    samples = np.frombuffer(out.stdout, dtype="<f4")
    def rms(a, b):
        return np.sqrt(np.mean(samples[int(a * 48000):int(b * 48000)] ** 2))
    before, during, after = rms(0.2, 0.4), rms(1.5, 1.8), rms(3, 3.3)
    assert before > 0.01
    assert during / before == pytest.approx(0.2, abs=0.015)
    assert after / before == pytest.approx(1, abs=0.015)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")
def test_cli_renders_generated_music_to_mp4(tmp_path):
    source = tmp_path / "video.json"
    source.write_text(json.dumps(video({"generate": "pop", "seed": 2, "bpm": 110})))
    out = tmp_path / "video.mp4"
    assert main(["render", str(source), "-o", str(out), "-q"]) == 0
    info = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(out),
    ]))
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2, abs=0.1)
