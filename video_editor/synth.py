"""직접 합성하는 효과음과 배경 음악 (저작권 걱정이 없는 소리: 전부 이 코드가 숫자로 만든 소리입니다).

외부 샘플이나 밈 음원은 쓰지 않습니다. 효과음 20종(`SFX`)과 음악 9곡(`BGM`)을 만들 수 있고,
`library.py`가 보관함에 등록합니다. 소리는 모두 48kHz 모노이고 최대 크기를 0.8 정도로 맞춥니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pedalboard as pb

from .engine import SAMPLE_RATE as SR

TAU = 2 * np.pi
VERSION = 1   # 합성 방식을 바꾸면 올려서 보관함의 소리를 새로 만들게 합니다


# ---------------------------------------------------------------- 기본 부품

def tt(d: float) -> np.ndarray:
    return np.arange(int(round(SR * d))) / SR


def midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def decay_env(d: float, tau: float, attack: float = 0.003) -> np.ndarray:
    t = tt(d)
    e = np.exp(-t / max(tau, 1e-4))
    a = int(SR * attack)
    if a > 1:
        e[:a] *= np.linspace(0, 1, a)
    return e


def phase(freq) -> np.ndarray:
    return np.cumsum(freq) / SR


def sine(freq, n: int | None = None) -> np.ndarray:
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    return np.sin(TAU * phase(f))


def saw(freq, n: int | None = None) -> np.ndarray:
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    return 2 * (phase(f) % 1.0) - 1


def square(freq, n: int | None = None, duty: float = 0.5) -> np.ndarray:
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    return np.where((phase(f) % 1.0) < duty, 1.0, -1.0)


def tri(freq, n: int | None = None) -> np.ndarray:
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=float)
    return 2 * np.abs(2 * (phase(f) % 1.0) - 1) - 1


_rng = np.random.default_rng(7)


def noise(n: int) -> np.ndarray:
    return _rng.uniform(-1, 1, n)


def hp(x: np.ndarray, hz: float = 3000) -> np.ndarray:
    return pb.Pedalboard([pb.HighpassFilter(hz)])(x.astype(np.float32), SR).astype(np.float64)


def lp(x: np.ndarray, hz: float = 1200) -> np.ndarray:
    return pb.Pedalboard([pb.LowpassFilter(hz)])(x.astype(np.float32), SR).astype(np.float64)


def bp(x: np.ndarray, hz: float, q: float = 2.0) -> np.ndarray:
    return pb.Pedalboard([pb.HighpassFilter(hz / 1.6), pb.LowpassFilter(hz * 1.6)])(x.astype(np.float32), SR).astype(np.float64)


def mix_at(buf: np.ndarray, t: float, x: np.ndarray, gain: float = 1.0) -> None:
    i = int(round(t * SR))
    if i >= buf.size or i < 0:
        return
    x = x[: buf.size - i]
    buf[i: i + x.size] += x * gain


def finish(x: np.ndarray, peak: float = 0.8, fade: float = 0.01) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    x = x - np.mean(x)
    m = np.max(np.abs(x)) or 1.0
    x = x * (peak / m)
    n = min(int(SR * fade), x.size // 2)
    if n > 1:
        r = np.linspace(0, 1, n)
        x[:n] *= r
        x[-n:] *= r[::-1]
    return x.astype(np.float32)


def note(f: float, d: float, kind: str = "sine", tau: float = 0.2, attack: float = 0.004, vib: float = 0.0) -> np.ndarray:
    t = tt(d)
    n = t.size
    fr = np.full(n, f) * (1 + vib * np.sin(TAU * 5.5 * t))
    wave = {"sine": sine, "saw": saw, "square": square, "tri": tri}[kind](fr, n)
    return wave * decay_env(d, tau, attack)


# ---------------------------------------------------------------- 드럼

def kick(d: float = 0.35, f0: float = 150, f1: float = 45) -> np.ndarray:
    t = tt(d)
    f = f1 + (f0 - f1) * np.exp(-t / 0.03)
    return np.sin(TAU * phase(f)) * decay_env(d, 0.12, 0.001) * 1.1


def snare(d: float = 0.25) -> np.ndarray:
    n = int(SR * d)
    return (hp(noise(n), 1500) * 0.9 + sine(190, n) * 0.5) * decay_env(d, 0.07, 0.001)


def hat(d: float = 0.06, open_: bool = False) -> np.ndarray:
    d = 0.25 if open_ else d
    return hp(noise(int(SR * d)), 7000) * decay_env(d, 0.09 if open_ else 0.02, 0.0005) * 0.6


def clap(d: float = 0.22) -> np.ndarray:
    out = np.zeros(int(SR * d))
    for k, off in enumerate((0.0, 0.012, 0.024)):
        mix_at(out, off, hp(noise(int(SR * 0.05)), 1200) * decay_env(0.05, 0.012, 0.0005), 0.7)
    mix_at(out, 0.03, hp(noise(int(SR * 0.18)), 1200) * decay_env(0.18, 0.06, 0.0005), 0.8)
    return out


# ---------------------------------------------------------------- 효과음

def sfx_pop() -> np.ndarray:
    d = 0.22; t = tt(d)
    f = 350 + 1100 * (1 - np.exp(-t / 0.025))
    return finish(np.sin(TAU * phase(f)) * decay_env(d, 0.06, 0.001))


def sfx_boing() -> np.ndarray:
    d = 0.9; t = tt(d)
    f = 180 + 520 * np.exp(-t / 0.35) + 60 * np.sin(TAU * 11 * t) * np.exp(-t / 0.4)
    return finish(np.sin(TAU * phase(f)) * decay_env(d, 0.35, 0.002) + 0.3 * np.sin(TAU * phase(f * 2)) * decay_env(d, 0.2))


def sfx_ding() -> np.ndarray:
    d = 1.4; t = tt(d)
    x = sum(np.sin(TAU * f * t) * a * np.exp(-t / tau) for f, a, tau in ((1320, 1, 0.5), (1320 * 2.76, 0.3, 0.2), (1320 * 5.4, 0.12, 0.08)))
    return finish(x)


def sfx_dingdong() -> np.ndarray:
    out = np.zeros(int(SR * 2.0))
    for off, f in ((0.0, 880), (0.55, 660)):
        t = tt(1.2)
        mix_at(out, off, sum(np.sin(TAU * f * k * t) * a * np.exp(-t / tau) for k, a, tau in ((1, 1, 0.5), (2.4, 0.3, 0.15), (3, 0.15, 0.25))))
    return finish(out)


def sfx_beep() -> np.ndarray:
    d = 0.9
    return finish(square(1000, int(SR * d)) * 0.5 + sine(1000, int(SR * d)) * 0.5, fade=0.004)


def sfx_whoosh() -> np.ndarray:
    d = 0.7; n = int(SR * d); t = tt(d)
    x = noise(n)
    out = np.zeros(n)
    # 가운데로 갈수록 높고 밝아졌다가 사라지는 소리: 구간마다 필터 중심을 옮깁니다
    for i, f in enumerate(np.geomspace(400, 5000, 8)):
        seg = bp(x, f)
        w = np.exp(-((t - (0.15 + 0.5 * i / 8 * 0.6)) ** 2) / (2 * 0.12 ** 2))
        out += seg * w
    return finish(out * np.sin(np.pi * np.clip(t / d, 0, 1)) ** 0.8)


def sfx_riser() -> np.ndarray:
    d = 2.2; n = int(SR * d); t = tt(d)
    f = 150 * 2 ** (t / d * 3.5)
    x = 0.5 * np.sin(TAU * phase(f)) + 0.6 * hp(noise(n), 1000) * (t / d)
    return finish(x * (t / d) ** 1.5, fade=0.03)


def sfx_impact() -> np.ndarray:
    d = 1.6; t = tt(d); n = t.size
    f = 38 + 90 * np.exp(-t / 0.05)
    x = np.sin(TAU * phase(f)) * np.exp(-t / 0.55) + 0.4 * lp(noise(n), 400) * np.exp(-t / 0.2)
    return finish(x)


def sfx_punch() -> np.ndarray:
    d = 0.3; n = int(SR * d)
    x = kick(d, 220, 70) + 0.5 * lp(noise(n), 2500) * decay_env(d, 0.04, 0.0005)
    return finish(x)


def sfx_sparkle() -> np.ndarray:
    out = np.zeros(int(SR * 1.5))
    for i, k in enumerate((0, 4, 7, 12, 16, 19, 24, 28)):
        at = 0.07 * i
        mix_at(out, at, note(midi(84 + k % 12 + (12 if k > 12 else 0)), 0.5, "sine", 0.12), 0.8 - 0.05 * i)
        mix_at(out, at, note(midi(96 + k % 12), 0.3, "sine", 0.06), 0.25)
    return finish(out)


def sfx_fail() -> np.ndarray:
    out = np.zeros(int(SR * 2.6))
    for i, (off, midi_n, d) in enumerate(((0.0, 62, 0.45), (0.5, 61, 0.45), (1.0, 60, 0.45), (1.5, 59, 1.0))):
        n = int(SR * d); t = tt(d)
        vib = 1 + (0.03 * np.sin(TAU * 7 * t) if i == 3 else 0)
        f = midi(midi_n) * vib * (1 - (0.25 * np.clip(t - 0.3, 0, 1) if i == 3 else 0))
        x = lp(saw(f, n) * decay_env(d, 0.5, 0.01), 1400 if i < 3 else 900)
        mix_at(out, off, x)
    return finish(out)


def sfx_win() -> np.ndarray:
    out = np.zeros(int(SR * 2.0))
    for off, notes, d in ((0.0, (60, 64, 67), 0.14), (0.14, (64, 67, 72), 0.14), (0.28, (67, 72, 76), 0.14), (0.42, (72, 76, 79, 84), 1.2)):
        for m in notes:
            mix_at(out, off, note(midi(m), d, "tri", 0.4 if d > 1 else 0.1), 0.35)
    return finish(out)


def sfx_coin() -> np.ndarray:
    out = np.zeros(int(SR * 0.7))
    mix_at(out, 0.0, note(988, 0.08, "square", 0.3), 0.5)
    mix_at(out, 0.07, note(1319, 0.5, "square", 0.18), 0.5)
    return finish(out)


def _formant(x, f1, f2):
    return bp(x, f1) + 0.7 * bp(x, f2)


def sfx_laugh() -> np.ndarray:
    """'하하하' 흉내: 음높이가 내려가는 짧은 모음 덩어리 여러 번 (사람 웃음 녹음이 아니라 합성 소리예요)."""
    out = np.zeros(int(SR * 1.8))
    for i in range(6):
        d = 0.16; n = int(SR * d); t = tt(d)
        f0 = 260 - 14 * i
        voiced = _formant(saw(f0 * (1 + 0.04 * np.sin(TAU * 6 * t)), n), 800, 1300)
        breath = _formant(noise(n), 1000, 2500) * 0.4
        mix_at(out, 0.2 * i + (0.08 if i > 2 else 0), (voiced + breath) * decay_env(d, 0.07, 0.01), 1.0 - 0.08 * i)
    return finish(out)


def sfx_clap() -> np.ndarray:
    out = np.zeros(int(SR * 2.2))
    rng = np.random.default_rng(3)
    for k in range(70):
        mix_at(out, rng.uniform(0, 1.6), clap(0.12), rng.uniform(0.15, 0.5))
    env = np.minimum(1, tt(2.2) / 0.3) * np.minimum(1, (2.2 - tt(2.2)) / 0.6)
    return finish(out * env, fade=0.02)


def sfx_drumroll() -> np.ndarray:
    d = 2.0; out = np.zeros(int(SR * d))
    t = 0.0
    while t < d - 0.1:
        mix_at(out, t, snare(0.1), 0.25 + 0.75 * (t / d))
        t += 0.055 - 0.02 * (t / d)
    mix_at(out, d - 0.08, kick(0.4), 1.2)
    return finish(out)


def sfx_shutter() -> np.ndarray:
    out = np.zeros(int(SR * 0.4))
    for off, g in ((0.0, 1.0), (0.09, 0.8)):
        mix_at(out, off, bp(noise(int(SR * 0.04)), 3500) * decay_env(0.04, 0.008, 0.0003) * 2 + note(180, 0.04, "sine", 0.01) * 0.5, g)
    return finish(out)


def sfx_typing() -> np.ndarray:
    out = np.zeros(int(SR * 1.5)); rng = np.random.default_rng(5); t = 0.0
    while t < 1.35:
        mix_at(out, t, bp(noise(int(SR * 0.03)), rng.uniform(2200, 3800)) * decay_env(0.03, 0.006, 0.0003), rng.uniform(0.5, 1.0))
        t += rng.uniform(0.05, 0.14)
    return finish(out)


def sfx_heartbeat() -> np.ndarray:
    out = np.zeros(int(SR * 2.0))
    for off in (0.0, 0.9):
        mix_at(out, off, kick(0.25, 90, 45))
        mix_at(out, off + 0.2, kick(0.25, 80, 42), 0.7)
    return finish(out)


def sfx_scratch() -> np.ndarray:
    d = 0.6; n = int(SR * d); t = tt(d)
    f = 400 + 1500 * np.abs(np.sin(TAU * 2.2 * t))
    x = hp(saw(f, n), 500) * 0.6 + hp(noise(n), 2000) * 0.3
    return finish(x * np.minimum(1, (d - t) / 0.1))


def sfx_question() -> np.ndarray:
    out = np.zeros(int(SR * 0.9))
    mix_at(out, 0.0, note(midi(67), 0.18, "tri", 0.2), 0.8)
    mix_at(out, 0.2, note(midi(74), 0.5, "tri", 0.25), 0.9)
    return finish(out)


def sfx_stinger() -> np.ndarray:
    d = 1.2; n = int(SR * d); t = tt(d)
    f = midi(88) * (1 + 0.02 * np.sin(TAU * 8 * t))
    x = (saw(f, n) + saw(f * 1.5, n) * 0.6 + square(f * 1.06, n) * 0.3) * decay_env(d, 0.35, 0.002)
    return finish(lp(x, 5000) + 0.4 * kick(d, 120, 50))


def sfx_horn() -> np.ndarray:
    d = 0.9; n = int(SR * d); t = tt(d)
    f = 330 * (1 + 0.5 * np.exp(-t / 0.05))
    x = lp(saw(f, n) + 0.5 * saw(f * 1.01, n), 2500) * np.minimum(1, (d - t) / 0.2) * decay_env(d, 0.9, 0.01)
    return finish(x)


@dataclass
class SfxSpec:
    key: str
    name: str
    tags: list[str]
    fn: Callable[[], np.ndarray]


SFX: list[SfxSpec] = [
    SfxSpec("pop", "뿅", ["귀여움", "등장", "pop"], sfx_pop),
    SfxSpec("boing", "띠용", ["놀람", "코믹", "boing"], sfx_boing),
    SfxSpec("ding", "딩!", ["정답", "알림", "ding"], sfx_ding),
    SfxSpec("dingdong", "딩동", ["알림", "정답", "초인종"], sfx_dingdong),
    SfxSpec("beep", "삐- (검열음)", ["검열", "방송사고", "beep"], sfx_beep),
    SfxSpec("whoosh", "휙 (전환)", ["전환", "이동", "whoosh"], sfx_whoosh),
    SfxSpec("riser", "상승음 (긴장 고조)", ["긴장", "빌드업", "riser"], sfx_riser),
    SfxSpec("impact", "두둥 (쾅)", ["충격", "강조", "impact"], sfx_impact),
    SfxSpec("punch", "퍽", ["타격", "액션", "punch"], sfx_punch),
    SfxSpec("sparkle", "반짝", ["반짝", "마법", "sparkle"], sfx_sparkle),
    SfxSpec("fail", "빠바바밤 (실패)", ["실패", "슬픔", "코믹", "fail"], sfx_fail),
    SfxSpec("win", "짜잔 (성공)", ["성공", "승리", "fanfare"], sfx_win),
    SfxSpec("coin", "코인", ["게임", "보상", "coin"], sfx_coin),
    SfxSpec("laugh", "하하하 (합성 웃음)", ["웃음", "코믹", "laugh"], sfx_laugh),
    SfxSpec("clap", "박수", ["박수", "환호", "clap"], sfx_clap),
    SfxSpec("drumroll", "드럼롤", ["긴장", "발표", "drumroll"], sfx_drumroll),
    SfxSpec("shutter", "찰칵", ["카메라", "사진", "shutter"], sfx_shutter),
    SfxSpec("typing", "타닥타닥", ["키보드", "타자", "typing"], sfx_typing),
    SfxSpec("heartbeat", "두근두근", ["심장", "긴장", "heartbeat"], sfx_heartbeat),
    SfxSpec("scratch", "레코드 긁기", ["멈춤", "당황", "scratch"], sfx_scratch),
    SfxSpec("question", "응? (물음표)", ["의문", "코믹", "question"], sfx_question),
    SfxSpec("stinger", "깜짝 (스팅)", ["놀람", "공포", "stinger"], sfx_stinger),
    SfxSpec("horn", "빵~ (뿔피리)", ["코믹", "알림", "horn"], sfx_horn),
]


# ---------------------------------------------------------------- 배경 음악

def _bar_len(bpm: float) -> float:
    return 4 * 60.0 / bpm


@dataclass
class BgmSpec:
    key: str
    name: str
    mood: str
    tags: list[str]
    bpm: int
    fn: Callable[[], np.ndarray] = field(repr=False, default=None)


def _canvas(bpm: float, bars: int, tail: float = 1.2) -> tuple[np.ndarray, float, float]:
    beat = 60.0 / bpm
    return np.zeros(int(SR * (bars * 4 * beat + tail))), beat, bars * 4 * beat


def _chord_notes(root: int, quality: str) -> list[int]:
    return {"maj": [0, 4, 7], "min": [0, 3, 7], "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11], "dom7": [0, 4, 7, 10], "sus": [0, 5, 7]}[quality]
    

def _pad(buf, t, d, notes, gain=0.12, cutoff=1400, kind="saw"):
    n = tt(d).size
    x = np.zeros(n)
    for m in notes:
        f = midi(m)
        for det in (-0.004, 0.0, 0.004):
            x += (saw if kind == "saw" else tri)(f * (1 + det), n)
    x = lp(x / (3 * len(notes)), cutoff)
    env = np.minimum(1, tt(d) / 0.15) * np.minimum(1, (d - tt(d)) / 0.25)
    mix_at(buf, t, x * env, gain)


def _drums(buf, beat, total, pattern: dict, swing: float = 0.0, gain: float = 1.0):
    """pattern: 16분음표 칸 문자열 ('x' 한 번, 'X' 센 소리, '.' 쉼, 'o' 열린 하이햇)."""
    step = beat / 4
    bars = int(round(total / (4 * beat)))
    for b in range(bars):
        for i in range(16):
            t = b * 4 * beat + i * step + (swing * step if i % 2 else 0.0)
            for name, pat in pattern.items():
                ch = pat[i % len(pat)]
                if ch == ".":
                    continue
                g = 1.0 if ch in "XO" else 0.7
                if name == "kick":
                    mix_at(buf, t, kick(), 0.9 * g * gain)
                elif name == "snare":
                    mix_at(buf, t, snare(), 0.7 * g * gain)
                elif name == "clap":
                    mix_at(buf, t, clap(), 0.7 * g * gain)
                elif name == "hat":
                    mix_at(buf, t, hat(open_=ch == "o"), 0.5 * g * gain)


def _progression(buf, beat, bars, chords, bass_kind="tri", pad_gain=0.1, pad_cut=1200, bass_gain=0.5, bass_pattern="root", octave=36, pad_octave=60, kind="saw"):
    bar = 4 * beat
    for b in range(bars):
        root, q = chords[b % len(chords)]
        notes = [pad_octave + root + i for i in _chord_notes(root, q)]
        _pad(buf, b * bar, bar, notes, pad_gain, pad_cut, kind)
        f = midi(octave + root)
        if bass_pattern == "root":
            mix_at(buf, b * bar, note(f, bar * 0.95, bass_kind, bar * 0.6), bass_gain)
        elif bass_pattern == "eighth":
            for i in range(8):
                mix_at(buf, b * bar + i * beat / 2, note(f * (2 if i % 4 == 3 else 1), beat * 0.45, bass_kind, 0.15), bass_gain)
        elif bass_pattern == "offbeat":
            for i in range(4):
                mix_at(buf, b * bar + i * beat + beat / 2, note(f, beat * 0.45, bass_kind, 0.18), bass_gain)
        elif bass_pattern == "walk":
            for i, o in enumerate((0, 7, 12, 7)):
                mix_at(buf, b * bar + i * beat, note(midi(octave + root + o), beat * 0.6, "tri", 0.12), bass_gain)


def bgm_lofi() -> np.ndarray:
    bpm = 80; bars = 12
    buf, beat, total = _canvas(bpm, bars)
    ch = [(9, "m7"), (2, "m7"), (7, "dom7"), (0, "maj7")]
    _progression(buf, beat, bars, ch, "sine", 0.14, 900, 0.7, "root", 33, 57, "tri")
    _drums(buf, beat, total, {"kick": "X..x....x.x.....", "snare": "....X.......X...", "hat": "x.x.x.x.x.x.x.xo"}, swing=0.3, gain=0.8)
    for b in range(bars):
        root, q = ch[b % 4]
        for i, off in enumerate((0, 3, 7, 10)):
            mix_at(buf, b * 4 * beat + (2 + i * 0.75) * beat, note(midi(72 + root + off), beat * 1.2, "tri", 0.3), 0.12)
    buf = pb.Pedalboard([pb.LowpassFilter(5500), pb.Reverb(room_size=0.3, wet_level=0.12, dry_level=0.9)])(buf.astype(np.float32), SR)
    return finish(buf, 0.7, 0.05)


def bgm_pop() -> np.ndarray:
    bpm = 124; bars = 16
    buf, beat, total = _canvas(bpm, bars)
    ch = [(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")]
    _progression(buf, beat, bars, ch, "saw", 0.08, 2600, 0.4, "offbeat", 36, 60)
    _drums(buf, beat, total, {"kick": "X...X...X...X...", "clap": "....X.......X...", "hat": "..x...x...x...xo"})
    for b in range(bars):
        root, q = ch[b % 4]
        arp = [0, 4, 7, 12, 7, 4, 7, 12]
        for i, off in enumerate(arp):
            mix_at(buf, b * 4 * beat + i * beat / 2, note(midi(72 + root + off), beat * 0.4, "square", 0.1), 0.1)
    return finish(pb.Pedalboard([pb.Reverb(room_size=0.2, wet_level=0.08)])(buf.astype(np.float32), SR), 0.75, 0.05)


def bgm_tense() -> np.ndarray:
    bpm = 70; bars = 8
    buf, beat, total = _canvas(bpm, bars, 2.0)
    for b in range(bars):
        root = (0, 0, 3, 1)[b % 4]
        _pad(buf, b * 4 * beat, 4 * beat, [36 + root, 43 + root, 48 + root], 0.16, 600)
        for i in range(8):
            mix_at(buf, b * 4 * beat + i * beat / 2, note(midi(36 + root), beat * 0.4, "saw", 0.12), 0.18 * (0.5 + 0.5 * (i % 2 == 0)))
        mix_at(buf, b * 4 * beat, kick(0.6, 90, 35), 0.9)
    for i in range(0, bars * 4, 2):
        mix_at(buf, i * beat + beat, lp(noise(int(SR * 0.3)), 1500) * decay_env(0.3, 0.1) * 0.25)
    riser = sfx_riser() * 0.25
    mix_at(buf, total - 2.2, riser)
    return finish(pb.Pedalboard([pb.Reverb(room_size=0.7, wet_level=0.25)])(buf.astype(np.float32), SR), 0.7, 0.05)


def bgm_quirky() -> np.ndarray:
    bpm = 110; bars = 14
    buf, beat, total = _canvas(bpm, bars)
    ch = [(0, "maj"), (5, "maj"), (7, "dom7"), (0, "maj")]
    _progression(buf, beat, bars, ch, "tri", 0.0, 1000, 0.55, "walk", 36, 60)
    melody = [0, 4, 7, 4, 9, 7, 4, 0]
    for b in range(bars):
        root, _ = ch[b % 4]
        for i, off in enumerate(melody):
            if (b + i) % 3 == 2:
                continue
            mix_at(buf, b * 4 * beat + i * beat / 2, note(midi(76 + root + off), beat * 0.22, "tri", 0.07), 0.25)
        mix_at(buf, b * 4 * beat + beat, clap(0.15), 0.25)
        mix_at(buf, b * 4 * beat + 3 * beat, clap(0.15), 0.25)
    return finish(buf, 0.75, 0.05)


def bgm_chiptune() -> np.ndarray:
    bpm = 140; bars = 16
    buf, beat, total = _canvas(bpm, bars)
    ch = [(9, "min"), (5, "maj"), (0, "maj"), (7, "maj")]
    for b in range(bars):
        root, q = ch[b % 4]
        for i in range(16):
            arp = _chord_notes(root, q)
            mix_at(buf, b * 4 * beat + i * beat / 4, note(midi(69 + root + arp[i % 3] + (12 if i % 8 >= 4 else 0)), beat * 0.2, "square", 0.08), 0.12)
        for i in range(8):
            mix_at(buf, b * 4 * beat + i * beat / 2, note(midi(33 + root + (12 if i % 2 else 0)), beat * 0.4, "tri", 0.2), 0.45)
    step = beat / 4
    for b in range(bars):
        for i in range(16):
            t = b * 4 * beat + i * step
            if i % 8 == 0:
                mix_at(buf, t, kick(0.2, 120, 50), 0.8)
            if i % 8 == 4:
                mix_at(buf, t, hp(noise(int(SR * 0.12)), 2000) * decay_env(0.12, 0.04), 0.5)
            if i % 2 == 0:
                mix_at(buf, t, hp(noise(int(SR * 0.03)), 6000) * decay_env(0.03, 0.01), 0.25)
    return finish(buf, 0.7, 0.05)


def bgm_hiphop() -> np.ndarray:
    bpm = 90; bars = 12
    buf, beat, total = _canvas(bpm, bars)
    ch = [(0, "m7"), (8, "maj7"), (5, "m7"), (7, "dom7")]
    _progression(buf, beat, bars, ch, "sine", 0.12, 1100, 0.8, "root", 31, 55, "tri")
    _drums(buf, beat, total, {"kick": "X.....x..x......", "snare": "....X.......X...", "hat": "x.x.x.x.x.x.x.xx"}, swing=0.25, gain=1.0)
    for b in range(bars):
        root, _ = ch[b % 4]
        for i, off in enumerate((0, 7, 12, 10)):
            mix_at(buf, b * 4 * beat + (i * 1.0 + 0.5) * beat, note(midi(67 + root + off), beat * 0.5, "tri", 0.15), 0.15)
    return finish(pb.Pedalboard([pb.LowpassFilter(7000), pb.Compressor(threshold_db=-18, ratio=3)])(buf.astype(np.float32), SR), 0.75, 0.05)


def bgm_vlog() -> np.ndarray:
    bpm = 100; bars = 14
    buf, beat, total = _canvas(bpm, bars)
    ch = [(7, "maj"), (0, "maj"), (4, "min"), (2, "maj")]
    for b in range(bars):
        root, q = ch[b % 4]
        notes = [55 + root + i for i in _chord_notes(root, q)]
        for i, t in enumerate((0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5)):
            m = notes[(i if i % 2 == 0 else i + 1) % len(notes)] + (12 if i % 4 == 3 else 0)
            mix_at(buf, b * 4 * beat + t * beat, note(midi(m), beat * 0.9, "tri", 0.35), 0.28)
        mix_at(buf, b * 4 * beat, note(midi(36 + root), beat * 1.8, "sine", 0.5), 0.5)
    _drums(buf, beat, total, {"kick": "X.......X.x.....", "clap": "....X.......X...", "hat": "x.x.x.x.x.x.x.x."}, gain=0.7)
    return finish(pb.Pedalboard([pb.Reverb(room_size=0.25, wet_level=0.1)])(buf.astype(np.float32), SR), 0.75, 0.05)


def bgm_ambient() -> np.ndarray:
    bpm = 60; bars = 8
    buf, beat, total = _canvas(bpm, bars, 3.0)
    ch = [(0, "maj7"), (9, "m7"), (5, "maj7"), (7, "sus")]
    for b in range(bars):
        root, q = ch[b % 4]
        _pad(buf, b * 4 * beat, 4 * beat + 1.0, [48 + root + i for i in _chord_notes(root, q)], 0.2, 900, "tri")
        _pad(buf, b * 4 * beat, 4 * beat + 1.0, [60 + root + i for i in _chord_notes(root, q)], 0.12, 1500, "saw")
        for i, off in enumerate((0, 7, 12, 16)):
            mix_at(buf, b * 4 * beat + (1 + i * 0.75) * beat, note(midi(79 + root + off), 2.0, "sine", 0.7), 0.1)
    mix_at(buf, 0, np.zeros(1))
    return finish(pb.Pedalboard([pb.Reverb(room_size=0.9, wet_level=0.35, dry_level=0.7)])(buf.astype(np.float32), SR), 0.7, 0.1)


def bgm_edm() -> np.ndarray:
    bpm = 128; bars = 16
    buf, beat, total = _canvas(bpm, bars)
    ch = [(9, "min"), (5, "maj"), (0, "maj"), (7, "maj")]
    for b in range(bars):
        root, q = ch[b % 4]
        chord = [57 + root + i for i in _chord_notes(root, q)]
        if b >= 4:   # 4마디 뒤부터 코드가 크게 들어옵니다
            for i in range(8):
                _pad(buf, b * 4 * beat + i * beat / 2, beat * 0.45, chord, 0.2, 3500)
        for i in range(4):
            mix_at(buf, b * 4 * beat + i * beat + beat / 2, note(midi(33 + root), beat * 0.45, "saw", 0.2), 0.45)
    _drums(buf, beat, total, {"kick": "X...X...X...X...", "clap": "....X.......X...", "hat": "..o...o...o...o."})
    return finish(pb.Pedalboard([pb.Compressor(threshold_db=-16, ratio=3), pb.Reverb(room_size=0.2, wet_level=0.06)])(buf.astype(np.float32), SR), 0.8, 0.05)


BGM: list[BgmSpec] = [
    BgmSpec("lofi", "로파이 휴식", "차분", ["로파이", "공부", "브이로그", "chill"], 80, bgm_lofi),
    BgmSpec("pop", "신나는 팝", "신남", ["팝", "밝음", "댄스", "upbeat"], 124, bgm_pop),
    BgmSpec("tense", "긴장 시네마틱", "긴장", ["긴장", "미스터리", "시네마틱", "tense"], 70, bgm_tense),
    BgmSpec("quirky", "엉뚱 코믹", "코믹", ["코믹", "장난", "귀여움", "quirky"], 110, bgm_quirky),
    BgmSpec("chiptune", "8비트 게임", "레트로", ["게임", "레트로", "8bit", "chiptune"], 140, bgm_chiptune),
    BgmSpec("hiphop", "힙합 비트", "자신감", ["힙합", "비트", "멋짐", "hiphop"], 90, bgm_hiphop),
    BgmSpec("vlog", "밝은 브이로그", "밝음", ["브이로그", "어쿠스틱", "일상", "vlog"], 100, bgm_vlog),
    BgmSpec("ambient", "몽환 앰비언트", "감성", ["감성", "몽환", "잔잔", "ambient"], 60, bgm_ambient),
    BgmSpec("edm", "에너지 EDM", "에너지", ["EDM", "에너지", "하이라이트", "edm"], 128, bgm_edm),
]
