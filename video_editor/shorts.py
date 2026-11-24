"""쇼츠·릴스(9:16) 내보내기: 포맷별 길이 제한, 앱 UI에 가려지는 영역(안전 영역), 화면 맞추기(리프레임) 계산.

숫자는 모두 화면 크기에 대한 비율(0~1)입니다. 플랫폼 UI는 앱 버전마다 조금씩 달라서 '대략 이 정도는 가려진다'는
넉넉한 기준값이며, 미리보기의 오버레이도 이 값으로 가려지는 영역을 그립니다 (플랫폼의 실제 이미지·로고는 쓰지 않습니다).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .subtitles import Cue, SubtitleStyle

OUT_W, OUT_H = 1080, 1920
ASPECT = OUT_W / OUT_H

# limit: 최대 길이(초). safe: 글자·중요한 것을 두어도 안 가려지는 사각형 (left/right는 x, top/bottom은 y).
FORMATS: dict[str, dict] = {
    "shorts": {"label": "YouTube 쇼츠", "limit": 180.0,
               "safe": {"left": 0.05, "right": 0.84, "top": 0.10, "bottom": 0.78}},
    "reels": {"label": "Instagram 릴스", "limit": 90.0,
              "safe": {"left": 0.05, "right": 0.83, "top": 0.12, "bottom": 0.74}},
}


class TrackPoint(BaseModel):
    t: float = Field(ge=0)                        # 타임라인 시각 (초)
    cx: float = Field(0.5, ge=0.0, le=1.0)
    cy: float = Field(0.5, ge=0.0, le=1.0)


class ShortSpec(BaseModel):
    format: Literal["shorts", "reels"] = "shorts"
    start: float = Field(0.0, ge=0)               # 타임라인에서 자를 구간 (초)
    end: float | None = Field(None, gt=0)         # 비우면 끝까지
    zoom: float = Field(1.0, ge=1.0, le=5.0)      # 1 = 세로 화면에 꽉 차는 가장 큰 영역
    cx: float = Field(0.5, ge=0.0, le=1.0)        # 잘라낼 영역의 가운데 (원본 화면 비율)
    cy: float = Field(0.5, ge=0.0, le=1.0)
    fit: bool = False                             # 켜면 자르지 않고 전체를 폭에 맞춰 넣고 위아래는 흐린 배경으로 채움
    safe_subs: bool = True                        # 자막을 안전 영역 안으로 옮김
    track: list[TrackPoint] = Field(default_factory=list, max_length=400)   # 자동 리프레임: 시각마다 구도 중심 (있으면 cx·cy 대신 따라갑니다)


def window(W: int, H: int, spec: ShortSpec) -> tuple[int, int, int, int]:
    """원본 W×H에서 잘라낼 9:16 영역 (x, y, w, h). 모두 짝수 정수."""
    cw0 = min(float(W), H * ASPECT)
    cw = max(2.0, cw0 / spec.zoom)
    ch = cw / ASPECT
    x = min(max(spec.cx * W - cw / 2, 0.0), W - cw)
    y = min(max(spec.cy * H - ch / 2, 0.0), H - ch)
    ev = lambda v: int(v) // 2 * 2
    return ev(x), ev(y), max(2, ev(cw)), max(2, ev(ch))


def span(spec: ShortSpec, length: float) -> tuple[float, float]:
    """검증된 (시작, 끝). 제한 길이를 넘으면 ValueError."""
    start = spec.start
    end = min(spec.end if spec.end is not None else length, length)
    if end - start < 0.2:
        raise ValueError("자를 구간이 너무 짧아요.")
    limit = FORMATS[spec.format]["limit"]
    if end - start > limit + 1e-6:
        raise ValueError(f"{FORMATS[spec.format]['label']}는 최대 {int(limit)}초예요. (지금 {end - start:.1f}초)")
    return start, end


def safe_style(style: SubtitleStyle, fmt: str) -> SubtitleStyle:
    """자막이 앱 UI에 안 가리도록 위치·폭을 안전 영역 안으로 맞춘 복사본."""
    s = FORMATS[fmt]["safe"]
    width = min(style.max_width, s["right"] - s["left"])
    x = min(max(style.x, s["left"] + width / 2), s["right"] - width / 2)
    pad = style.size * 1.6                      # 두 줄까지 들어가도록 위아래 여유
    y = min(max(style.y, s["top"] + pad), s["bottom"] - pad)
    return style.model_copy(update={"x": x, "y": y, "max_width": width})


def shift_cues(cues: list[Cue], start: float, end: float) -> list[Cue]:
    """[start, end) 구간에 걸치는 자막만 남기고 0초 기준으로 옮김."""
    out = []
    for c in cues:
        if c.end <= start or c.start >= end:
            continue
        lost = max(0.0, start - c.start)              # 앞이 잘렸으면 낱말 시각도 그만큼 당깁니다
        words = [w.model_copy(update={"s": max(0.0, w.s - lost), "e": max(0.0, w.e - lost)}) for w in c.words if w.e > lost]
        out.append(c.model_copy(update={"start": max(c.start, start) - start, "end": min(c.end, end) - start, "words": words}))
    return out


def _lerp_expr(keys: list[tuple[float, float]]) -> str:
    """키 (시각, 값) 사이를 직선으로 잇는 ffmpeg 식 (바깥은 첫·끝 값 그대로). 변수는 t(초)."""
    keys = sorted(keys)
    expr = f"{keys[-1][1]:.5f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(keys, keys[1:]))):
        if t1 - t0 < 1e-6:
            continue
        expr = f"if(lt(t,{t1:.4f}),{v0:.5f}+({v1 - v0:.5f})*(t-{t0:.4f})/{t1 - t0:.4f},{expr})"
    return f"if(lt(t,{keys[0][0]:.4f}),{keys[0][1]:.5f},{expr})"


def track_keys(spec: ShortSpec, start: float) -> list[tuple[float, float, float]]:
    """리프레임 점을 자른 영상 기준 시각(0부터)으로. 점이 둘 미만이면 빈 목록."""
    pts = sorted(spec.track, key=lambda p: p.t)
    return [(p.t - start, p.cx, p.cy) for p in pts] if len(pts) >= 2 else []


def video_filter(W: int, H: int, spec: ShortSpec, start: float, end: float) -> str:
    """합성된 [vbase]에서 구간을 자르고 1080×1920 [vshort]로 만드는 filter_complex 조각."""
    head = f"[vbase]trim=start={start:.4f}:end={end:.4f},setpts=PTS-STARTPTS"
    if spec.fit:
        return (f"{head},split[sa][sb];"
                f"[sa]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},boxblur=30:5,eq=brightness=-0.15[sbg];"
                f"[sb]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,setsar=1[sfg];"
                f"[sbg][sfg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[vshort]")
    x, y, w, h = window(W, H, spec)
    keys = track_keys(spec, start)
    if keys:   # 자동 리프레임: 시간에 따라 크롭 위치가 움직입니다 (식 안의 쉼표는 따옴표로 감쌉니다)
        cx = _lerp_expr([(t, a) for t, a, _ in keys])
        cy = _lerp_expr([(t, b) for t, _, b in keys])
        xe = f"max(0,min({W - w},({cx})*{W}-{w / 2:.2f}))"
        ye = f"max(0,min({H - h},({cy})*{H}-{h / 2:.2f}))"
        return (f"{head},crop={w}:{h}:x='{xe}':y='{ye}',scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,format=yuv420p[vshort]")
    return f"{head},crop={w}:{h}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1,format=yuv420p[vshort]"
