"""배경 음악의 박자(비트)를 찾고, 그 박자에 맞춰 영상 컷을 배치합니다.

- `detect`: 소리의 변화량(onset)으로 템포를 잡고 박자 시각을 동적계획법으로 고릅니다 (librosa 없이 numpy만 씁니다).
  라이브러리 음악처럼 BPM을 알면 `hint_bpm`으로 넘겨 더 정확히 잡습니다.
- `snap_cuts`: 영상 클립 경계를 가장 가까운 박자에 맞춰 줄이거나(원본이 남으면 늘이기도) 뒤 클립을 당깁니다.
- `split_on_beats`: 고른 영상 클립을 N박마다 잘라 줍니다. 전환 효과를 걸 수도 있습니다.
모든 시각은 '타임라인 초'입니다 (미디어 시각 → 타임라인 시각 변환은 `timeline_beats`).
"""
from __future__ import annotations

import uuid

import numpy as np

from .editor import Clip, Project, clip_len

HOP = 512
NFFT = 2048


def onset_envelope(x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """(onset 세기[프레임], 프레임 간격(초))"""
    x = np.asarray(x, dtype=np.float32)
    if x.size < NFFT * 2:
        return np.zeros(0, dtype=np.float32), HOP / sr
    n = 1 + (x.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    mag = np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT).astype(np.float32), axis=1))
    # 저음~중음 위주 (킥·스네어가 박자를 이끕니다), 로그 압축 후 양의 변화만
    hi = int(8000 / (sr / NFFT))
    lg = np.log1p(30.0 * mag[:, :hi])
    flux = np.maximum(0.0, np.diff(lg, axis=0, prepend=lg[:1])).sum(axis=1)
    flux = flux - np.convolve(flux, np.ones(21) / 21, mode="same")   # 느린 변화를 빼서 박자만 남깁니다
    flux = np.maximum(flux, 0)
    sd = flux.std()
    return (flux / sd if sd > 0 else flux).astype(np.float32), HOP / sr


def estimate_tempo(env: np.ndarray, dt: float, lo: float = 60.0, hi: float = 180.0, hint: float | None = None) -> float:
    if env.size < 8:
        return hint or 120.0
    n = 1 << int(np.ceil(np.log2(env.size * 2)))
    f = np.fft.rfft(env - env.mean(), n)
    ac = np.fft.irfft(f * np.conj(f))[: env.size]
    ac = ac / (ac[0] or 1.0)
    bpms = np.arange(lo, hi + 0.25, 0.25)
    lags = 60.0 / bpms / dt
    ok = lags < env.size - 2
    bpms, lags = bpms[ok], lags[ok]
    # 각 BPM의 점수 = 그 간격과 2배·3배·4배 간격에서의 자기상관 합 (박자가 규칙적이면 배수에서도 높습니다)
    def at(l):
        i = np.clip(l, 0, env.size - 2)
        lo_i = np.floor(i).astype(int)
        w = i - lo_i
        return ac[lo_i] * (1 - w) + ac[lo_i + 1] * w
    score = at(lags) + 0.5 * at(2 * lags) + 0.25 * at(3 * lags) + 0.25 * at(4 * lags)
    center = hint or 105.0
    score = score * np.exp(-0.5 * (np.log2(bpms / center) / (0.35 if hint else 0.55)) ** 2)
    return float(bpms[int(np.argmax(score))])


