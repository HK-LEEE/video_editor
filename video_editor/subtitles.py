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

# ASS의 글자 크기는 '줄 높이' 기준이라 같은 숫자여도 CSS보다 글자가 작게 나옵니다. 한글·영문 글꼴에서 미리보기와
# 비슷해지게 키워 주는 값 (글꼴마다 몇 % 다를 수 있어요).
ASS_SIZE_FACTOR = 1.25
LANE_STEP = 1.45   # 자막 트랙 한 칸 = 글자 크기 × 이 값만큼 위로 (미리보기도 같은 값)
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class CueWord(BaseModel):
    """자막 안의 낱말 하나. 시각은 자막 시작 기준 초(그래서 자막을 옮겨도 같이 따라갑니다)."""
    s: float = Field(ge=0)
    e: float = Field(ge=0)
    w: str


class Cue(BaseModel):
    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = ""
    lang: str = ""                                  # "" = 언어 구분 없음, 예: "ko", "en"
    source: Literal["manual", "ai", "translation"] = "manual"
    words: list[CueWord] = []                       # 있으면 낱말별 강조(카라오케) 자막에 씁니다
    emotion: str = ""                               # 더빙할 때 이 문장을 읽는 감정 ("" = 자동)
    color: str = ""                                 # 이 자막만 다른 글자색 (#RRGGBB, ""이면 전체 스타일을 따름)
    lane: int = Field(0, ge=0, le=9)                # 자막 트랙 번호 (0이 맨 위 줄). 시간이 겹치면 위쪽으로 쌓아 보여줘요


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
    karaoke: bool = False                           # 말하는 낱말마다 색이 바뀌는 쇼츠식 자막 (낱말 시각이 있는 자막만)
    hl_color: str = "#ffd400"                       # 말하고 있는(이미 말한) 낱말의 색
    italic: bool = False
    shadow: float = Field(0.0, ge=0.0, le=0.5)      # 그림자 거리 = 글자 크기의 비율 (0이면 없음)
    spacing: float = Field(0.0, ge=0.0, le=0.5)     # 글자 사이 간격 = 글자 크기의 비율
    upper: bool = False                             # 영문을 모두 대문자로
    box_pad: float = Field(0.25, ge=0.0, le=1.0)    # 배경 상자의 안쪽 여백 = 글자 크기의 비율
    glow: float = Field(0.0, ge=0.0, le=1.0)        # 외곽선 색으로 번지는 빛 (네온)


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


def _ass_bgr(hex_color: str) -> str:
    """#RRGGBB → 글 중간에 쓰는 색 태그 값 &HBBGGRR&."""
    return f"&H{hex_color[5:7]}{hex_color[3:5]}{hex_color[1:3]}&".upper()


def _ass_text(text: str) -> str:
    text = text.strip().replace("\\", "＼").replace("{", "(").replace("}", ")")
    return "\\N".join(line.strip() for line in text.splitlines())


def karaoke_words(c: Cue, lang: str = "") -> list[CueWord] | None:
    """낱말 시각이 자막 글과 맞을 때만 돌려줍니다 (글을 고쳤으면 None → 일반 자막으로)."""
    if not c.words:
        return None
    flat = lambda t: re.sub(r"\s+", "", t)
    if flat("".join(w.w for w in c.words)) != flat(c.text):
        return None
    return sorted(c.words, key=lambda w: w.s)


def karaoke_text(c: Cue, words: list[CueWord], cjk: bool, upper: bool = False) -> str:
    """ASS의 \\k 태그: 낱말 앞에서 색이 바뀝니다. 낱말 사이 쉼도 앞 낱말 시간에 넣어 흐름이 끊기지 않게 합니다."""
    cs = lambda sec: max(0, int(round(sec * 100)))
    out = [f"{{\\k{cs(words[0].s)}}}"] if words[0].s > 0.01 else []
    for i, w in enumerate(words):
        nxt = words[i + 1].s if i + 1 < len(words) else max(w.e, w.s + 0.05)
        out.append(f"{{\\k{cs(max(nxt - w.s, 0.01))}}}{_ass_text(w.w.upper() if upper else w.w)}" + ("" if cjk or i + 1 == len(words) else " "))
    return "".join(out)


