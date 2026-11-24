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
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .thumbnail import font

# 부드러운 엔딩: 이미지(채널 로고·사진)가 들어가고 천천히 떠오르는 따뜻한 느낌. 이미지를 안 골라도 하트 장식이 대신 들어가요.
SOFT_KINDS = {
    "soft_logo": "💛 부드럽게 떠오르는 로고 (이미지)",
    "soft_card": "💛 둥근 카드 + 감사 인사 (이미지)",
    "soft_hearts": "💛 하트가 피어오르는 감사 (이미지)",
    "soft_text": "💛 글로 전하는 따뜻한 인사 (이미지 작게)",
}
# 부드러운 종류를 고르면 문구·색을 이 값으로 채워요 (고쳐 쓸 수 있어요)
SOFT_PRESETS = {
    "soft_logo": {"text": "오늘도 들어주셔서 고마워요", "sub": "구독으로 다음 사연도 함께해요", "accent": "#f08a5d", "bg1": "#fff6ea", "bg2": "#fcd9bd", "text_color": "#5b3a29", "duration": 4.0},
    "soft_card": {"text": "끝까지 함께해 주셔서 감사해요", "sub": "좋아요와 구독은 큰 힘이 돼요", "accent": "#f08a5d", "bg1": "#fff2e2", "bg2": "#f9cfae", "text_color": "#5b3a29", "duration": 4.0},
    "soft_hearts": {"text": "고마운 마음 가득 담아", "sub": "다음 이야기에서 만나요", "accent": "#f2788a", "bg1": "#fff1ec", "bg2": "#fbcdc4", "text_color": "#6a3a3a", "duration": 4.0},
    "soft_text": {"text": "따뜻한 하루 보내세요", "sub": "알림 설정하고 다음 사연도 들어요", "accent": "#e8985e", "bg1": "#fdf3e3", "bg2": "#f7dcb8", "text_color": "#5b3a29", "duration": 4.0},
}
KINDS = {
    **SOFT_KINDS,
    "subscribe": "구독 버튼 누르기",
    "heart": "좋아요 하트 팡",
    "bell": "알림 종 흔들기",
    "thanks": "시청 감사 + 다음 영상",
}
# 인트로·아웃트로 카드(짧은 제목 화면). 템플릿이 쓰고, 엔딩 화면의 '모양' 목록에는 나오지 않습니다.
CARD_KINDS = {
    "pop": "통통 튀는 제목 (예능)",
    "slide": "옆에서 밀려 들어오는 제목 (정보)",
    "wipe": "띠가 훑고 지나가는 제목 (뉴스)",
    "fade": "서서히 떠오르는 제목 (감성)",
    "line": "가는 선이 그어지는 제목 (미니멀)",
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
    pad = int(size * 0.3)       # 흔들 때 가장자리가 잘리지 않게 여백을 둡니다
    big = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0)); big.paste(im, (pad, pad))
    return big.rotate(angle, resample=Image.BICUBIC, center=(c + pad, size * .08 + pad))


