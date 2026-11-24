"""'글로 편집' 채팅: 한국어로 편집 지시를 적으면 Claude가 프로젝트 상태를 보고 편집 작업(ops) 목록으로 바꿔 줍니다.

흐름
1. `plan_edit`: 프로젝트 상태(트랙·클립·자막·화면 글자·대본)와 지시를 Claude에게 보내 ops를 받고, 값을 검사·정리해서 사람이 읽는 계획(summaries)으로 돌려줍니다. 아직 아무것도 바꾸지 않아요.
2. `apply_ops`: 사용자가 계획을 확인하고 '적용'하면, 같은 ops를 *지금의* 프로젝트에 적용한 새 프로젝트를 돌려줍니다. 화면이 그 결과를 한 번에 받아서
   실행 취소(⌘Z) 한 번으로 되돌릴 수 있어요. 적용은 계산만 하는 결정적 코드라 Claude를 다시 부르지 않습니다.

시간은 모두 '지금 타임라인' 기준 초입니다. 잘라내기(cut)는 모두 모아 뒤에서부터 한꺼번에 처리해서, 여러 곳을 잘라도 시각이 밀리지 않아요.
Claude에게는 영상·소리를 보내지 않고 글(대본·자막·트랙 이름)만 보냅니다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from . import claude_copy
from .editor import Clip, Project, Track, VolKey, clip_len, project_length
from .overlays import Overlay
from .subtitles import HEX, Cue, SubtitleStyle

EPS = 1e-3
MAX_OPS = 40
MAX_WORDS_CHARS = 14000
FILL_RE = re.compile(r"^(어+|음+|으+|흠+|엄+|에+|으음|um+|uh+|erm?|hmm+|ah+)$", re.I)

OPS = ("cut", "delete_words", "volume", "mute", "subtitle_style", "cue_style", "cue_text", "add_cue", "delete_cue",
       "add_overlay", "edit_overlay", "delete_overlay", "short")

STYLE_PROPS = {
    "color": "string", "size": "number", "x": "number", "y": "number", "bold": "boolean", "outline": "number", "outline_color": "string",
    "bg_color": "string", "bg_opacity": "number", "opacity": "number", "max_width": "number", "karaoke": "boolean", "hl_color": "string",
    "shadow": "number", "glow": "number", "upper": "boolean", "font": "string",
}
SUB_FIELDS = set(SubtitleStyle.model_fields)
OV_FIELDS = (set(Overlay.model_fields) - {"id", "tpl", "kind"})

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "ops": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": list(OPS)},
                "start": {"type": "number"}, "end": {"type": "number"},
                "texts": {"type": "array", "items": {"type": "string"}}, "fillers": {"type": "boolean"},
                "track": {"type": "string"}, "clip": {"type": "string"}, "factor": {"type": "number"}, "value": {"type": "number"},
                "mute": {"type": "boolean"},
                "cues": {"type": "array", "items": {"type": "string"}}, "cue": {"type": "string"},
                "id": {"type": "string"}, "kind": {"type": "string"}, "text": {"type": "string"}, "format": {"type": "string"},
                "style": {"type": "object", "properties": {k: {"type": v} for k, v in STYLE_PROPS.items()}, "additionalProperties": False},
            },
            "required": ["op"], "additionalProperties": False}},
    },
    "required": ["reply", "ops"],
    "additionalProperties": False,
}

SYSTEM = """당신은 한국어 숏폼 영상 편집기의 편집 비서입니다. 사용자의 편집 지시를 읽고, 함께 주어지는 프로젝트 상태(JSON)에 맞는 편집 작업(ops) 목록으로 바꿔서 답합니다.
사용할 수 있는 작업(op)과 필드:
- cut: start,end — 타임라인의 그 구간을 잘라내고 뒤를 당깁니다 (모든 트랙·자막·글자 함께).
- delete_words: texts(지울 낱말 목록, 예 ["음","어"]) 또는 fillers:true(군말 어·음·으·흠 전부), 선택으로 start,end로 범위를 좁힙니다. 대본(transcript)의 낱말 단위로 찾아 잘라냅니다.
- volume: track(트랙 id) 또는 clip(클립 id), factor(지금 값에 곱할 배율, 예 줄여=0.5) 또는 value(절대값, 1이 100%).
- mute: track, mute(true/false).
- subtitle_style: style — 모든 자막의 모양(color, size 화면높이비율, y 0~1, bold, outline, bg_color, bg_opacity, karaoke, hl_color 등). 색은 #RRGGBB.
- cue_style: cues(자막 id 목록) 또는 start,end(그 구간의 자막 전부), style.color — 그 자막들만 다른 글자색으로.
- cue_text: cue(자막 id), text — 자막 내용 고치기. add_cue: start,end,text — 자막 추가. delete_cue: cues 또는 start,end.
- add_overlay: kind(title 훅 제목 | watermark 워터마크 | text 자유 글자), text, start,end(안 쓰면 title·text는 3초, watermark는 끝까지), style(x,y,size,color,bg_color,bg_opacity…).
- edit_overlay: id(없으면 kind로 찾음), text,start,end,style. delete_overlay: id 또는 kind.
- short: start,end — 쇼츠·릴스로 뽑을 구간(초). format은 shorts 또는 reels(안 쓰면 shorts).
규칙:
- 모든 시각은 '지금 타임라인' 기준 초입니다(편집하기 전 시각). cut을 여러 개 써도 모두 지금 시각 기준으로 적으세요. 영상 길이는 length입니다.
- "앞 3초 잘라" → cut 0~3. "끝 5초 잘라" → cut (length-5)~length. "N초부터 M초까지 지워" → cut N~M.
- "'음' 다 지워" → delete_words texts ["음"]. "군말 다 지워" → delete_words fillers:true.
- 자막에서 "N번째"는 subtitles 목록의 i 번호입니다. "장면"은 자막 한 덩어리나 시간 구간으로 가장 자연스럽게 풀이하세요. 전체 자막이면 subtitle_style, 일부면 cue_style.
- "BGM·배경음악·음악"은 role이 music인 트랙, "목소리·보이스"는 role이 voice인 트랙입니다. 줄여/작게=factor 0.5 안팎, 키워/크게=1.5 안팎, 껐다=mute.
- "이 부분 쇼츠로 뽑아": 사용자가 구간을 말하지 않으면 playhead(재생 위치) 주변이나 선택한 자막·대본의 가장 핵심 구간을 15~60초로 잡아 short로 답합니다.
- 존재하지 않는 id를 지어내지 마세요. 지시가 모호하거나 할 수 없는 일(예: 영상 색 보정, 새 배경음 만들기)이면 ops를 비우고 reply에서 짧게 되묻거나 안내하세요.
- reply: 무엇을 할지 한두 문장(한국어, 존댓말). ops와 어긋나는 약속은 하지 마세요. 대화 기록이 있으면 '그거', '더 줄여' 같은 말도 앞 대화에 이어 풀이합니다.
- 반드시 지정된 JSON 객체 하나만 출력합니다."""


# ---------------------------------------------------------------- 상태 요약 (Claude에게 보낼 글)

class Word(BaseModel):
    w: str
    s: float
    e: float


def _t(x: float) -> float:
    return round(float(x), 2)


def _role(t: Track) -> str:
    if t.kind == "video":
        return "video"
    if t.voice:
        return "voice"
    n = t.name or ""
    if any(k in n for k in ("음악", "효과", "BGM", "bgm", "배경")):
        return "music"
    return "audio"


def build_context(p: Project, words: list[Word], media_names: dict[str, str], playhead: float = 0.0, selected: dict | None = None,
                  short_range: dict | None = None) -> dict:
    cues = sorted(p.subtitles, key=lambda c: (c.start, c.end))
    ctx = {
        "length": _t(project_length(p)),
        "playhead": _t(playhead),
        "tracks": [{"id": t.id, "name": t.name, "kind": t.kind, "role": _role(t), "volume": _t(t.volume), "mute": t.mute} for t in p.tracks],
        "clips": [{"id": c.id, "track": c.track, "name": media_names.get(c.media, "?"), "start": _t(c.start), "end": _t(c.start + clip_len(c)),
                   "speed": c.speed, "gain": _t(c.gain), **({"tpl": c.tpl} if c.tpl else {})} for c in sorted(p.clips, key=lambda c: c.start)][:120],
        "subtitles": [{"i": i + 1, "id": c.id, "start": _t(c.start), "end": _t(c.end), "text": c.text.strip()[:80],
                       **({"color": c.color} if c.color else {})} for i, c in enumerate(cues)][:150],
        "subtitle_style": {k: v for k, v in p.subtitle_style.model_dump().items() if k in ("color", "size", "y", "bold", "outline", "bg_color", "bg_opacity", "karaoke", "hl_color")},
        "overlays": [{"id": o.id, "kind": o.kind, "text": o.text, "start": _t(o.start), "end": None if o.end is None else _t(o.end)} for o in p.overlays],
    }
    if selected:
        ctx["selected"] = selected
    if short_range:
        ctx["short_range"] = short_range
    if words:
        lines, cur, t0 = [], [], None
        for w in words:
            if t0 is None:
                t0 = w.s
            cur.append(w.w)
            if len(cur) >= 8 or w.w.endswith((".", "?", "!", "。")):
                lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
                cur, t0 = [], None
        if cur:
            lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
        ctx["transcript"] = "\n".join(lines)[:MAX_WORDS_CHARS]
    else:
        ctx["transcript"] = None
        ctx["note"] = "낱말 단위 대본이 아직 없어요. 낱말을 지우는 지시면 '대본 불러오기'를 먼저 하라고 안내하세요. 자막은 subtitles에 있어요."
    return ctx


def _prompt(ctx: dict, history: list[dict], text: str) -> str:
    hist = ""
    if history:
        hist = "이전 대화:\n" + "\n".join(f"- {'사용자' if h.get('role') == 'user' else '비서'}: {str(h.get('text', ''))[:300]}" for h in history[-8:]) + "\n\n"
    return f"프로젝트 상태:\n{json.dumps(ctx, ensure_ascii=False)}\n\n{hist}사용자 지시: {text.strip()}"


# ---------------------------------------------------------------- ops 검사·정리

def _num(v, lo: float | None = None, hi: float | None = None) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def _style(raw, allowed: set[str]) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k not in allowed or v is None:
            continue
        if k.endswith("color") and not (isinstance(v, str) and HEX.match(v)):
            continue
        out[k] = v
    return out


def _find_track(p: Project, ref) -> Track | None:
    if not ref:
        return None
    ref = str(ref)
    t = next((t for t in p.tracks if t.id == ref), None)
    if t:
        return t
    low = ref.lower()
    t = next((t for t in p.tracks if t.name and (t.name.lower() == low)), None) or next((t for t in p.tracks if t.name and low in t.name.lower()), None)
    if t:
        return t
    role = {"music": "music", "bgm": "music", "음악": "music", "voice": "voice", "보이스": "voice", "목소리": "voice"}.get(low)
    return next((t for t in p.tracks if role and _role(t) == role), None)


def _cues_in(p: Project, a: float, b: float) -> list[Cue]:
    return [c for c in sorted(p.subtitles, key=lambda c: c.start) if c.end > a + EPS and c.start < b - EPS]


def _fmt(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def normalize_ops(p: Project, raw, words: list[Word] | None = None) -> tuple[list[dict], list[str]]:
    """Claude가 준 ops를 안전한 모양으로 정리합니다. 쓸 수 없는 것은 빼고 이유를 warnings에 적어요."""
    ops, warns = [], []
    L = project_length(p)
    if not isinstance(raw, list):
        return [], ["작업 목록을 읽지 못했어요."]
    for r in raw[:MAX_OPS]:
        if not isinstance(r, dict) or r.get("op") not in OPS:
            warns.append("모르는 작업은 건너뛰었어요.")
            continue
        k = r["op"]
        a, b = _num(r.get("start")), _num(r.get("end"))
        if k == "cut":
            if a is None or b is None:
                warns.append("자르기: 시작과 끝이 없어서 건너뛰었어요.")
                continue
            a, b = max(0.0, min(a, b)), min(L, max(a, b))
            if b - a < 0.05:
                warns.append("자르기: 구간이 영상 밖이거나 너무 짧아서 건너뛰었어요.")
                continue
            ops.append({"op": "cut", "start": round(a, 3), "end": round(b, 3)})
        elif k == "delete_words":
            texts = [str(x).strip() for x in (r.get("texts") or []) if str(x).strip()]
            fillers = bool(r.get("fillers"))
            if not texts and not fillers:
                warns.append("낱말 지우기: 지울 낱말이 없어서 건너뛰었어요.")
                continue
            ops.append({"op": "delete_words", "texts": texts[:20], "fillers": fillers,
                        "start": None if a is None else max(0.0, a), "end": None if b is None else min(L, b)})
        elif k == "volume":
            t, c = _find_track(p, r.get("track")), next((c for c in p.clips if c.id == r.get("clip")), None)
            f, v = _num(r.get("factor"), 0.0, 8.0), _num(r.get("value"), 0.0, 4.0)
            if not (t or c) or (f is None and v is None):
                warns.append("볼륨: 대상 트랙/클립이나 값을 몰라서 건너뛰었어요.")
                continue
            ops.append({"op": "volume", "track": None if c else t.id, "clip": c.id if c else None, "factor": f, "value": v})
        elif k == "mute":
            t = _find_track(p, r.get("track"))
            if not t or not isinstance(r.get("mute"), bool):
                warns.append("음소거: 대상 트랙을 몰라서 건너뛰었어요.")
                continue
            ops.append({"op": "mute", "track": t.id, "mute": r["mute"]})
        elif k == "subtitle_style":
            st = _style(r.get("style"), SUB_FIELDS)
            if st:
                ops.append({"op": "subtitle_style", "style": st})
            else:
                warns.append("자막 모양: 바꿀 값이 없어서 건너뛰었어요.")
        elif k in ("cue_style", "delete_cue"):
            ids = [str(i) for i in (r.get("cues") or []) if any(c.id == str(i) for c in p.subtitles)]
            if not ids and a is not None and b is not None:
                ids = [c.id for c in _cues_in(p, a, b)]
            color = (_style(r.get("style"), {"color"}) or {}).get("color")
            if not ids or (k == "cue_style" and not color):
                warns.append("자막 고르기: 대상 자막이나 색을 몰라서 건너뛰었어요.")
                continue
            ops.append({"op": k, "cues": ids, **({"color": color} if k == "cue_style" else {})})
        elif k == "cue_text":
            cid, text = str(r.get("cue") or ""), r.get("text")
            if not any(c.id == cid for c in p.subtitles) or not isinstance(text, str):
                warns.append("자막 고치기: 대상 자막을 몰라서 건너뛰었어요.")
                continue
            ops.append({"op": "cue_text", "cue": cid, "text": text[:200]})
        elif k == "add_cue":
            if a is None or b is None or not isinstance(r.get("text"), str) or not r["text"].strip():
                warns.append("자막 추가: 시각이나 글이 없어서 건너뛰었어요.")
                continue
            ops.append({"op": "add_cue", "start": max(0.0, min(a, b)), "end": max(a, b, a + 0.3), "text": r["text"].strip()[:200]})
        elif k == "add_overlay":
            kind = r.get("kind") if r.get("kind") in ("title", "watermark", "text") else "text"
            text = r.get("text")
            if not isinstance(text, str) or not text.strip():
                warns.append("화면 글자 추가: 글이 없어서 건너뛰었어요.")
                continue
            ops.append({"op": "add_overlay", "kind": kind, "text": text.strip()[:60], "start": a, "end": b, "style": _style(r.get("style"), OV_FIELDS)})
        elif k in ("edit_overlay", "delete_overlay"):
            o = next((o for o in p.overlays if o.id == r.get("id")), None) or next((o for o in p.overlays if o.kind == r.get("kind")), None)
            if not o:
                warns.append("화면 글자: 대상을 찾지 못해서 건너뛰었어요.")
                continue
            if k == "delete_overlay":
                ops.append({"op": k, "id": o.id})
            else:
                ops.append({"op": k, "id": o.id, "text": r.get("text") if isinstance(r.get("text"), str) else None, "start": a, "end": b,
                            "style": _style(r.get("style"), OV_FIELDS)})
        elif k == "short":
            if a is None or b is None or L <= 0:
                warns.append("쇼츠 뽑기: 구간을 몰라서 건너뛰었어요.")
                continue
            a, b = max(0.0, min(a, b)), min(L, max(a, b))
            if b - a < 1:
                warns.append("쇼츠 뽑기: 구간이 너무 짧아서 건너뛰었어요.")
                continue
            ops.append({"op": "short", "start": round(a, 2), "end": round(b, 2), "format": "reels" if r.get("format") == "reels" else "shorts"})
    if len(raw) > MAX_OPS:
        warns.append(f"작업이 너무 많아 앞의 {MAX_OPS}개만 쓰기로 했어요.")
    return ops, warns


# ---------------------------------------------------------------- 적용 (결정적)

def _env_at(keys: list[VolKey], t: float) -> float:
    if not keys:
        return 1.0
    ks = sorted(keys, key=lambda k: k.t)
    if t <= ks[0].t:
        return ks[0].v
    for a, b in zip(ks, ks[1:]):
        if t <= b.t:
            return b.v if b.t == a.t else a.v + (b.v - a.v) * (t - a.t) / (b.t - a.t)
    return ks[-1].v


def _shift_keys(keys: list[VolKey], d: float) -> list[VolKey]:
    if not keys:
        return []
    if d <= 0:
        return [VolKey(t=k.t - d, v=k.v) for k in keys]
    return [VolKey(t=0.0, v=_env_at(keys, d)), *[VolKey(t=k.t - d, v=k.v) for k in keys if k.t > d + 1e-6]]


def _uid() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def ripple_cut(p: Project, a: float, b: float) -> None:
    """타임라인 [a,b)를 모든 트랙에서 잘라내고 뒤를 당깁니다 (클립·자막·화면 글자·트랙 볼륨 점)."""
    d = b - a
    links: dict[str, str] = {}
    out: list[Clip] = []
    for k in p.clips:
        k0, k1 = k.start, k.start + clip_len(k)
        if k1 <= a + EPS:
            out.append(k)
            continue
        if k0 >= b - EPS:
            k.start = round(k.start - d, 4)
            out.append(k)
            continue
        has_l, has_r = k0 < a - EPS, k1 > b + EPS
        src_l, src_r = k.src_in + (a - k0) * k.speed, k.src_in + (b - k0) * k.speed
        old_out, keys = k.src_out, k.vol_keys
        lt = a - k0
        trim = [*[x for x in keys if x.t < lt], VolKey(t=lt, v=_env_at(keys, lt))] if keys else []
        if has_l and has_r:
            right = k.model_copy(deep=True, update={"id": _uid(), "src_in": src_r, "src_out": old_out, "start": round(a, 4),
                                                    "vol_keys": _shift_keys(keys, b - k0)})
            if k.link:
                right.link = links.setdefault(k.link, _uid())
            k.vol_keys, k.src_out = trim, src_l
            out += [k, right]
        elif has_l:
            k.vol_keys, k.src_out = trim, src_l
            out.append(k)
        elif has_r:
            k.src_in, k.start, k.vol_keys = src_r, round(a, 4), _shift_keys(keys, b - k0)
            out.append(k)
        # 통째로 잘리면 버립니다
    p.clips = out
    subs = []
    for q in p.subtitles:
        if q.start >= b - EPS:
            q.start, q.end = round(q.start - d, 4), round(q.end - d, 4)
        elif q.end > a + EPS:
            q.words = []
            if q.start >= a:
                q.start = a
            if q.end > b:
                q.end = round(q.end - d, 4)
            elif q.end > a:
                q.end = a
            if q.end - q.start < 0.05:
                continue
        subs.append(q)
    p.subtitles = subs
    ovs = []
    for o in p.overlays:
        if o.start >= b - EPS:
            o.start = round(o.start - d, 4)
            if o.end is not None:
                o.end = round(o.end - d, 4)
        elif o.end is None or o.end > a + EPS:
            if o.start > a:
                o.start = a
            if o.end is not None:
                o.end = round(o.end - d, 4) if o.end > b else a
                if o.end - o.start < 0.05:
                    continue
        ovs.append(o)
    p.overlays = ovs
    for t in p.tracks:
        t.vol_keys = [VolKey(t=round(k.t - d, 4) if k.t >= b else k.t, v=k.v) for k in t.vol_keys if not (a < k.t < b)]


def _clean(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower())


def word_ranges(words: list[Word], texts: list[str], fillers: bool, lo: float | None = None, hi: float | None = None) -> tuple[list[tuple[float, float]], int]:
    """지울 낱말들이 차지한 구간. 이어진 낱말은 한 덩어리로, 앞뒤 쉼을 조금(최대 0.06·0.1초) 같이 잘라 말이 끊기지 않게 합니다."""
    want = {_clean(t) for t in texts if _clean(t)}
    idx = [i for i, w in enumerate(words)
           if (lo is None or w.e > lo) and (hi is None or w.s < hi)
           and ((_clean(w.w) in want) or (fillers and FILL_RE.match(_clean(w.w))))]
    groups: list[list[int]] = []
    for i in idx:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    for g in groups:
        a, b = words[g[0]], words[g[-1]]
        pv = words[g[0] - 1] if g[0] > 0 else None
        nx = words[g[-1] + 1] if g[-1] + 1 < len(words) else None
        gap_b = max(0.0, a.s - pv.e) if pv else 0.1
        gap_a = max(0.0, nx.s - b.e) if nx else 0.1
        out.append((max(0.0, a.s - min(0.06, gap_b / 2)), b.e + min(0.1, gap_a / 2)))
    return out, len(idx)


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + EPS:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


@dataclass
class Applied:
    project: Project
    summaries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    short: dict | None = None
    cut_seconds: float = 0.0


def _vol_text(v: float) -> str:
    return f"{round(v * 100)}%"


def apply_ops(project: Project, ops: list[dict], words: list[Word] | None = None, media_names: dict[str, str] | None = None) -> Applied:
    """ops를 프로젝트에 적용한 새 프로젝트와 요약을 돌려줍니다 (원본은 건드리지 않아요)."""
    p = project.model_copy(deep=True)
    res = Applied(p)
    words = words or []
    media_names = media_names or {}
    cuts: list[tuple[float, float]] = []
    L0 = project_length(p)
    for op in ops:
        k = op["op"]
        if k == "cut":
            cuts.append((op["start"], op["end"]))
            res.summaries.append(f"{_fmt(op['start'])} ~ {_fmt(op['end'])} 구간 {op['end'] - op['start']:.1f}초를 잘라내고 뒤를 당겨요")
        elif k == "delete_words":
            rs, n = word_ranges(words, op["texts"], op["fillers"], op.get("start"), op.get("end"))
            label = "군말" if op["fillers"] and not op["texts"] else "、".join(f"'{t}'" for t in op["texts"]) + (" + 군말" if op["fillers"] else "")
            if not rs:
                res.warnings.append(f"{label}에 해당하는 낱말을 대본에서 못 찾았어요." + ("" if words else " (먼저 '대본 불러오기'를 눌러 주세요)"))
                continue
            cuts += rs
            res.summaries.append(f"{label} {n}개({len(rs)}곳, 총 {sum(b - a for a, b in rs):.1f}초)를 잘라내요")
        elif k == "volume":
            if op.get("clip"):
                c = next((c for c in p.clips if c.id == op["clip"]), None)
                if not c:
                    continue
                new = min(4.0, max(0.0, c.gain * op["factor"] if op.get("factor") is not None else op["value"]))
                res.summaries.append(f"'{media_names.get(c.media, '클립')}' 클립 볼륨 {_vol_text(c.gain)} → {_vol_text(new)}")
                c.gain = new
            else:
                t = next((t for t in p.tracks if t.id == op["track"]), None)
                if not t:
                    continue
                new = min(4.0, max(0.0, t.volume * op["factor"] if op.get("factor") is not None else op["value"]))
                res.summaries.append(f"'{t.name or '오디오'}' 트랙 볼륨 {_vol_text(t.volume)} → {_vol_text(new)}")
                t.volume = new
        elif k == "mute":
            t = next((t for t in p.tracks if t.id == op["track"]), None)
            if t:
                t.mute = op["mute"]
                res.summaries.append(f"'{t.name or '트랙'}' 트랙 {'음소거' if op['mute'] else '음소거 해제'}")
        elif k == "subtitle_style":
            try:
                p.subtitle_style = SubtitleStyle.model_validate({**p.subtitle_style.model_dump(), **op["style"]})
                res.summaries.append("전체 자막 모양 바꾸기: " + ", ".join(f"{a}={b}" for a, b in op["style"].items()))
            except ValidationError:
                res.warnings.append("자막 모양 값이 범위를 벗어나 건너뛰었어요.")
        elif k == "cue_style":
            n = 0
            for c in p.subtitles:
                if c.id in op["cues"]:
                    c.color, n = op["color"], n + 1
            res.summaries.append(f"자막 {n}개의 글자색을 {op['color']}(으)로")
        elif k == "delete_cue":
            before = len(p.subtitles)
            p.subtitles = [c for c in p.subtitles if c.id not in op["cues"]]
            res.summaries.append(f"자막 {before - len(p.subtitles)}개 지우기")
        elif k == "cue_text":
            c = next((c for c in p.subtitles if c.id == op["cue"]), None)
            if c:
                res.summaries.append(f"자막 '{c.text.strip()[:20]}' → '{op['text'][:20]}'")
                c.text, c.words = op["text"], []
        elif k == "add_cue":
            lane = 0
            while lane < 9 and any(q.lane == lane and q.start < op["end"] and op["start"] < q.end for q in p.subtitles):
                lane += 1
            p.subtitles.append(Cue(id=_uid(), start=op["start"], end=op["end"], text=op["text"], source="manual", lane=lane))
            res.summaries.append(f"{_fmt(op['start'])}에 자막 '{op['text'][:20]}' 추가")
        elif k == "add_overlay":
            kind = op["kind"]
            start = 0.0 if kind == "title" and op["start"] is None else (op["start"] or 0.0)
            end = op["end"] if op["end"] is not None else (None if kind == "watermark" else start + 3.0)
            base = {"title": dict(y=0.14, size=0.062, bg_color="#e11d48", bg_opacity=0.92, outline=0.0),
                    "watermark": dict(x=0.16, y=0.06, size=0.028, opacity=0.55, outline=0.08, max_width=0.5),
                    "text": dict(y=0.5, size=0.07, outline=0.12)}[kind]
            try:
                o = Overlay(id=_uid(), kind=kind, text=op["text"], start=start, end=end, **{**base, **op["style"]})
            except ValidationError:
                res.warnings.append("화면 글자 값이 범위를 벗어나 건너뛰었어요.")
                continue
            p.overlays.append(o)
            res.summaries.append(f"화면 글자({ {'title': '훅 제목', 'watermark': '워터마크', 'text': '글자'}[kind] }) '{o.text}' 추가 ({_fmt(o.start)} ~ {'끝까지' if o.end is None else _fmt(o.end)})")
        elif k == "edit_overlay":
            o = next((o for o in p.overlays if o.id == op["id"]), None)
            if not o:
                continue
            upd = dict(op["style"])
            if op.get("text") is not None:
                upd["text"] = op["text"]
            if op.get("start") is not None:
                upd["start"] = op["start"]
            if op.get("end") is not None:
                upd["end"] = op["end"]
            try:
                new = Overlay.model_validate({**o.model_dump(), **upd})
            except ValidationError:
                res.warnings.append("화면 글자 값이 범위를 벗어나 건너뛰었어요.")
                continue
            p.overlays[p.overlays.index(o)] = new
            res.summaries.append(f"화면 글자 '{o.text[:16]}' 고치기: " + ", ".join(f"{a}={b}" for a, b in upd.items()))
        elif k == "delete_overlay":
            o = next((o for o in p.overlays if o.id == op["id"]), None)
            if o:
                p.overlays = [x for x in p.overlays if x.id != o.id]
                res.summaries.append(f"화면 글자 '{o.text[:16]}' 지우기")
        elif k == "short":
            res.short = {"start": op["start"], "end": op["end"], "format": op["format"]}
            res.summaries.append(f"{_fmt(op['start'])} ~ {_fmt(op['end'])} ({op['end'] - op['start']:.0f}초)를 {'릴스' if op['format'] == 'reels' else '유튜브 쇼츠'} 화면으로 뽑기")
    merged = [(max(0.0, a), min(L0, b)) for a, b in _merge(cuts) if min(L0, b) - max(0.0, a) >= 0.05]
    if merged and sum(b - a for a, b in merged) >= L0 - 0.05:
        res.warnings.append("영상 전체를 잘라내는 지시라서 자르기는 건너뛰었어요.")
        merged = []
        res.summaries = [s for s in res.summaries if "잘라내" not in s]
    for a, b in sorted(merged, reverse=True):          # 뒤에서부터 잘라야 앞쪽 시각이 안 밀려요
        ripple_cut(p, a, b)
        res.cut_seconds += b - a
    if res.short and merged:                            # 쇼츠 구간도 잘라낸 만큼 당겨 줍니다 (지금 시각 → 자른 뒤 시각)
        def after(t: float) -> float:
            for a, b in merged:
                t = t - (b - a) if t >= b else (a if t > a else t)
            return t
        res.short = {**res.short, "start": round(after(res.short["start"]), 2), "end": round(after(res.short["end"]), 2)}
    return res


# ---------------------------------------------------------------- Claude에게 계획 받기

class Plan(BaseModel):
    reply: str = ""
    ops: list[dict] = []
    summaries: list[str] = []
    warnings: list[str] = []
    short: dict | None = None
    length_before: float = 0.0
    length_after: float = 0.0


def plan_edit(root: Path, project: Project, text: str, words: list[Word], media_names: dict[str, str], history: list[dict] | None = None,
              playhead: float = 0.0, selected: dict | None = None, short_range: dict | None = None, model: str | None = None,
              client=None, runner=None) -> Plan:
    """지시 하나를 계획으로. 준비가 안 됐거나 호출이 실패하면 RuntimeError(한국어 안내)."""
    if not text.strip():
        raise RuntimeError("어떻게 편집할지 적어 주세요.")
    ctx = build_context(project, words, media_names, playhead, selected, short_range)
    data = claude_copy.ask(root, SYSTEM, SCHEMA, _prompt(ctx, history or [], text), model, client=client, runner=runner, max_tokens=3000)
    if not isinstance(data, dict):
        raise RuntimeError("Claude의 답을 읽지 못했어요. 한 번 더 말해 주세요.")
    ops, warns = normalize_ops(project, data.get("ops"), words)
    ap = apply_ops(project, ops, words, media_names)
    return Plan(reply=str(data.get("reply") or "").strip()[:600], ops=ops, summaries=ap.summaries, warnings=warns + ap.warnings, short=ap.short,
                length_before=round(project_length(project), 2), length_after=round(project_length(ap.project), 2))
