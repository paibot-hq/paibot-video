"""A small deterministic music generator.

Every style is a chord progression, a drum pattern and a few synthesized
instruments (additive oscillators, noise drums, a convolution reverb). The same
style, seed and length always give the same track, so videos are reproducible.
"""

from __future__ import annotations

import math
import os
import hashlib
import json
import tempfile
import wave
from dataclasses import dataclass

import numpy as np

SR = 44100
TAU = 2 * math.pi


@dataclass(frozen=True)
class Style:
    bpm: float
    root: int  # MIDI note of the key's tonic (bass register)
    chords: tuple  # per bar: semitone offsets from root
    swing: float  # 0.5 = straight 16ths
    kick: tuple  # 16-step pattern
    snare: tuple
    hat: tuple
    open_hat: tuple = ()
    pump: float = 0.0  # sidechain depth
    reverb: float = 0.25
    describe: str = ""


MIN = (0, 3, 7)
MAJ = (0, 4, 7)

STYLES: dict[str, Style] = {
    "synthwave": Style(
        bpm=100, root=45,  # A minor: Am F C G
        chords=((0, 3, 7, 12), (-4, 0, 3, 8), (3, 7, 10, 15), (-2, 2, 5, 10)),
        swing=0.5, kick=(0, 8), snare=(4, 12), hat=(0, 2, 4, 6, 8, 10, 12, 14),
        pump=0.5, reverb=0.35, describe="80s neon: driving bass, arpeggios, gated drums"),
    "pop": Style(
        bpm=118, root=48,  # C major: C G Am F
        chords=((0, 4, 7, 12), (-5, -1, 2, 7), (-3, 0, 4, 9), (-7, -3, 0, 5)),
        swing=0.5, kick=(0, 4, 8, 12), snare=(4, 12), hat=(2, 6, 10, 14), open_hat=(14,),
        pump=0.35, reverb=0.25, describe="bright and upbeat: four-on-the-floor, plucky melody"),
    "lofi": Style(
        bpm=78, root=50,  # D: Dm7 G7 Cmaj7 Am7
        chords=((0, 3, 7, 10), (5, 9, 12, 15), (-2, 2, 5, 9), (-5, -2, 2, 5)),
        swing=0.6, kick=(0, 7, 10), snare=(4, 12), hat=(0, 2, 4, 6, 8, 10, 12, 14),
        pump=0.0, reverb=0.3, describe="relaxed lo-fi hip hop: dusty keys, swung drums"),
    "ambient": Style(
        bpm=64, root=45,  # A: Am(add9) Fmaj7 C Gsus
        chords=((0, 7, 12, 14), (-4, 3, 7, 12), (3, 10, 15, 19), (-2, 5, 10, 14)),
        swing=0.5, kick=(), snare=(), hat=(),
        pump=0.0, reverb=0.6, describe="slow and cinematic: wide pads, soft bells, no drums"),
}


