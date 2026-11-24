"""프로젝트별 작업 폴더: 예전 구조 옮기기(안전·되풀이 가능) · 만들기/열기/이름/복제/지우기 · 프로젝트를 건너는 템플릿 일괄 적용."""
import hashlib
import json
from pathlib import Path

import urllib.parse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_editor import make_video, make_voice, tracks, upload
from tests.test_shorts_tools import _setup_template, wait
from video_editor import workspaces
from video_editor.editor import Workspace, make_router


def SD(home: Path) -> Path:
    return home / "spaces" / "기본-워크스페이스" / "projects"


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def legacy_workspace(home: Path) -> dict:
    """예전 구조의 작업 폴더를 만듭니다: 미디어·캐시·내보내기·보관한 프로젝트·캐릭터·템플릿·설정·효과음 보관함 항목."""
    (home / "media").mkdir(parents=True), (home / "cache").mkdir(), (home / "exports").mkdir(), (home / "projects").mkdir()
    voice = make_voice(home / "tmp_voice.wav", 1.0)
    (home / "media" / ("a" * 32 + ".wav")).write_bytes(voice.read_bytes())
    meta = {"id": "a" * 32, "name": "내 목소리.wav", "file": "a" * 32 + ".wav", "recorded": True, "kind": "audio",
            "duration": 1.0, "has_video": False, "has_audio": True, "width": 0, "height": 0}
    (home / "media" / ("a" * 32 + ".json")).write_text(json.dumps(meta))
    (home / "media" / ("b" * 32 + ".wav")).write_bytes(voice.read_bytes())   # 효과음 보관함 항목
    (home / "media" / ("b" * 32 + ".json")).write_text(json.dumps({**meta, "id": "b" * 32, "file": "b" * 32 + ".wav", "name": "뿅", "lib": "sfx", "user": False}))
    (home / "media" / ("c" * 32 + ".wav")).write_bytes(voice.read_bytes())   # 템플릿 인트로
    (home / "media" / ("c" * 32 + ".json")).write_text(json.dumps({**meta, "id": "c" * 32, "file": "c" * 32 + ".wav", "name": "인트로"}))
    (home / "cache" / ("a" * 32 + "_raw.wav")).write_bytes(b"cache-bytes")
    (home / "cache" / ("a" * 32 + "_ai_x_30.wav")).write_bytes(b"ai-output")
    (home / "exports" / "video_editor_1234.mp4").write_bytes(b"mp4-bytes")
    saved = {"id": "d" * 12, "name": "예전 보관본", "saved_at": 1.0, "project": {"tracks": tracks(), "clips": []}}
    (home / "projects" / ("d" * 12 + ".json")).write_text(json.dumps(saved))
    (home / "characters.json").write_text(json.dumps([{"id": "ch1", "name": "아저씨", "preset": "deep", "intensity": 1.0, "ref": "a" * 32, "note": ""}]))
    (home / "templates.json").write_text(json.dumps([{"id": "t1", "name": "내 채널", "intro": "c" * 32}]))
    (home / "settings.json").write_text(json.dumps({"engine": "cli"}))
    voice.unlink()
    return {"before": tree(home)}


