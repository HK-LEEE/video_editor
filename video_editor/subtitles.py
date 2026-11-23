"""자막 트랙: 구간별 자막(Cue)과 스타일, SRT·ASS 내보내기.

자막은 프로젝트의 한 트랙(`Project.subtitles`)에 구간 단위로 들어갑니다.
나중에 AI 자동 자막(받아쓰기)·번역 자막도 같은 트랙에 Cue로 채우면 됩니다
(`source`에 만든 방식, `lang`에 언어를 적어 둡니다. `Project.subtitle_lang`을 지정하면 그 언어만 입힙니다).
화면 좌표는 모두 화면 크기에 대한 비율(0~1)이라 해상도와 쇼츠·릴스 같은 세로 화면에 그대로 씁니다.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class Cue(BaseModel):
    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = ""
    lang: str = ""                                  # "" = 언어 구분 없음, 예: "ko", "en"
    source: Literal["manual", "ai", "translation"] = "manual"


class SubtitleStyle(BaseModel):
    font: str = "Apple SD Gothic Neo"
    size: float = Field(0.06, ge=0.01, le=0.3)      # 글자 크기 = 화면 높이의 비율
    bold: bool = True
    color: str = "#ffffff"
    outline_color: str = "#000000"
    outline: float = Field(0.1, ge=0.0, le=0.5)     # 외곽선 굵기 = 글자 크기의 비율
    bg_color: str = "#000000"
    bg_opacity: float = Field(0.0, ge=0.0, le=1.0)  # 0이면 배경 상자 없음
    x: float = Field(0.5, ge=0.0, le=1.0)           # 글자 덩어리의 가운데 위치 (화면 비율)
    y: float = Field(0.85, ge=0.0, le=1.0)
    max_width: float = Field(0.9, ge=0.2, le=1.0)   # 한 줄 최대 폭 (화면 폭 비율)


def _color(v: str, default: str) -> str:
    return v if HEX.match(v or "") else default


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def active_cues(cues: list[Cue], lang: str = "", length: float | None = None) -> list[Cue]:
    out = [c for c in cues if c.text.strip() and c.end > c.start and (not lang or c.lang in ("", lang))]
    if length is not None:
        out = [c for c in out if c.start < length]
    return sorted(out, key=lambda c: (c.start, c.end))


def to_srt(cues: list[Cue], lang: str = "") -> str:
    blocks = []
    for i, c in enumerate(active_cues(cues, lang), 1):
        blocks.append(f"{i}\n{srt_time(c.start)} --> {srt_time(c.end)}\n{c.text.strip()}\n")
    return "\n".join(blocks)


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_rgb(hex_color: str, alpha: float = 1.0) -> str:
    """#RRGGBB → ASS의 &HAABBGGRR (AA는 00이 불투명)."""
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    a = int(round((1 - alpha) * 255))
    return f"&H{a:02X}{b}{g}{r}".upper()


def _ass_text(text: str) -> str:
    text = text.strip().replace("\\", "＼").replace("{", "(").replace("}", ")")
    return "\\N".join(line.strip() for line in text.splitlines())


def to_ass(cues: list[Cue], style: SubtitleStyle, width: int, height: int, lang: str = "",
           length: float | None = None) -> str:
    """화면 크기(width×height)에 맞춘 ASS 자막. 배경 상자가 있으면 글자 아래에 상자를 한 겹 더 깝니다."""
    size = max(8, round(style.size * height))
    outline = round(style.outline * size, 1)
    box_pad = round(size * 0.25, 1)
    margin_h = round((1 - style.max_width) / 2 * width)
    bold = -1 if style.bold else 0
    font = style.font.replace(",", " ") or "sans-serif"
    color = _color(style.color, "#ffffff")
    oc = _color(style.outline_color, "#000000")
    bg = _color(style.bg_color, "#000000")
    fmt = ("Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
           "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding")

    def row(name, primary, outl, back, border, ol):
        return (f"Style: {name},{font},{size},{primary},&H000000FF,{outl},{back},{bold},0,0,0,100,100,0,0,"
                f"{border},{ol},0,5,{margin_h},{margin_h},0,1")

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
             "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]", f"Format: {fmt}",
             row("Text", _ass_rgb(color), _ass_rgb(oc), "&H00000000", 1, outline)]
    if style.bg_opacity > 0:
        # BorderStyle 3: 글자 뒤에 OutlineColour 색 상자. 글자 자체는 투명하게 해서 상자만 보이게 합니다.
        lines.append(row("Box", "&HFF000000", _ass_rgb(bg, style.bg_opacity), "&H00000000", 3, box_pad))
    lines += ["", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    pos = f"{{\\an5\\pos({round(style.x * width)},{round(style.y * height)})}}"
    for c in active_cues(cues, lang, length):
        end = c.end if length is None else min(c.end, length)
        text = pos + _ass_text(c.text)
        if style.bg_opacity > 0:
            lines.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(end)},Box,,0,0,0,,{text}")
        lines.append(f"Dialogue: 1,{ass_time(c.start)},{ass_time(end)},Text,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"
