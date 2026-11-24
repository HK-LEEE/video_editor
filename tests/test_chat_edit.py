"""글로 편집 채팅(chat_edit)과 샘플 템플릿."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_editor import make_video, tracks, upload
from tests.test_shorts_tools import fake_cli, wait
from video_editor import chat_edit, claude_copy
from video_editor.chat_edit import Word
from video_editor.editor import Project, Workspace, make_router, probe, project_length


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    ws = Workspace(tmp_path / "ws")
    app.include_router(make_router(ws))
    return TestClient(app), tmp_path, ws


def proj(**kw):
    base = {"tracks": [{"id": "v1", "kind": "video", "name": "V1"}, {"id": "a1", "kind": "audio", "name": "원본 소리"},
                       {"id": "a2", "kind": "audio", "name": "보이스", "voice": True}, {"id": "a3", "kind": "audio", "name": "음악·효과음", "volume": 1.0}],
            "clips": [{"id": "cv", "track": "v1", "media": "m1", "src_in": 0, "src_out": 20, "start": 0, "link": "L"},
                      {"id": "ca", "track": "a1", "media": "m1", "src_in": 0, "src_out": 20, "start": 0, "link": "L"},
                      {"id": "cm", "track": "a3", "media": "m2", "src_in": 0, "src_out": 20, "start": 0}],
            "subtitles": [{"id": "s1", "start": 0.0, "end": 4.0, "text": "안녕하세요"}, {"id": "s2", "start": 4.0, "end": 9.0, "text": "음 그래서 말이죠"},
                          {"id": "s3", "start": 9.0, "end": 14.0, "text": "끝입니다"}],
            "overlays": [{"id": "h", "kind": "title", "text": "훅", "start": 0, "end": 3}, {"id": "w", "kind": "watermark", "text": "@me", "start": 0}]}
    base.update(kw)
    return Project(**base)


WORDS = [Word(w=x, s=s, e=e) for x, s, e in [("안녕하세요", 0.2, 1.0), ("음", 1.2, 1.5), ("그래서", 1.6, 2.1), ("음", 4.4, 4.7), ("말이죠", 4.8, 5.3), ("어", 6.0, 6.3)]]


def test_cut_ripples_every_track_subtitle_and_overlay():
    r = chat_edit.apply_ops(proj(), [{"op": "cut", "start": 0, "end": 3}])
    p = r.project
    assert project_length(p) == pytest.approx(17)
    assert all(c.start == pytest.approx(0) and c.src_in == pytest.approx(3) for c in p.clips)
    assert p.subtitles[0].start == pytest.approx(0) and p.subtitles[0].end == pytest.approx(1.0)   # 앞 3초가 잘린 첫 자막
    assert p.subtitles[1].start == pytest.approx(1.0)
    assert p.overlays[0].end is not None and p.overlays[0].end - p.overlays[0].start < 0.05 or all(o.id != "h" for o in p.overlays)  # 3초 훅은 통째로 잘림
    assert [o.id for o in p.overlays] == ["w"] and p.overlays[0].end is None


def test_cut_middle_splits_clips_and_keeps_links_paired():
    p = chat_edit.apply_ops(proj(), [{"op": "cut", "start": 5, "end": 8}, {"op": "cut", "start": 12, "end": 14}]).project
    assert project_length(p) == pytest.approx(15)
    v = sorted((c for c in p.clips if c.track == "v1"), key=lambda c: c.start)
    assert len(v) == 3 and v[1].start == pytest.approx(5) and v[1].src_in == pytest.approx(8) and v[2].src_in == pytest.approx(14)
    links = {c.link for c in p.clips if c.track in ("v1", "a1")}
    assert len(links) == 3 and all(sum(1 for c in p.clips if c.link == l) == 2 for l in links)
    assert p.subtitles[-1].end == pytest.approx(9.0 - 0)  # 마지막 자막이 당겨짐


def test_delete_words_and_fillers_resolve_against_transcript():
    ops, warns = chat_edit.normalize_ops(proj(), [{"op": "delete_words", "texts": ["음"]}], WORDS)
    r = chat_edit.apply_ops(proj(), ops, WORDS)
    assert "'음' 2개" in r.summaries[0] and 0.5 < r.cut_seconds < 1.0
    ops, _ = chat_edit.normalize_ops(proj(), [{"op": "delete_words", "fillers": True}], WORDS)
    assert "3개" in chat_edit.apply_ops(proj(), ops, WORDS).summaries[0]       # 음, 음, 어
    # 대본이 없으면 안내
    r = chat_edit.apply_ops(proj(), ops, [])
    assert r.cut_seconds == 0 and "대본 불러오기" in r.warnings[0]


def test_volume_mute_style_overlay_and_cue_ops():
    p0 = proj()
    ops, warns = chat_edit.normalize_ops(p0, [
        {"op": "volume", "track": "music", "factor": 0.5},
        {"op": "volume", "track": "nope", "factor": 0.5},
        {"op": "mute", "track": "a1", "mute": True},
        {"op": "subtitle_style", "style": {"color": "#ffee00", "size": 0.09, "bogus": 1, "bg_color": "red"}},
        {"op": "cue_style", "start": 4, "end": 9, "style": {"color": "#ffff00"}},
        {"op": "add_overlay", "kind": "title", "text": "새 훅", "start": 1, "end": 4, "style": {"bg_color": "#112233", "size": 9}},
        {"op": "edit_overlay", "kind": "watermark", "style": {"opacity": 0.3}},
        {"op": "delete_overlay", "id": "h"},
        {"op": "cut", "start": -5, "end": 999},
        {"op": "bogus"},
    ])
    assert len(warns) == 2
    r = chat_edit.apply_ops(p0, [o for o in ops if o["op"] != "cut"])
    p = r.project
    assert next(t for t in p.tracks if t.id == "a3").volume == pytest.approx(0.5)
    assert next(t for t in p.tracks if t.id == "a1").mute
    assert p.subtitle_style.color == "#ffee00" and p.subtitle_style.size == 0.09 and p.subtitle_style.bg_color == "#000000"
    assert [c.color for c in p.subtitles] == ["", "#ffff00", ""]
    assert next(o for o in p.overlays if o.id == "w").opacity == pytest.approx(0.3) and not any(o.id == "h" for o in p.overlays)
    # size 9는 범위 밖이라 그 작업만 건너뜀
    assert not any(o.text == "새 훅" for o in p.overlays) and any("범위" in w for w in r.warnings)
    assert p0.subtitle_style.color == "#ffffff"            # 원본은 그대로


def test_cut_everything_is_refused_and_short_passthrough():
    r = chat_edit.apply_ops(proj(), [{"op": "cut", "start": 0, "end": 20}, {"op": "short", "start": 4, "end": 30, "format": "shorts"}])
    assert project_length(r.project) == pytest.approx(20) and any("전체" in w for w in r.warnings)
    ops, _ = chat_edit.normalize_ops(proj(), [{"op": "short", "start": 4, "end": 30}])
    assert ops[0]["end"] == 20 and chat_edit.apply_ops(proj(), ops).short == {"start": 4.0, "end": 20.0, "format": "shorts"}


def test_plan_edit_runs_headless_cli_with_project_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-0000")
    monkeypatch.setattr(claude_copy, "cli_path", lambda: "/usr/bin/claude")
    answer = {"reply": "앞 3초를 잘라낼게요.", "ops": [{"op": "cut", "start": 0, "end": 3}, {"op": "volume", "track": "a3", "factor": 0.6}]}
    run = fake_cli(json.dumps({"result": "", "structured_output": answer}))
    plan = chat_edit.plan_edit(tmp_path, proj(), "앞 3초 잘라, BGM 줄여", WORDS, {"m1": "영상.mp4", "m2": "음악.wav"}, [{"role": "user", "text": "안녕"}],
                               runner=run)
    args, kw = run.calls[0]
    assert args[:2] == ["/usr/bin/claude", "-p"] and "--tools" in args and "ANTHROPIC_API_KEY" not in kw["env"]
    assert "영상.mp4" in kw["input"] and "음악·효과음" in kw["input"] and "앞 3초 잘라" in kw["input"] and "안녕하세요" in kw["input"]
    assert plan.reply.startswith("앞 3초") and len(plan.ops) == 2 and plan.length_after == pytest.approx(17)
    assert any("볼륨" in s for s in plan.summaries)
    with pytest.raises(RuntimeError):
        chat_edit.plan_edit(tmp_path, proj(), "  ", [], {})


def test_chat_edit_routes_plan_then_apply(api, monkeypatch):
    c, tmp, ws = api
    monkeypatch.setattr(claude_copy, "cli_path", lambda: "/usr/bin/claude")
    answer = {"reply": "음 지울게요", "ops": [{"op": "delete_words", "texts": ["음"]}]}
    real, fake = claude_copy.run_cli, fake_cli(json.dumps({"result": json.dumps(answer)}))
    monkeypatch.setattr(claude_copy, "run_cli", lambda user, model, timeout=180, runner=None, system=None, schema=None: real(user, model, runner=fake, system=system, schema=schema))
    m = upload(c, make_video(tmp / "v.mp4", 3, size="270x480"))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"},
                                       {"id": "c1a", "track": "a1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"}]}
    words = [{"w": "안녕", "s": 0.1, "e": 0.5}, {"w": "음", "s": 1.0, "e": 1.4}, {"w": "하세요", "s": 1.5, "e": 2.0}]
    r = c.post("/api/chat-edit/plan", json={"project": p, "text": "'음' 다 지워", "words": words})
    assert r.status_code == 200, r.text
    res = wait(c, r.json()["job"])
    assert res["status"] == "done", res
    plan = res["result"]
    assert plan["reply"] == "음 지울게요" and plan["length_after"] < plan["length_before"]
    a = c.post("/api/chat-edit/apply", json={"project": p, "ops": plan["ops"], "words": words})
    assert a.status_code == 200, a.text
    out = a.json()
    assert out["length_after"] == pytest.approx(3 - 0.44, abs=0.2) and len([x for x in out["project"]["clips"] if x["track"] == "v1"]) == 2
    assert c.post("/api/chat-edit/apply", json={"project": p, "ops": [{"op": "cut"}]}).status_code == 400
    # 키·로그인이 없으면 안내
    monkeypatch.setattr(claude_copy, "cli_path", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert c.post("/api/chat-edit/plan", json={"project": p, "text": "앞 3초 잘라"}).status_code == 503


# ---------------------------------------------------------------- 샘플 템플릿

def test_builtin_templates_listed_and_protected(api):
    c, tmp, ws = api
    items = [t for t in c.get("/api/templates").json() if t["builtin"]]
    assert [t["name"] for t in items] == ["예능형", "정보형", "감성형", "뉴스형", "미니멀"]
    assert all(t["subtitle_style"] and t["hook"] and t["watermark"] and t["ending_card"] for t in items)
    assert c.delete(f"/api/templates/{items[0]['id']}").status_code == 400
    saved = c.post("/api/templates", json={**items[0], "name": "내 예능"}).json()       # 고쳐 저장하면 내 템플릿
    assert not saved["builtin"] and not saved["id"].startswith("builtin-")


@pytest.mark.parametrize("tid", ["builtin-variety", "builtin-news", "builtin-minimal"])
def test_apply_builtin_template_one_click_and_export(api, tid):
    c, tmp, ws = api
    m = upload(c, make_video(tmp / "v.mp4", 3, size="270x480"))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"},
                                       {"id": "c1a", "track": "a1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"}],
         "subtitles": [{"id": "s", "start": 0.5, "end": 1.5, "text": "안녕"}]}
    r = c.post("/api/template/apply", json={"project": p, "template_id": tid, "channel": "하루", "hook_text": ""})
    assert r.status_code == 200, r.text
    a = r.json()
    tags = {x["tpl"] for x in a["clips"] if x["tpl"]}
    assert "ending" in tags
    assert any(o["kind"] == "title" and o["text"] == "여기에 훅 제목" for o in a["overlays"])        # 훅 제목 자리 만들기
    assert any(o["kind"] == "watermark" and "하루" in o["text"] for o in a["overlays"]) or tid == "builtin-news"
    wm = [o["text"] for o in a["overlays"] if o["kind"] == "watermark"][0]
    assert "{channel}" not in wm and "하루" in wm
    a2 = c.post("/api/template/apply", json={"project": a, "template_id": tid, "channel": "하루"}).json()     # 다시 입혀도 안 쌓임
    assert len(a2["clips"]) == len(a["clips"]) and len(a2["overlays"]) == len(a["overlays"])
    ex = c.post("/api/export", json=a2)
    assert ex.status_code == 200, ex.text
    assert probe(ws.root / "exports" / ex.json()["file"])["duration"] > 5


def test_batch_apply_builtin_template_to_media(api):
    c, tmp, ws = api
    vids = [upload(c, make_video(tmp / f"v{i}.mp4", 2, size="270x480")) for i in range(2)]
    r = c.post("/api/template/batch", json={"template_id": "builtin-info", "media_ids": [v["id"] for v in vids], "channel": "채널"})
    res = wait(c, r.json()["job"], 120)
    assert res["status"] == "done" and all(x["ok"] for x in res["result"]["results"]), res


def test_cue_color_reaches_ass_and_shorts_shift():
    from video_editor.shorts import shift_cues
    from video_editor.subtitles import Cue, SubtitleStyle, to_ass
    cues = [Cue(id="a", start=0, end=2, text="하나"), Cue(id="b", start=2, end=4, text="둘", color="#ffff00")]
    ass = to_ass(cues, SubtitleStyle(), 1080, 1920)
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert "\\c&H00FFFF&" in lines[1] and "\\c&H" not in lines[0]
    assert shift_cues(cues, 1, 4)[1].color == "#ffff00"
    # 낱말 강조 자막은 자체 색(강조 색)을 쓰므로 글자색 덮어쓰기를 넣지 않아요
    assert "\\c&H00FFFF&" not in to_ass(cues, SubtitleStyle(karaoke=True), 1080, 1920)
    bad = Cue(id="c", start=0, end=1, text="x", color="red")
    assert "\\c&H" not in to_ass([bad], SubtitleStyle(), 1080, 1920)