def test_migrates_legacy_workspace_without_losing_anything(tmp_path):
    home = tmp_path / "ws"
    home.mkdir()
    before = legacy_workspace(home)["before"]
    ws = Workspace(home)
    pid = ws.pid
    assert ws.projects.info(pid)["name"] == "기존 작업" and ws.projects.info(pid)["origin"] == "migrated"
    p = SD(home) / pid
    # 내 파일은 프로젝트 안으로 (같은 내용), 효과음 보관함 항목은 공유 보관함으로
    assert (p / "media" / ("a" * 32 + ".wav")).exists() and (p / "media" / ("a" * 32 + ".json")).exists()
    assert (home / "library" / "media" / ("b" * 32 + ".wav")).exists() and not (p / "media" / ("b" * 32 + ".wav")).exists()
    assert (p / "cache" / ("a" * 32 + "_ai_x_30.wav")).read_bytes() == b"ai-output"        # AI 결과 캐시
    assert (p / "exports" / "video_editor_1234.mp4").read_bytes() == b"mp4-bytes"
    assert json.loads((p / "saved" / ("d" * 12 + ".json")).read_text())["name"] == "예전 보관본"
    # 예전 폴더는 비었으니 사라지고, 공유 설정 파일은 제자리
    assert not (home / "media").exists() and not (home / "cache").exists() and not (home / "exports").exists()
    assert not (home / "projects").exists()
    assert (home / "characters.json").exists() and (home / "templates.json").exists() and (home / "settings.json").exists()
    # 템플릿 인트로·캐릭터 샘플은 어느 프로젝트에서도 쓰도록 공유 보관함에도 있음
    assert (home / "library" / "media" / ("c" * 32 + ".wav")).exists() and (home / "library" / "media" / ("a" * 32 + ".wav")).exists()
    # 모든 파일 내용이 그대로 남아 있음 (이름만 바뀜): 옮기기 전 내용 해시 집합 ⊆ 옮긴 뒤
    after = set(tree(home).values())
    assert set(before.values()) <= after
    # 백업·기록
    mig = home / "_migration"
    manifest = json.loads(next(mig.glob("manifest-2*.json")).read_text())
    assert manifest["verified"] and manifest["project"] == pid
    bak = next(mig.glob("backup-2*"))
    assert (bak / "media" / ("a" * 32 + ".json")).exists() and (bak / "projects" / ("d" * 12 + ".json")).exists() and (bak / "characters.json").exists()


def test_migration_is_idempotent_and_resumable(tmp_path):
    home = tmp_path / "ws"
    home.mkdir()
    legacy_workspace(home)
    ws = Workspace(home)
    snap = tree(home)
    Workspace(home)                       # 다시 켜도
    assert tree(home) == snap and len(list((home / "_migration").glob("manifest-*.json"))) == 2
    assert len(Workspace(home).projects.list()) == 1
    # 옮기다 멈춘 상태(프로젝트 폴더는 있고, 예전 파일이 일부 남음)에서도 이어서 끝냄
    (home / "media").mkdir()
    (home / "media" / ("e" * 32 + ".wav")).write_bytes(b"late")
    (home / "media" / ("e" * 32 + ".json")).write_text(json.dumps({"id": "e" * 32, "name": "늦게", "file": "e" * 32 + ".wav"}))
    ws2 = Workspace(home)
    assert len(ws2.projects.list()) == 1 and (SD(home) / ws.pid / "media" / ("e" * 32 + ".wav")).read_bytes() == b"late"
    assert not (home / "media").exists()


def test_migration_never_overwrites_existing_files(tmp_path):
    home = tmp_path / "ws"
    home.mkdir()
    legacy_workspace(home)
    ws = Workspace(home)
    (home / "cache").mkdir()
    (home / "cache" / ("a" * 32 + "_raw.wav")).write_bytes(b"different")        # 같은 이름이 이미 프로젝트에 있음
    workspaces.migrate_legacy(home)
    assert (SD(home) / ws.pid / "cache" / ("a" * 32 + "_raw.wav")).read_bytes() == b"cache-bytes"
    left = list((SD(home) / ws.pid / "legacy-leftovers").glob("*"))
    assert len(left) == 1 and left[0].read_bytes() == b"different"


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    ws = Workspace(tmp_path / "ws")
    app.include_router(make_router(ws))
    return TestClient(app), tmp_path, ws


