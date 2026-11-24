import subprocess
import time

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.aj_clips import make_clip
from video_editor import autojoin as aj
from video_editor.editor import Workspace, make_router


@pytest.fixture()
def env(tmp_path):
    app = FastAPI()
    ws = Workspace(tmp_path / "ws")
    app.include_router(make_router(ws))
    c = TestClient(app)
    ids = {}

    def up(name, **kw):
        p = tmp_path / f"{name}.mp4"
        make_clip(p, **kw)
        with p.open("rb") as f:
            r = c.post("/api/media", files={"file": (p.name, f, "video/mp4")})
        assert r.status_code == 200, r.text
        ids[name] = r.json()["id"]
        return ids[name]

    return c, ws, ids, up


def job(c, path, body):
    r = c.post(path, json=body)
    assert r.status_code == 200, r.text
    for _ in range(600):
        s = c.get(f"/api/ai/jobs/{r.json()['job']}").json()
        if s["status"] in ("done", "error"):
            assert s["status"] == "done", s["error"]
            return s["result"]
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_trim_finds_black_frozen_and_glitch(env):
    c, ws, ids, up = env
    up("a", black_head=0.5, freeze_tail=1.0, speed=3)
    up("g", glitch_head=3, speed=3)
    up("ok", speed=3)
    pl = job(c, "/api/autojoin/plan", {"clips": [{"media": ids[k]} for k in ("a", "g", "ok")]})
    a, g, ok = pl["clips"]
    assert abs(a["src_in"] - 0.5) < 0.1 and a["src_out"] < 4.2 and a["tail_trimmed"]
    assert "검은 화면 0.50초" in a["reasons_in"][0] and "멈춘" in a["reasons_out"][0]
    assert g["src_in"] >= 3 / 30 - 0.05 and "글리치" in g["reasons_in"][0]
    assert ok["src_in"] == 0 and ok["src_out"] == ok["duration"] and not ok["reasons_in"]


def test_seam_choice_and_color_loudness_matching(env):
    c, ws, ids, up = env
    up("a", base=(60, 90, 200), speed=3, volume=0.5)
    up("same", base=(60, 90, 200), phase=0.0, speed=3, volume=0.5)
    up("bright", base=(90, 120, 235), speed=3, volume=0.1)       # 같은 장면인데 더 밝고 소리는 작음
    up("other", base=(230, 150, 70), phase=2.0, speed=3, volume=0.5)
    pl = job(c, "/api/autojoin/plan", {"clips": [{"media": ids[k]} for k in ("a", "bright", "other")]})
    s1, s2 = pl["seams"]
    assert s1["transition"] in ("dissolve", "cut") and s1["color"]["brightness"] < 0   # 밝은 쪽을 어둡게
    assert s2["transition"] == "dip"                                                   # 아주 다른 장면
    assert pl["clips"][1]["gain"] > 1.5                                                # 작은 소리를 키움
    off = job(c, "/api/autojoin/plan", {"clips": [{"media": ids[k]} for k in ("a", "bright")],
                                        "options": {"match_color": False, "match_loudness": False}})
    assert off["seams"][0]["color"]["brightness"] == 0 and off["clips"][1]["gain"] == 1.0


def test_override_and_auto_order(env):
    c, ws, ids, up = env
    up("a", base=(60, 90, 200), speed=0.0, seconds=3)
    up("x", base=(230, 150, 70), speed=0.0, seconds=3)
    up("a2", base=(62, 92, 198), speed=0.0, seconds=3)
    clips = [{"media": ids[k]} for k in ("a", "x", "a2")]
    pl = job(c, "/api/autojoin/plan", {"clips": clips, "options": {"auto_order": True}})
    assert pl["order"] == [0, 2, 1]                       # 닮은 영상끼리 붙임
    pl = job(c, "/api/autojoin/plan", {"clips": clips, "seams": [{"transition": "morph", "dur": 0.7}, {"transition": "cut"}]})
    assert pl["seams"][0]["transition"] == "morph" and pl["seams"][0]["dur"] == 0.7 and pl["seams"][1]["transition"] == "cut"


def test_build_layout_links_crossfade_and_tail(env):
    c, ws, ids, up = env
    up("a", black_head=0.5, freeze_tail=1.0, speed=3, seconds=5, volume=0.3)
    up("b", base=(80, 110, 215), speed=3, seconds=5, volume=0.3)
    r = job(c, "/api/autojoin/build", {"clips": [{"media": ids["a"]}, {"media": ids["b"]}], "start": 2.0,
                                       "seams": [{"transition": "dissolve", "dur": 0.4}]})
    cl = r["clips"]
    v = [x for x in cl if x["track"] == "v1"]
    a = [x for x in cl if x["track"] in ("a1", "a2")]
    assert len(v) == 2 and len(a) == 2
    assert v[0]["start"] == 2.0 and abs(v[1]["start"] - (2.0 + v[0]["src_out"] - v[0]["src_in"])) < 1e-6
    assert v[1]["transition"] == "fade" and v[1]["transition_dur"] == 0.4 and v[0]["tail_hold"] is True
    assert v[0]["link"] == a[0]["link"] and a[0]["track"] != a[1]["track"]            # 소리는 번갈아 두 트랙에
    assert a[0]["src_out"] > v[0]["src_out"]                                          # 앞 소리가 겹침 구간만큼 더 이어짐
    assert a[1]["vol_keys"][0] == {"t": 0.0, "v": 0.0}


def test_morph_bridge_and_preview(env):
    c, ws, ids, up = env
    up("a", base=(60, 90, 200), speed=3, seconds=4)
    up("b", base=(230, 150, 70), phase=2.0, speed=3, seconds=4)
    body = {"clips": [{"media": ids["a"]}, {"media": ids["b"]}], "seams": [{"transition": "morph", "dur": 0.5}]}
    r = job(c, "/api/autojoin/build", body)
    bridge = [x for x in r["clips"] if x["media"] not in ids.values()]
    assert len(bridge) == 1 and bridge[0]["src_out"] == pytest.approx(0.5, abs=0.05)
    meta = ws.meta(bridge[0]["media"])
    assert meta["has_video"] and not meta["has_audio"] and meta["width"] == 160
    again = job(c, "/api/autojoin/build", body)                                        # 같은 다리는 다시 만들지 않음
    assert [x["media"] for x in again["clips"]] == [x["media"] for x in r["clips"]]
    pv = job(c, "/api/autojoin/preview", {**body, "seam": 0})
    f = ws.root / "exports" / pv["url"].rsplit("/", 1)[1]
    assert f.exists()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                               capture_output=True, text=True).stdout)
    assert 3.0 < dur < 4.0                                                             # 1.6 + 0.5 + 1.6


def test_nonvideo_rejected(env, tmp_path):
    import soundfile as sf
    c, ws, ids, up = env
    sf.write(tmp_path / "s.wav", np.zeros(48000, np.float32), 48000)
    with (tmp_path / "s.wav").open("rb") as f:
        mid = c.post("/api/media", files={"file": ("s.wav", f, "audio/wav")}).json()["id"]
    assert c.post("/api/autojoin/plan", json={"clips": [{"media": mid}]}).status_code == 400
