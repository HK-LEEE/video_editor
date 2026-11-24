"""자동 이어 붙이기 시험용 합성 영상: 색 바탕 위를 움직이는 원. 앞쪽 검은 화면·글리치, 뒤쪽 멈춘 화면을 넣을 수 있습니다."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

W, H, FPS = 160, 284, 30


def frame(t: float, base, phase: float, speed: float = 1.0) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    g = (0.75 + 0.25 * yy / H)[..., None] * np.array(base, np.float32)
    cx = W / 2 + W * 0.3 * np.sin(phase + t * speed * 2.0)
    cy = H / 2 + H * 0.2 * np.cos(phase * 0.7 + t * speed * 1.3)
    d = (xx - cx) ** 2 + (yy - cy) ** 2
    g = np.where((d < (W * 0.18) ** 2)[..., None], np.array([240, 235, 225], np.float32), g)
    return np.clip(g, 0, 255).astype(np.uint8)


def make_clip(path: Path, base=(60, 90, 200), phase: float = 0.0, seconds: float = 5.0, speed: float = 1.0,
              black_head: float = 0.0, freeze_tail: float = 0.0, glitch_head: int = 0,
              freq: float = 440.0, volume: float = 0.3, audio: bool = True) -> None:
    """합성 영상을 만듭니다."""
    n = int(seconds * FPS)
    nb, nf = int(black_head * FPS), int(freeze_tail * FPS)
    frames = []
    for i in range(n):
        t = i / FPS
        if i < nb:
            frames.append(np.zeros((H, W, 3), np.uint8))
        elif i >= n - nf:
            frames.append(frames[n - nf - 1])
        elif i < nb + glitch_head:
            frames.append(np.full((H, W, 3), 255, np.uint8) if i % 2 == 0 else np.zeros((H, W, 3), np.uint8) + 128)
        else:
            frames.append(frame(t - nb / FPS, base, phase, speed))
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f={freq}:r=48000:d={seconds}", "-af", f"volume={volume}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15", "-shortest", str(path)]
    subprocess.run(cmd, input=b"".join(f.tobytes() for f in frames), check=True)
