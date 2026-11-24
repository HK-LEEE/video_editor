"""자동 이어 붙이기: 짧은 영상(보통 AI가 만든 5~10초 캐릭터 쇼츠)을 순서대로 이어 붙일 때 어색하지 않게 다듬습니다.

이음새(앞 영상의 끝 → 뒤 영상의 시작)마다
- 앞뒤에 붙어 있는 불량 프레임(검은 화면·멈춘 화면·깜빡이는 글리치)을 잘라내고,
- 뒤 영상의 색·밝기를 앞 영상 끝에 맞추고, 소리 크기를 맞추고, 짧게 겹쳐(크로스페이드) 섞고,
- 마지막·첫 화면이 얼마나 닮았는지, 움직임이 이어지는지로 하드컷 / 크로스 디졸브 / AI 프레임 보간(모핑) / 암전 중에서 고릅니다.
- (선택) 끝 화면과 시작 화면이 가장 닮은 순서를 제안합니다.

화면 분석은 영상을 32x18 작은 그림으로 줄여 numpy로 하기 때문에 빠릅니다(영상당 1~2초, 결과는 캐시).
모핑은 RIFE(rife-ncnn-vulkan, `bash scripts/setup_ai_morph.sh`로 설치)를 쓰고, 없으면 ffmpeg의 움직임 보간(minterpolate)으로 대신합니다.

시각은 모두 '원본 미디어 기준 초'입니다. 계획(plan) → 타임라인 클립(layout)·미리보기(preview)가 같은 계획을 써서 서로 어긋나지 않습니다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .editor import (SR, Clip, Color, Project, Track, TEMP_GAIN, Workspace, _active_rms, _check_id, add_media,
                     color_filters, export_video, media_audio)

ROOT = Path(__file__).resolve().parent.parent
PFPS = 24                    # 분석용 프레임 수 (초당)
GW, GH = 32, 18              # 분석용 그림 크기
WIN = 6                      # 이음새 근처에서 보는 프레임 수 (0.25초)
FREEZE_DIFF = 0.0025         # 이보다 덜 달라지면 '멈춘 화면'
BLACK_Y = 0.04               # 이보다 어두우면 '검은 화면'
MAX_HEAD_TRIM = 1.0
MAX_TAIL_TRIM = 1.5
MIN_KEEP = 1.0
SETUP_MORPH = "bash scripts/setup_ai_morph.sh"
DEFAULT_DUR = {"cut": 0.0, "dissolve": 0.4, "dip": 0.6, "morph": 0.5}
TRANSITION_OF = {"cut": "none", "dissolve": "fade", "dip": "dip", "morph": "none"}
KINDS = ("auto", "cut", "dissolve", "dip", "morph")
LABEL = {"cut": "하드컷", "dissolve": "크로스 디졸브", "dip": "암전(검게 어두워졌다 나타남)", "morph": "AI 모핑"}


# ================================================================ 모핑 엔진 (RIFE)

def rife_dir() -> Path:
    return Path(os.environ.get("RIFE_DIR") or ROOT / "third_party" / "rife")


def rife_bin() -> Path | None:
    for p in (os.environ.get("RIFE_BIN"), rife_dir() / "rife-ncnn-vulkan", shutil.which("rife-ncnn-vulkan")):
        if p and Path(p).exists():
            return Path(p)
    return None


def rife_model() -> Path:
    return rife_dir() / "rife-v4.6"


def morph_engine() -> str:
    return "rife" if rife_bin() and rife_model().exists() else "ffmpeg"


# ================================================================ 화면 분석

def load_frames(ws: Workspace, mid: str) -> np.ndarray:
    """영상을 초당 24장, 32x18 RGB(0~1)로 줄인 배열 (N, 18, 32, 3). 캐시합니다."""
    cache = ws.root / "cache" / f"{mid}_aj.npy"
    if cache.exists():
        return np.load(cache).astype(np.float32) / 255.0
    meta = ws.meta(mid)
    if not meta["has_video"]:
        raise HTTPException(400, f"영상이 아닌 미디어입니다: {meta['name']}")
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", "300", "-i", str(ws.media_file(mid)), "-an",
                        "-vf", f"fps={PFPS},scale={GW}:{GH}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True)
    n = len(r.stdout) // (GW * GH * 3)
    if r.returncode != 0 or n < 2:
        raise HTTPException(400, f"영상을 읽을 수 없어요: {meta['name']} {r.stderr[-200:].decode(errors='ignore')}")
    fr = np.frombuffer(r.stdout[: n * GW * GH * 3], np.uint8).reshape(n, GH, GW, 3)
    tmp = cache.with_suffix(".tmp.npy")
    np.save(tmp, fr)
    tmp.replace(cache)
    return fr.astype(np.float32) / 255.0


def frame_diffs(fr: np.ndarray) -> np.ndarray:
    """d[i] = i번째와 i+1번째 화면이 얼마나 다른가 (0~1)."""
    return np.abs(fr[1:] - fr[:-1]).mean(axis=(1, 2, 3)) if len(fr) > 1 else np.zeros(0, np.float32)


def suggest_trim(fr: np.ndarray) -> dict:
    """앞뒤의 검은 화면·멈춘 화면·글리치(갑자기 확 튀는 프레임)를 찾아 잘라낼 프레임 수를 돌려줍니다."""
    n = len(fr)
    Y = fr.mean(axis=(1, 2, 3))
    d = frame_diffs(fr)
    med = float(np.median(d)) if d.size else 0.0
    p75 = float(np.percentile(d, 75)) if d.size else 0.0
    moving = p75 > 0.003                       # 클립 전체가 가만히 있는 영상이면 '멈춘 화면' 판정은 하지 않습니다
    freeze = min(FREEZE_DIFF, 0.25 * p75)
    spike = max(0.12, 8 * med)
    max_h, max_t = int(MAX_HEAD_TRIM * PFPS), int(MAX_TAIL_TRIM * PFPS)
    head, tail, rin, rout = 0, 0, [], []

    # ---- 앞
    k = 0
    while k < min(max_h, n - 1) and Y[k] < BLACK_Y:
        k += 1
    if k:
        head = k; rin.append(f"검은 화면 {k / PFPS:.2f}초")
    if moving:
        k = head
        while k < min(max_h, n - 2) and d[k] < freeze:
            k += 1
        if k - head >= 3:
            rin.append(f"멈춘 화면 {(k - head) / PFPS:.2f}초"); head = k
    sp = [i for i in range(head, min(head + WIN - 1, n - 2)) if d[i] > spike]    # 시작 직후 확 튀는 프레임 = 글리치
    if sp:
        rin.append("시작 글리치"); head = sp[-1] + 1
    head = min(head, max_h)

    # ---- 뒤
    k = 0
    while k < min(max_t, n - 1 - head) and Y[n - 1 - k] < BLACK_Y:
        k += 1
    if k:
        tail = k; rout.append(f"검은 화면 {k / PFPS:.2f}초")
    if moving:
        k = tail
        while k < min(max_t, n - 2 - head) and d[n - 2 - k] < freeze:
            k += 1
        if k - tail >= 3:
            rout.append(f"멈춘 화면 {(k - tail) / PFPS:.2f}초"); tail = k
    sp = [j for j in range(tail, min(tail + WIN - 1, n - 2 - head)) if d[n - 2 - j] > spike]   # 끝 직전에 확 튀는 프레임
    if sp:
        rout.append("끝 글리치"); tail = sp[-1] + 1
    tail = min(tail, max_t)

    while n - head - tail < MIN_KEEP * PFPS and (head or tail):   # 너무 많이 잘리지 않게
        if tail >= head and tail:
            tail -= 1
        elif head:
            head -= 1
    return {"head": head, "tail": tail, "reasons_in": rin if head else [], "reasons_out": rout if tail else []}


def stats(fr: np.ndarray) -> dict:
    """프레임 묶음의 평균 밝기·채도·빨강-파랑 차이."""
    m = fr.reshape(-1, 3).mean(axis=0)
    return {"Y": float(0.299 * m[0] + 0.587 * m[1] + 0.114 * m[2]),
            "sat": float((fr.max(axis=3) - fr.min(axis=3)).mean()),
            "rb": float(m[0] - m[2]), "rs": float(m[0] + m[2])}


def apply_color(s: dict, c: Color) -> dict:
    """색 보정을 한 뒤의 평균 값 (편집기 색 보정 모델의 근사)."""
    return {"Y": min(1.0, max(0.0, s["Y"] + c.brightness)), "sat": s["sat"] * c.saturation,
            "rb": s["rb"] + TEMP_GAIN * c.temperature * s["rs"], "rs": s["rs"]}


def match_color(target: dict, src: dict, strength: float) -> Color:
    """src(뒤 영상의 시작) 화면을 target(앞 영상의 끝)에 가깝게 만드는 보정 값."""
    b = float(np.clip((target["Y"] - src["Y"]) * strength, -0.15, 0.15))
    ratio = target["sat"] / src["sat"] if src["sat"] > 0.02 and target["sat"] > 0.02 else 1.0
    sat = float(np.clip(ratio ** strength, 0.7, 1.4))
    t = (target["rb"] - src["rb"]) / (TEMP_GAIN * src["rs"]) * strength if src["rs"] > 0.05 else 0.0
    t = float(np.clip(t, -0.5, 0.5))
    return Color(brightness=round(b, 3) if abs(b) >= 0.01 else 0.0, saturation=round(sat, 3) if abs(sat - 1) >= 0.04 else 1.0,
                 temperature=round(t, 3) if abs(t) >= 0.03 else 0.0)


def seam_distance(a: np.ndarray, b: np.ndarray) -> float:
    """두 화면(앞 끝, 뒤 시작)이 모양으로 얼마나 다른가. 전체 밝기·색 차이는 빼고 비교합니다 (그건 색 맞춤이 고치니까)."""
    return float(np.abs((a - a.mean(axis=(0, 1))) - (b - b.mean(axis=(0, 1)))).mean())


# ================================================================ 계획

class JoinClipIn(BaseModel):
    media: str
    src_in: float | None = Field(None, ge=0)       # 비우면 자동(불량 프레임 정리)
    src_out: float | None = Field(None, gt=0)


class SeamIn(BaseModel):
    transition: Literal["auto", "cut", "dissolve", "dip", "morph"] = "auto"
    dur: float | None = Field(None, ge=0.0, le=3.0)
    color: bool = True                              # 뒤 영상 색을 맞출지
    audio: bool = True                              # 소리를 짧게 겹쳐 섞을지


class JoinOptions(BaseModel):
    auto_trim: bool = True
    match_color: bool = True
    color_strength: float = Field(0.8, ge=0.0, le=1.0)
    match_loudness: bool = True
    audio_xfade: float = Field(0.12, ge=0.0, le=1.0)    # 하드컷 이음새에서 소리를 겹쳐 섞는 길이
    auto_order: bool = False
    allow_morph: bool = True                            # 자동 선택에서 AI 모핑을 쓸지
    dedupe: bool = True                                 # 앞 영상 끝 = 뒤 영상 첫 화면이면 중복 프레임 한두 장 건너뜀


class PlanReq(BaseModel):
    clips: list[JoinClipIn] = Field(min_length=1, max_length=60)
    seams: list[SeamIn] = []
    options: JoinOptions = JoinOptions()


def _auto_bounds(ws: Workspace, c: JoinClipIn, opt: JoinOptions) -> tuple[dict, float, float, np.ndarray]:
    meta = ws.meta(c.media)
    if not meta["has_video"]:
        raise HTTPException(400, f"영상이 아닌 미디어입니다: {meta['name']}")
    fr = load_frames(ws, c.media)
    dur = meta["duration"]
    sug = suggest_trim(fr) if opt.auto_trim else {"head": 0, "tail": 0, "reasons_in": [], "reasons_out": []}
    a_in = sug["head"] / PFPS
    a_out = dur - sug["tail"] / PFPS if sug["tail"] else dur
    return sug, a_in, a_out, fr


def _boundary(fr: np.ndarray, src_in: float, src_out: float):
    """(앞쪽 프레임 묶음, 뒤쪽 프레임 묶음)과 각각의 맨 끝/맨 앞 프레임."""
    n = len(fr)
    i0 = min(n - 2, max(0, int(round(src_in * PFPS))))
    i1 = min(n - 1, max(i0 + 1, int(round(src_out * PFPS)) - 1))
    head, tail = fr[i0: i0 + WIN], fr[max(i0, i1 - WIN + 1): i1 + 1]
    return head, tail


def _motion(w: np.ndarray) -> float:
    return float(np.abs(w[1:] - w[:-1]).mean()) if len(w) > 1 else 0.0


def suggest_order(ws: Workspace, clips: list[JoinClipIn], opt: JoinOptions) -> list[int]:
    """첫 영상은 그대로 두고, 끝 화면이 다음 영상의 시작 화면과 가장 닮은 순서로 줄을 세웁니다 (탐욕적)."""
    info = []
    for c in clips:
        _, a_in, a_out, fr = _auto_bounds(ws, c, opt)
        h, t = _boundary(fr, a_in, a_out)
        info.append((h[0], t[-1]))
    order, left = [0], set(range(1, len(clips)))
    while left:
        last = info[order[-1]][1]
        nxt = min(left, key=lambda j: seam_distance(last, info[j][0]))
        order.append(nxt); left.discard(nxt)
    return order


def plan(ws: Workspace, req: PlanReq) -> dict:
    opt = req.options
    for c in req.clips:
        _check_id(c.media)
    order = list(range(len(req.clips)))
    if opt.auto_order and len(req.clips) > 2:
        order = suggest_order(ws, req.clips, opt)
    ordered = [req.clips[i] for i in order]
    rife = morph_engine() == "rife"

    items = []
    for i, c in enumerate(ordered):
        meta = ws.meta(c.media)
        sug, a_in, a_out, fr = _auto_bounds(ws, c, opt)
        src_in = c.src_in if c.src_in is not None else a_in
        src_out = c.src_out if c.src_out is not None else a_out
        src_in = min(max(0.0, src_in), meta["duration"] - 0.2)
        src_out = min(meta["duration"], max(src_in + 0.2, src_out))
        items.append({"media": c.media, "name": meta["name"], "duration": meta["duration"], "has_audio": meta["has_audio"],
                      "width": meta["width"], "height": meta["height"], "src_in": src_in, "src_out": src_out,
                      "auto_in": a_in, "auto_out": a_out,
                      "auto": c.src_in is None or c.src_out is None, "reasons_in": sug["reasons_in"] if c.src_in is None else [],
                      "reasons_out": sug["reasons_out"] if c.src_out is None else [], "tail_trimmed": sug["tail"] > 0,
                      "_fr": fr})

    # 앞 영상 끝과 똑같은 화면으로 시작하면(이미지→영상 이어 만들기) 첫 화면을 한두 장 건너뛰어 멈칫하지 않게 합니다
    if opt.dedupe:
        for i in range(1, len(items)):
            a, b = items[i - 1], items[i]
            if ordered[i].src_in is not None or b["src_out"] - b["src_in"] < MIN_KEEP + 0.2:
                continue
            _, ta = _boundary(a["_fr"], a["src_in"], a["src_out"])
            hb, _ = _boundary(b["_fr"], b["src_in"], b["src_out"])
            if float(np.abs(ta[-1] - hb[0]).mean()) < 0.012:
                b["src_in"] += 2 / PFPS
                b["reasons_in"] = b["reasons_in"] + ["앞 영상 끝과 같은 첫 화면 건너뜀"]

    # ---- 소리 크기
    levels = []
    for it in items:
        lv = None
        if it["has_audio"]:
            a = media_audio(ws, it["media"])[int(it["src_in"] * SR): int(it["src_out"] * SR)]
            r = _active_rms(a) if a.size else 0.0
            lv = 20 * np.log10(r) if r > 1e-5 else None
        it["level_db"] = lv
        levels.append(lv)
    known = [v for v in levels if v is not None]
    target_db = float(np.median(known)) if known else None
    for it in items:
        g = 1.0
        if opt.match_loudness and it["level_db"] is not None and target_db is not None and abs(target_db - it["level_db"]) > 0.8:
            g = float(np.clip(10 ** ((target_db - it["level_db"]) / 20), 0.4, 2.5))
        it["gain"] = round(g, 3)

    # ---- 이음새
    seams, cur_color = [], Color()
    items[0]["color"] = cur_color.model_dump()
    for i in range(1, len(items)):
        a, b = items[i - 1], items[i]
        ov = req.seams[i - 1] if i - 1 < len(req.seams) else SeamIn()
        ha, ta = _boundary(a["_fr"], a["src_in"], a["src_out"])
        hb, tb = _boundary(b["_fr"], b["src_in"], b["src_out"])
        sa, sb = apply_color(stats(ta), Color(**a["color"])), stats(hb)
        cgap = abs(sa["Y"] - sb["Y"]) + abs(sa["rb"] - sb["rb"]) + abs(sa["sat"] - sb["sat"])    # 밝기·색감 차이
        d = seam_distance(ta[-1], hb[0]) + 0.5 * cgap     # 모양 차이 + 색 차이
        ma, mb = _motion(ta), _motion(hb)
        la, lb = a["src_out"] - a["src_in"], b["src_out"] - b["src_in"]

        if d < 0.05:
            kind, dur, why = "cut", 0.0, "끝 장면과 시작 장면이 거의 같아서 그냥 이어도 자연스러워요"
        elif d < 0.10 and abs(ma - mb) < 0.015 and min(ma, mb) > 0.02:
            kind, dur, why = "cut", 0.0, "움직임의 속도가 이어져서 하드컷이 자연스러워요"
        elif d < 0.16:
            kind, dur, why = "dissolve", 0.3 if max(ma, mb) > 0.03 else 0.4, "장면이 조금 달라서 짧게 겹쳐 녹여요"
        elif d < 0.42:
            if rife and opt.allow_morph:
                kind, dur, why = "morph", 0.5 if max(ma, mb) < 0.03 else 0.4, "장면이 꽤 달라서 AI가 사이 화면을 만들어 이어요"
            else:
                kind, dur, why = "dissolve", 0.5, "장면이 꽤 달라서 천천히 겹쳐 녹여요" + ("" if rife else " (AI 모핑을 쓰려면 설치가 필요해요)")
        else:
            kind, dur, why = "dip", 0.6, "전혀 다른 장면이라 잠깐 어두워졌다가 이어요"
        if ov.transition != "auto":
            kind = ov.transition
            dur = DEFAULT_DUR[kind]
            why = "직접 고른 방식"
        if ov.dur is not None and kind != "cut":
            dur = max(0.1, ov.dur)
        if kind != "cut":
            dur = round(min(dur, 0.4 * min(la, lb), 3.0), 2)
            if dur < 0.1:
                kind, dur = "cut", 0.0

        # 색 맞춤: 앞 영상 끝(보정 뒤)에 뒤 영상 시작을 맞춥니다. 너무 다른 장면이면 약하게만.
        corr = Color()
        if opt.match_color and ov.color and kind != "dip":
            k = 1.0 if cgap < 0.12 else 0.6 if cgap < 0.25 else 0.3    # 아주 다른 장면이면 약하게만 맞춥니다
            corr = match_color(sa, sb, opt.color_strength * k)
        b["color"] = corr.model_dump()
        # 소리: 하드컷은 짧게, 디졸브·암전은 화면과 같은 길이, 모핑은 사이 화면 동안 앞 소리가 사라지게
        xf = (dur if kind in ("dissolve", "dip") else dur if kind == "morph" else opt.audio_xfade if kind == "cut" else 0.0) if ov.audio else 0.0
        seams.append({"transition": kind, "dur": dur, "reason": why, "label": LABEL[kind], "distance": round(d, 3),
                      "color_gap": round(cgap, 3), "motion_a": round(ma, 4), "motion_b": round(mb, 4), "audio_xfade": round(xf, 3),
                      "color_on": ov.color, "audio_on": ov.audio, "color": corr.model_dump()})

    for it in items:
        it.pop("_fr")
    return {"order": order, "clips": items, "seams": seams, "morph_engine": morph_engine(), "target_db": target_db,
            "total": round(sum(it["src_out"] - it["src_in"] for it in items) + sum(s["dur"] for s in seams if s["transition"] == "morph"), 2)}


# ================================================================ 모핑 다리 (사이 화면 만들기)

def _grab(src: Path, t: float, out: Path, W: int, H: int, extra: list[str] | None = None) -> None:
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=decrease", f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1"]
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{max(0.0, t):.4f}", "-i", str(src),
                        "-frames:v", "1", "-vf", ",".join((extra or []) + vf), str(out)], capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"화면을 뽑지 못했어요: {r.stderr[-300:]}")


def interpolate(fa: Path, fb: Path, n: int, outdir: Path, W: int, H: int, fps: int) -> str:
    """fa와 fb 사이에 n장을 만들어 outdir/f%03d.png로 저장. 쓴 엔진 이름을 돌려줍니다."""
    if morph_engine() == "rife":
        ok = True
        for k in range(1, n + 1):
            r = subprocess.run([str(rife_bin()), "-0", str(fa), "-1", str(fb), "-o", str(outdir / f"f{k:03d}.png"),
                                "-m", str(rife_model()), "-s", f"{k / (n + 1):.5f}"], capture_output=True, text=True)
            if r.returncode != 0 or not (outdir / f"f{k:03d}.png").exists():
                ok = False
                break
        if ok:
            return "rife"
        for f in outdir.glob("f*.png"):
            f.unlink()
    # 대체: ffmpeg 움직임 보간. [앞, 앞, 뒤, 뒤] 4장을 1fps 영상으로 보고 (n+1)fps로 늘리면, 가운데 1초(앞→뒤)에 n장이 생깁니다.
    seq = outdir / "seq"
    seq.mkdir(exist_ok=True)
    for i, src in enumerate((fa, fa, fb, fb), 1):
        shutil.copy(src, seq / f"s{i}.png")
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-framerate", "1", "-i", str(seq / "s%d.png"),
                        "-vf", f"minterpolate=fps={n + 1}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=none", str(outdir / "m%03d.png")],
                       capture_output=True, text=True)
    got = sorted(outdir.glob("m*.png"))
    if r.returncode != 0 or len(got) < 2 * (n + 1):
        raise RuntimeError(f"사이 화면 만들기 실패: {r.stderr[-300:]}")
    for k in range(1, n + 1):          # got[n+1]이 앞 화면 그대로, got[2(n+1)-1] 다음이 뒤 화면
        got[n + 1 + k].rename(outdir / f"f{k:03d}.png")
    return "ffmpeg"


def make_bridge(ws: Workspace, a: dict, b: dict, dur: float, W: int, H: int, fps: int = 30) -> dict:
    """앞 영상의 마지막 화면 → 뒤 영상의 첫 화면(색 보정 뒤)을 이어 주는 dur초짜리 영상(소리 없음)을 보관함에 만듭니다."""
    n = max(2, int(round(dur * fps)))
    col = Color(**b["color"])
    key = hashlib.sha1(f"{a['media']}|{a['src_out']:.3f}|{b['media']}|{b['src_in']:.3f}|{col.model_dump_json()}|{n}|{W}x{H}|{morph_engine()}".encode()).hexdigest()[:20]
    ref = ws.root / "cache" / f"bridge_{key}.id"
    if ref.exists():
        try:
            ws.meta(ref.read_text().strip())
            return {"media": ref.read_text().strip(), "engine": morph_engine(), "cached": True}
        except HTTPException:
            pass
    with tempfile.TemporaryDirectory(dir=ws.root / "cache") as td:
        td = Path(td)
        fa, fb = td / "a.png", td / "b.png"
        _grab(ws.media_file(a["media"]), a["src_out"] - 1.5 / PFPS, fa, W, H, color_filters(Color(**a["color"])))
        _grab(ws.media_file(b["media"]), b["src_in"], fb, W, H, color_filters(col))
        engine = interpolate(fa, fb, n, td, W, H, fps)
        out = td / "bridge.mp4"
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(td / "f%03d.png"),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"사이 화면을 영상으로 만들지 못했어요: {r.stderr[-300:]}")
        dst = ws.root / "cache" / f"bridge_{uuid.uuid4().hex}.mp4"
        shutil.move(str(out), dst)
    meta = add_media(ws, dst, f"이음 모핑 ({Path(a['name']).stem}→{Path(b['name']).stem}).mp4")
    ref.write_text(meta["id"])
    return {"media": meta["id"], "engine": engine, "cached": False}


# ================================================================ 타임라인 클립 만들기

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def layout(ws: Workspace, pl: dict, start: float, vtrack: str, atracks: tuple[str, str], bridges: dict[int, str] | None = None,
           first: int = 0, last: int | None = None, window: float | None = None) -> list[dict]:
    """계획(plan)대로 타임라인에 놓을 클립 목록. 영상+소리는 link로 묶은 두 클립입니다.
    first/last/window: 미리보기용으로 일부 이음새 주변만 만들 때 (window초만 남김)."""
    clips, seams = pl["clips"], pl["seams"]
    last = len(clips) - 1 if last is None else last
    bridges = bridges or {}
    out: list[dict] = []
    grid = 30.0                                  # 편집기 내보내기 화면 속도: 클립 길이·시작이 이 칸에 맞아야 겹침 사이에 빈 프레임이 안 생깁니다
    t = round(start * grid) / grid
    for i in range(first, last + 1):
        it = clips[i]
        src_in, src_out = it["src_in"], it["src_out"]
        if window:
            if i < last:
                src_in = max(src_in, src_out - window)
            if i > first:
                src_out = min(src_out, src_in + window)
        # 원본 프레임 칸에 맞추되 0.2ms 앞으로 당깁니다: 내보내기가 -ss를 소수 4자리로 반올림해서, 칸 바로 위로 올라가면 첫 프레임이 빠지고 끝에 빈 프레임이 생겨요
        src_in = max(0.0, round(src_in * grid) / grid - 0.0002)
        limit = int(it["duration"] * grid + 1e-6) / grid
        src_out = src_in + max(1, int(round((min(src_out, limit) - src_in) * grid))) / grid
        if src_out > limit + 1e-9:
            src_out -= 1 / grid
        L = src_out - src_in
        prev = seams[i - 1] if i > first else None
        nxt = seams[i] if i < last else None
        # 이음새 앞쪽: 이 클립이 시작할 때의 전환
        trans, tdur = "none", 0.5
        if prev:
            trans, tdur = TRANSITION_OF[prev["transition"]], max(0.1, prev["dur"])
            if prev["transition"] in ("cut", "morph"):
                trans = "none"
        if prev and prev["transition"] == "morph" and (i - 1) in bridges:    # 사이 화면 다리를 먼저 놓습니다
            bd = max(1, int(round(ws.meta(bridges[i - 1])["duration"] * grid))) / grid
            out.append(Clip(id=_uid(), track=vtrack, media=bridges[i - 1], src_in=0, src_out=bd, start=t).model_dump())
            t += bd
        link = _uid() if it["has_audio"] else None
        prev_tail_trimmed = clips[i - 1]["tail_trimmed"] if prev else False
        v = Clip(id=_uid(), track=vtrack, media=it["media"], src_in=src_in, src_out=src_out, start=t, link=link,
                 color=Color(**it["color"]), transition=trans, transition_dur=tdur if trans != "none" else 0.5)
        out.append(v.model_dump())
        if prev and prev_tail_trimmed and trans != "none":      # 앞 영상: 잘라낸 끝 부분(불량 프레임)을 겹침에 쓰지 않음
            for c in reversed(out[:-1]):
                if c["track"] == vtrack and c["media"] == clips[i - 1]["media"]:
                    c["tail_hold"] = True
                    break
        if it["has_audio"]:
            m = ws.meta(it["media"])
            aud_total = m["duration"]
            keys = []
            xin = prev["audio_xfade"] if prev else 0.0
            if prev and prev["transition"] == "morph":
                xin = min(0.1, L / 4) if prev["audio_on"] else 0.0
            xin = min(xin, L / 3)
            if xin > 0.005:
                keys += [{"t": 0.0, "v": 0.0}, {"t": round(xin, 4), "v": 1.0}]
            ext = 0.0
            if nxt:      # 다음 영상과 겹쳐 섞기 위해 이 소리를 원본의 뒤쪽 소리로 조금 더 이어 줍니다
                ext = min(nxt["audio_xfade"], max(0.0, aud_total - src_out), 3.0)
                if nxt["transition"] == "morph" and nxt["audio_on"]:
                    ext = min(nxt["dur"], max(0.0, aud_total - src_out))
                if ext > 0.005:
                    keys += [{"t": round(L, 4), "v": 1.0}, {"t": round(L + ext, 4), "v": 0.0}]
            keys.sort(key=lambda k: k["t"])
            a = Clip(id=_uid(), track=atracks[i % 2], media=it["media"], src_in=src_in, src_out=src_out + ext, start=t, link=link,
                     gain=it["gain"], vol_keys=keys)
            out.append(a.model_dump())
        t += L
    return out


def canvas_for(ws: Workspace, pl: dict) -> tuple[int, int]:
    c = pl["clips"][0]
    w, h = c["width"] or 1280, c["height"] or 720
    k = min(1.0, 1920 / max(w, h))
    return int(w * k) // 2 * 2, int(h * k) // 2 * 2


def make_bridges(ws: Workspace, pl: dict, only: set[int] | None = None) -> dict[int, str]:
    W, H = canvas_for(ws, pl)
    out = {}
    for i, s in enumerate(pl["seams"]):
        if s["transition"] == "morph" and (only is None or i in only):
            out[i] = make_bridge(ws, pl["clips"][i], pl["clips"][i + 1], s["dur"], W, H)["media"]
    return out


# ================================================================ 미리보기

PREVIEW_SIDE = 640
PREVIEW_WINDOW = 1.6


def preview(ws: Workspace, pl: dict, k: int) -> Path:
    """k번째 이음새 앞뒤 1.6초씩을 실제 내보내기와 같은 방식으로 만든 작은 영상."""
    bridges = make_bridges(ws, pl, {k})
    tracks = [Track(id="v1", kind="video", name="V1"), Track(id="a1", kind="audio", name="A1"), Track(id="a2", kind="audio", name="A2")]
    clips = layout(ws, pl, 0.0, "v1", ("a1", "a2"), bridges, first=k, last=k + 1, window=PREVIEW_WINDOW)
    return export_video(ws, Project(tracks=tracks, clips=[Clip(**c) for c in clips]), max_side=PREVIEW_SIDE)


# ================================================================ 서버 주소

class BuildReq(PlanReq):
    start: float = Field(0.0, ge=0)
    vtrack: str = "v1"
    atracks: list[str] = Field(["a1", "a2"], min_length=2, max_length=2)


class PreviewReq(PlanReq):
    seam: int = Field(0, ge=0)


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    def launch(ref: str, work):
        key = hashlib.sha1(ref.encode()).hexdigest()[:16]
        job = jobs.active("autojoin", key) or jobs.submit("autojoin", key, work)
        return {"status": job.status, "job": job.id}

    def check(req: PlanReq) -> None:
        if len(req.clips) < 1:
            raise HTTPException(400, "영상을 하나 이상 골라 주세요.")
        for c in req.clips:
            _check_id(c.media)
            if not ws.meta(c.media)["has_video"]:
                raise HTTPException(400, "영상이 아닌 미디어가 섞여 있어요.")
        if len(req.seams) > max(0, len(req.clips) - 1):
            raise HTTPException(400, "이음새 설정이 영상 수보다 많아요.")

    @router.get("/autojoin/status")
    def status():
        return {"morph": morph_engine(), "setup_morph": SETUP_MORPH, "rife": rife_bin() is not None}

    @router.post("/autojoin/plan")
    def plan_route(b: PlanReq):
        check(b)
        return launch("plan" + b.model_dump_json() + str(rife_bin()), lambda: plan(ws, b))

    @router.post("/autojoin/build")
    def build_route(b: BuildReq):
        check(b)

        def work():
            pl = plan(ws, b)
            bridges = make_bridges(ws, pl)
            clips = layout(ws, pl, b.start, b.vtrack, (b.atracks[0], b.atracks[1]), bridges)
            made = {m: ws.meta(m) for m in bridges.values()}
            return {"plan": pl, "clips": clips, "media": made}

        return launch("build" + b.model_dump_json() + str(rife_bin()), work)

    @router.post("/autojoin/preview")
    def preview_route(b: PreviewReq):
        check(b)
        if len(b.clips) < 2 or b.seam >= len(b.clips) - 1:
            raise HTTPException(400, "미리 볼 이음새가 없어요.")

        def work():
            pl = plan(ws, b)
            out = preview(ws, pl, b.seam)
            return {"url": f"/api/exports/{out.name}", "seam": pl["seams"][b.seam]}

        return launch("preview" + b.model_dump_json() + str(rife_bin()), work)
