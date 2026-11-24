"""목소리 등록: 긴 녹음에서 샘플로 쓰기 좋은 10~30초 구간을 고릅니다.

AI 음성 변환(Seed-VC)은 샘플이 한 사람의 말소리로 깨끗하고 10~30초일 때 가장 비슷하게 나옵니다.
말이 이어지는 비율이 높고, 배경 잡음이 작고, 소리가 깨지지(클리핑) 않은 구간을 점수로 골라요.
"""
from __future__ import annotations

import numpy as np

FRAME_S = 0.05
MIN_S, TARGET_S, MAX_S = 5.0, 20.0, 30.0


def _frames(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    n = max(1, int(FRAME_S * sr))
    k = x.size // n
    if k == 0:
        return np.zeros(0), np.zeros(0)
    f = x[: k * n].reshape(k, n)
    rms = np.sqrt((f ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9), np.abs(f).max(axis=1)


def analyze(x: np.ndarray, sr: int, target: float = TARGET_S) -> dict:
    """{start, end, duration, speech_s, noise_db, snr_db, clipped, warnings[]} — 시각은 초."""
    dur = x.size / sr
    db, pk = _frames(x, sr)
    if db.size == 0 or dur < 1.0:
        return {"start": 0.0, "end": dur, "duration": dur, "speech_s": 0.0, "noise_db": -90.0, "snr_db": 0.0, "clipped": 0.0,
                "warnings": ["소리가 너무 짧아요."]}
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    speech = (db > max(floor + 10.0, -50.0)).astype(np.float32)
    clip = (pk > 0.985).astype(np.float32)
    win = int(round(min(target, MAX_S, dur) / FRAME_S))
    if dur <= MAX_S:      # 짧으면 앞뒤 무음만 잘라 냅니다
        idx = np.flatnonzero(speech)
        a = max(0, (idx[0] if idx.size else 0) - int(0.2 / FRAME_S))
        b = min(db.size, (idx[-1] + 1 if idx.size else db.size) + int(0.3 / FRAME_S))
    else:
        cs, cc = np.concatenate([[0], np.cumsum(speech)]), np.concatenate([[0], np.cumsum(clip)])
        step = max(1, int(0.5 / FRAME_S))
        best, a = -1e9, 0
        for s in range(0, db.size - win + 1, step):
            sp = (cs[s + win] - cs[s]) / win
            score = sp - 3.0 * (cc[s + win] - cc[s]) / win
            if score > best + 1e-6:
                best, a = score, s
        # 시작·끝을 가까운 쉼(조용한 곳)으로 옮겨 말 중간에서 끊기지 않게
        r = int(0.6 / FRAME_S)
        lo, hi = max(0, a - r), min(db.size, a + r)
        a = lo + int(np.argmin(db[lo:hi]))
        b = min(db.size, a + win)
        lo, hi = max(a + int(MIN_S / FRAME_S), b - r), min(db.size, b + r)
        if hi > lo:
            b = lo + int(np.argmin(db[lo:hi]))
    start, end = a * FRAME_S, min(dur, b * FRAME_S)
    seg = slice(a, max(a + 1, b))
    speech_s = float(speech[seg].sum() * FRAME_S)
    clipped = float(clip[seg].mean())
    snr = peak - floor
    warn = []
    if end - start < 8:
        warn.append("샘플이 짧아요. 10초 이상이 더 비슷하게 나와요.")
    if snr < 20:
        warn.append("배경 잡음이 큰 편이에요. 잡음 제거를 켜 주세요.")
    if clipped > 0.01:
        warn.append("소리가 깨진 곳이 있어요. 마이크를 조금 멀리하거나 작게 녹음해 보세요.")
    if speech_s < 0.5 * (end - start):
        warn.append("말하는 부분이 적고 쉬는 시간이 많아요.")
    return {"start": round(start, 2), "end": round(end, 2), "duration": round(dur, 2), "speech_s": round(speech_s, 1),
            "noise_db": round(floor, 1), "snr_db": round(snr, 1), "clipped": round(clipped, 4), "warnings": warn}
