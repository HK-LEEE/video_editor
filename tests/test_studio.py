import subprocess

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voxshift.engine import SAMPLE_RATE as SR
from voxshift.studio import Workspace, make_router, media_duration, has_audio


@pytest.fixture()
def client(tmp_path):
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def make_video(path, seconds=4, audio=True):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=15:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_take(path, seconds=1.5):
    t = np.arange(int(SR * seconds)) / SR
    phase = 2 * np.pi * 140 * t
    x = sum(np.sin(k * phase) / k for k in range(1, 20))
    sf.write(path, (0.3 * x / np.abs(x).max()).astype(np.float32), SR)
    return path


def test_voices_lists_original_and_presets(client):
    c, _ = client
    names = [v["name"] for v in c.get("/api/voices").json()]
    assert names[0] == "original" and "robot" in names and "demon" in names


def test_full_dub_flow(client):
    c, tmp = client
    video = make_video(tmp / "v.mp4")
    v = c.post("/api/videos", files={"file": ("v.mp4", video.read_bytes(), "video/mp4")}).json()
    assert v["duration"] == pytest.approx(4, abs=0.2) and v["has_audio"]
    assert c.get(f"/api/videos/{v['id']}/info").json()["duration"] == pytest.approx(4, abs=0.2)

    take = make_take(tmp / "t.wav")
    t = c.post("/api/takes", files={"file": ("t.wav", take.read_bytes(), "audio/wav")}).json()
    assert t["duration"] == pytest.approx(1.5, abs=0.05)

    r = c.get(f"/api/takes/{t['id']}/audio", params={"voice": "robot", "intensity": 1.5})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"

    clips = [  # 같은 테이크를 두 구간으로 잘라 다른 목소리로
        {"take_id": t["id"], "src_in": 0, "src_out": 0.7, "start": 0.5, "voice": "deep"},
        {"take_id": t["id"], "src_in": 0.7, "src_out": 1.5, "start": 2.0, "voice": "chipmunk", "intensity": 2},
    ]
    out = c.post("/api/export", json={"video_id": v["id"], "clips": clips, "original_volume": 0}).json()
    mp4 = tmp / "out.mp4"
    mp4.write_bytes(c.get(out["url"]).content)
    assert has_audio(mp4)
    assert media_duration(mp4) == pytest.approx(4, abs=0.3)

    # 목소리는 0.5~1.2초, 2.0~2.8초에만 있고 나머지는 조용해야 함
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, np.float32)
    rms = lambda s, e: float(np.sqrt(np.mean(a[int(s * SR):int(e * SR)] ** 2)))
    assert rms(0.6, 1.1) > 0.02 and rms(2.1, 2.7) > 0.02
    assert rms(1.4, 1.9) < 0.005 and rms(3.0, 3.9) < 0.005


def test_export_mixes_original_audio(client):
    c, tmp = client
    video = make_video(tmp / "v.mp4", seconds=2)
    v = c.post("/api/videos", files={"file": ("v.mp4", video.read_bytes(), "video/mp4")}).json()
    out = c.post("/api/export", json={"video_id": v["id"], "clips": [], "original_volume": 0.5}).json()
    mp4 = tmp / "o.mp4"
    mp4.write_bytes(c.get(out["url"]).content)
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(mp4), "-ac", "1", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    assert np.sqrt(np.mean(np.frombuffer(pcm, np.float32) ** 2)) > 0.03  # sine 기본 진폭 1/8 × 0.5


def test_video_without_audio_and_bad_ids(client):
    c, tmp = client
    video = make_video(tmp / "silent.mp4", seconds=1, audio=False)
    v = c.post("/api/videos", files={"file": ("silent.mp4", video.read_bytes(), "video/mp4")}).json()
    assert v["has_audio"] is False
    assert c.get("/api/videos/../../etc/passwd").status_code in (400, 404)
    assert c.get("/api/videos/zzz/info").status_code == 400
    assert c.get("/api/exports/..%2Fsecret").status_code == 404
