"""하이라이트 자동 추출: 긴 영상에서 쇼츠·릴스(30~60초)로 쓸 만한 구간을 골라 점수를 매깁니다.

문장 단위로 끊어서(받아쓰기가 있으면 문장·낱말 시각, 없으면 말소리 덩어리) 연속된 문장들을 창으로 묶고,
점수 = 말의 밀도(쉼이 적고 빠르게 말함) + 목소리 높낮이·크기 변화 + 강조 표현(!, ?, 감정 단어, 핵심 낱말) + 시작 한 줄의 흡인력 + 문장이 온전히 끝남.
창은 항상 문장의 처음과 끝에 맞춰서 말이 중간에 끊기지 않습니다. 서로 겹치지 않는 상위 n개를 돌려줍니다.
"""
from __future__ import annotations

import re

import numpy as np

from . import emotion
from .ai_tools import frame_db

HOOK_WORDS = {
    "ko": ["진짜", "사실", "비밀", "꿀팁", "핵심", "결론", "중요", "반드시", "절대", "놀랍", "충격", "처음", "마지막", "이유", "방법", "알고 계", "아세요", "아시나요", "몰랐", "실수", "최고", "최악"],
    "en": ["secret", "actually", "truth", "mistake", "never", "always", "important", "reason", "how to", "tip", "best", "worst", "shocking", "did you know", "here's"],
}
SENT_END = re.compile(r"[.!?。？！…]$")


def sentence_units(segments: list[dict], max_gap: float = 0.8, max_len: float = 14.0) -> list[dict]:
    """받아쓰기 구간 → 문장 [{start,end,text}] (낱말 사이 쉼이 길거나 문장부호에서 끊음)."""
    units: list[dict] = []
    for s in segments:
        words = s.get("words") or []
        if not words:
            if s["text"].strip() and s["end"] > s["start"]:
                units.append({"start": s["start"], "end": s["end"], "text": s["text"].strip()})
            continue
        cur: list[dict] = []

        def flush():
            if cur:
                units.append({"start": cur[0]["s"], "end": cur[-1]["e"], "text": " ".join(w["w"] for w in cur).strip()})
                cur.clear()

        for w in words:
            if cur and (w["s"] - cur[-1]["e"] > max_gap or w["e"] - cur[0]["s"] > max_len):
                flush()
            cur.append(w)
            if SENT_END.search(w["w"]):
                flush()
        flush()
    return [u for u in units if u["end"] > u["start"] and u["text"]]


def units_from_audio(x: np.ndarray, sr: int, min_gap: float = 0.45, threshold_db: float | None = None) -> list[dict]:
    """받아쓰기가 없을 때: 말소리가 이어지는 덩어리를 문장 대신 씁니다."""
    db = frame_db(x, sr, 0.02)
    if db.size == 0:
        return []
    thr = threshold_db if threshold_db is not None else float(np.clip(np.percentile(db, 95) - 28, -60, -25))
    loud = db >= thr
    units, i, n, gap_f = [], 0, loud.size, int(min_gap / 0.02)
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j, last = i, i
        while j < n and (loud[j] or j - last <= gap_f):
            if loud[j]:
                last = j
            j += 1
        if (last + 1 - i) * 0.02 >= 0.4:
            units.append({"start": round(i * 0.02, 3), "end": round((last + 1) * 0.02, 3), "text": ""})
        i = j
    return units