def test_project_crud_and_isolation(api):
    c, tmp, ws = api
    info = c.get("/api/workspace").json()
    first = info["active"]
    assert [p["name"] for p in info["projects"]] == ["기본 프로젝트"]
    m1 = upload(c, make_voice(tmp / "a.wav"))
    assert [m["id"] for m in c.get("/api/media").json() if not m.get("lib")] == [m1["id"]]
    new = c.post("/api/workspace/projects", json={"name": "쇼츠 시리즈"}).json()
    assert c.get("/api/workspace").json()["active"] == new["id"]
    assert not [m for m in c.get("/api/media").json() if not m.get("lib")]            # 새 프로젝트는 비어 있음
    m2 = upload(c, make_voice(tmp / "b.wav"))
    assert c.get(f"/api/media/{m1['id']}/info").status_code == 404                     # 다른 프로젝트 미디어는 안 보임
    # 이름 바꾸기 · 복제(미디어까지) · 열기
    assert c.patch(f"/api/workspace/projects/{new['id']}", json={"name": "쇼츠 2"}).json()["name"] == "쇼츠 2"
    dup = c.post(f"/api/workspace/projects/{new['id']}/duplicate", json={}).json()
    assert dup["name"] == "쇼츠 2 복사본" and (tmp / "ws" / "spaces" / "기본-워크스페이스" / "projects" / dup["id"] / "media" / m2["file"]).exists()
    assert c.post("/api/workspace/open", json={"id": dup["id"]}).status_code == 200
    assert c.get(f"/api/media/{m2['id']}/info").status_code == 200
    c.post("/api/workspace/open", json={"id": first})
    assert c.get(f"/api/media/{m1['id']}/info").status_code == 200
    # 지우기: 이름을 똑같이 적어야 하고, 폴더째 _trash로 감
    assert c.post(f"/api/workspace/projects/{dup['id']}/delete", json={"confirm": "아님"}).status_code == 400
    r = c.post(f"/api/workspace/projects/{dup['id']}/delete", json={"confirm": dup["name"]})
    assert r.status_code == 200 and not (tmp / "ws" / "spaces" / "기본-워크스페이스" / "projects" / dup["id"]).exists()
    tr = tmp / "ws" / "spaces" / "기본-워크스페이스" / "_trash"
    assert list(tr.glob(f"{dup['id']}-*")) and (next(tr.glob(f"{dup['id']}-*")) / "media" / m2["file"]).exists()
    # 열려 있는 프로젝트를 지우면 다른 프로젝트가 열리고, 마지막 하나는 못 지움
    c.post(f"/api/workspace/projects/{first}/delete", json={"confirm": "기본 프로젝트"})
    assert c.get("/api/workspace").json()["active"] == new["id"]
    assert c.post(f"/api/workspace/projects/{new['id']}/delete", json={"confirm": "쇼츠 2"}).status_code == 400
    assert c.post("/api/workspace/open", json={"id": "../etc"}).status_code in (400, 404)


def test_timeline_file_per_project(api):
    c, tmp, ws = api
    first = ws.pid
    st = {"project": {"tracks": tracks(), "clips": []}, "ui": {"zoom": 80}}
    assert c.put("/api/workspace/timeline", json=st).json()["ok"]
    other = c.post("/api/workspace/projects", json={"name": "둘째"}).json()
    assert c.get("/api/workspace/timeline").json() == {}                               # 둘째는 비어 있음
    assert c.get("/api/workspace/timeline", params={"project": first}).json()["ui"]["zoom"] == 80
    dup = c.post(f"/api/workspace/projects/{first}/duplicate", json={"name": "복사"}).json()
    assert c.get("/api/workspace/timeline", params={"project": dup["id"]}).json()["ui"]["zoom"] == 80
    assert c.put("/api/workspace/timeline", json={"nope": 1}).status_code == 400
    assert other["id"] != first