def freq(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def additive(f: float, n: int, harmonics: int, rolloff: float, odd_only: bool = False, phase: float = 0.0) -> np.ndarray:
    """Band-limited saw/square-ish tone built from sine harmonics."""
    t = _t(n)
    out = np.zeros(n)
    k = 1
    while k <= harmonics and k * f < SR * 0.45:
        if not odd_only or k % 2 == 1:
            out += np.sin(TAU * k * f * t + phase * k) * (rolloff ** (k - 1)) / k
        k += 1
    return out


def env(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """ADSR over n samples (release at the end)."""
    e = np.full(n, s)
    na, nd, nr = int(a * SR), int(d * SR), int(r * SR)
    na = min(na, n)
    if na:
        e[:na] = np.linspace(0, 1, na, endpoint=False)
    if nd and na < n:
        m = min(nd, n - na)
        e[na:na + m] = np.linspace(1, s, nd, endpoint=False)[:m]
    if nr:
        m = min(nr, n)
        e[n - m:] *= np.linspace(1, 0, m)
    return e


def pluck_env(n: int, decay: float) -> np.ndarray:
    t = _t(n)
    a = np.minimum(1, t / 0.004)
    return a * np.exp(-t / decay)


class Mixer:
    def __init__(self, n: int):
        self.n = n
        self.buses: dict[str, np.ndarray] = {}

    def bus(self, name: str) -> np.ndarray:
        if name not in self.buses:
            self.buses[name] = np.zeros((2, self.n))
        return self.buses[name]

    def add(self, name: str, start: float, sig: np.ndarray, gain: float = 1.0, pan: float = 0.0) -> None:
        i = int(round(start * SR))
        if i >= self.n or i + len(sig) <= 0:
            return
        j0 = max(0, -i)
        sig = sig[j0:]
        i = max(i, 0)
        m = min(len(sig), self.n - i)
        b = self.bus(name)
        lg = gain * math.cos((pan + 1) * math.pi / 4) * math.sqrt(2)
        rg = gain * math.sin((pan + 1) * math.pi / 4) * math.sqrt(2)
        b[0, i:i + m] += sig[:m] * lg
        b[1, i:i + m] += sig[:m] * rg


# -- drums ----------------------------------------------------------------------

def kick(rng) -> np.ndarray:
    n = int(0.45 * SR)
    t = _t(n)
    f = 42 + 120 * np.exp(-t * 32)
    ph = TAU * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 7.5)
    click = rng.standard_normal(n) * np.exp(-t * 400) * 0.25
    return np.tanh((body + click) * 1.6)


def highpass_noise(rng, n: int, smooth: int = 6) -> np.ndarray:
    x = rng.standard_normal(n)
    k = np.ones(smooth) / smooth
    return x - np.convolve(x, k, mode="same")


def snare(rng) -> np.ndarray:
    n = int(0.35 * SR)
    t = _t(n)
    noise = highpass_noise(rng, n, 4) * np.exp(-t * 16)
    tone = np.sin(TAU * 185 * t) * np.exp(-t * 28)
    return noise * 0.55 + tone * 0.45


def hat(rng, open_: bool = False) -> np.ndarray:
    n = int((0.32 if open_ else 0.06) * SR)
    t = _t(n)
    return highpass_noise(rng, n, 2) * np.exp(-t * (9 if open_ else 70)) * 0.35


# -- effects ----------------------------------------------------------------------

def reverb(x: np.ndarray, rng, seconds: float = 2.2) -> np.ndarray:
    """Convolution with decaying stereo noise: cheap and smooth."""
    n_ir = int(seconds * SR)
    t = _t(n_ir)
    decay = np.exp(-t * 6.9 / seconds)
    ir = rng.standard_normal((2, n_ir)) * decay
    ir[:, : int(0.012 * SR)] = 0  # predelay
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True))
    size = 1 << int(math.ceil(math.log2(x.shape[1] + n_ir)))
    out = np.empty_like(x)
    for c in range(2):
        y = np.fft.irfft(np.fft.rfft(x[c], size) * np.fft.rfft(ir[c], size), size)
        out[c] = y[: x.shape[1]]
    return out


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    size = x.shape[-1]
    spec = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(size, 1 / SR)
    spec *= 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec, size, axis=-1)


def delay(x: np.ndarray, seconds: float, feedback: float, mix: float) -> np.ndarray:
    d = int(seconds * SR)
    out = x.copy()
    tap = x.copy()
    for k in range(1, 5):
        tap = np.roll(tap, d, axis=1) * feedback
        tap[:, :d] = 0
        # ping-pong: swap channels on each echo
        tap = tap[::-1]
        out += tap * mix
    return out


# -- generator --------------------------------------------------------------------

def _parameters(style: str, length: float, seed: int, bpm: float | None) -> tuple[int, float]:
    if style not in STYLES:
        raise ValueError("unknown music style; choose " + ", ".join(STYLES))
    if isinstance(length, bool) or not math.isfinite(length) or length < 1 / SR:
        raise ValueError("length must be finite and at least one audio sample (1/44100 second)")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    bpm = STYLES[style].bpm if bpm is None else bpm
    if isinstance(bpm, bool) or not math.isfinite(bpm) or not 40 <= bpm <= 300:
        raise ValueError("bpm must be a finite number between 40 and 300")
    return round(length * SR), float(bpm)


