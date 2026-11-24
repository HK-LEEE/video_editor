"""썸네일 만들기: 영상의 한 장면을 9:16으로 맞추고 큰 글자를 얹은 PNG."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = ["/System/Library/Fonts/AppleSDGothicNeo.ttc", "/Library/Fonts/AppleGothic.ttf", "C:/Windows/Fonts/malgunbd.ttf",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
         "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
W, H = 1080, 1920


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for f in FONTS:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except OSError:
                continue
    return ImageFont.load_default()


def grab_frame(src: Path, t: float, out: Path) -> None:
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"장면을 뽑지 못했어요: {r.stderr[-300:]}")


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text.replace("\n", " "):
        if draw.textlength(cur + ch, font=f) > max_w and cur:
            lines.append(cur.strip()); cur = ch
        else:
            cur += ch
    return lines + ([cur.strip()] if cur.strip() else [])


def render(frame: Path, text: str, out: Path, color: str = "#ffe135", position: float = 0.2) -> Path:
    """frame 이미지를 1080x1920에 꽉 채우고(가운데 자르기) text를 position(위에서 비율)에 크게 넣습니다."""
    im = Image.open(frame).convert("RGB")
    k = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * k) + 1, int(im.height * k) + 1))
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(int(H * 0.45)):   # 위쪽을 살짝 어둡게 해서 글자가 잘 보이게
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(150 * (1 - y / (H * 0.45)))))
    im = Image.alpha_composite(im.convert("RGBA"), shade)
    d = ImageDraw.Draw(im)
    size = 150
    f = font(size)
    lines = wrap(d, text, f, int(W * 0.88))
    while len(lines) > 3 and size > 70:
        size -= 10; f = font(size); lines = wrap(d, text, f, int(W * 0.88))
    y = int(H * position)
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((W - w) / 2, y), ln, font=f, fill=color, stroke_width=max(6, size // 12), stroke_fill="#000000")
        y += int(size * 1.2)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.convert("RGB").save(out, "PNG")
    return out