def test_library_and_templates_are_shared_and_batch_works_across_projects(api):
    c, tmp, ws = api
    first = ws.pid
    # 프로젝트 A: 템플릿(인트로·아웃트로·엔딩)을 만들고, 영상 하나를 프로젝트로 보관
    tpl, intro, outro = _setup_template(c, tmp)
    va = upload(c, make_video(tmp / "va.mp4", 2, size="270x480"))
    sa = c.post("/api/projects", json={"name": "A의 영상", "project": {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "v1", "media": va["id"], "src_in": 0, "src_out": 2, "start": 0}]}}).json()
    # 프로젝트 B: 다른 영상
    b = c.post("/api/workspace/projects", json={"name": "B"}).json()
    vb = upload(c, make_video(tmp / "vb.mp4", 2, size="270x480"))
    sb = c.post("/api/projects", json={"name": "B의 영상", "project": {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "v1", "media": vb["id"], "src_in": 0, "src_out": 2, "start": 0}]}}).json()
    # 템플릿·효과음은 B에서도 보이고, 인트로 미디어도 B에서 읽힘
    assert [t["name"] for t in c.get("/api/templates").json() if not t["builtin"]] == ["내 채널"]
    assert any(m["id"] == intro["id"] for m in c.get("/api/media").json())
    assert c.get(f"/api/media/{intro['id']}/info").status_code == 200
    lib = c.post("/api/library/ensure").json()
    assert lib["items"] and all((ws.global_shared / f"{m['id']}.json").exists() for m in lib["media"])
    # 보관본 목록은 두 프로젝트를 다 보여 주고, 한 번에 둘 다 입히고 내보냄
    listing = c.get("/api/projects").json()
    assert {x["name"] for x in listing} == {"A의 영상", "B의 영상"} and {x["workspace"] for x in listing} == {first, b["id"]}
    res = wait(c, c.post("/api/template/batch", json={"template_id": tpl["id"], "project_ids": [sa["id"], sb["id"]]}).json()["job"], 120)
    assert res["status"] == "done" and all(x["ok"] for x in res["result"]["results"]), res
    pa = c.get(f"/api/projects/{sa['id']}").json()
    assert pa["workspace"] == first and any(x["tpl"] == "intro" for x in pa["project"]["clips"])
    ex = wait(c, c.post("/api/batch/export", json={"project_ids": [sa["id"], sb["id"]], "format": "shorts"}).json()["job"], 180)
    assert ex["status"] == "done" and all(x["ok"] for x in ex["result"]["results"]), ex
    for x in ex["result"]["results"]:
        assert c.get(x["url"]).status_code == 200                                      # 다른 프로젝트의 내보내기도 받을 수 있음
    # 보관본은 프로젝트 사이에서도 지울 수 있음
    c.delete(f"/api/projects/{sa['id']}")
    assert [x["name"] for x in c.get("/api/projects").json()] == ["B의 영상"]


# ================================================================ 워크스페이스 > 프로젝트

def v2_workspace(home: Path) -> None:
    """프로젝트 구분만 있던 구조(workspace/projects/<프로젝트>/ + workspace.json active/recent)."""
    legacy_workspace(home)
    Workspace.__new__(Workspace)
    workspaces.migrate_legacy(home)           # v1 → v2 까지만


def test_migrates_v2_projects_into_default_space(tmp_path):
    home = tmp_path / "ws"
    home.mkdir()
    legacy_workspace(home)
    workspaces.migrate_legacy(home)
    assert (home / "projects" / "기존-작업" / "project.json").exists() and not (home / "spaces").exists()
    # 사용자가 이미 만든 두 번째 프로젝트도 같이 옮겨져야 함
    pr = workspaces.Projects.__new__(workspaces.Projects)
    pr.home, pr.base, pr.reg = home, home / "projects", home / "workspace.json"
    second = pr._create("쇼츠", origin="created")
    before = tree(home / "projects")
    ws = Workspace(home)
    sd = home / "spaces" / "기본-워크스페이스"
    assert ws.spaces.info(ws.sid)["name"] == "기본 워크스페이스" and ws.sid == "기본-워크스페이스"
    assert not (home / "projects").exists()
    assert {p["id"] for p in ws.projects.list()} == {"기존-작업", second["id"]} and ws.pid == "기존-작업"
    assert tree(sd / "projects") == before                                   # 내용은 그대로, 이름(위치)만 바뀜
    manifest = json.loads(next((home / "_migration").glob("manifest-spaces-*.json")).read_text())
    assert manifest["verified"] and (next((home / "_migration").glob("backup-spaces-*")) / "workspace.json").exists()
    assert json.loads((home / "workspace.json").read_text())["active_space"] == "기본-워크스페이스"
    snap = tree(home)
    Workspace(home)                                                           # 다시 켜도 그대로
    assert tree(home) == snap and len(list((home / "_migration").glob("manifest-spaces-*.json"))) == 1