def generate(style: str, length: float, seed: int = 1, bpm: float | None = None) -> np.ndarray:
    total, bpm = _parameters(style, length, seed, bpm)
    length = total / SR
    st = STYLES[style]
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = beat * 4
    step = beat / 4
    bars = int(math.ceil(length / bar)) + 1
    n = int((bars * bar + 2.5) * SR)
    mx = Mixer(n)

    k_s, s_s = kick(rng), snare(rng)
    h_s, oh_s = hat(rng), hat(rng, True)
    # Small per-seed variations so different seeds sound different.
    transpose = int(rng.choice([0, 0, 2, -2, 3, -3]))
    order = list(range(len(st.chords)))
    if rng.random() < 0.5:
        order = order[:2] + order[2:][::-1]
    chords = [tuple(c + transpose for c in st.chords[i]) for i in order]
    root = st.root
    kicks: list[float] = []
    melody_note = 0

    def step_time(b: int, s: int) -> float:
        t = b * bar + s * step
        if s % 2 == 1:
            t += (st.swing - 0.5) * 2 * step
        return t

    for b in range(bars):
        ch = chords[b % len(chords)]
        t0 = b * bar
        intro = b < 2
        last = b == bars - 1
        fill = (b % 4 == 3) and not intro

        # pad
        pad_len = int((bar + 0.6) * SR)
        for off in ch:
            m = root + 12 + off
            for det, pan in ((-0.07, -0.6), (0.0, 0.0), (0.07, 0.6)):
                harm = 4 if style in ("lofi", "ambient") else 8
                sig = additive(freq(m + det), pad_len, harm, 0.62, phase=rng.random() * TAU)
                sig *= env(pad_len, 0.25 if style != "ambient" else 1.2, 0.3, 0.8, 0.6)
                mx.add("pad", t0, sig, 0.045 if style != "ambient" else 0.06, pan)

        if style == "ambient":
            # sparse bells
            for s in range(0, 16, 4):
                if rng.random() < 0.55:
                    m = root + 24 + int(rng.choice(ch)) + 12 * int(rng.random() < 0.3)
                    nlen = int(1.8 * SR)
                    sig = (np.sin(TAU * freq(m) * _t(nlen)) + 0.3 * np.sin(TAU * freq(m) * 2.01 * _t(nlen))) * pluck_env(nlen, 0.6)
                    mx.add("lead", step_time(b, s), sig, 0.07, float(rng.uniform(-0.7, 0.7)))
            # long bass
            nlen = int((bar + 0.4) * SR)
            mx.add("bass", t0, np.sin(TAU * freq(root + ch[0]) * _t(nlen)) * env(nlen, 0.8, 0.2, 0.9, 0.5), 0.16)
            continue

        # bass
        if not intro or style == "lofi":
            bass_steps = {"synthwave": range(0, 16, 2), "pop": (0, 3, 6, 8, 11, 14), "lofi": (0, 7, 10)}[style]
            for s in bass_steps:
                m = root + ch[0] + (12 if style == "synthwave" and s % 4 == 2 else 0)
                dur = {"synthwave": step * 1.8, "pop": step * 2.5, "lofi": beat * 1.4}[style]
                nlen = int(dur * SR)
                if style == "lofi":
                    sig = additive(freq(m), nlen, 3, 0.4) * env(nlen, 0.01, 0.2, 0.7, 0.15)
                else:
                    sig = additive(freq(m), nlen, 10, 0.7) * pluck_env(nlen, 0.16 if style == "synthwave" else 0.22)
                mx.add("bass", step_time(b, s), sig, 0.22 if style != "lofi" else 0.3)

        # arps / melody / keys
        if style == "synthwave":
            pattern = [0, 1, 2, 3, 2, 1, 0, 1] * 2
            for s in range(16):
                m = root + 24 + ch[pattern[s] % len(ch)]
                nlen = int(step * 1.6 * SR)
                sig = additive(freq(m), nlen, 9, 0.75, odd_only=True) * pluck_env(nlen, 0.09)
                mx.add("arp", step_time(b, s), sig, 0.05, -0.3 if s % 2 else 0.3)
        elif style == "pop":
            scale = [0, 2, 4, 7, 9, 12, 14, 16]
            for s in range(0, 16, 2):
                if rng.random() < 0.7 or s == 0:
                    melody_note = int(np.clip(melody_note + rng.integers(-2, 3), 0, len(scale) - 1))
                    if s == 0:
                        melody_note = min(range(len(scale)), key=lambda i: abs(scale[i] % 12 - (ch[1] - transpose) % 12))
                    m = root + 24 + transpose + scale[melody_note]
                    nlen = int(step * 2.2 * SR)
                    sig = (np.sin(TAU * freq(m) * _t(nlen)) + 0.35 * additive(freq(m) * 2, nlen, 3, 0.5)) * pluck_env(nlen, 0.18)
                    mx.add("lead", step_time(b, s), sig, 0.09 if not intro else 0.06, 0.15)
        elif style == "lofi":
            for s in (0, 6, 10):
                nlen = int(beat * 1.5 * SR)
                t = _t(nlen)
                for off in ch:
                    m = root + 12 + off
                    sig = (np.sin(TAU * freq(m) * t) + 0.25 * np.sin(TAU * freq(m) * 2 * t)) * (1 + 0.15 * np.sin(TAU * 5 * t))
                    mx.add("keys", step_time(b, s) + rng.uniform(0, 0.012), sig * pluck_env(nlen, 0.45), 0.05, -0.2)

        # drums
        if intro or last:
            continue
        for s in range(16):
            tt = step_time(b, s)
            if s in st.kick:
                mx.add("drums", tt, k_s, 0.55)
                kicks.append(tt)
            if s in st.snare:
                mx.add("snare", tt, s_s, 0.32)
            if s in st.hat:
                vel = 0.7 + 0.3 * rng.random()
                mx.add("drums", tt, h_s, 0.5 * vel, 0.25)
            if s in st.open_hat:
                mx.add("drums", tt, oh_s, 0.35, 0.25)
            if fill and s >= 12 and style != "lofi":
                mx.add("snare", tt, s_s, 0.12 + 0.05 * (s - 12))

    # final chord ring-out on the downbeat after the end of the music
    end_t = (bars - 1) * bar
    nlen = int(2.5 * SR)
    for off in chords[0]:
        sig = additive(freq(root + 12 + off), nlen, 6, 0.6) * env(nlen, 0.02, 0.4, 0.5, 2.0)
        mx.add("pad", end_t, sig, 0.05)

    # sidechain pump on the tonal parts
    if st.pump and kicks:
        g = np.ones(n)
        t = _t(int(0.4 * SR))
        dip = 1 - st.pump * np.exp(-t * 9)
        for kt in kicks:
            i = int(kt * SR)
            m = min(len(dip), n - i)
            if m > 0:
                g[i:i + m] = np.minimum(g[i:i + m], dip[:m])
        for name in ("pad", "bass", "arp", "keys"):
            if name in mx.buses:
                mx.buses[name] *= g

    if "arp" in mx.buses:
        mx.buses["arp"] = delay(mx.buses["arp"], beat * 0.75, 0.45, 0.6)
    send = np.zeros((2, n))
    for name, amt in (("pad", 1.0), ("arp", 0.8), ("lead", 0.8), ("snare", 0.7 if style == "synthwave" else 0.4), ("keys", 0.6)):
        if name in mx.buses:
            send += mx.buses[name] * amt
    wet = reverb(send, rng, 3.5 if style == "ambient" else 2.0)
    mix = sum(mx.buses.values()) + wet * st.reverb
    if style == "lofi":
        mix = lowpass(mix, 3200)
        crackle = (rng.random(n) < 0.0007) * rng.standard_normal(n) * 0.25 + rng.standard_normal(n) * 0.004
        mix += lowpass(np.vstack([crackle, crackle]), 6000)
    mix = np.tanh(mix * 1.3) / math.tanh(1.3)
    peak = float(np.max(np.abs(mix))) or 1.0
    mix *= 0.89 / peak
    mix = mix[:, :total]
    fade = int(min(1.5, length / 4) * SR)
    if fade:
        mix[:, -fade:] *= np.linspace(1, 0, fade) ** 1.5
    return mix


def write_wav(path: str, mix: np.ndarray) -> None:
    data = (np.clip(mix.T, -1, 1) * 32767).astype("<i2")
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # A unique temporary file also lets simultaneous renders share a cache.
    fd, tmp = tempfile.mkstemp(prefix=".music-", suffix=".part", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as file:
            with wave.open(file, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes(data.tobytes())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def track(style: str, length: float, seed: int, bpm: float | None, cache_dir: str) -> str:
    samples, tempo = _parameters(style, length, seed, bpm)
    os.makedirs(cache_dir, exist_ok=True)
    key = json.dumps([2, style, seed, tempo, samples]).encode()
    digest = hashlib.sha256(key).hexdigest()[:24]
    path = os.path.join(cache_dir, f"music-{style}-{digest}.wav")
    if not os.path.exists(path):
        write_wav(path, generate(style, samples / SR, seed, tempo))
    return path


def beat_of(style: str, bpm: float | None) -> float:
    return 60.0 / (bpm or STYLES[style].bpm)
