"""구독·좋아요 엔딩 애니메이션: 직접 그린 원본 디자인 (플랫폼의 로고·버튼 이미지를 쓰지 않습니다).

프리셋 4종을 PIL로 한 장씩 그려 ffmpeg로 mp4(소리 없음)로 만듭니다. 화면 크기와 문구·색을 바꿔 여러 번 만들 수 있고,
같은 설정은 한 번만 만들어 보관함(media)에 둡니다 (`ending_media`).
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw

from .thumbnail import font

KINDS = {
    "subscribe": "구독 버튼 누르기",
    "heart": "좋아요 하트 팡",
    "bell": "알림 종 흔들기",
    "thanks": "시청 감사 + 다음 영상",
}
FPS = 30
DEFAULTS = {"kind": "subscribe", "text": "구독 & 좋아요", "sub": "알림 설정까지!", "accent": "#ff3b3b", "bg1": "#1b1030", "bg2": "#3a1c5e",
            "duration": 3.0}


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ease_back(x: float) -> float:
    x = min(1.0, max(0.0, x)); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _bg(W: int, H: int, c1: str, c2: str) -> np.ndarray:
    a, b = np.array(_rgb(c1), float), np.array(_rgb(c2), float)
    y = np.linspace(0, 1, H)[:, None, None]
    return (a * (1 - y) + b * y).astype(np.uint8).repeat(W, axis=1)


def _text_center(d: ImageDraw.ImageDraw, xy, text, size, fill, stroke=0, scale=1.0, W=0):
    f = font(max(10, int(size * scale)))
    w = d.textlength(text, font=f)
    if W and w > W * 0.9:   # 화면보다 넓으면 줄입니다
        f = font(max(10, int(size * scale * W * 0.9 / w))); w = d.textlength(text, font=f)
    d.text((xy[0] - w / 2, xy[1] - f.size / 2), text, font=f, fill=fill, stroke_width=stroke, stroke_fill="#000000")


def _rrect(d, box, r, fill):
    d.rounded_rectangle(box, radius=r, fill=fill)


def _heart(cx, cy, s):
    pts = []
    for i in range(80):
        t = i / 80 * 2 * math.pi
        pts.append((cx + s * 16 * math.sin(t) ** 3, cy - s * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
    return pts


def _bell(size: int, color: str, angle: float) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(im); c = size / 2
    col = _rgb(color)
    d.ellipse((c - size * .28, size * .12, c + size * .28, size * .62), fill=col)                 # 머리
    d.polygon([(c - size * .28, size * .38), (c + size * .28, size * .38), (c + size * .4, size * .72), (c - size * .4, size * .72)], fill=col)
    d.rounded_rectangle((c - size * .44, size * .7, c + size * .44, size * .78), radius=size * .04, fill=col)
    d.ellipse((c - size * .09, size * .76, c + size * .09, size * .94), fill=col)                     # 추
    d.ellipse((c - size * .05, size * .04, c + size * .05, size * .14), fill=col)                    # 꼭지
    return im.rotate(angle, resample=Image.BICUBIC, center=(c, size * .08))


def frame(p: dict, t: float, W: int, H: int, bg: np.ndarray, rng: np.random.Generator) -> Image.Image:
    im = Image.fromarray(bg.copy()); d = ImageDraw.Draw(im, "RGBA")
    acc = _rgb(p["accent"]); u = min(W, H * 0.6)    # u: 크기 기준 (세로 화면이면 폭, 가로 화면이면 높이의 일부)
    cx, cy = W / 2, H / 2
    kind = p["kind"]
    appear = _ease_back(t / 0.5)
    if kind == "subscribe":
        _text_center(d, (cx, cy - u * 0.55), p["text"], u * 0.10, "#ffffff", 3, appear, W)
        bw, bh = u * 0.62, u * 0.2
        press = 0.0 if t < 1.0 else min(1.0, (t - 1.0) / 0.12)          # 1초에 눌림
        sc = (1 - 0.06 * press * (1 if t < 1.3 else max(0, 1 - (t - 1.3) / 0.2))) * min(1.0, _ease_back((t - 0.15) / 0.5))
        done = t >= 1.1
        fill = (80, 80, 90) if done else acc
        box = (cx - bw * sc / 2, cy - bh * sc / 2, cx + bw * sc / 2, cy + bh * sc / 2)
        _rrect(d, box, bh * sc / 2, fill)
        _text_center(d, (cx, cy), "구독중 ✓" if done else "구독", bh * 0.42, "#ffffff", 0, sc, W)
        if 0.5 <= t < 1.1:        # 손가락 대신 점이 다가옵니다
            k = _ease_out((t - 0.5) / 0.5)
            px, py = cx + u * 0.25 * (1 - k) + u * 0.2, cy + u * 0.35 * (1 - k) + bh * 0.2
            d.ellipse((px - u * 0.04, py - u * 0.04, px + u * 0.04, py + u * 0.04), fill=(255, 255, 255, 230))
        if t >= 1.0:              # 눌린 자리에서 퍼지는 물결
            k = (t - 1.0) / 0.6
            if k < 1:
                r = bh * (0.3 + 1.2 * k)
                d.ellipse((cx + u * 0.2 - r, cy + bh * 0.2 - r, cx + u * 0.2 + r, cy + bh * 0.2 + r), outline=(255, 255, 255, int(200 * (1 - k))), width=4)
        if t >= 1.4:              # 종이 울립니다
            ang = math.sin((t - 1.4) * 22) * 18 * max(0, 1 - (t - 1.4) / 1.2)
            b = _bell(int(u * 0.2), "#ffd23f", ang)
            im.paste(b, (int(cx - b.width / 2), int(cy + u * 0.28)), b)
            _text_center(d, (cx, cy + u * 0.62), p["sub"], u * 0.065, "#ffffff", 2, min(1, (t - 1.4) / 0.3 + 0.01), W)
    elif kind == "heart":
        beat = 1 + 0.08 * math.sin(t * 9) * (1 if t > 0.5 else 0)
        s = u * 0.0165 * min(1, _ease_back(t / 0.55)) * beat
        d.polygon(_heart(cx, cy - u * 0.05, s), fill=acc)
        _text_center(d, (cx, cy + u * 0.5), p["text"], u * 0.095, "#ffffff", 3, appear, W)
        _text_center(d, (cx, cy + u * 0.64), p["sub"], u * 0.06, "#ffffff", 2, min(1, max(0.01, (t - 0.4) / 0.3)), W)
        for i in range(24):       # 퍼지는 작은 하트
            a = rng.uniform(0, 2 * math.pi) if t == 0 else (i * 2.399963)
            sp = u * (0.25 + 0.5 * ((i * 37) % 10) / 10)
            k = (t - 0.35) / 1.5
            if 0 < k < 1:
                x, y = cx + math.cos(a) * sp * k, cy - u * 0.05 + math.sin(a) * sp * k - u * 0.1 * k * (1 - k)
                d.polygon(_heart(x, y, u * 0.004 * (1 - k * 0.5)), fill=acc + (int(255 * (1 - k)),))
    elif kind == "bell":
        ang = math.sin(t * 18) * 22 * max(0, 1 - max(0, t - 0.4) / 2.2) * min(1, t / 0.2)
        b = _bell(int(u * 0.55 * min(1, _ease_back(t / 0.5) + 0.001)) + 2, "#ffd23f", ang)
        im.paste(b, (int(cx - b.width / 2), int(cy - u * 0.45)), b)
        if t > 0.6:
            for side in (-1, 1):  # 소리 파동
                for i in range(3):
                    k = ((t * 1.6 + i / 3) % 1.0)
                    r = u * (0.22 + 0.2 * k)
                    d.arc((cx + side * u * 0.3 - r, cy - u * 0.2 - r, cx + side * u * 0.3 + r, cy - u * 0.2 + r),
                          -35 if side > 0 else 145, 35 if side > 0 else 215, fill=(255, 255, 255, int(200 * (1 - k))), width=5)
        _text_center(d, (cx, cy + u * 0.4), p["text"], u * 0.095, "#ffffff", 3, appear, W)
        _text_center(d, (cx, cy + u * 0.54), p["sub"], u * 0.06, "#ffd23f", 2, min(1, max(0.01, (t - 0.4) / 0.3)), W)
    else:  # thanks
        x = cx - (1 - _ease_out(t / 0.6)) * W
        _text_center(d, (x, cy - u * 0.2), p["text"], u * 0.11, "#ffffff", 3, 1, W)
        _text_center(d, (cx, cy + u * 0.02), p["sub"], u * 0.065, "#ffd23f", 2, min(1, max(0.01, (t - 0.5) / 0.3)), W)
        for i in range(3):        # 아래로 튀는 화살표
            y0 = cy + u * 0.25 + i * u * 0.1 + math.sin(t * 8 - i) * u * 0.02
            a = int(255 * (0.35 + 0.65 * ((math.sin(t * 6 - i * 1.2) + 1) / 2)))
            d.polygon([(cx - u * 0.07, y0), (cx + u * 0.07, y0), (cx, y0 + u * 0.06)], fill=acc + (a,))
    return im


def render(path, params: dict, W: int, H: int) -> None:
    p = {**DEFAULTS, **params}
    if p["kind"] not in KINDS:
        raise ValueError("모르는 엔딩 종류예요.")
    W, H = W // 2 * 2, H // 2 * 2
    n = int(round(float(p["duration"]) * FPS))
    bg = _bg(W, H, p["bg1"], p["bg2"])
    rng = np.random.default_rng(1)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(n):
            proc.stdin.write(frame(p, i / FPS, W, H, bg, rng).convert("RGB").tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode()
        if proc.wait() != 0:
            raise RuntimeError(f"엔딩을 만들지 못했어요: {err[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()


def ending_key(params: dict, W: int, H: int) -> str:
    p = {**DEFAULTS, **params}
    return hashlib.md5((json.dumps(p, sort_keys=True, ensure_ascii=False) + f"|{W}x{H}|v1").encode()).hexdigest()