def test_space_crud_and_isolation(api):
    c, tmp, ws = api
    info = c.get("/api/workspace").json()
    first = info["space"]
    assert [s["name"] for s in info["spaces"]] == ["기본 워크스페이스"] and info["spaces"][0]["projects"] == 1
    upload(c, make_voice(tmp / "a.wav"))
    sp = c.post("/api/workspace/spaces", json={"name": "채널 B"}).json()
    now = c.get("/api/workspace").json()
    assert now["space"] == sp["id"] and [p["name"] for p in now["projects"]] == ["기본 프로젝트"]     # 새 워크스페이스엔 빈 프로젝트 하나
    assert not [m for m in c.get("/api/media").json() if not m.get("lib")]
    assert c.patch(f"/api/workspace/spaces/{sp['id']}", json={"name": "채널 B2"}).json()["name"] == "채널 B2"
    c.post("/api/workspace/spaces/open", json={"id": first})
    assert len([m for m in c.get("/api/media").json() if not m.get("lib")]) == 1
    # 지우기: 이름 확인 필요, 폴더째 _trash
    assert c.post(f"/api/workspace/spaces/{sp['id']}/delete", json={"confirm": "아님"}).status_code == 400
    assert c.post(f"/api/workspace/spaces/{sp['id']}/delete", json={"confirm": "채널 B2"}).status_code == 200
    assert list((tmp / "ws" / "_trash").glob(f"space-{sp['id']}-*"))
    assert c.post(f"/api/workspace/spaces/{first}/delete", json={"confirm": "기본 워크스페이스"}).status_code == 400   # 마지막 하나


def test_templates_and_endings_are_per_space_plus_global(api):
    c, tmp, ws = api
    a_end = c.post("/api/endings", json={"name": "A 엔딩", "kind": "heart"}).json()
    g_end = c.post("/api/endings", json={"name": "공용 엔딩", "kind": "bell", "scope": "global"}).json()
    a_tpl = c.post("/api/templates", json={"name": "A 템플릿", "ending": a_end["id"], "watermark": {"text": "@a"}}).json()
    g_tpl = c.post("/api/templates", json={"name": "공용 템플릿", "ending": g_end["id"], "scope": "global"}).json()
    assert a_tpl["scope"] == "space" and g_tpl["scope"] == "global"
    # 공용 템플릿은 전용 엔딩을 못 씀
    assert c.post("/api/templates", json={"name": "나쁨", "ending": a_end["id"], "scope": "global"}).status_code == 400
    b = c.post("/api/workspace/spaces", json={"name": "B"}).json()
    assert [t["name"] for t in [t for t in c.get("/api/templates").json() if not t["builtin"]]] == ["공용 템플릿"]             # B에는 전용(A)이 안 보이고 공용만
    assert [e["name"] for e in c.get("/api/endings").json()["items"]] == ["공용 엔딩"]
    b_tpl = c.post("/api/templates", json={"name": "B 템플릿"}).json()
    assert {t["name"] for t in [t for t in c.get("/api/templates").json() if not t["builtin"]]} == {"공용 템플릿", "B 템플릿"}
    c.post("/api/workspace/spaces/open", json={"id": ws.spaces.list()[0]["id"] if ws.spaces.list()[0]["id"] != b["id"] else ws.spaces.list()[1]["id"]})
    assert {t["name"] for t in [t for t in c.get("/api/templates").json() if not t["builtin"]]} == {"A 템플릿", "공용 템플릿"}
    # 범위 바꾸기: 전용 → 공용으로 옮겨 저장
    moved = c.post("/api/templates", json={**a_tpl, "ending": g_end["id"], "scope": "global"}).json()
    assert moved["id"] == a_tpl["id"] and moved["scope"] == "global"
    assert sorted(t["name"] for t in [t for t in c.get("/api/templates").json() if not t["builtin"]]) == ["A 템플릿", "공용 템플릿"]
    assert len([t for t in [t for t in c.get("/api/templates").json() if not t["builtin"]] if t["id"] == a_tpl["id"]]) == 1
    c.delete(f"/api/templates/{g_tpl['id']}")
    assert [t["name"] for t in [t for t in c.get("/api/templates").json() if not t["builtin"]]] == ["A 템플릿"]
    assert b_tpl["scope"] == "space"


def test_space_library_uploads_are_per_space_and_defaults_global(api):
    c, tmp, ws = api
    first = ws.sid
    c.post("/api/library/ensure")
    up = c.post("/api/library/upload", files={"file": ("내소리.wav", make_voice(tmp / "s.wav", 1.0).read_bytes())}, data={"kind": "sfx", "name": "내 효과음"}).json()
    names = lambda: {i["name"] for i in c.get("/api/library").json()}
    assert "내 효과음" in names() and "뿅" in names()
    c.post("/api/workspace/spaces", json={"name": "B"})
    assert "내 효과음" not in names() and "뿅" in names()          # 올린 소리는 그 워크스페이스에만, 기본 소리는 어디서나
    c.post("/api/workspace/spaces/open", json={"id": first})
    assert "내 효과음" in names() and up["id"]


