import subprocess

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_editor.editor import Workspace, make_router, probe
from video_editor.engine import SAMPLE_RATE as SR


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def make_video(path, seconds=3, color="red", tone=1000, audio=True, size="320x240"):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_voice(path, seconds=1.0):
    t = np.arange(int(SR * seconds)) / SR
    x = sum(np.sin(k * 2 * np.pi * 140 * t) / k for k in range(1, 20))
    sf.write(path, (0.3 * x / np.abs(x).max()).astype(np.float32), SR)
    return path


def upload(c, path, **form):
    r = c.post("/api/media", files={"file": (path.name, path.read_bytes())}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def tracks(**over):
    base = [
        {"id": "v1", "kind": "video"}, {"id": "v2", "kind": "video"},
        {"id": "a1", "kind": "audio"}, {"id": "a2", "kind": "audio", "voice": True},
    ]
    for t in base:
        t.update(over.get(t["id"], {}))
    return base


def decode(path, video_frame_at=None):
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.float32)


def frame_rgb(path, t):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)


def rms(a, s, e):
    return float(np.sqrt(np.mean(a[int(s * SR):int(e * SR)] ** 2)))


def test_media_upload_kinds(api):
    c, tmp = api
    v = upload(c, make_video(tmp / "a.mp4"))
    assert v["kind"] == "video" and v["has_audio"] and v["width"] == 320
    a = upload(c, make_voice(tmp / "v.wav"))
    assert a["kind"] == "audio" and not a["has_video"]
    rec = upload(c, make_voice(tmp / "r.wav"), recorded="true", name="녹음 1")
    assert rec["recorded"] and rec["name"] == "녹음 1" and rec["file"].endswith(".wav")
    assert {m["id"] for m in c.get("/api/media").json()} == {v["id"], a["id"], rec["id"]}
    pk = c.get(f"/api/media/{a['id']}/peaks", params={"voice": "robot"}).json()
    assert pk["rate"] == 100 and len(pk["peaks"]) == pytest.approx(100, abs=2)
    bad = tmp / "x.txt"; bad.write_text("hello")
    assert c.post("/api/media", files={"file": ("x.txt", b"hello")}).status_code == 400
    assert c.get("/api/media/zz/info").status_code == 400


def test_export_stacks_video_tracks_and_fills_gaps(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 3, "red", 500))
    blue = upload(c, make_video(tmp / "b.mp4", 2, "blue", 2000))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v2", "media": blue["id"], "src_in": 0, "src_out": 1, "start": 1},   # 1~2초 위에 파랑
        {"id": "c3", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 1, "start": 4},   # 3~4초는 빈 곳
        {"id": "c4", "track": "a1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
    ]
    out = c.post("/api/export", json={"tracks": tracks(), "clips": clips}).json()
    mp4 = tmp / "o.mp4"; mp4.write_bytes(c.get(out["url"]).content)
    info = probe(mp4)
    assert info["duration"] == pytest.approx(5, abs=0.15) and info["width"] == 320
    r, g, b = frame_rgb(mp4, 0.5); assert r > 150 and b < 80          # 빨강
    r, g, b = frame_rgb(mp4, 1.5); assert b > 150 and r < 80          # 위 트랙 파랑
    assert frame_rgb(mp4, 3.5).max() < 30                             # 빈 곳 검정
    r, g, b = frame_rgb(mp4, 4.5); assert r > 150                     # 다시 빨강
    a = decode(mp4)
    assert rms(a, 0.2, 2.8) > 0.03 and rms(a, 3.2, 4.8) < 0.003      # 소리는 a1 클립 구간에만

    # 위 트랙을 숨기면 아래 빨강이 보임
    hidden = c.post("/api/export", json={"tracks": tracks(v2={"mute": True}), "clips": clips}).json()
    mp4b = tmp / "h.mp4"; mp4b.write_bytes(c.get(hidden["url"]).content)
    r, g, b = frame_rgb(mp4b, 1.5); assert r > 150 and b < 80


