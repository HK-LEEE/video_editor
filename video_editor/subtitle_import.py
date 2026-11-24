"""자막 파일 불러오기: SRT·VTT·ASS(SSA)를 읽어 구간별 자막 목록으로 바꿉니다.

글자 인코딩은 BOM → UTF-8 → CP949(EUC-KR) → 그 밖의 순서로 자동 감지합니다.
돌려주는 자막은 {start, end, text}이고, 프로젝트에 넣는 일(시작 위치·언어·합치기)은 화면에서 합니다.
"""
from __future__ import annotations

import re

MAX_BYTES = 5 * 1024 * 1024
FORMATS = (".srt", ".vtt", ".ass", ".ssa")


class SubtitleParseError(ValueError):
    pass


def decode(raw: bytes) -> tuple[str, str]:
    """바이트 → (글, 인코딩 이름)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace"), "utf-8"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), "utf-16"
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc), "cp949" if enc == "euc-kr" else enc
        except UnicodeDecodeError:
            continue
    for enc in ("utf-16", "cp1252"):   # 마지막 보루
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8"


_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_ARROW = re.compile(rf"{_TIME}\s*-->\s*{_TIME}")
_TAGS = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")
_ASS_BREAK = re.compile(r"\\[Nn]")


def _sec(h, m, s, frac) -> float:
    ms = int((frac or "0").ljust(3, "0")[:3])
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + ms / 1000


def _clean(text: str) -> str:
    text = _TAGS.sub("", text).replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return "\n".join(line.strip() for line in text.strip().split("\n") if line.strip())


def parse_srt_vtt(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        for i, line in enumerate(lines):
            m = _ARROW.search(line)
            if not m:
                continue
            g = m.groups()
            start, end = _sec(*g[:4]), _sec(*g[4:])
            body = _clean("\n".join(lines[i + 1:]))
            if body and end > start:
                cues.append({"start": start, "end": end, "text": body})
            break
    return cues


def _ass_time(v: str) -> float:
    h, m, rest = v.strip().split(":")
    s, _, cs = rest.partition(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int((cs or "0").ljust(2, "0")[:2]) / 100


def parse_ass(text: str) -> list[dict]:
    cues, fmt = [], None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.lower().startswith("format:") and fmt is None:
            cols = [c.strip().lower() for c in line.split(":", 1)[1].split(",")]
            if "start" in cols and "text" in cols:
                fmt = cols
        elif line.lower().startswith("dialogue:") and fmt:
            parts = line.split(":", 1)[1].lstrip().split(",", len(fmt) - 1)
            if len(parts) < len(fmt):
                continue
            row = dict(zip(fmt, parts))
            try:
                start, end = _ass_time(row["start"]), _ass_time(row["end"])
            except (ValueError, KeyError):
                continue
            body = _clean(_ASS_BREAK.sub("\n", row["text"]))
            if body and end > start:
                cues.append({"start": start, "end": end, "text": body})
    return sorted(cues, key=lambda c: (c["start"], c["end"]))


def parse_subtitles(raw: bytes, name: str = "") -> dict:
    """파일 바이트 → {cues, encoding, format}. 형식은 내용으로 가려내고 이름은 참고만 해요."""
    if not raw.strip():
        raise SubtitleParseError("빈 파일이에요.")
    if len(raw) > MAX_BYTES:
        raise SubtitleParseError("자막 파일이 너무 커요 (5MB 이하).")
    text, enc = decode(raw)
    head = text.lstrip("﻿").lstrip()[:2000].lower()
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if "[script info]" in head or (ext in ("ass", "ssa") and "dialogue:" in text.lower()):
        fmt, cues = "ass", parse_ass(text)
    else:
        fmt = "vtt" if head.startswith("webvtt") else "srt"
        cues = parse_srt_vtt(text)
    if not cues:
        raise SubtitleParseError("자막을 찾지 못했어요. SRT·VTT·ASS 파일인지 확인해 주세요.")
    return {"cues": cues, "encoding": enc, "format": fmt}
