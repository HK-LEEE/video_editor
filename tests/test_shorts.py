import subprocess

import numpy as np
import pytest

from video_editor import shorts
from video_editor.subtitles import Cue, SubtitleStyle
from .test_editor import api, bright_pixels, decode, export_to, frame_px, rms, tracks, upload  # noqa: F401
from video_editor.engine import SAMPLE_RATE as SR


def split_video(path, seconds=6, size="640x360"):
    """왼쪽 절반 빨강, 오른쪽 절반 파랑, 사인파 소리."""
    w, h = (int(v) for v in size.split("x"))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=red:s={w // 2}x{h}:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"color=c=blue:s={w // 2}x{h}:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=600:duration={seconds}",
                    "-filter_complex", "[0:v][1:v]hstack[v]", "-map", "[v]", "-map", "2:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def body(c, tmp, spec, seconds=6, **extra):
    m = upload(c, split_video(tmp / "s.mp4", seconds))
    clips = [{"id": "c", "track": "v1", "media": m["id"], "src_in": 0, "src_out": seconds, "start": 0},
             {"id": "a", "track": "a1", "media": m["id"], "src_in": 0, "src_out": seconds, "start": 0}]
    return {"project": {"tracks": tracks(), "clips": clips, **extra}, "spec": spec}


def export_short(c, tmp, b, name="s_out.mp4"):
    r = c.post("/api/export/short", json=b)
    assert r.status_code == 200, r.text
    p = tmp / name
    p.write_bytes(c.get(r.json()["url"]).content)
    return p


def info(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.split()
    w, h = out[0].split(",")[:2]
    return int(w), int(h), float(out[1] if len(out) > 1 else out[0].split(",")[-1])


def test_formats_endpoint(api):
    c, _ = api
    j = c.get("/api/short/formats").json()
    assert j["out"] == [1080, 1920] and set(j["formats"]) == {"shorts", "reels"}
    assert j["formats"]["reels"]["limit"] < j["formats"]["shorts"]["limit"]


def test_export_is_1080x1920_and_trimmed_with_audio(api):
    c, tmp = api
    mp4 = export_short(c, tmp, body(c, tmp, {"format": "shorts", "start": 1.0, "end": 3.5}))
    w, h, dur = info(mp4)
    assert (w, h) == (1080, 1920) and dur == pytest.approx(2.5, abs=0.15)
    assert rms(decode(mp4), 0.2, 2.2) > 0.03


@pytest.mark.parametrize("cx,red", [(0.1, True), (0.9, False)])
def test_reframe_pans_over_source(api, cx, red):
    c, tmp = api
    mp4 = export_short(c, tmp, body(c, tmp, {"format": "reels", "zoom": 2.0, "cx": cx, "end": 2}))
    r, g, b = frame_px(mp4, 1.0, 0.5, 0.5, size=(54, 96))
    assert (r > 150 and b < 80) if red else (b > 150 and r < 80)


def test_fit_mode_keeps_whole_frame_with_blur_bars(api):
    c, tmp = api
    mp4 = export_short(c, tmp, body(c, tmp, {"format": "shorts", "fit": True, "end": 2}))
    assert info(mp4)[:2] == (1080, 1920)
    # 가운데 줄 왼쪽은 빨강, 오른쪽은 파랑(전체 화면이 들어감), 맨 위는 어두운 배경
    assert frame_px(mp4, 1.0, 0.2, 0.5, size=(54, 96))[0] > 120
    assert frame_px(mp4, 1.0, 0.8, 0.5, size=(54, 96))[2] > 120


def test_limits_are_enforced_per_format(api):
    c, tmp = api
    b = body(c, tmp, {"format": "reels", "start": 0, "end": 6})
    assert c.post("/api/export/short", json=b).status_code == 200
    r = c.post("/api/export/short", json=body(c, tmp, {"format": "reels", "start": 0, "end": 120}, seconds=6))
    assert r.status_code == 200      # 끝이 프로젝트 길이(6초)를 넘으면 6초로 잘림 — 제한 이내
    assert c.post("/api/export/short", json=body(c, tmp, {"format": "reels", "start": 5.95, "end": 6})).status_code == 400


def test_over_limit_rejected(monkeypatch, api):
    c, tmp = api
    monkeypatch.setitem(shorts.FORMATS["reels"], "limit", 2.0)
    r = c.post("/api/export/short", json=body(c, tmp, {"format": "reels", "start": 0, "end": 5}))
    assert r.status_code == 400 and "최대" in r.json()["detail"]
    assert c.post("/api/export/short", json=body(c, tmp, {"format": "shorts", "start": 0, "end": 5})).status_code == 200


def test_subtitles_move_into_safe_area_and_are_shifted(api):
    c, tmp = api
    cue = {"id": "k", "start": 2.0, "end": 4.0, "text": "안전 영역"}
    style = {"size": 0.06, "y": 0.95, "x": 0.5}          # 맨 아래 → 보통은 앱 UI 밑으로 들어감
    spec = {"format": "shorts", "start": 1.0, "end": 5.0, "safe_subs": True}
    mp4 = export_short(c, tmp, body(c, tmp, spec, subtitles=[cue], subtitle_style=style))

    def white_rows(t):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(mp4), "-frames:v", "1", "-vf", "scale=108:192",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        img = np.frombuffer(raw, np.uint8).reshape(192, 108, 3)
        return np.where((img.min(axis=2) > 200).sum(axis=1) > 0)[0]
    assert len(white_rows(0.3)) == 0                      # 자막은 원본 2~4초 = 잘라낸 영상 1~3초에 나옴
    rows = white_rows(1.5)
    assert len(rows) > 0 and rows.max() / 192 < shorts.FORMATS["shorts"]["safe"]["bottom"] + 0.02
    free = export_short(c, tmp, body(c, tmp, {**spec, "safe_subs": False}, subtitles=[cue], subtitle_style=style), "f.mp4")
    mp4 = free
    assert white_rows(1.5).max() / 192 > shorts.FORMATS["shorts"]["safe"]["bottom"]


def test_safe_style_clamps_and_window_math():
    s = shorts.safe_style(SubtitleStyle(x=0.95, y=0.9, size=0.06, max_width=0.9), "reels")
    sf = shorts.FORMATS["reels"]["safe"]
    assert s.x + s.max_width / 2 <= sf["right"] + 1e-9 and s.y < sf["bottom"]
    x, y, w, h = shorts.window(1920, 1080, shorts.ShortSpec(zoom=1, cx=0.5))
    assert (w, h) == (606, 1080) and abs(x - (1920 - 607) / 2) <= 2
    x, y, w, h = shorts.window(1920, 1080, shorts.ShortSpec(zoom=2, cx=1.0, cy=0.0))
    assert x + w <= 1920 and y == 0                       # 화면 밖으로 안 나감
    assert shorts.shift_cues([Cue(id="a", start=1, end=3, text="x")], 2, 10)[0].start == 0
