"""타임라인 안전장치: 클립이 있던 프로젝트를 빈 타임라인으로 덮어쓰기 막기 + 이전 버전 스냅샷·복원."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_editor import workspaces
from video_editor.editor import Workspace, make_router


@pytest.fixture()
def c(tmp_path):
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app)


def clip(i, start=0.0):
    return {"id": f"c{i}", "track": "t", "media": "m", "src_in": 0, "src_out": 2, "start": start, "speed": 1}


def put(c, n, **extra):
    return c.put("/api/workspace/timeline", json={"project": {"tracks": [], "clips": [clip(i, i * 2) for i in range(n)]}, "ui": {}, **extra})


def test_empty_overwrite_is_refused_unless_confirmed(c):
    assert put(c, 3).status_code == 200
    r = put(c, 0)
    assert r.status_code == 409
    assert len(c.get("/api/workspace/timeline").json()["project"]["clips"]) == 3   # 그대로
    assert put(c, 0, allow_empty=True).status_code == 200                           # 사용자가 확인했을 때만
    assert c.get("/api/workspace/timeline").json()["project"]["clips"] == []
    assert put(c, 0).status_code == 200                                             # 이미 비어 있으면 그대로 저장 가능


def test_history_keeps_last_versions_and_restores(c, monkeypatch):
    monkeypatch.setattr(workspaces, "HISTORY_GAP", 0)
    monkeypatch.setattr(workspaces, "HISTORY_KEEP", 5)
    for n in range(1, 10):
        assert put(c, n).status_code == 200
    vs = c.get("/api/workspace/timeline/history").json()["versions"]
    assert len(vs) == 5                                    # 최근 것만 돌려가며
    assert [v["clips"] for v in vs] == [8, 7, 6, 5, 4]     # 최신순, 저장 직전의 내용
    snap = c.get(f"/api/workspace/timeline/history/{vs[-1]['name']}").json()
    assert len(snap["project"]["clips"]) == 4
    got = c.post("/api/workspace/timeline/restore", json={"name": vs[-1]["name"]}).json()
    assert len(got["project"]["clips"]) == 4
    assert len(c.get("/api/workspace/timeline").json()["project"]["clips"]) == 4
    assert c.get("/api/workspace/timeline/history").json()["versions"][0]["clips"] == 9   # 복원 직전 상태도 남음
    assert c.post("/api/workspace/timeline/restore", json={"name": "../x"}).status_code == 400


def test_big_shrink_always_snapshots(c, monkeypatch):
    monkeypatch.setattr(workspaces, "HISTORY_GAP", 10_000)   # 시간 간격으로는 안 남는 설정
    put(c, 10); put(c, 10)
    put(c, 2)                                                 # 절반 이하로 줄면 직전 내용을 꼭 남김
    vs = c.get("/api/workspace/timeline/history").json()["versions"]
    assert any(v["clips"] == 10 for v in vs)


def test_disabled_clip_is_left_out_of_length_and_mix():
    from video_editor.editor import Project, project_length
    p = Project.model_validate({"tracks": [{"id": "t", "kind": "video"}], "clips": [
        {"id": "a", "track": "t", "media": "m", "src_in": 0, "src_out": 2, "start": 0},
        {"id": "b", "track": "t", "media": "m", "src_in": 0, "src_out": 2, "start": 5, "disabled": True}]})
    assert project_length(p) == 2.0