def track_beats(env: np.ndarray, dt: float, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Ellis(2007) 방식 동적계획법: 템포 간격에 가깝게 이어지면서 onset이 센 프레임을 고릅니다."""
    n = env.size
    if n < 4:
        return np.zeros(0)
    period = 60.0 / bpm / dt
    score = np.zeros(n)
    prev = -np.ones(n, dtype=int)
    for i in range(n):
        lo, hi = int(i - 2 * period), int(i - period / 2)
        if hi < 0:
            score[i] = env[i]
            continue
        lo = max(lo, 0)
        if hi < lo:
            score[i] = env[i]
            continue
        js = np.arange(lo, hi + 1)
        pen = tightness * (np.log((i - js) / period)) ** 2
        cand = score[js] - pen
        k = int(np.argmax(cand))
        score[i] = env[i] + cand[k]
        prev[i] = js[k]
    # 마지막 구간에서 가장 높은 점수에서 거꾸로 따라갑니다
    tail = score[max(0, n - int(period)):]
    i = int(np.argmax(tail)) + max(0, n - int(period))
    out = [i]
    while prev[i] >= 0:
        i = prev[i]
        out.append(i)
    return np.array(out[::-1]) * dt


def detect(x: np.ndarray, sr: int, hint_bpm: float | None = None) -> dict:
    """{bpm, beats[초], confidence}. 소리가 너무 짧거나 박자가 없으면 beats가 빈 목록입니다."""
    env, dt = onset_envelope(x, sr)
    if env.size < 40 or float(env.max(initial=0)) <= 0:
        return {"bpm": hint_bpm or 0.0, "beats": [], "confidence": 0.0}
    bpm = estimate_tempo(env, dt, hint=hint_bpm)
    beats = track_beats(env, dt, bpm)
    if beats.size >= 3:
        bpm = float(60.0 / np.median(np.diff(beats)))
    # 첫 박자는 보통 곡 처음에 있으니, 처음에서 한 박자 안쪽이면 그 앞으로 박자를 더 채웁니다
    step = 60.0 / bpm
    while beats.size and beats[0] - step >= -0.02:
        beats = np.insert(beats, 0, beats[0] - step)
    conf = float(np.clip(np.mean(env[np.clip((beats / dt).astype(int), 0, env.size - 1)]) / (env.mean() + 1e-6) / 4, 0, 1)) if beats.size else 0.0
    return {"bpm": round(float(bpm), 2), "beats": [round(float(b), 4) for b in beats if b >= 0], "confidence": round(conf, 3)}


def grid(bpm: float, offset: float, length: float) -> list[float]:
    """BPM과 첫 박자 시각으로 만든 일정한 박자 격자 (감지가 불안할 때 대신 씁니다)."""
    step = 60.0 / bpm
    return [round(offset + i * step, 4) for i in range(int(max(0.0, length - offset) / step) + 1)]


def timeline_beats(beats: list[float], c: Clip) -> list[float]:
    """미디어 기준 박자를 오디오 클립이 타임라인에 놓인 시각으로 (잘린 구간 밖은 뺍니다)."""
    return [round(c.start + (b - c.src_in) / c.speed, 4) for b in beats if c.src_in <= b < c.src_out]


# ---------------------------------------------------------------- 컷 배치

def _nearest(beats: list[float], t: float) -> float:
    return min(beats, key=lambda b: abs(b - t))


def _group(p: Project, ids: set[str] | None) -> list[list[Clip]]:
    """영상 클립(전체 화면) 묶음 (영상 + 연결된 소리), 시작 시각 순. ids를 주면 그 클립들만."""
    video_tracks = {t.id for t in p.tracks if t.kind == "video" and not t.mute}
    heads = [c for c in p.clips if c.track in video_tracks and c.layout == "full" and (ids is None or c.id in ids)]
    heads.sort(key=lambda c: (c.start, c.id))
    return [[c] + [k for k in p.clips if c.link and k.link == c.link and k is not c] for c in heads]


def snap_cuts(p: Project, beats: list[float], media_len: dict[str, float], ids: set[str] | None = None,
              min_beats: int = 1, align_start: bool = False, transition: str = "none", transition_dur: float = 0.2) -> Project:
    """영상 클립이 끝나는 자리를 박자에 맞추고 뒤 클립을 이어 붙입니다. media_len은 미디어별 원본 길이(늘이기 한도)."""
    if len(beats) < 2:
        raise ValueError("박자를 찾지 못했어요. 먼저 음악 클립에서 박자를 찾아 주세요.")
    beats = sorted(beats)
    beat = float(np.median(np.diff(beats)))
    groups = _group(p, ids)
    if not groups:
        raise ValueError("맞출 영상 클립이 없어요.")
    out = p.model_copy(deep=True)
    by_id = {c.id: c for c in out.clips}
    cursor = _nearest(beats, groups[0][0].start) if align_start else groups[0][0].start
    shift_map: list[tuple[float, float, float, float]] = []   # (옛 시작, 옛 끝, 새 시작, 새 끝)
    first = True
    for g in groups:
        head = by_id[g[0].id]
        old_s, old_len = head.start, clip_len(head)
        want = cursor + old_len
        end = _nearest([b for b in beats if b >= cursor + min_beats * beat - 1e-6] or [beats[-1]], want)
        new_len = max(end - cursor, min_beats * beat)
        delta = new_len - old_len                     # >0 늘이기, <0 줄이기
        room = (media_len.get(head.media, head.src_out) - head.src_out) / head.speed
        if delta > room + 1e-6:                       # 원본이 모자라면 한 박 줄입니다
            new_len = max(end - beat - cursor, min_beats * beat)
            delta = new_len - old_len
            if delta > room + 1e-6:
                new_len, delta = old_len, 0.0
        for k in g:
            c = by_id[k.id]
            c.start = round(cursor, 4)
            c.src_out = round(max(c.src_in + 0.05, c.src_out + delta * c.speed), 4)
            if first is False and k.id == g[0].id and transition != "none":
                c.transition, c.transition_dur = transition, min(transition_dur, max(0.1, new_len / 2))
        shift_map.append((old_s, old_s + old_len, cursor, cursor + new_len))
        cursor = round(cursor + new_len, 4)
        first = False
    # 자막은 속한 클립이 움직인 만큼 같이 옮깁니다
    for cue in out.subtitles:
        for os_, oe, ns, ne in shift_map:
            if os_ - 1e-6 <= cue.start < oe:
                d = ns - os_
                cue.start = max(0.0, round(cue.start + d, 4))
                cue.end = round(min(cue.end + d, ne), 4)
                if cue.end <= cue.start:
                    cue.end = round(cue.start + 0.2, 4)
                break
    return out


def split_on_beats(p: Project, beats: list[float], ids: set[str], every: int = 4, transition: str = "none",
                   transition_dur: float = 0.2) -> Project:
    """고른 영상 클립(과 연결된 소리)을 박자 N개마다 자릅니다."""
    if len(beats) < 2:
        raise ValueError("박자를 찾지 못했어요. 먼저 음악 클립에서 박자를 찾아 주세요.")
    every = max(1, int(every))
    out = p.model_copy(deep=True)
    groups = _group(out, ids)
    if not groups:
        raise ValueError("자를 영상 클립을 먼저 골라 주세요.")
    new_clips: list[Clip] = []
    for g in groups:
        head = g[0]
        s, e = head.start, head.start + clip_len(head)
        inside = [b for b in sorted(beats) if s + 0.15 < b < e - 0.15]
        cuts = inside[every - 1::every] if every > 1 else inside
        # 같은 클립에서 첫 컷이 시작과 너무 가깝거나 끝과 너무 가까우면 건너뜁니다 (위 0.15초 여유)
        if not cuts:
            continue
        edges = [s] + cuts + [e]
        for gi, c in enumerate(g):
            c.src_out = round(c.src_in + (edges[1] - edges[0]) * c.speed, 4)
        for i in range(1, len(edges) - 1):
            link = uuid.uuid4().hex[:8] if head.link else None
            for c in g:
                n = c.model_copy(deep=True)
                n.id = uuid.uuid4().hex[:8]
                n.link = link
                n.start = round(edges[i], 4)
                n.src_in = round(c.src_in + (edges[i] - s) * c.speed, 4)
                n.src_out = round(n.src_in + (edges[i + 1] - edges[i]) * c.speed, 4)
                n.transition, n.transition_dur = "none", 0.5
                if c.id == head.id and transition != "none":
                    n.transition, n.transition_dur = transition, min(transition_dur, max(0.1, (edges[i + 1] - edges[i]) / 2))
                new_clips.append(n)
    if not new_clips:
        raise ValueError("고른 클립 안에 자를 박자가 없어요.")
    out.clips.extend(new_clips)
    return out
