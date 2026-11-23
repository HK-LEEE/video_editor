import time

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_editor import decode, make_video, make_voice, rms, tracks, upload
from video_editor import ai_voice
from video_editor.editor import Workspace, make_router
from video_editor.engine import SAMPLE_RATE as SR


@pytest.fixture()
def api(tmp_path, monkeypatch):
    def fake_seedvc(source, target, out, steps=30, pitch_shift=0):
        x, sr = sf.read(source, dtype="float32")   # 변환 결과: 소리 크기를 절반으로 + 길이를 조금 다르게
        sf.write(out, np.concatenate([x * 0.5, np.zeros(37, np.float32)]), sr)
        return out

    monkeypatch.setattr(ai_voice, "is_installed", lambda: True)
    monkeypatch.setattr(ai_voice, "run_seedvc", fake_seedvc)
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def wait(c, job):
    for _ in range(100):
        s = c.get(f"/api/ai/jobs/{job}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("timeout")


def project(src, clip_voice):
    return {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "a2", "media": src["id"], "src_in": 0, "src_out": 1.0, "start": 0, "voice": clip_voice}]}


def test_ai_convert_flow_and_mix_cache(api):
    c, tmp = api
    src = upload(c, make_voice(tmp / "me.wav"), recorded="true")
    ref = upload(c, make_voice(tmp / "ref.wav"))
    voice = f"ai:{ref['id']}"
    p = project(src, voice)

    assert c.get("/api/ai/status").json()["installed"]
    assert not c.get("/api/ai/ready", params={"media": src["id"], "ref": ref["id"]}).json()["ready"]

    before = decode(c.post("/api/mix", json=p) and _save(c, p, tmp / "m0.wav"))  # 변환 전 = 원본 소리
    j = c.post("/api/ai/convert", json={"media": src["id"], "ref": ref["id"]}).json()
    assert wait(c, j["job"])["status"] == "done"
    assert c.get("/api/ai/ready", params={"media": src["id"], "ref": ref["id"]}).json()["ready"]

    after = decode(_save(c, p, tmp / "m1.wav"))   # 같은 프로젝트라도 변환 뒤엔 새 소리
    assert rms(after, 0.1, 0.9) == pytest.approx(rms(before, 0.1, 0.9) * 0.5, rel=0.1)
    assert abs(len(after) - len(before)) < 5      # 길이 유지
    assert c.post("/api/ai/convert", json={"media": src["id"], "ref": ref["id"]}).json()["status"] == "done"


def _save(c, p, path):
    path.write_bytes(c.post("/api/mix", json=p).content)
    return path


def test_export_blocked_until_converted(api):
    c, tmp = api
    v = upload(c, make_video(tmp / "a.mp4"))
    ref = upload(c, make_voice(tmp / "ref.wav"))
    p = project(v, f"ai:{ref['id']}")
    r = c.post("/api/export", json=p)
    assert r.status_code == 409 and "AI 변환" in r.json()["detail"]


def test_not_installed_and_errors(api, monkeypatch):
    c, tmp = api
    src = upload(c, make_voice(tmp / "me.wav"))
    ref = upload(c, make_voice(tmp / "ref.wav"))
    monkeypatch.setattr(ai_voice, "is_installed", lambda: False)
    r = c.post("/api/ai/convert", json={"media": src["id"], "ref": ref["id"]})
    assert r.status_code == 503 and "setup_ai_voice" in r.json()["detail"]

    monkeypatch.setattr(ai_voice, "is_installed", lambda: True)
    monkeypatch.setattr(ai_voice, "run_seedvc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    j = c.post("/api/ai/convert", json={"media": src["id"], "ref": ref["id"]}).json()
    s = wait(c, j["job"])
    assert s["status"] == "error" and "boom" in s["error"]
    assert c.post("/api/ai/convert", json={"media": "zz", "ref": ref["id"]}).status_code == 400
