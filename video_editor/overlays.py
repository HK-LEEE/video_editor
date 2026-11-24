"""화면 위에 얹는 글자: 첫 3초 훅 제목, 워터마크, 자유 글자.

자막과 달리 문장마다가 아니라 '이름 붙은 한 장'이고(`kind`), 시작·끝 시각이 없으면 영상 끝까지 보입니다.
미리보기(CSS)와 내보내기(ASS)가 같은 값(화면 비율 좌표·글자 크기)을 씁니다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .subtitles import ASS_SIZE_FACTOR, HEX, _ass_rgb, _ass_text, ass_time

KINDS = ("title", "watermark", "text")


class Overlay(BaseModel):
    id: str
    kind: Literal["title", "watermark", "text"] = "text"
    text: str = ""
    start: float = Field(0.0, ge=0)
    end: float | None = Field(None, gt=0)          # 비우면 영상 끝까지
    x: float = Field(0.5, ge=0.0, le=1.0)
    y: float = Field(0.15, ge=0.0, le=1.0)
    size: float = Field(0.06, ge=0.01, le=0.3)     # 글자 크기 = 화면 높이의 비율
    max_width: float = Field(0.9, ge=0.1, le=1.0)
    font: str = "Apple SD Gothic Neo"
    bold: bool = True
    color: str = "#ffffff"
    outline_color: str = "#000000"
    outline: float = Field(0.1, ge=0.0, le=0.5)
    bg_color: str = "#000000"
    bg_opacity: float = Field(0.0, ge=0.0, le=1.0)
    box_pad: float = Field(0.3, ge=0.0, le=1.0)
    opacity: float = Field(1.0, ge=0.05, le=1.0)   # 글자 전체 투명도 (워터마크는 보통 0.5 안팎)
    tpl: str = ""                                  # 템플릿이 넣은 글자의 표시 (다시 적용할 때 먼저 지웁니다)


def _c(v: str, default: str) -> str:
    return v if HEX.match(v or "") else default


def visible(o: Overlay, length: float, t0: float = 0.0) -> tuple[float, float] | None:
    """[t0, length) 안에서 보이는 구간을 t0 기준 초로. 안 보이면 None."""
    end = length if o.end is None else min(o.end, length)
    s, e = max(o.start, t0), end
    return (s - t0, e - t0) if e - s > 0.01 and o.text.strip() else None


def clamp_safe(o: Overlay, safe: dict) -> Overlay:
    """앱 UI에 안 가리게 안전 영역 안으로 옮긴 복사본 (워터마크는 구석이라 그대로 둡니다)."""
    if o.kind == "watermark":
        return o
    width = min(o.max_width, safe["right"] - safe["left"])
    x = min(max(o.x, safe["left"] + width / 2), safe["right"] - width / 2)
    pad = o.size * 1.6
    y = min(max(o.y, safe["top"] + pad), safe["bottom"] - pad)
    return o.model_copy(update={"x": x, "y": y, "max_width": width})


def to_ass(overlays: list[Overlay], width: int, height: int, length: float, t0: float = 0.0, safe: dict | None = None) -> str | None:
    """보이는 글자가 없으면 None."""
    items = []
    for o in overlays:
        v = visible(o, length, t0)
        if v:
            items.append((clamp_safe(o, safe) if safe else o, v))
    if not items:
        return None
    fmt = ("Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
           "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding")
    styles, events = [], []
    for i, (o, (s, e)) in enumerate(items):
        px = max(8.0, o.size * height)
        size = round(px * ASS_SIZE_FACTOR)
        margin = round((1 - o.max_width) / 2 * width)
        font = (o.font or "sans-serif").replace(",", " ")
        bold = -1 if o.bold else 0
        a = o.opacity
        row = lambda name, primary, outl, border, ol: (f"Style: {name},{font},{size},{primary},&H000000FF,{outl},&H00000000,{bold},0,0,0,100,100,0,0,"
                                                      f"{border},{ol},0,5,{margin},{margin},0,1")
        styles.append(row(f"T{i}", _ass_rgb(_c(o.color, "#ffffff"), a), _ass_rgb(_c(o.outline_color, "#000000"), a), 1, round(o.outline * px, 1)))
        pos = f"{{\\an5\\pos({round(o.x * width)},{round(o.y * height)})}}"
        text = _ass_text(o.text)
        if o.bg_opacity > 0:
            styles.append(row(f"B{i}", "&HFF000000", _ass_rgb(_c(o.bg_color, "#000000"), o.bg_opacity * a), 3, round(px * o.box_pad, 1)))
            events.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},B{i},,0,0,0,,{pos}{text}")
        events.append(f"Dialogue: 1,{ass_time(s)},{ass_time(e)},T{i},,0,0,0,,{pos}{text}")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0",
                      "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", f"Format: {fmt}", *styles, "",
                      "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text", *events]) + "\n"