def to_ass(cues: list[Cue], style: SubtitleStyle, width: int, height: int, lang: str = "",
           length: float | None = None) -> str:
    """화면 크기(width×height)에 맞춘 ASS 자막. 배경 상자가 있으면 글자 아래에 상자를 한 겹 더 깝니다."""
    px = max(8.0, style.size * height)              # 미리보기(CSS)의 글자 크기와 같은 기준
    size = round(px * ASS_SIZE_FACTOR)
    outline = round(style.outline * px, 1)
    box_pad = round(px * style.box_pad, 1)
    shadow = round(style.shadow * px, 1)
    spacing = round(style.spacing * px, 1)
    italic = -1 if style.italic else 0
    margin_h = round((1 - style.max_width) / 2 * width)
    bold = -1 if style.bold else 0
    font = style.font.replace(",", " ") or "sans-serif"
    color = _color(style.color, "#ffffff")
    oc = _color(style.outline_color, "#000000")
    bg = _color(style.bg_color, "#000000")
    fmt = ("Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,"
           "StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding")

    def row(name, primary, outl, back, border, ol, secondary="&H000000FF", sh=0.0):
        return (f"Style: {name},{font},{size},{primary},{secondary},{outl},{back},{bold},{italic},0,0,100,100,{spacing},0,"
                f"{border},{ol},{sh},5,{margin_h},{margin_h},0,1")

    kara = style.karaoke
    hl = _color(style.hl_color, "#ffd400")
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
             "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
             "[V4+ Styles]", f"Format: {fmt}",
             # 카라오케: SecondaryColour = 아직 안 말한 낱말, PrimaryColour = 말한 낱말
             row("Text", _ass_rgb(hl if kara else color), _ass_rgb(oc), "&H80000000" if shadow else "&H00000000", 1, outline,
                 _ass_rgb(color) if kara else "&H000000FF", shadow)]
    if style.bg_opacity > 0:
        # BorderStyle 3: 글자 뒤에 OutlineColour 색 상자. 글자 자체는 투명하게 해서 상자만 보이게 합니다.
        lines.append(row("Box", "&HFF000000", _ass_rgb(bg, style.bg_opacity), "&H00000000", 3, box_pad))
    lines += ["", "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text"]
    glow = f"\\blur{round(style.glow * px * 0.2, 1)}" if style.glow > 0 else ""
    def at(c: Cue) -> tuple[str, str]:
        y = max(0.03, style.y - c.lane * style.size * LANE_STEP)     # 겹치는 자막은 트랙 번호만큼 위로 쌓습니다
        base = f"\\an5\\pos({round(style.x * width)},{round(y * height)})"
        return f"{{{base}{glow}}}", f"{{{base}}}"
    for c in active_cues(cues, lang, length):
        end = c.end if length is None else min(c.end, length)
        pos, box_pos = at(c)
        shown = c.text.upper() if style.upper else c.text
        own = f"{{\\c{_ass_bgr(c.color)}}}" if HEX.match(c.color or "") and not style.karaoke else ""   # 이 자막만의 글자색
        text = pos + own + _ass_text(shown)
        words = karaoke_words(c) if kara else None
        top = pos + karaoke_text(c, words, c.lang in ("ja", "zh"), style.upper) if words else text
        if style.bg_opacity > 0:
            lines.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(end)},Box,,0,0,0,,{box_pos}{_ass_text(shown)}")
        lines.append(f"Dialogue: 1,{ass_time(c.start)},{ass_time(end)},Text,,0,0,0,,{top}")
    return "\n".join(lines) + "\n"
