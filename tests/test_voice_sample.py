import numpy as np

from video_editor import voice_sample

SR = 48_000


def _speechlike(sec, amp=0.3, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) + 0.01 * rng.standard_normal(t.size)).astype(np.float32)


def _quiet(sec, seed=1):
    return (0.003 * np.random.default_rng(seed).standard_normal(int(sec * SR))).astype(np.float32)


def test_short_clip_trims_silence():
    x = np.concatenate([_quiet(2), _speechlike(8), _quiet(3)])
    r = voice_sample.analyze(x, SR)
    assert 1.3 <= r["start"] <= 2.1 and 9.9 <= r["end"] <= 10.8


def test_long_clip_picks_speech_window():
    x = np.concatenate([_quiet(60), _speechlike(40), _quiet(60)])
    r = voice_sample.analyze(x, SR)
    assert 60 - 1 <= r["start"] and r["end"] <= 100 + 1
    assert 8 <= r["end"] - r["start"] <= voice_sample.MAX_S + 1


def test_noisy_and_clipped_warn():
    x = np.clip(_speechlike(12, amp=3.0) + 0.2 * np.random.default_rng(3).standard_normal(12 * SR), -1, 1).astype(np.float32)
    r = voice_sample.analyze(x, SR)
    assert r["warnings"]
