"""자동 리프레임: 영상 속 사람 얼굴(없으면 몸)을 따라가며 9:16 화면의 구도 중심을 정합니다.

검출은 OpenCV(.venv-aitools의 작업자)가 몇 프레임마다 한 번 하고, 여기서는 그 결과로
(1) 가장 큰 주인공을 이어서 따라가고 (2) 빈 구간을 메우고 (3) 카메라처럼 부드럽게(작게 흔들리면 가만히, 크게 벗어나면 따라감)
(4) 점을 줄여서 ShortSpec.track(시각·가운데 x·y)으로 만듭니다.
"""
from __future__ import annotations

import numpy as np


def pick_subject(frames: list[dict]) -> list[tuple[float, float, float, float] | None]:
    """frames: [{t, boxes:[[cx, cy, size]...]}] (화면 비율 0~1). 이전 주인공과 가장 가깝고 큰 것을 이어 고릅니다.
    반환: 프레임마다 (t, cx, cy, size) 또는 None."""
    out: list[tuple[float, float, float, float] | None] = []
    prev = None
    for f in frames:
        boxes = f.get("boxes") or []
        if not boxes:
            out.append(None)
            continue
        if prev is None:
            b = max(boxes, key=lambda b: b[2])
        else:
            def cost(b):
                d = np.hypot(b[0] - prev[0], b[1] - prev[1])
                return d - 0.6 * b[2]                      # 가깝고 큰 쪽을 선호
            b = min(boxes, key=cost)
        prev = (b[0], b[1])
        out.append((float(f["t"]), float(b[0]), float(b[1]), float(b[2])))
    return out


def smooth_path(samples: list[tuple[float, float, float, float] | None], times: list[float], hold: float = 2.5,
                dead: float = 0.07, follow: float = 0.18, rate: float = 0.5) -> list[tuple[float, float, float]]:
    """times 시각마다 (t, cx, cy)를 돌려줍니다.
    - 검출이 없는 구간은 마지막 위치를 hold초 붙들고, 그 뒤에는 천천히 화면 가운데로 돌아갑니다.
    - 카메라는 목표가 중심에서 dead(화면 비율)보다 멀어졌을 때만 움직이고, 한 걸음에 follow 비율만큼만 따라가서 부드럽습니다.
    - rate: 카메라가 1초에 움직일 수 있는 최대 거리(화면 비율)."""
    pts = [(s[0], s[1], s[2]) for s in samples if s]
    if not times:
        return []
    if not pts:
        return [(t, 0.5, 0.5) for t in times]
    pt = np.array([p[0] for p in pts]); px = np.array([p[1] for p in pts]); py = np.array([p[2] for p in pts])
    cam = np.array([px[0], py[0]], dtype=float)
    out, prev_t = [], times[0]
    for t in times:
        i = int(np.argmin(np.abs(pt - t)))
        seen = abs(pt[i] - t) <= hold
        tgt = np.array([np.interp(t, pt, px), np.interp(t, pt, py)]) if seen else np.array([0.5, 0.5])
        dt = max(t - prev_t, 1e-3)
        d = tgt - cam
        for k in range(2):
            over = max(0.0, abs(d[k]) - dead)                    # 여유 폭 안에서는 가만히
            move = min(rate * dt * (1.0 if seen else 0.3), over * follow * (1.0 if seen else 0.3) * 4)
            cam[k] += move * (1 if d[k] > 0 else -1)
        out.append((float(t), float(np.clip(cam[0], 0, 1)), float(np.clip(cam[1], 0, 1))))
        prev_t = t
    return out


def simplify(points: list[tuple[float, float, float]], eps: float = 0.012, max_pts: int = 60) -> list[tuple[float, float, float]]:
    """직선으로 이어도 거의 같은 점들을 빼서 (Ramer–Douglas–Peucker) 점 수를 줄입니다."""
    if len(points) <= 2:
        return list(points)

    def rdp(lo: int, hi: int, e: float, keep: set[int]) -> None:
        if hi <= lo + 1:
            return
        t0, t1 = points[lo][0], points[hi][0]
        worst, wi = 0.0, -1
        for i in range(lo + 1, hi):
            a = (points[i][0] - t0) / max(t1 - t0, 1e-9)
            ex = points[lo][1] + (points[hi][1] - points[lo][1]) * a
            ey = points[lo][2] + (points[hi][2] - points[lo][2]) * a
            d = max(abs(points[i][1] - ex), abs(points[i][2] - ey))
            if d > worst:
                worst, wi = d, i
        if worst > e:
            keep.add(wi)
            rdp(lo, wi, e, keep)
            rdp(wi, hi, e, keep)

    while True:
        keep = {0, len(points) - 1}
        rdp(0, len(points) - 1, eps, keep)
        if len(keep) <= max_pts or eps > 0.5:
            break
        eps *= 1.5
    return [points[i] for i in sorted(keep)]


def build_track(frames: list[dict], start: float, end: float, step: float = 0.25) -> list[dict]:
    """검출 결과 → 미디어 기준 시각 [{t, cx, cy}] (start~end)."""
    samples = pick_subject(frames)
    times = [round(start + i * step, 4) for i in range(int((end - start) / step) + 1)]
    if times[-1] < end - 1e-6:
        times.append(end)
    path = simplify(smooth_path(samples, times))
    return [{"t": round(t, 3), "cx": round(cx, 4), "cy": round(cy, 4)} for t, cx, cy in path]