def test_move_project_between_spaces_keeps_template_media_working(api):
    c, tmp, ws = api
    first = ws.sid
    tpl, intro, outro = _setup_template(c, tmp)               # 전용 템플릿 → 인트로·아웃트로가 이 워크스페이스 보관함에 복사됨
    v = upload(c, make_video(tmp / "v.mp4", 2, size="270x480"))
    sv = c.post("/api/projects", json={"name": "내 영상", "project": {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "v1", "media": v["id"], "src_in": 0, "src_out": 2, "start": 0}]}}).json()
    res = wait(c, c.post("/api/template/batch", json={"template_id": tpl["id"], "project_ids": [sv["id"]]}).json()["job"], 120)
    assert res["status"] == "done" and all(x["ok"] for x in res["result"]["results"])
    assert c.put("/api/workspace/timeline", json={"project": {"tracks": tracks(), "clips": []}, "ui": {}}).json()["ok"]
    pid = ws.pid
    b = c.post("/api/workspace/spaces", json={"name": "B"}).json()
    c.post("/api/workspace/spaces/open", json={"id": first})
    r = c.post(f"/api/workspace/projects/{pid}/move", json={"space": b["id"]})
    assert r.status_code == 200, r.text
    # 열린 프로젝트를 옮겼으니 B가 열려 있고, 옮긴 프로젝트가 열려 있음; 출발 워크스페이스엔 빈 프로젝트가 새로 생김
    info = c.get("/api/workspace").json()
    assert info["space"] == b["id"] and info["active"] == r.json()["id"]
    assert (tmp / "ws" / "spaces" / b["id"] / "projects" / r.json()["id"] / "media" / v["file"]).exists()
    assert [p["name"] for p in ws.spaces.projects(first).listing()["projects"]] == ["기본 프로젝트"]
    # 보관본과 그 안의 인트로(전용 보관함 → 복사됨)가 B에서도 열림
    got = c.get(f"/api/projects/{sv['id']}").json()
    assert got["workspace"] == r.json()["id"] and any(x["tpl"] == "intro" for x in got["project"]["clips"])
    assert c.get(f"/api/media/{intro['id']}/info").status_code == 200
    assert (tmp / "ws" / "spaces" / b["id"] / "library" / "media" / f"{intro['id']}.json").exists()
    ex = wait(c, c.post("/api/batch/export", json={"project_ids": [sv["id"]], "format": "shorts"}).json()["job"], 180)
    assert ex["status"] == "done" and all(x["ok"] for x in ex["result"]["results"]), ex


def test_space_project_table_and_operations_on_other_space(api):
    c, tmp, ws = api
    first, pid = ws.sid, ws.pid
    m = upload(c, make_voice(tmp / "a.wav"))
    c.put("/api/workspace/timeline", json={"project": {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "a1", "media": m["id"], "src_in": 0, "src_out": 4, "start": 2, "speed": 2}]}, "ui": {}})
    t = c.get(f"/api/workspace/spaces/{first}/projects").json()
    row = t["projects"][0]
    assert t["space_active"] and row["clips"] == 1 and row["length"] == pytest.approx(4.0) and row["media"] == 1 and row["bytes"] > 0 and row["active"]
    b = c.post("/api/workspace/spaces", json={"name": "B"}).json()["id"]        # 이제 B가 열려 있음
    assert not c.get(f"/api/workspace/spaces/{first}/projects").json()["space_active"]
    # 열려 있지 않은 워크스페이스(first)의 프로젝트도 다룸: 만들기(열지 않고) · 이름 · 복제 · 옮기기 · 지우기
    new = c.post(f"/api/workspace/projects?space={first}&open=false", json={"name": "새것"}).json()
    assert c.get("/api/workspace").json()["space"] == b                          # 열지 않았으니 그대로 B
    assert c.patch(f"/api/workspace/projects/{new['id']}?space={first}", json={"name": "새것2"}).json()["name"] == "새것2"
    dup = c.post(f"/api/workspace/projects/{new['id']}/duplicate?space={first}", json={}).json()
    mv = c.post(f"/api/workspace/projects/{dup['id']}/move?space={first}", json={"space": b})
    assert mv.status_code == 200 and c.get("/api/workspace").json()["space"] == b          # 열려 있지 않던 프로젝트를 옮겨도 열린 곳은 그대로
    names = lambda sid: {p["name"] for p in c.get(f"/api/workspace/spaces/{sid}/projects").json()["projects"]}
    assert names(first) == {"기본 프로젝트", "새것2"} and "새것2 복사본" in names(b)
    assert c.post(f"/api/workspace/projects/{new['id']}/delete?space={first}", json={"confirm": "새것2"}).status_code == 200
    assert names(first) == {"기본 프로젝트"}
    # 워크스페이스를 열면서 그 안의 프로젝트를 같이 열기
    c.post(f"/api/workspace/projects?space={first}&open=false", json={"name": "둘째"})
    second = [p for p in c.get(f"/api/workspace/spaces/{first}/projects").json()["projects"] if p["name"] == "둘째"][0]["id"]
    assert c.post("/api/workspace/spaces/open", json={"id": first, "project": second}).status_code == 200
    info = c.get("/api/workspace").json()
    assert info["space"] == first and info["active"] == second


