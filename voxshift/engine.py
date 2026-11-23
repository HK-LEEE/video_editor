"""DSP 변조 엔진.

WORLD 보코더(pyworld)로 목소리를 피치(F0), 스펙트럼 포락선(포먼트), 비주기성으로 나눈 뒤
피치와 포먼트를 따로 움직이고 다시 합성합니다. 그 위에 pedalboard 효과를 얹습니다.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pedalboard as pb
import pyworld
import soundfile as sf

SAMPLE_RATE = 48_000


@dataclass
class VoiceSettings:
    name: str = "custom"
    label: str = "직접 조절"
    pitch: float = 0.0          # 반음
    formant: float = 1.0        # 포먼트 비율
    monotone_hz: float | None = None
    whisper: bool = False       # 성대 울림을 없애 속삭이는 소리로
    effects: list[dict] = field(default_factory=list)

    def scaled(self, intensity: float) -> "VoiceSettings":
        """강도를 곱한 복사본. 1.0 = 그대로, 2.0 = 피치·포먼트 변화가 두 배."""
        if intensity == 1.0:
            return self
        return replace(self, pitch=self.pitch * intensity,
                       formant=float(self.formant ** intensity))

    @classmethod
    def from_dict(cls, cfg: dict, name: str = "custom") -> "VoiceSettings":
        return cls(
            name=name,
            label=cfg.get("label", name),
            pitch=float(cfg.get("pitch", 0.0)),
            formant=float(cfg.get("formant", 1.0)),
            monotone_hz=cfg.get("monotone_hz"),
            whisper=bool(cfg.get("whisper", False)),
            effects=list(cfg.get("effects") or []),
        )


# ---------------------------------------------------------------- 입출력

def load_audio(path: str | Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """m4a/wav/webm 등 무엇이든 ffmpeg로 읽어 모노 float32로 돌려줍니다."""
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
        "-ac", "1", "-ar", str(sr), "-f", "f32le", "-",
    ]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True).stdout
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg가 필요합니다. 맥에서는 `brew install ffmpeg`") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"오디오를 읽지 못했습니다: {e.stderr.decode(errors='ignore')}") from e
    audio = np.frombuffer(out, dtype=np.float32).copy()
    if audio.size == 0:
        raise ValueError("비어 있는 오디오입니다.")
    return audio


def save_wav(path: str | Path, audio: np.ndarray, sr: int = SAMPLE_RATE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, sr, subtype="PCM_16")
    return path


# ---------------------------------------------------------------- 전처리

def trim_silence(audio: np.ndarray, sr: int, threshold_db: float = -45.0, pad_s: float = 0.1) -> np.ndarray:
    """앞뒤 무음을 잘라냅니다 (전부 무음이면 그대로 둡니다)."""
    frame = int(sr * 0.02)
    if audio.size < frame:
        return audio
    n = audio.size // frame
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    loud = np.where(20 * np.log10(rms) > threshold_db)[0]
    if loud.size == 0:
        return audio
    pad = int(sr * pad_s)
    start = max(0, loud[0] * frame - pad)
    end = min(audio.size, (loud[-1] + 1) * frame + pad)
    return audio[start:end]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2) + 1e-12))


# ---------------------------------------------------------------- 피치·포먼트

def _warp_envelope(env: np.ndarray, ratio: float) -> np.ndarray:
    """주파수 축으로 포락선을 늘이거나 줄입니다. ratio>1이면 포먼트가 올라갑니다."""
    if abs(ratio - 1.0) < 1e-6:
        return env
    bins = env.shape[1]
    src = np.arange(bins) / ratio  # 새 bin k는 원래 k/ratio 위치의 값을 가져옴
    src = np.clip(src, 0, bins - 1)
    lo = np.floor(src).astype(int)
    hi = np.minimum(lo + 1, bins - 1)
    frac = src - lo
    return env[:, lo] * (1 - frac) + env[:, hi] * frac


def shift_voice(audio: np.ndarray, sr: int, pitch: float = 0.0, formant: float = 1.0,
                monotone_hz: float | None = None, whisper: bool = False) -> np.ndarray:
    if pitch == 0 and formant == 1.0 and monotone_hz is None and not whisper:
        return audio
    x = audio.astype(np.float64)
    f0, t = pyworld.dio(x, sr, frame_period=5.0)
    f0 = pyworld.stonemask(x, f0, t, sr)
    sp = pyworld.cheaptrick(x, f0, t, sr)
    ap = pyworld.d4c(x, f0, t, sr)

    voiced = f0 > 0
    if monotone_hz is not None:
        f0 = np.where(voiced, float(monotone_hz), 0.0)
    f0 = f0 * (2.0 ** (pitch / 12.0))
    if whisper:
        f0 = np.zeros_like(f0)        # 모든 프레임을 무성음(숨소리)으로 합성
        ap = np.ones_like(ap)

    sp = _warp_envelope(sp, formant)
    ap = _warp_envelope(ap, formant)

    y = pyworld.synthesize(f0, np.ascontiguousarray(sp), np.ascontiguousarray(ap), sr, frame_period=5.0)
    return y[: audio.size].astype(np.float32)


# ---------------------------------------------------------------- 효과

def _ring_mod(audio: np.ndarray, sr: int, freq_hz: float = 50.0, mix: float = 0.6) -> np.ndarray:
    t = np.arange(audio.size) / sr
    wet = audio * np.sin(2 * np.pi * freq_hz * t).astype(np.float32)
    return (1 - mix) * audio + mix * wet


def _make_plugin(fx: dict):
    kind = fx["type"]
    if kind == "low_shelf":
        return pb.LowShelfFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 200), gain_db=fx.get("gain_db", 3))
    if kind == "high_shelf":
        return pb.HighShelfFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 4000), gain_db=fx.get("gain_db", 3))
    if kind == "highpass":
        return pb.HighpassFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 300))
    if kind == "lowpass":
        return pb.LowpassFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 3400))
    if kind == "distortion":
        return pb.Distortion(drive_db=fx.get("drive_db", 10))
    if kind == "bitcrush":
        return pb.Bitcrush(bit_depth=fx.get("bit_depth", 10))
    if kind == "reverb":
        return pb.Reverb(room_size=fx.get("room_size", 0.5), wet_level=fx.get("wet_level", 0.3))
    if kind == "delay":
        return pb.Delay(delay_seconds=fx.get("delay_s", 0.25), feedback=fx.get("feedback", 0.4), mix=fx.get("mix", 0.3))
    if kind == "phaser":
        return pb.Phaser(rate_hz=fx.get("rate_hz", 1.0), depth=fx.get("depth", 0.8), mix=fx.get("mix", 0.6))
    if kind == "chorus":
        return pb.Chorus(rate_hz=fx.get("rate_hz", 1.0), depth=fx.get("depth", 0.25), mix=fx.get("mix", 0.5))
    raise ValueError(f"알 수 없는 효과: {kind}")


def apply_effects(audio: np.ndarray, sr: int, effects: list[dict]) -> np.ndarray:
    for fx in effects:
        if fx["type"] == "ring_mod":
            audio = _ring_mod(audio, sr, fx.get("freq_hz", 50.0), fx.get("mix", 0.6))
        else:
            audio = pb.Pedalboard([_make_plugin(fx)])(audio, sr)
    return audio.astype(np.float32)


# ---------------------------------------------------------------- 전체 파이프라인

def process(audio: np.ndarray, settings: VoiceSettings, sr: int = SAMPLE_RATE,
            trim: bool = True, intensity: float = 1.0) -> np.ndarray:
    """전처리 → 피치·포먼트 → 효과 → 음량 맞추기 → 리미터."""
    settings = settings.scaled(intensity)
    if trim:
        audio = trim_silence(audio, sr)
    in_rms = _rms(audio)

    y = shift_voice(audio, sr, settings.pitch, settings.formant, settings.monotone_hz, settings.whisper)
    y = apply_effects(y, sr, settings.effects)

    y = y * (in_rms / _rms(y))  # 원본과 비슷한 크기로
    y = pb.Pedalboard([pb.Limiter(threshold_db=-1.0)])(y.astype(np.float32), sr)
    return np.clip(y, -1.0, 1.0).astype(np.float32)
