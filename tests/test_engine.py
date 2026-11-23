import numpy as np
import pyworld
import pytest

from voxshift import VoiceSettings, load_presets, process
from voxshift.engine import SAMPLE_RATE as SR, load_audio, save_wav, trim_silence


def voice_like(f0=130.0, seconds=1.5):
    """배음이 풍부한 가짜 '목소리' (톱니파 + 약한 비브라토)."""
    t = np.arange(int(SR * seconds)) / SR
    freq = f0 * (1 + 0.01 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    x = sum(np.sin(k * phase) / k for k in range(1, 30))
    return (0.3 * x / np.max(np.abs(x))).astype(np.float32)


def median_f0(x):
    f0, t = pyworld.dio(x.astype(np.float64), SR, frame_period=5.0)
    f0 = pyworld.stonemask(x.astype(np.float64), f0, t, SR)
    return float(np.median(f0[f0 > 0]))


def test_all_presets_produce_audio():
    x = voice_like()
    for name, s in load_presets().items():
        y = process(x, s, trim=False)
        assert y.dtype == np.float32, name
        assert abs(y.size - x.size) < SR * 0.05, name
        assert np.isfinite(y).all() and np.max(np.abs(y)) <= 1.0, name
        assert np.sqrt(np.mean(y ** 2)) > 0.01, name


@pytest.mark.parametrize("semitones", [-6, 6])
def test_pitch_shift_moves_f0(semitones):
    x = voice_like(150)
    y = process(x, VoiceSettings(pitch=semitones), trim=False)
    expected = 150 * 2 ** (semitones / 12)
    assert median_f0(y) == pytest.approx(expected, rel=0.05)


def test_formant_only_keeps_pitch():
    x = voice_like(150)
    y = process(x, VoiceSettings(formant=1.2), trim=False)
    assert median_f0(y) == pytest.approx(150, rel=0.05)


def test_robot_is_monotone():
    x = voice_like(150)
    robot = VoiceSettings(monotone_hz=110)
    y = process(x, robot, trim=False)
    assert median_f0(y) == pytest.approx(110, rel=0.05)


def test_trim_silence():
    x = np.concatenate([np.zeros(SR), voice_like(seconds=0.5), np.zeros(SR)])
    assert trim_silence(x, SR).size < SR


def test_roundtrip_m4a(tmp_path):
    import subprocess
    wav = save_wav(tmp_path / "a.wav", voice_like())
    m4a = tmp_path / "a.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(wav), str(m4a)], check=True)
    y = load_audio(m4a)
    assert abs(y.size - SR * 1.5) < SR * 0.1


def test_intensity_doubles_pitch_change():
    x = voice_like(150)
    y = process(x, VoiceSettings(pitch=3), trim=False, intensity=2.0)
    assert median_f0(y) == pytest.approx(150 * 2 ** (6 / 12), rel=0.05)


def test_whisper_has_no_pitch():
    x = voice_like(150)
    y = process(x, VoiceSettings(whisper=True), trim=False)
    f0, _ = pyworld.dio(y.astype(np.float64), SR, frame_period=5.0)
    assert np.mean(f0 > 0) < 0.3


def test_presets_survive_max_intensity():
    x = voice_like()
    for name, s in load_presets().items():
        y = process(x, s, trim=False, intensity=3.0)
        assert np.isfinite(y).all() and np.sqrt(np.mean(y ** 2)) > 0.005, name
