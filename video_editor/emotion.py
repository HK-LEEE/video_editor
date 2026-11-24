"""감정: 글에서 감정 알아채기 + 감정에 맞게 말투(높낮이·속도·세기·음색)를 입히기.

맥의 `say`나 espeak처럼 감정을 모르는 읽기 엔진 위에, 문장마다 억양을 얹는 방식입니다 (모델 다운로드 없음).
- 말 높이(F0)를 pyworld로 직접 움직입니다: 전체 높낮이, 억양의 폭(크게/작게), 문장 끝 올림·내림.
- 속도는 읽을 때 반영하고, 소리 크기·밝기(고음 EQ)·약한 찌그러짐은 pedalboard로 입힙니다.
- 글에서의 감정 알아채기는 사전(한국어·영어 낱말과 !?, ㅋㅋ, ㅠㅠ 같은 표시) 방식이라 완벽하지 않아요.
  그래서 문장 앞에 [화남] 처럼 적거나, 자막마다 감정을 직접 골라 덮어쓸 수 있습니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pedalboard as pb
import pyworld

from .engine import SAMPLE_RATE as SR, peak_limit

EMOTIONS = {
    "neutral": "보통", "happy": "기쁨", "excited": "신남", "sad": "슬픔",
    "angry": "화남", "calm": "차분", "whisper": "속삭임",
}
ALIASES = {v: k for k, v in EMOTIONS.items()} | {k: k for k in EMOTIONS} | {
    "기쁜": "happy", "행복": "happy", "즐거움": "happy", "신나게": "excited", "흥분": "excited", "슬픈": "sad",
    "우울": "sad", "분노": "angry", "화난": "angry", "화내며": "angry", "침착": "calm", "평온": "calm", "차분히": "calm",
    "속삭": "whisper", "보통": "neutral", "중립": "neutral", "joy": "happy", "angry": "angry", "anger": "angry",
    "sadness": "sad", "calm": "calm", "excite": "excited",
}


@dataclass(frozen=True)
class Profile:
    rate: float = 1.0        # 말 속도 배율
    pitch: float = 0.0       # 전체 높낮이 (반음)
    spread: float = 1.0      # 억양 폭 (1보다 크면 높낮이 변화가 커짐, 작으면 단조롭게)
    end: float = 0.0         # 문장 끝 높낮이 변화 (반음, + 올림 / - 내림)
    gain_db: float = 0.0     # 소리 크기
    tilt_db: float = 0.0     # 고음 밝기 (+ 밝고 또렷 / - 어둡고 먹먹)
    drive_db: float = 0.0    # 거칠기 (살짝 찌그러짐)
    formant: float = 1.0
    pause: float = 0.25      # 다음 문장 앞 쉼 (초)
    breathy: bool = False


PROFILES: dict[str, Profile] = {
    "neutral": Profile(),
    "happy": Profile(rate=1.08, pitch=1.5, spread=1.35, end=1.2, gain_db=1.0, tilt_db=2.0, pause=0.2),
    "excited": Profile(rate=1.2, pitch=2.5, spread=1.65, end=1.5, gain_db=2.5, tilt_db=3.0, pause=0.15),
    "sad": Profile(rate=0.82, pitch=-2.0, spread=0.6, end=-2.0, gain_db=-3.0, tilt_db=-3.0, formant=0.98, pause=0.55),
    "angry": Profile(rate=1.12, pitch=-0.5, spread=1.5, end=-0.5, gain_db=3.5, tilt_db=3.0, drive_db=6.0, formant=0.96, pause=0.18),
    "calm": Profile(rate=0.9, pitch=-0.7, spread=0.75, end=-0.5, gain_db=-1.0, tilt_db=-1.5, pause=0.45),
    "whisper": Profile(rate=0.92, gain_db=-4.0, tilt_db=2.0, pause=0.4, breathy=True),
}

# ---------------------------------------------------------------- 글에서 감정 알아채기

LEX: dict[str, dict[str, list[str]]] = {
    "happy": {"ko": ["기쁘", "기쁜", "기쁨", "기뻐", "기뻤", "기쁠", "행복", "즐겁", "즐거", "좋았", "좋은", "좋아", "좋네", "좋다", "좋습", "반갑", "신나는", "행복", "좋아", "좋은", "좋다", "좋네", "즐거", "웃", "하하", "ㅎㅎ", "ㅋㅋ", "사랑", "고마", "감사", "축하", "최고", "다행", "재밌", "재미있", "멋지", "예쁘", "맛있"],
              "en": ["happy", "glad", "love", "great", "wonderful", "thank", "congrat", "nice", "enjoy", "awesome", "haha", "lol", "delight"]},
    "excited": {"ko": ["대박", "우와", "와!", "헐", "드디어", "신나", "짜릿", "믿을 수 없", "놀라", "미쳤", "엄청", "세상에", "빨리", "지금 바로", "놓치지"],
                "en": ["wow", "amazing", "incredible", "can't wait", "finally", "unbelievable", "insane", "omg", "awesome!"]},
    "sad": {"ko": ["슬프", "슬픈", "슬픔", "슬퍼", "슬펐", "슬플", "울었", "울고", "울음", "우울", "눈물", "울", "아쉽", "미안", "그립", "그리워", "외롭", "힘들", "안타깝", "죽고", "포기", "ㅠ", "ㅜ", "흑", "후회", "떠나", "이별"],
            "en": ["sad", "sorry", "miss you", "lonely", "cry", "tears", "depress", "regret", "hurt", "lost", "goodbye", "unfortunately"]},
    "angry": {"ko": ["화나", "화난", "화났", "화가", "화내", "화를", "짜증나", "짜증", "열받", "싫어", "싫다", "미치", "당장", "뭐라고", "제발", "분노", "도대체", "어떻게 이럴", "용서", "닥쳐", "꺼져", "거짓말", "책임"],
              "en": ["angry", "furious", "hate", "damn", "shut up", "how dare", "stupid", "idiot", "annoying", "unacceptable", "enough"]},
    "calm": {"ko": ["차분", "차분한", "천천히", "편안한", "편안", "괜찮", "휴식", "안심", "조용", "고요", "숨을", "잠시", "여유", "평화", "명상"],
             "en": ["calm", "relax", "breathe", "peaceful", "gently", "slowly", "quiet", "rest", "it's okay", "take your time"]},
    "whisper": {"ko": ["비밀", "쉿", "몰래", "속삭"], "en": ["secret", "shh", "whisper", "quietly"]},
}
SENT_END = re.compile(r"(?<=[.!?。？！…])\s+|\n+")
TAG = re.compile(r"^\s*[\[\(【]\s*([^\]\)】]{1,12}?)\s*[\]\)】]\s*")


def normalize(label: str) -> str | None:
    """'화남', 'angry', '신남' 같은 이름을 표준 이름으로. 모르는 이름이면 None."""
    k = (label or "").strip().lower()
    return ALIASES.get(k) or ALIASES.get(label.strip())


def detect(text: str, lang: str = "ko") -> tuple[str, float]:
    """문장 하나의 감정과 세기(0~1). 단서가 없으면 ('neutral', 0)."""
    t = text.strip()
    if not t:
        return "neutral", 0.0
    low = t.lower()
    lg = "ko" if lang in ("ko",) else "en"
    score = {k: 0.0 for k in LEX}
    for emo, langs in LEX.items():
        for w in langs.get(lg, []) + (langs.get("en", []) if lg == "ko" else []):
            if w in low:
                score[emo] += 1.0
    ex = len(re.findall(r"[!！]", t))
    if ex and not any(score.values()):
        score["excited"] += 0.6 + 0.2 * min(ex, 3)           # 느낌표만 있는 문장
    elif ex:
        best = max(score, key=score.get)
        score[best] += 0.3 * min(ex, 3)
    if re.search(r"[A-Z]{4,}", t) and not re.search(r"[가-힣]", t):
        score["angry"] += 0.4                                  # 영어 대문자 외침
    if re.search(r"(ㅋ{2,}|ㅎ{2,})", t):
        score["happy"] += 1.0
    if re.search(r"(ㅠ{1,}|ㅜ{1,})", t):
        score["sad"] += 1.0
    if re.search(r"(\.{3,}|…)$", t) and not any(score.values()):
        score["sad"] += 0.35
    emo = max(score, key=score.get)
    if score[emo] <= 0:
        return "neutral", 0.0
    return emo, float(min(1.0, 0.35 + 0.25 * score[emo]))


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_END.split(text.strip()) if p and p.strip()]
    return parts


def plan(text: str, lang: str = "ko", emotion: str = "auto", level: float = 1.0) -> list[dict]:
    """글을 문장으로 나누고 문장마다 감정을 정합니다.
    문장 앞의 [화남] 표시가 가장 우선, 그다음 emotion(고정 감정), 'auto'면 글에서 알아챕니다.
    반환: [{text, emotion, level, question}]  (level: 감정 세기 배율, 문장마다 단서의 세기가 반영됨)"""
    fixed = None if emotion in ("", "auto") else normalize(emotion)
    if emotion not in ("", "auto") and fixed is None:
        raise ValueError(f"모르는 감정: {emotion}")
    out = []
    for sent in split_sentences(text):
        m = TAG.match(sent)
        tagged = normalize(m.group(1)) if m else None
        body = sent[m.end():].strip() if tagged else sent
        if not body:
            continue
        if tagged:
            emo, strength = tagged, 1.0
        elif fixed:
            emo, strength = fixed, 1.0
        else:
            emo, strength = detect(body, lang)
            strength = 0.6 + 0.4 * strength if emo != "neutral" else 0.0
        out.append({"text": body, "emotion": emo, "level": round(level * strength, 3),
                    "question": body.rstrip().endswith(("?", "？"))})
    return out


# ---------------------------------------------------------------- 말투 입히기

def _mix(p: Profile, level: float) -> Profile:
    """level 0 = 보통, 1 = 그대로, 2 = 두 배로 과장."""
    n = Profile()
    lv = max(0.0, level)
    lin = lambda a, b: b + (a - b) * lv
    return Profile(rate=float(np.clip(lin(p.rate, n.rate), 0.5, 1.8)), pitch=lin(p.pitch, 0), spread=max(0.2, lin(p.spread, 1.0)),
                   end=lin(p.end, 0), gain_db=lin(p.gain_db, 0), tilt_db=lin(p.tilt_db, 0), drive_db=max(0.0, lin(p.drive_db, 0)),
                   formant=float(np.clip(lin(p.formant, 1.0), 0.8, 1.2)), pause=max(0.05, lin(p.pause, n.pause)), breathy=p.breathy and lv > 0.3)


def profile_for(emotion: str, level: float = 1.0) -> Profile:
    return _mix(PROFILES[emotion], level)


def prosody(x: np.ndarray, emotion: str, level: float = 1.0, question: bool = False, sr: int = SR) -> np.ndarray:
    """소리 x (문장 하나)에 감정의 높낮이·세기·음색을 입힙니다. 길이는 그대로입니다."""
    p = profile_for(emotion, level)
    end_semi = p.end + (1.8 * min(max(level, 0.6), 1.5) if question else 0.0)
    if (emotion == "neutral" and not question) or x.size < sr * 0.05 or not np.any(x):
        return x
    y = x
    if abs(p.pitch) > 0.01 or abs(p.spread - 1) > 0.01 or abs(end_semi) > 0.01 or abs(p.formant - 1) > 0.005 or p.breathy:
        y = _warp_f0(x, sr, p, end_semi)
    chain = []
    if abs(p.tilt_db) > 0.05:
        chain.append(pb.HighShelfFilter(cutoff_frequency_hz=3000, gain_db=p.tilt_db))
        if p.tilt_db < 0:
            chain.append(pb.LowpassFilter(cutoff_frequency_hz=float(np.clip(9000 + 450 * p.tilt_db, 4500, 14000))))
    if p.drive_db > 0.1:
        chain.append(pb.Distortion(drive_db=p.drive_db))
    if p.gain_db:
        chain.append(pb.Gain(gain_db=p.gain_db))
    if chain:
        y = pb.Pedalboard(chain)(y.astype(np.float32), sr)
    return np.asarray(y, dtype=np.float32)[: x.size] if y.size >= x.size else np.pad(y, (0, x.size - y.size)).astype(np.float32)


def _warp_f0(x: np.ndarray, sr: int, p: Profile, end_semi: float) -> np.ndarray:
    d = x.astype(np.float64)
    f0, t = pyworld.dio(d, sr, frame_period=5.0)
    f0 = pyworld.stonemask(d, f0, t, sr)
    sp = pyworld.cheaptrick(d, f0, t, sr)
    ap = pyworld.d4c(d, f0, t, sr)
    voiced = f0 > 0
    if voiced.sum() >= 3:
        logf = np.log2(f0[voiced])
        mean = float(np.mean(logf))
        pos = np.linspace(0, 1, f0.size)
        tail = np.clip((pos - 0.65) / 0.35, 0, 1) ** 1.5           # 문장 끝 35% 구간에서 올리거나 내림
        shifted = mean + (np.log2(np.where(voiced, f0, 1.0)) - mean) * p.spread + p.pitch / 12.0 + end_semi / 12.0 * tail
        f0 = np.where(voiced, 2.0 ** shifted, 0.0)
    if p.breathy:
        f0 = np.zeros_like(f0)
        ap = np.ones_like(ap)
    if abs(p.formant - 1.0) > 1e-3:
        from .engine import _warp_envelope
        sp = _warp_envelope(sp, p.formant)
    y = pyworld.synthesize(f0, np.ascontiguousarray(sp), np.ascontiguousarray(ap), sr, frame_period=5.0)
    return y[: x.size].astype(np.float32)


def apply_spans(audio: np.ndarray, spans: list[dict], sr: int = SR) -> np.ndarray:
    """audio 위의 구간 [{start,end,emotion,level,question}](초)마다 prosody를 입힙니다."""
    out = audio.astype(np.float32).copy()
    for sp in spans:
        a, b = int(max(0, sp["start"]) * sr), int(min(audio.size / sr, sp["end"]) * sr)
        if b - a < sr * 0.05 or (sp["emotion"] == "neutral" and not sp.get("question")) or (sp.get("level", 1) <= 0 and not sp.get("question")):
            continue
        seg = prosody(audio[a:b], sp["emotion"], sp.get("level", 1.0), bool(sp.get("question")), sr)
        n = min(int(sr * 0.01), (b - a) // 4)
        if n > 1:   # 이음새가 딱딱 끊기지 않게 바깥쪽 10ms 섞기
            w = np.linspace(0, 1, n, dtype=np.float32)
            seg[:n] = seg[:n] * w + audio[a:a + n] * (1 - w)
            seg[-n:] = seg[-n:] * w[::-1] + audio[b - n:b] * (1 - w[::-1])
        out[a:b] = seg
    return out


def synth_emotional(text: str, lang: str, synth, emotion: str = "auto", level: float = 1.0, rate: float = 1.0,
                    sr: int = SR, post=None) -> tuple[np.ndarray, list[dict]]:
    """text를 문장마다 감정을 정해 읽어 한 덩어리로 이어 붙입니다.
    synth(sentence, rate) -> 소리(float32, 모노, sr)는 읽기 엔진 호출입니다.
    post(소리) 가 있으면 (예: 보이스 캐릭터로 바꾸기) 감정을 입히기 *전에* 전체 소리에 적용합니다 — 길이는 같아야 합니다.
    반환: (소리, 문장 구간 목록)"""
    items = plan(text, lang, emotion, level)
    if not items:
        raise RuntimeError("읽을 글이 없어요.")
    parts, spans, t = [], [], 0.0
    for i, it in enumerate(items):
        p = profile_for(it["emotion"], it["level"]) if it["emotion"] != "neutral" else Profile()
        x = synth(it["text"], float(np.clip(rate * p.rate, 0.4, 2.5)))
        if i:
            gap = np.zeros(int(sr * p.pause), dtype=np.float32)
            parts.append(gap)
            t += gap.size / sr
        spans.append({**it, "start": round(t, 4), "end": round(t + x.size / sr, 4)})
        parts.append(x.astype(np.float32))
        t += x.size / sr
    audio = np.concatenate(parts)
    if post is not None:
        converted = post(audio)
        audio = np.pad(converted, (0, max(0, audio.size - converted.size)))[: audio.size]
    return peak_limit(apply_spans(audio, spans, sr), sr), spans
