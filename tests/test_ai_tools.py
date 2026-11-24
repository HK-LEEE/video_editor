import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_editor import make_video, make_voice, rms, upload
from video_editor import ai_tools
from video_editor.editor import Workspace, make_router
from video_editor.engine import SAMPLE_RATE as SR

SEGMENTS = [{"start": 0.5, "end": 4.0, "text": "음 안녕하세요 어 반갑습니다.", "words": [
    {"s": 0.5, "e": 0.9, "w": "음"}, {"s": 1.0, "e": 1.6, "w": "안녕하세요"}, {"s": 1.7, "e": 2.0, "w": "어..."},
    {"s": 2.1, "e": 3.0, "w": "반갑습니다."}]}]


def speech_with_gaps(path, parts=((0, 1.0), (2.5, 3.5), (5.0, 6.0)), total=6.5):
    """말(0~1, 2.5~3.5, 5~6초) 사이가 조용한 신호"""
    t = np.arange(int(SR * total)) / SR
    x = np.zeros_like(t, dtype=np.float32)
    for a, b in parts:
        m = (t >= a) & (t < b)
        x[m] = 0.3 * np.sin(2 * np.pi * 180 * t[m])
    sf.write(path, x, SR)
    return path


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_tools, "is_installed", lambda: True)
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def wait(c, job):
    for _ in range(200):
        s = c.get(f"/api/ai/jobs/{job}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("timeout")


def run(c, path, body):
    r = c.post(path, json=body)
    assert r.status_code == 200, r.text
    return wait(c, r.json()["job"])


# ------------------------------------------------------------ 순수 함수

def test_silence_ranges_finds_gaps_and_keeps_pad(tmp_path):
    x, _ = sf.read(speech_with_gaps(tmp_path / "s.wav"), dtype="float32")
    cuts = ai_tools.silence_ranges(x, SR, min_silence=0.5, pad=0.1)
    assert len(cuts) == 3                                    # 1~2.5, 3.5~5, 6~6.5(끝)
    a = cuts[0]
    assert a["start"] == pytest.approx(1.1, abs=0.05) and a["end"] == pytest.approx(2.4, abs=0.05)
    assert ai_tools.silence_ranges(x, SR, min_silence=2.0) == []    # 더 긴 무음만 → 없음


def test_find_fillers_and_merge():
    f = ai_tools.find_fillers(SEGMENTS, "ko")
    assert [w["text"] for w in f] == ["음", "어..."]
    f2 = ai_tools.find_fillers(SEGMENTS, "ko", extra=["반갑습니다"])
    assert len(f2) == 3
    merged = ai_tools.merge_cuts([{"start": 1, "end": 2, "kind": "silence"}, {"start": 1.98, "end": 2.5, "kind": "filler"},
                                  {"start": 4, "end": 5, "kind": "silence"}])
    assert [(m["start"], m["end"]) for m in merged] == [(1, 2.5), (4, 5)] and merged[0]["kind"] == "silence+filler"


def test_segments_to_cues_splits_long_lines():
    words = [{"s": i * 0.5, "e": i * 0.5 + 0.4, "w": "가나다라마"} for i in range(12)]
    cues = ai_tools.segments_to_cues([{"start": 0, "end": 6, "text": "x", "words": words}], "ko", max_chars=14)
    assert len(cues) > 2 and all(len(c["text"]) <= 20 for c in cues)
    assert cues[0]["start"] == 0 and cues[-1]["end"] == pytest.approx(5.9) and cues[0]["lang"] == "ko"
    plain = ai_tools.segments_to_cues([{"start": 1, "end": 2, "text": " 안녕 ", "words": []}], "ko")
    assert plain == [{"start": 1, "end": 2, "text": "안녕", "lang": "ko", "words": []}]


def test_scene_changes(tmp_path):
    import subprocess
    p = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=30:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=30:d=2", "-f", "lavfi", "-i", "color=c=lime:s=160x120:r=30:d=2",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(p)], check=True)
    times = ai_tools.scene_changes(p)
    assert len(times) == 2 and times[0] == pytest.approx(2.0, abs=0.1) and times[1] == pytest.approx(4.0, abs=0.1)


def test_denoise_both_engines(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    t = np.arange(SR * 2) / SR
    x = (0.3 * np.sin(2 * np.pi * 200 * t) * (t > 1) + 0.05 * rng.standard_normal(t.size)).astype(np.float32)
    src = tmp_path / "n.wav"
    sf.write(src, x, SR)
    model = Path(__file__).parent / "fixtures" / "bd.rnnn"
    for name, path in (("fft", tmp_path / "none.rnnn"), ("rnnoise", model)):
        if name == "rnnoise" and not path.exists():
            continue
        monkeypatch.setattr(ai_tools, "rnnoise_model", lambda p=path: p)
        out = tmp_path / f"{name}.wav"
        assert ai_tools.denoise(src, out) == name
        y, _ = sf.read(out, dtype="float32")
        assert abs(y.size - x.size) < 100
        assert rms(y, 0.1, 0.9) < rms(x, 0.1, 0.9) * 0.6      # 소음만 있는 앞 1초는 눈에 띄게 줄어듦


# ------------------------------------------------------------ 주소(API)

def fake_synth(monkeypatch):
    def synth(text, lang, out, rate=1.0, voice="", sr=SR):
        n = int(SR * 0.3 * max(1, len(text) / 3) / rate)
        t = np.arange(n) / SR
        sf.write(out, (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32), SR)
        return out
    monkeypatch.setattr(ai_tools, "tts_engine", lambda: "fake")
    monkeypatch.setattr(ai_tools, "synthesize", synth)


def test_transcribe_job_cache_and_errors(api, monkeypatch):
    c, tmp = api
    m = upload(c, make_voice(tmp / "v.wav"))
    calls = []

    def fake(wav, lang, verbatim):
        calls.append((lang, verbatim))
        assert Path(wav).exists()
        return {"lang": lang or "ko", "segments": SEGMENTS}

    monkeypatch.setattr(ai_tools, "transcribe", fake)
    r = run(c, "/api/ai/transcribe", {"media": m["id"], "lang": "ko"})
    assert r["status"] == "done" and r["result"]["lang"] == "ko"
    assert [x["text"] for x in r["result"]["cues"]][0].startswith("음 안녕하세요")
    run(c, "/api/ai/transcribe", {"media": m["id"], "lang": "ko"})
    assert calls == [("ko", False)]                                   # 두 번째는 캐시
    assert c.post("/api/ai/transcribe", json={"media": m["id"], "lang": "xx"}).status_code == 400
    monkeypatch.setattr(ai_tools, "is_installed", lambda: False)
    r = c.post("/api/ai/transcribe", json={"media": m["id"], "lang": "ko"})
    assert r.status_code == 503 and "setup_ai_tools" in r.json()["detail"]


def test_cuts_silence_only_and_with_fillers(api, monkeypatch):
    c, tmp = api
    m = upload(c, speech_with_gaps(tmp / "g.wav"))
    r = run(c, "/api/ai/cuts", {"media": m["id"], "silence": True, "min_silence": 0.5})
    res = r["result"]
    assert len(res["cuts"]) == 3 and res["removed"] > 3.0 and res["duration"] == pytest.approx(6.5, abs=0.1)
    monkeypatch.setattr(ai_tools, "transcribe", lambda wav, lang, verbatim: {"lang": "ko", "segments": SEGMENTS})
    r = run(c, "/api/ai/cuts", {"media": m["id"], "silence": False, "fillers": True, "lang": "ko"})
    assert [x["kind"] for x in r["result"]["cuts"]] == ["filler", "filler"]
    assert c.post("/api/ai/cuts", json={"media": m["id"], "silence": False, "fillers": False}).status_code == 400


def test_scenes_and_denoise_endpoints(api, tmp_path):
    c, tmp = api
    import subprocess
    p = tmp / "s.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=30:d=2",
                    "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=30:d=2", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                    "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(p)], check=True)
    v = upload(c, p)
    r = run(c, "/api/ai/scenes", {"media": v["id"]})
    assert len(r["result"]["times"]) == 1
    audio_only = upload(c, make_voice(tmp / "a.wav"))
    assert c.post("/api/ai/scenes", json={"media": audio_only["id"]}).status_code == 400

    r = run(c, "/api/ai/denoise", {"media": v["id"]})
    new = r["result"]["media"]
    assert r["status"] == "done" and new["kind"] == "audio" and "노이즈 제거" in new["name"]
    assert new["duration"] == pytest.approx(v["duration"], abs=0.1) and r["result"]["engine"] in ("fft", "rnnoise")
    assert any(m["id"] == new["id"] for m in c.get("/api/media").json())


def test_tts_and_translate_and_dub(api, monkeypatch):
    c, tmp = api
    fake_synth(monkeypatch)
    r = run(c, "/api/ai/tts", {"text": "안녕하세요 여러분", "lang": "ko"})
    assert r["status"] == "done" and r["result"]["media"]["duration"] > 0.5

    monkeypatch.setattr(ai_tools, "translate", lambda texts, src, dst: [f"[{dst}]{t}" for t in texts])
    r = run(c, "/api/ai/translate", {"texts": ["안녕", "", "고마워"], "src": "ko", "dst": "en"})
    assert r["result"]["texts"] == ["[en]안녕", "[en]", "[en]고마워"]
    assert c.post("/api/ai/translate", json={"texts": ["a"], "src": "ko", "dst": "ko"}).status_code == 400

    cues = [{"start": 0.0, "end": 1.0, "text": "hello there"}, {"start": 2.0, "end": 2.4, "text": "a very long sentence here ok"}]
    r = run(c, "/api/ai/dub", {"cues": cues, "lang": "en"})
    assert r["status"] == "done", r
    m = r["result"]["media"]
    assert r["status"] == "done" and m["duration"] == pytest.approx(2.9, abs=0.1)
    x, _ = sf.read(tmp / "ws" / "projects" / "기본-프로젝트" / "media" / m["file"], dtype="float32")
    assert rms(x, 0.1, 0.9) > 0.05 and rms(x, 1.2, 1.8) < 0.001      # 자막 사이는 조용
    assert rms(x, 2.0, 2.4) > 0.05                                    # 둘째 자막 구간에 소리
    assert rms(x, 2.5, 2.85) > 0.05                                   # 너무 긴 문장은 1.6배까지만 빨라지고 끝에서 잘림