def test_mix_mute_solo_volume_and_duck(api):
    c, tmp = api
    music = upload(c, make_video(tmp / "m.mp4", 4, tone=440))
    voice = upload(c, make_voice(tmp / "v.wav", 1.0))
    clips = [
        {"id": "m", "track": "a1", "media": music["id"], "src_in": 0, "src_out": 4, "start": 0},
        {"id": "v", "track": "a2", "media": voice["id"], "src_in": 0, "src_out": 1, "start": 1},
    ]
    def mix(**over):
        r = c.post("/api/mix", json={"tracks": tracks(**over), "clips": clips})
        p = tmp / "mix.wav"; p.write_bytes(r.content)
        return sf.read(p, dtype="float32")[0]

    base = mix()
    assert rms(base, 0.2, 0.8) > 0.03 and rms(base, 1.2, 1.8) > rms(base, 0.2, 0.8)
    assert rms(mix(a1={"mute": True}), 0.2, 0.8) < 1e-4                     # 음소거
    assert rms(mix(a2={"solo": True}), 0.2, 0.8) < 1e-4                     # 솔로면 다른 트랙 안 들림
    assert rms(mix(a1={"volume": 0.5}), 0.2, 0.8) == pytest.approx(rms(base, 0.2, 0.8) * 0.5, rel=0.05)
    ducked = mix(a1={"duck": 1.0}, a2={"mute": False})
    music_only = mix(a2={"mute": True})
    ducked_music_only = c.post("/api/mix", json={"tracks": tracks(a1={"duck": 1.0}), "clips": clips})
    # 덕킹: 보이스가 나오는 동안 a1이 줄어듦 → 전체에서 a1 몫이 줄어든 만큼 확인
    assert rms(ducked, 3.0, 3.8) == pytest.approx(rms(music_only, 3.0, 3.8), rel=0.05)   # 보이스 끝난 뒤엔 그대로
    assert rms(ducked, 1.2, 1.8) < rms(base, 1.2, 1.8)


def test_voice_change_and_track_fx(api):
    c, tmp = api
    voice = upload(c, make_voice(tmp / "v.wav", 1.0))
    clip = {"id": "v", "track": "a2", "media": voice["id"], "src_in": 0, "src_out": 1, "start": 0.5, "gain": 0.2}
    def mix(clip_over=None, **over):
        pad = {**clip, "id": "pad", "start": 2.0, "gain": 0.0}   # 타임라인 길이를 2.6초로
        r = c.post("/api/mix", json={"tracks": tracks(**over), "clips": [{**clip, **(clip_over or {})}, pad]})
        p = tmp / "mix.wav"; p.write_bytes(r.content)
        return sf.read(p, dtype="float32")[0]
    plain = mix()
    leveled = mix(a2={"voice": True, "level": True})
    assert -24 < 20 * np.log10(rms(leveled, 0.55, 1.45)) < -16
    wet = mix(a2={"voice": True, "level": True, "room": 1.0})
    assert rms(wet, 1.52, 1.8) > rms(leveled, 1.52, 1.8) * 3
    robot = mix({"voice": "robot"})
    assert not np.allclose(robot[: plain.size], plain[: robot.size], atol=1e-3)


def test_same_track_overlap_later_clip_wins(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 3, "red"))
    blue = upload(c, make_video(tmp / "b.mp4", 1, "blue"))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v1", "media": blue["id"], "src_in": 0, "src_out": 1, "start": 1},
    ]
    out = c.post("/api/export", json={"tracks": tracks(), "clips": clips}).json()
    mp4 = tmp / "o.mp4"; mp4.write_bytes(c.get(out["url"]).content)
    r, g, b = frame_rgb(mp4, 0.5); assert r > 150 and b < 80
    r, g, b = frame_rgb(mp4, 1.5); assert b > 150 and r < 80
    r, g, b = frame_rgb(mp4, 2.5); assert r > 150 and b < 80