def frame(p: dict, t: float, W: int, H: int, bg: np.ndarray, rng: np.random.Generator) -> Image.Image:
    im = Image.fromarray(bg.copy()); d = ImageDraw.Draw(im, "RGBA")
    acc = _rgb(p["accent"]); u = min(W, H * 0.6)    # u: 크기 기준 (세로 화면이면 폭, 가로 화면이면 높이의 일부)
    cx, cy = W / 2, H / 2
    kind = p["kind"]
    appear = _ease_back(t / 0.5)
    if kind in CARD_KINDS:
        _card(d, im, kind, p, t, W, H, acc, u, cx, cy)
        return im
    if kind in SOFT_KINDS:
        _soft(im, kind, p, t, W, H)
        return im
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
            im.paste(b, (int(cx - b.width / 2), int(cy + u * 0.28) - (b.width - int(u * 0.2)) // 2), b)
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
        im.paste(b, (int(cx - b.width / 2), int(cy - u * 0.45) - (b.width - int(u * 0.55 * min(1, _ease_back(t / 0.5) + 0.001)) - 2) // 2), b)
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


def _card(d, im, kind, p, t, W, H, acc, u, cx, cy):
    """인트로·아웃트로 카드 한 장. 제목(text)과 부제(sub)가 kind마다 다른 움직임으로 나타납니다."""
    dur = max(float(p["duration"]), 0.5)
    out = min(1.0, max(0.0, (dur - t) / 0.25))             # 끝나기 0.25초 전부터 사라짐
    a = lambda x: int(255 * min(1.0, max(0.0, x)) * out)
    white, soft = (255, 255, 255), (255, 255, 255)
    title, sub = p["text"], p["sub"]
    if kind == "pop":
        s = _ease_back(t / 0.45)
        bounce = 1 + 0.05 * math.sin(max(0.0, t - 0.45) * 12) * max(0.0, 1 - (t - 0.45) / 0.6)
        for i in range(6):                                          # 뒤에서 도는 동그라미 장식
            ang = i * 1.047 + t * 1.3
            r = u * (0.42 + 0.03 * math.sin(t * 3 + i))
            x, y = cx + math.cos(ang) * r * 1.1, cy + math.sin(ang) * r * 0.9
            rad = u * 0.045 * min(1.0, t / 0.4)
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=acc + (int(110 * out),))
        _text_center(d, (cx, cy - u * 0.04), title, u * 0.15, white + (a(s),), 4, max(0.01, s * bounce), W)
        bar = u * 0.5 * _ease_out((t - 0.2) / 0.4)
        d.rounded_rectangle((cx - bar / 2, cy + u * 0.09, cx + bar / 2, cy + u * 0.115), radius=u * 0.012, fill=acc + (a(1),))
        if sub:
            _text_center(d, (cx, cy + u * 0.22), sub, u * 0.065, soft + (a((t - 0.5) / 0.3),), 2, 1, W)
    elif kind == "slide":
        k = _ease_out(t / 0.5)
        x = cx - (1 - k) * W * 0.6
        d.rectangle((0, cy - u * 0.2, W * k * 0.04 + u * 0.03, cy + u * 0.2), fill=acc + (a(1),))
        _text_center(d, (x, cy - u * 0.04), title, u * 0.12, white + (a(k),), 0, 1, W)
        if sub:
            _text_center(d, (cx + (1 - k) * W * 0.5, cy + u * 0.12), sub, u * 0.058, acc + (a(k),), 0, 1, W)
        d.rectangle((cx - u * 0.35, cy + u * 0.2, cx - u * 0.35 + u * 0.7 * k, cy + u * 0.205), fill=white + (a(0.6),))
    elif kind == "wipe":
        k = _ease_out(t / 0.45)
        y0, y1 = cy - u * 0.13, cy + u * 0.13
        d.rectangle((0, y0, W * k, y1), fill=acc + (a(1),))
        shown = max(0.0, (t - 0.25) / 0.3)
        _text_center(d, (cx, cy), title, u * 0.11, white + (a(shown),), 0, 1, W)
        d.rectangle((0, y1 + u * 0.02, W * k * 0.6, y1 + u * 0.035), fill=white + (a(0.7),))
        if sub:
            _text_center(d, (cx, y1 + u * 0.12), sub, u * 0.055, soft + (a((t - 0.55) / 0.3),), 0, 1, W)
    elif kind == "fade":
        k = min(1.0, t / 0.8)
        d.ellipse((cx - u * 0.6, cy - u * 0.6, cx + u * 0.6, cy + u * 0.6), fill=acc + (int(28 * k * out),))
        _text_center(d, (cx, cy - u * 0.02 - (1 - _ease_out(k)) * u * 0.03), title, u * 0.1, white + (a(k),), 0, 1, W)
        if sub:
            _text_center(d, (cx, cy + u * 0.12), sub, u * 0.055, acc + (a((t - 0.4) / 0.6),), 0, 1, W)
    else:  # line
        k = _ease_out(t / 0.5)
        half = u * 0.36 * k
        d.rectangle((cx - half, cy - 2, cx + half, cy + 2), fill=white + (a(1),))
        _text_center(d, (cx, cy - u * 0.075), title, u * 0.085, white + (a((t - 0.2) / 0.4),), 0, 1, W)
        if sub:
            _text_center(d, (cx, cy + u * 0.075), sub, u * 0.045, acc + (a((t - 0.4) / 0.4),), 0, 1, W)


# ---------------------------------------------------------------- 부드러운 엔딩

def _ease_io(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _load_square(path: str, size: int) -> Image.Image:
    """사진을 가운데 정사각형으로 잘라 size로 줄입니다 (투명 배경은 그대로)."""
    im = Image.open(path).convert("RGBA")
    w, h = im.size; s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    return im.resize((size, size), Image.LANCZOS)


def _mask(size: int, radius: float) -> Image.Image:
    k = 4   # 4배로 그려 줄이면 가장자리가 매끈해요
    m = Image.new("L", (size * k, size * k), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size * k - 1, size * k - 1), radius=radius * k, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def _placeholder(size: int, accent: str) -> Image.Image:
    """이미지를 안 골랐을 때: 연한 동그라미 안의 하트."""
    im = Image.new("RGBA", (size, size), _rgb("#fffaf3") + (255,)); d = ImageDraw.Draw(im, "RGBA")
    d.ellipse((size * .08, size * .08, size * .92, size * .92), fill=_rgb(accent) + (40,))
    d.polygon(_heart(size / 2, size * .46, size * .022), fill=_rgb(accent) + (255,))
    return im


def _sprite(p: dict, size: int, radius_frac: float, border: int = 0) -> Image.Image:
    """모서리가 둥근 이미지 + 부드러운 그림자를 한 장으로 만들어 둡니다. radius_frac=0.5면 동그라미."""
    path = p.get("image_path")
    try:
        img = _load_square(path, size) if path else _placeholder(size, p["accent"])
    except Exception:
        img = _placeholder(size, p["accent"])
    r = size * radius_frac
    m = _mask(size, r)
    pad = int(size * 0.14)
    out = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("L", out.size, 0)
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + size * .035, pad + size, pad + size * 1.035), radius=r, fill=70)
    sh = sh.filter(ImageFilter.GaussianBlur(size * 0.04))
    out.paste(Image.new("RGBA", out.size, _rgb(p.get("text_color", "#5b3a29")) + (255,)), (0, 0), sh)
    if border:
        ring = Image.new("L", (size + 2 * border, size + 2 * border), 0)
        k = 4; rm = Image.new("L", ((size + 2 * border) * k,) * 2, 0)
        ImageDraw.Draw(rm).rounded_rectangle((0, 0, (size + 2 * border) * k - 1, (size + 2 * border) * k - 1), radius=(r + border) * k, fill=255)
        ring = rm.resize(ring.size, Image.LANCZOS)
        out.paste(Image.new("RGBA", ring.size, (255, 255, 255, 255)), (pad - border, pad - border), ring)
    out.paste(img, (pad, pad), ImageChops.multiply(m, img.getchannel("A")))
    return out


def _put(im: Image.Image, sp: Image.Image, cx: float, cy: float, scale: float, alpha: float) -> None:
    if alpha <= 0.01 or scale <= 0.01:
        return
    w, h = max(2, int(sp.width * scale)), max(2, int(sp.height * scale))
    s = sp.resize((w, h), Image.BILINEAR)
    if alpha < 1:
        s.putalpha(s.getchannel("A").point(lambda v: int(v * alpha)))
    im.paste(s, (int(cx - w / 2), int(cy - h / 2)), s)


def _stext(d, xy, text, size, fill, W, alpha=1.0, shadow=True):
    """가운데 맞춤 글자. 화면보다 넓으면 줄이고, 은은한 그림자를 깝니다."""
    if not text or alpha <= 0.01:
        return
    f = font(max(10, int(size)))
    w = d.textlength(text, font=f)
    if w > W * 0.86:
        f = font(max(10, int(size * W * 0.86 / w))); w = d.textlength(text, font=f)
    x, y = xy[0] - w / 2, xy[1] - f.size / 2
    col = _rgb(fill)
    if shadow:
        d.text((x + 2, y + max(2, f.size * 0.05)), text, font=f, fill=(255, 255, 255, int(150 * alpha)))
    d.text((x, y), text, font=f, fill=col + (int(255 * alpha),))


def _sparkle(d, x, y, r, fill):
    d.polygon([(x, y - r), (x + r * .28, y - r * .28), (x + r, y), (x + r * .28, y + r * .28), (x, y + r), (x - r * .28, y + r * .28), (x - r, y), (x - r * .28, y - r * .28)], fill=fill)


def _floaters(d, t, W, H, u, acc, out, n=14, hearts=True):
    """천천히 떠오르는 작은 하트·반짝이 (같은 시각이면 늘 같은 모양)."""
    for i in range(n):
        ph = (i * 0.6180339) % 1.0
        sp = 0.07 + 0.05 * ((i * 7) % 5) / 5
        k = (t * sp + ph) % 1.0
        x = W * (0.08 + 0.84 * ((i * 0.381966 + 0.13) % 1.0)) + math.sin(t * 0.9 + i) * u * 0.02
        y = H * (1.02 - 1.08 * k)
        al = int(120 * math.sin(math.pi * k) * out)
        r = u * (0.012 + 0.012 * ((i * 3) % 4) / 4)
        if hearts and i % 2 == 0:
            d.polygon(_heart(x, y, r / 8), fill=acc + (al,))
        else:
            _sparkle(d, x, y, r * 1.3, (255, 255, 255, min(255, al + 60)))


def _soft(im: Image.Image, kind: str, p: dict, t: float, W: int, H: int) -> None:
    ctx = p["_ctx"]
    d = ImageDraw.Draw(im, "RGBA")
    dur = max(float(p["duration"]), 1.5)
    out = min(1.0, max(0.0, (dur - t) / 0.5))                 # 끝나기 0.5초 전부터 서서히 사라짐
    acc, tc = _rgb(p["accent"]), p.get("text_color") or "#5b3a29"
    u = min(W * 0.95, H * 0.58)
    cx = W / 2
    k_img = _ease_io(t / 1.0)                                   # 이미지가 1초 동안 부드럽게 나타남
    k_t1, k_t2 = _ease_io((t - 0.6) / 0.8), _ease_io((t - 1.2) / 0.8)
    float_y = math.sin(t * 1.6) * u * 0.008                     # 둥실둥실
    breath = 1 + 0.012 * math.sin(t * 2.0)
    title, sub = p["text"], p["sub"]
    t1y = lambda y: y + (1 - k_t1) * u * 0.035                  # 글자는 살짝 아래에서 올라옴
    t2y = lambda y: y + (1 - k_t2) * u * 0.035
    if kind == "soft_logo":
        cy = H * 0.38
        _floaters(d, t, W, H, u, acc, out, 12)
        _put(im, ctx["sp"], cx, cy + float_y, (0.94 + 0.06 * k_img) * breath, k_img * out)
        _stext(d, (cx, t1y(cy + u * 0.52)), title, u * 0.075, tc, W, k_t1 * out)
        _stext(d, (cx, t2y(cy + u * 0.52 + u * 0.1)), sub, u * 0.045, "#" + "%02x%02x%02x" % acc, W, k_t2 * out, False)
    elif kind == "soft_card":
        cw, ch = u * 0.86, u * 1.22
        cy = H * 0.45
        k = _ease_io(t / 0.9)
        x0, y0 = cx - cw / 2, cy - ch / 2 + (1 - k) * u * 0.05
        sh = ctx["card_shadow"]
        sh2 = sh if out >= 1 else sh.copy()
        _put(im, sh2, cx, y0 + ch / 2 + u * 0.02, 1.0, k * out * 0.9)
        d.rounded_rectangle((x0, y0, x0 + cw, y0 + ch), radius=u * 0.07, fill=(255, 253, 249, int(235 * k * out)))
        _put(im, ctx["sp"], cx, y0 + cw * 0.5 + float_y * 0.5, (0.96 + 0.04 * k_img) * breath, k_img * out)
        _stext(d, (cx, t1y(y0 + cw + u * 0.1)), title, u * 0.062, tc, cw / 0.86 * 0.92, k_t1 * out, False)
        _stext(d, (cx, t2y(y0 + cw + u * 0.19)), sub, u * 0.04, "#" + "%02x%02x%02x" % acc, cw / 0.86 * 0.92, k_t2 * out, False)
        d.rounded_rectangle((cx - u * 0.07, y0 + ch - u * 0.1, cx + u * 0.07, y0 + ch - u * 0.094), radius=u * 0.004, fill=acc + (int(120 * k_t2 * out),))
        _floaters(d, t, W, H, u, acc, out, 8)
    elif kind == "soft_hearts":
        cy = H * 0.36
        for ring in range(3):                                   # 이미지 뒤로 퍼지는 부드러운 빛
            kk = ((t * 0.35 + ring / 3) % 1.0)
            r = u * (0.36 + 0.35 * kk)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=acc + (int(70 * (1 - kk) * out * k_img),), width=max(2, int(u * 0.006)))
        _put(im, ctx["sp"], cx, cy + float_y, (0.9 + 0.1 * k_img) * (1 + 0.025 * math.sin(t * 3.2) * k_img), k_img * out)
        _stext(d, (cx, t1y(cy + u * 0.46)), title, u * 0.07, tc, W, k_t1 * out)
        _stext(d, (cx, t2y(cy + u * 0.46 + u * 0.1)), sub, u * 0.045, "#" + "%02x%02x%02x" % acc, W, k_t2 * out, False)
        _floaters(d, t, W, H, u, acc, out, 26)
    else:  # soft_text
        cy = H * 0.42
        k_h = _ease_io((t - 0.1) / 0.9)
        _put(im, ctx["sp"], cx, cy - u * 0.38 + float_y, (0.94 + 0.06 * k_img) * breath, k_img * out)
        _stext(d, (cx, t1y(cy + u * 0.07)), title, u * 0.085, tc, W, k_t1 * out)
        wl = u * 0.3 * k_t1                                      # 글자 밑에 물결 선이 번져요
        pts = [(cx - wl + i * wl / 20, cy + u * 0.15 + math.sin(i * 0.9 + t * 2) * u * 0.008) for i in range(41)]
        if wl > 4:
            d.line(pts, fill=acc + (int(200 * k_t1 * out),), width=max(3, int(u * 0.007)), joint="curve")
        _stext(d, (cx, t2y(cy + u * 0.25)), sub, u * 0.045, tc, W, k_t2 * out * 0.85, False)
        d.polygon(_heart(cx, cy + u * 0.4 + math.sin(t * 3) * u * 0.006, u * 0.0075 * (1 + 0.1 * math.sin(t * 4))), fill=acc + (int(230 * k_t2 * out),))
        _floaters(d, t, W, H, u, acc, out, 10)


def _soft_bg(W: int, H: int, c1: str, c2: str, kind: str) -> np.ndarray:
    """위는 연한 크림, 아래는 복숭아빛으로 번지고 가운데 위쪽에 따뜻한 빛이 감돕니다."""
    a, b = np.array(_rgb(c1), float), np.array(_rgb(c2), float)
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    ty = (yy / H)[..., None]
    base = a * (1 - ty) + b * ty
    gy = 0.36 if kind != "soft_card" else 0.45
    g = np.exp(-(((xx - W / 2) / (W * 0.55)) ** 2 + ((yy - H * gy) / (H * 0.32)) ** 2))[..., None]
    base = base * (1 - 0.55 * g) + np.array([255, 252, 244], float) * 0.55 * g
    v = (((xx - W / 2) / (W * 0.8)) ** 2 + ((yy - H / 2) / (H * 0.8)) ** 2)[..., None]      # 가장자리를 살짝 어둡게
    base = base * (1 - 0.06 * np.clip(v, 0, 1))
    return np.clip(base, 0, 255).astype(np.uint8)


def _soft_ctx(p: dict, W: int, H: int) -> dict:
    u = min(W * 0.95, H * 0.58)
    kind = p["kind"]
    if kind == "soft_logo":
        sp = _sprite(p, int(u * 0.66), 0.5, border=int(u * 0.012))
    elif kind == "soft_card":
        sp = _sprite(p, int(u * 0.76), 0.09)
    elif kind == "soft_hearts":
        sp = _sprite(p, int(u * 0.5), 0.5, border=int(u * 0.01))
    else:
        sp = _sprite(p, int(u * 0.3), 0.5, border=int(u * 0.008))
    ctx = {"sp": sp}
    if kind == "soft_card":
        cw, ch = int(u * 0.86), int(u * 1.22)
        pad = int(u * 0.12)
        m = Image.new("L", (cw + 2 * pad, ch + 2 * pad), 0)
        ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + cw, pad + ch), radius=u * 0.07, fill=60)
        m = m.filter(ImageFilter.GaussianBlur(u * 0.035))
        sh = Image.new("RGBA", m.size, _rgb(p.get("text_color", "#5b3a29")) + (255,)); sh.putalpha(m)
        ctx["card_shadow"] = sh
    return ctx


def render(path, params: dict, W: int, H: int) -> None:
    p = {**DEFAULTS, **params}
    if p["kind"] not in KINDS and p["kind"] not in CARD_KINDS:
        raise ValueError("모르는 엔딩 종류예요.")
    W, H = W // 2 * 2, H // 2 * 2
    n = int(round(float(p["duration"]) * FPS))
    if p["kind"] in SOFT_KINDS:
        bg = _soft_bg(W, H, p["bg1"], p["bg2"], p["kind"])
        p["_ctx"] = _soft_ctx(p, W, H)
    else:
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