def _score_window(units: list[dict], x: np.ndarray, sr: int, lang: str, win_db: np.ndarray, gdb: float) -> tuple[float, dict]:
    a, b = units[0]["start"], units[-1]["end"]
    dur = b - a
    spoken = sum(u["end"] - u["start"] for u in units)
    text = " ".join(u["text"] for u in units)
    nchar = len(re.sub(r"\s", "", text))
    density = spoken / dur                                       # 쉼이 적을수록 1에 가까움
    cps = nchar / max(spoken, 1e-6)                              # 초당 글자 수 (한국어 기준 5~9가 시원한 속도)
    pace = 1.0 - min(abs(cps - 7.0) / 7.0, 1.0) if nchar else 0.5
    i0, i1 = int(a / 0.02), int(b / 0.02)
    seg = win_db[i0:i1]
    seg = seg[seg > gdb - 35] if seg.size else seg
    dyn = float(np.clip((np.std(seg) if seg.size > 5 else 0) / 9.0, 0, 1))      # 목소리 크기 변화
    loud = float(np.clip((np.mean(seg) - gdb + 12) / 12, 0, 1)) if seg.size else 0.0
    low = text.lower()
    marks = len(re.findall(r"[!！?？]", text))
    hooks = sum(low.count(w) for w in HOOK_WORDS.get("ko" if lang == "ko" else "en", []))
    emo = [emotion.detect(u["text"], lang) for u in units if u["text"]]
    emo_s = float(np.mean([s for e, s in emo if e != "neutral"] or [0.0]))
    emph = float(np.clip(marks / max(len(units), 1) * 0.8 + hooks * 0.18 + emo_s, 0, 1.4)) / 1.4
    first = units[0]["text"]
    hook = 1.0 if re.search(r"[?？!！]$", first) or re.search(r"\d", first) or any(w in first.lower() for w in HOOK_WORDS.get("ko" if lang == "ko" else "en", [])) else 0.0
    finish = 1.0 if SENT_END.search(units[-1]["text"]) or not units[-1]["text"] else 0.4
    parts = {"밀도": density, "속도": pace, "변화": dyn, "크기": loud, "강조": emph, "도입": hook, "마무리": finish}
    w = {"밀도": 22, "속도": 8, "변화": 16, "크기": 6, "강조": 24, "도입": 14, "마무리": 10}
    score = sum(parts[k] * w[k] for k in w) / sum(w.values()) * 100
    return float(score), parts


def find_highlights(units: list[dict], audio: np.ndarray, sr: int, lang: str = "ko", n: int = 5,
                    min_len: float = 30.0, max_len: float = 60.0, duration: float | None = None) -> list[dict]:
    """상위 n개 (겹치지 않게). 항목: {start, end, score, title, reasons}"""
    if not units or min_len <= 0 or max_len < min_len:
        return []
    win_db = frame_db(audio, sr, 0.02) if audio.size else np.zeros(0)
    gdb = float(np.percentile(win_db, 95)) if win_db.size else -30.0
    cands = []
    for i in range(len(units)):
        for j in range(i, len(units)):
            dur = units[j]["end"] - units[i]["start"]
            if dur > max_len:
                break
            if dur >= min_len:
                score, parts = _score_window(units[i:j + 1], audio, sr, lang, win_db, gdb)
                cands.append((score, units[i]["start"], units[j]["end"], parts, units[i]["text"]))
    if not cands and duration is not None and duration >= 5:
        # 말이 너무 듬성해서 최소 길이를 못 채우면, 가장 긴 연속 구간을 하나라도 돌려줍니다
        a, b = units[0]["start"], min(units[-1]["end"], units[0]["start"] + max_len)
        sc, parts = _score_window([u for u in units if u["end"] <= b] or units[:1], audio, sr, lang, win_db, gdb)
        cands.append((sc, a, b, parts, units[0]["text"]))
    cands.sort(key=lambda c: -c[0])
    out: list[dict] = []
    for sc, a, b, parts, first in cands:
        if any(min(b, o["end"]) - max(a, o["start"]) > 0.25 * (b - a) for o in out):
            continue
        top = sorted(parts.items(), key=lambda kv: -kv[1])[:3]
        out.append({"start": round(a, 3), "end": round(b, 3), "score": round(sc, 1),
                    "title": (first[:34] + "…") if len(first) > 34 else first,
                    "reasons": [k for k, v in top if v >= 0.5]})
        if len(out) >= n:
            break
    return out