# ---------------------------------------------------------------- 딥링크

from video_editor.editor import link_path


def link_client(tmp_path):
    from video_editor.workspace_routes import add_page_routes
    app = FastAPI()
    ws = Workspace(tmp_path / "ws")
    app.include_router(make_router(ws))
    html = tmp_path / "e.html"
    html.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    add_page_routes(app, ws, html)
    return TestClient(app, follow_redirects=False), ws


def test_deeplink_pages_and_missing(tmp_path):
    c, ws = link_client(tmp_path)
    sid, pid = ws.sid, ws.pid
    r = c.get(f"/editor/w/{sid}/p/{pid}")
    assert r.status_code == 200 and f'"id": "{pid}"' in r.text and f'"space": "{sid}"' in r.text
    assert c.get("/editor").status_code == 200
    r = c.get(f"/editor/w/{sid}")                                                    # 워크스페이스만 → 마지막 프로젝트
    assert r.status_code == 302 and urllib.parse.unquote(r.headers["location"]) == f"/editor/w/{sid}/p/{pid}"
    for bad in (f"/editor/w/{sid}/p/없는것", "/editor/w/없는곳/p/x"):
        r = c.get(bad)
        assert r.status_code == 302 and r.headers["location"].startswith("/editor?missing=")
    assert c.get("/editor/w/없는곳").headers["location"].startswith("/editor?missing=")
    assert '"missing": "x/y"' in c.get("/editor?missing=x%2Fy").text


def test_deeplink_binds_each_window_to_its_project(tmp_path):
    """창(Referer)마다 다른 프로젝트를 동시에 열어도 서로 섞이지 않고, 저장도 엉뚱한 곳에 안 들어가요."""
    c, ws = link_client(tmp_path)
    sid = ws.sid
    a = ws.pid
    b = c.post("/api/workspace/projects?open=false", json={"name": "둘째"}).json()["id"]
    ra = {"referer": "http://x" + link_path(sid, a)}
    rb = {"referer": "http://x" + link_path(sid, b)}
    voice = make_voice(tmp_path / "a.wav")
    r = c.post("/api/media", files={"file": (voice.name, voice.read_bytes())}, headers=rb)       # B 창에서 올린 파일은 B 프로젝트로
    assert r.status_code == 200
    only = lambda h: [m["id"] for m in c.get("/api/media", headers=h).json() if not m.get("lib")]
    assert only(rb) == [r.json()["id"]] and only(ra) == []
    c.put("/api/workspace/timeline", json={"project": {"tag": "A"}}, headers=ra)
    c.put("/api/workspace/timeline", json={"project": {"tag": "B"}}, headers=rb)
    assert ws.projects.load_timeline(a)["project"]["tag"] == "A"
    assert ws.projects.load_timeline(b)["project"]["tag"] == "B"
    assert c.get("/api/workspace").json()["active"] == a                              # 전역 '열린 프로젝트'는 그대로
    # 없는 링크가 Referer 로 오면 묶지 않고 열려 있는 프로젝트로
    c.put("/api/workspace/timeline", json={"project": {"tag": "C"}}, headers={"referer": "http://x" + link_path(sid, "없음")})
    assert ws.projects.load_timeline(a)["project"]["tag"] == "C"
