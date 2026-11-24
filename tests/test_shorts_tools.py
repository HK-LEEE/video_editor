import subprocess
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_editor import make_video, tracks, upload
from video_editor import beats, claude_copy, overlays, synth
from video_editor.editor import Clip, Project, Track, Workspace, make_router, probe
from video_editor.engine import SAMPLE_RATE as SR


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    ws = Workspace(tmp_path / "ws")
    app.include_router(make_router(ws))
    return TestClient(app), tmp_path, ws


def wait(c, job, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/ai/jobs/{job}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("작업이 안 끝났어요")


def click_track(bpm, seconds=20, offset=0.0):
    x = np.zeros(int(SR * seconds), dtype=np.float32)
    step = 60 / bpm
    t = offset
    while t < seconds - 0.1:
        i = int(t * SR)
        x[i:i + 2000] += synth.kick(2000 / SR)[:2000] if False else (np.sin(2 * np.pi * 120 * np.arange(2000) / SR) * np.exp(-np.arange(2000) / 400)).astype(np.float32)
        t += step
    return x


# ---------------------------------------------------------------- 합성 소리 · 보관함

def test_every_synth_sound_is_valid():
    for s in synth.SFX:
        x = s.fn()
        assert x.size > SR * 0.1 and np.isfinite(x).all() and 0.3 < np.abs(x).max() <= 0.81, s.key
    for b in synth.BGM[:2]:
        x = b.fn()
        assert x.size > SR * 10 and np.isfinite(x).all()


def test_library_ensure_catalog_and_user_upload(api):
    c, tmp, ws = api
    items = c.get("/api/library").json()
    assert sum(1 for i in items if i["kind"] == "sfx") == len(synth.SFX) and not any(i["ready"] for i in items)
    r = c.post("/api/library/ensure").json()
    assert all(i["ready"] for i in r["items"])
    m = {x["key"]: x for x in r["media"]}["sfx_pop"]
    assert m["lib"] == "sfx" and m["has_audio"] and not m["has_video"]
    assert c.get(f"/api/media/{m['id']}/file").status_code == 200
    # 한 번 더 불러도 같은 id (다시 만들지 않음)
    assert {x["id"] for x in c.post("/api/library/ensure").json()["media"]} == {x["id"] for x in r["media"]}
    # 내가 올린 소리
    wav = tmp / "mine.wav"
    sf.write(wav, click_track(100, 6), SR)
    r = c.post("/api/library/upload", files={"file": ("mine.wav", wav.read_bytes())}, data={"kind": "bgm", "name": "내 곡", "tags": "신남,#브이로그"})
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["lib"] == "bgm" and mine["user"] and mine["tags"] == ["신남", "브이로그"] and 90 <= mine["bpm"] <= 110
    assert any(i["id"] == mine["id"] and i["user"] for i in c.get("/api/library").json())
    assert c.delete(f"/api/library/{r.json()['id']}").status_code == 200
    assert c.delete(f"/api/library/{m['id']}").status_code == 400     # 기본 소리는 못 지움


# ---------------------------------------------------------------- 박자

@pytest.mark.parametrize("bpm", [90, 120, 140])
def test_detects_click_track_tempo(bpm):
    r = beats.detect(click_track(bpm, 24, 0.3), SR, hint_bpm=bpm)
    assert abs(r["bpm"] - bpm) < bpm * 0.03
    assert len(r["beats"]) >= 24 * bpm / 60 - 3
    d = np.diff(r["beats"])
    assert abs(d.mean() - 60 / bpm) < 0.01 and d.std() < 0.02


def test_detect_without_hint_and_silence():
    assert abs(beats.detect(click_track(100, 24), SR)["bpm"] - 100) < 4
    assert beats.detect(np.zeros(SR * 5, dtype=np.float32), SR)["beats"] == []


def _proj(clips, subs=()):
    return Project(tracks=[Track(id="v1", kind="video"), Track(id="a1", kind="audio")], clips=clips, subtitles=list(subs))


def test_snap_cuts_lands_on_beats_and_ripples():
    grid = [i * 0.5 for i in range(40)]     # 120BPM
    p = _proj([Clip(id="c1", track="v1", media="m", src_in=0, src_out=2.2, start=0, link="L"),
               Clip(id="c1a", track="a1", media="m", src_in=0, src_out=2.2, start=0, link="L"),
               Clip(id="c2", track="v1", media="m", src_in=0, src_out=3.1, start=2.2)])
    out = beats.snap_cuts(p, grid, {"m": 10.0})
    by = {c.id: c for c in out.clips}
    ends = [by["c1"].start + (by["c1"].src_out - by["c1"].src_in), by["c2"].start + (by["c2"].src_out - by["c2"].src_in)]
    assert all(abs(e / 0.5 - round(e / 0.5)) < 1e-3 for e in ends)
    assert by["c2"].start == pytest.approx(ends[0])                    # 뒤 클립이 이어 붙음
    assert by["c1a"].src_out == by["c1"].src_out and by["c1a"].start == by["c1"].start   # 연결된 소리도 같이
    assert p.clips[0].src_out == 2.2                                    # 원본 프로젝트는 그대로


def test_snap_does_not_extend_past_source_and_moves_subtitles():
    from video_editor.subtitles import Cue
    grid = [i * 0.5 for i in range(40)]
    p = _proj([Clip(id="c1", track="v1", media="m", src_in=0, src_out=1.9, start=0),
               Clip(id="c2", track="v1", media="m", src_in=0, src_out=2.0, start=1.9)],
              [Cue(id="s", start=2.0, end=2.8, text="안녕")])
    out = beats.snap_cuts(p, grid, {"m": 1.9})       # 원본이 이미 끝까지 쓰이고 있음 → 늘릴 수 없음
    assert out.clips[0].src_out <= 1.9 + 1e-6
    cue = out.subtitles[0]
    assert cue.start >= 1.5 and cue.end > cue.start


def test_split_on_beats_every_n_with_transition():
    grid = [i * 0.5 for i in range(40)]
    p = _proj([Clip(id="c1", track="v1", media="m", src_in=1.0, src_out=9.0, start=0, link="L"),
               Clip(id="a1", track="a1", media="m", src_in=1.0, src_out=9.0, start=0, link="L")])
    out = beats.split_on_beats(p, grid, {"c1"}, every=4, transition="fade", transition_dur=0.2)
    vids = sorted([c for c in out.clips if c.track == "v1"], key=lambda c: c.start)
    assert [round(c.start, 2) for c in vids] == [0, 2, 4, 6]
    assert all(abs((c.src_out - c.src_in) - 2) < 1e-3 for c in vids)
    assert [c.transition for c in vids] == ["none", "fade", "fade", "fade"]
    assert all(c.src_in == pytest.approx(1.0 + c.start) for c in vids)
    assert len({c.link for c in out.clips}) == 4                       # 조각마다 영상+소리가 따로 연결
    with pytest.raises(ValueError):
        beats.split_on_beats(p, [0.0], {"c1"})


def test_beats_endpoints(api):
    c, tmp, ws = api
    wav = tmp / "b.wav"
    sf.write(wav, click_track(120, 12), SR)
    m = upload(c, wav)
    r = c.post("/api/beats", json={"media": m["id"], "bpm": 120}).json()
    assert abs(r["bpm"] - 120) < 3 and len(r["beats"]) > 15
    v = upload(c, make_video(tmp / "v.mp4", 6))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": v["id"], "src_in": 0, "src_out": 5.7, "start": 0, "link": "L"}]}
    plan = c.post("/api/beats/plan", json={"project": p, "beats": r["beats"], "mode": "split", "ids": ["c1"], "every": 2}).json()
    assert len(plan["clips"]) >= 3
    assert c.post("/api/beats/plan", json={"project": p, "beats": r["beats"], "mode": "split"}).status_code == 400
    assert c.post("/api/beats/plan", json={"project": p, "beats": [1.0], "mode": "snap"}).status_code == 400


# ---------------------------------------------------------------- 글자 오버레이와 내보내기

def test_overlay_ass_window_and_safe_area():
    o = overlays.Overlay(id="t", kind="title", text="이거 보면 놀람", start=0, end=3, y=0.02, bg_opacity=0.9, bg_color="#e11d48")
    ass = overlays.to_ass([o], 1080, 1920, 20, safe={"left": .05, "right": .84, "top": .1, "bottom": .78})
    assert "Dialogue" in ass and "Style: B0" in ass
    y = int(ass.split("\\pos(")[1].split(")")[0].split(",")[1])
    assert y >= 0.1 * 1920                                              # 안전 영역 위로 올라가지 않음
    assert overlays.to_ass([o], 1080, 1920, 20, t0=5) is None            # 구간 밖이면 없음
    w = overlays.Overlay(id="w", kind="watermark", text="@me", opacity=0.5)
    assert overlays.to_ass([w], 100, 100, 5)


def test_export_burns_overlays_full_and_short(api):
    c, tmp, ws = api
    m = upload(c, make_video(tmp / "v.mp4", 4, size="320x568"))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": 4, "start": 0}],
         "overlays": [{"id": "t", "kind": "title", "text": "TITLE", "start": 0, "end": 2, "y": 0.2, "size": 0.1, "bg_opacity": 1, "bg_color": "#00ff00"}]}
    r = c.post("/api/export", json=p)
    assert r.status_code == 200, r.text
    out = ws.root / "exports" / r.json()["file"]
    import subprocess
    def avg_green(t):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf", "scale=32:56", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        return int(((a[:, 1] > 200) & (a[:, 0] < 80)).sum())
    assert avg_green(0.5) > 20 and avg_green(3.0) == 0                  # 0~2초에만 초록 제목 상자
    r = c.post("/api/export/short", json={"project": p, "spec": {"format": "shorts"}})
    assert r.status_code == 200, r.text
    assert probe(ws.root / "exports" / r.json()["file"])["width"] == 1080


# ---------------------------------------------------------------- Claude 문구

FAKE = {"hook_titles": [{"text": "  이거 모르면   손해  ", "why": "궁금증"}], "thumbnail": {"text": "충격", "idea": "표정", "best_time": 2.5},
        "youtube": {"title": "제목", "description": "설명", "hashtags": ["Shorts", "#고양이", " 귀여움 "]},
        "instagram": {"caption": "캡션", "hashtags": ["#릴스"]}}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(FAKE))])


def fake_cli(stdout="", code=0, stderr=""):
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    run.calls = calls
    return run


def test_claude_copy_headless_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-0000")
    monkeypatch.setattr(claude_copy, "cli_path", lambda: "/usr/bin/claude")
    cues = [{"start": 0, "text": "안녕하세요"}]
    # 구조화 출력 / 코드블록 안의 JSON 모두 읽습니다
    for out in (json.dumps({"result": "", "structured_output": FAKE}), json.dumps({"result": "```json\n" + json.dumps(FAKE) + "\n```"})):
        run = fake_cli(out)
        d = claude_copy.generate(tmp_path, cues, 20, runner=run)
        args, kw = run.calls[0]
        assert args[:2] == ["/usr/bin/claude", "-p"] and "claude-sonnet-5-5" in args and "안녕하세요" in kw["input"]
        assert "ANTHROPIC_API_KEY" not in kw["env"] and d["hook_titles"][0]["text"] == "이거 모르면 손해"
    # 오래된 CLI는 옵션을 모르면 최소 옵션으로 다시
    class Old:
        n = 0
        def __call__(self, args, **kw):
            self.n += 1
            return SimpleNamespace(returncode=1, stdout="", stderr="error: unknown option '--tools'") if "--tools" in args else \
                SimpleNamespace(returncode=0, stdout=json.dumps({"result": json.dumps(FAKE)}), stderr="")
    old = Old()
    claude_copy.generate(tmp_path, cues, runner=old)
    assert old.n == 2
    # 로그인 안 됨 / 한도 / 시간 초과 / 이상한 답
    with pytest.raises(RuntimeError, match="로그인"):
        claude_copy.generate(tmp_path, cues, runner=fake_cli(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), 1))
    with pytest.raises(RuntimeError, match="한도"):
        claude_copy.generate(tmp_path, cues, runner=fake_cli("", 1, "Claude usage limit reached"))
    with pytest.raises(RuntimeError, match="읽지 못"):
        claude_copy.generate(tmp_path, cues, runner=fake_cli(json.dumps({"result": "음…"})))

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)
    with pytest.raises(RuntimeError, match="답하지 않았"):
        claude_copy.generate(tmp_path, cues, runner=slow)


def test_claude_copy_cli_missing_and_fallback(tmp_path, monkeypatch, api):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(claude_copy, "cli_path", lambda: None)
    with pytest.raises(RuntimeError, match="claude"):
        claude_copy.generate(tmp_path, [{"start": 0, "text": "x"}])
    c, _, ws = api
    r = c.post("/api/ai/copy", json={"cues": [{"start": 0, "text": "안녕"}]})
    assert r.status_code == 503 and "Claude Code" in r.json()["detail"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop1234")     # CLI가 없고 키가 있으면 API로
    assert claude_copy.engine_for(ws.root) == "api" and c.get("/api/settings").json()["ready"]
    assert c.put("/api/settings", json={"engine": "gpt"}).status_code == 400


def test_claude_copy_request_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fc = FakeClient()
    out = claude_copy.generate(tmp_path, [{"start": 1, "text": "안녕하세요"}, {"start": 0, "text": "시작"}], 30, "고양이 채널", client=fc)
    kw = fc.calls[0]
    assert kw["model"] == "claude-sonnet-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw and "temperature" not in kw                  # Sonnet 5.5에서 400이 나는 항목은 안 보냄
    body = kw["messages"][0]["content"]
    assert body.index("시작") < body.index("안녕하세요") and "고양이 채널" in body
    assert out["hook_titles"][0]["text"] == "이거 모르면 손해"
    assert out["youtube"]["hashtags"] == ["#Shorts", "#고양이", "#귀여움"]
    out = claude_copy.generate(tmp_path, [{"start": 0, "text": "x"}], model="claude-opus-5-5", client=fc)
    assert fc.calls[1]["model"] == "claude-opus-5-5" and out["model"] == "claude-opus-5-5"
    with pytest.raises(RuntimeError, match="대본이 비어"):
        claude_copy.generate(tmp_path, [], client=fc)


def test_settings_never_expose_key(api, monkeypatch):
    c, tmp, ws = api
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = c.get("/api/settings").json()
    assert not s["has_key"] and "ANTHROPIC_API_KEY" in s["no_key_help"]
    c.put("/api/settings", json={"engine": "api"})
    key = "sk-ant-api03-abcdefghijklmnop1234"
    s = c.put("/api/settings", json={"api_key": key, "model": "claude-opus-5-5"}).json()
    assert s["has_key"] and s["model"] == "claude-opus-5-5" and key not in json.dumps(s)
    assert key not in json.dumps(c.get("/api/settings").json())
    assert c.put("/api/settings", json={"model": "gpt"}).status_code == 400
    assert not c.put("/api/settings", json={"api_key": ""}).json()["has_key"]
    # 키가 없으면 분명한 안내
    r = c.post("/api/ai/copy", json={"cues": [{"start": 0, "text": "안녕"}]})
    assert r.status_code == 503 and "API 키" in r.json()["detail"]
    c.put("/api/settings", json={"engine": "cli"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert c.get("/api/settings").json()["source"] == "env"


def test_thumbnail(api):
    c, tmp, ws = api
    m = upload(c, make_video(tmp / "v.mp4", 3, color="blue", size="640x360"))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0}]}
    r = c.post("/api/thumbnail", json={"project": p, "t": 1.0, "text": "충격 반전"})
    assert r.status_code == 200, r.text
    png = c.get(r.json()["url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert c.post("/api/thumbnail", json={"project": p, "t": 9.0, "text": "x"}).status_code == 400


# ---------------------------------------------------------------- 템플릿 · 엔딩 · 일괄 적용

def _setup_template(c, tmp):
    intro = upload(c, make_video(tmp / "intro.mp4", 1, color="green", size="270x480"))
    outro = upload(c, make_video(tmp / "outro.mp4", 1, color="yellow", size="270x480", audio=False))
    end = c.post("/api/endings", json={"name": "구독 눌림", "kind": "heart", "duration": 1.5}).json()
    tpl = c.post("/api/templates", json={
        "name": "내 채널", "intro": intro["id"], "outro": outro["id"], "ending": end["id"],
        "subtitle_style": {"size": 0.08, "color": "#ffff00"}, "hook": {"y": 0.2, "bg_color": "#112233"},
        "watermark": {"text": "@my_channel"}}).json()
    return tpl, intro, outro


def test_apply_template_is_idempotent(api):
    c, tmp, ws = api
    tpl, intro, outro = _setup_template(c, tmp)
    m = upload(c, make_video(tmp / "v.mp4", 3, size="270x480"))
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"},
                                       {"id": "c1a", "track": "a1", "media": m["id"], "src_in": 0, "src_out": 3, "start": 0, "link": "L"}],
         "subtitles": [{"id": "s", "start": 0.5, "end": 1.5, "text": "안녕"}],
         "overlays": [{"id": "h", "kind": "title", "text": "훅", "start": 0, "end": 3}]}
    r1 = c.post("/api/template/apply", json={"project": p, "template_id": tpl["id"]})
    assert r1.status_code == 200, r1.text
    a = r1.json()
    tags = sorted(((x["tpl"], x["start"]) for x in a["clips"] if x["tpl"] and x["track"] == "v1"), key=lambda z: z[1])
    assert [t for t, _ in tags] == ["intro", "outro", "ending"]
    assert tags[0][1] == 0 and tags[1][1] == pytest.approx(4.0) and tags[2][1] == pytest.approx(5.0)
    main = next(x for x in a["clips"] if x["id"] == "c1")
    assert main["start"] == pytest.approx(1.0)                            # 인트로 길이만큼 밀림
    assert a["subtitles"][0]["start"] == pytest.approx(1.5)               # 자막도 같이
    assert a["subtitle_style"]["color"] == "#ffff00"
    hook = next(o for o in a["overlays"] if o["kind"] == "title")
    assert hook["y"] == 0.2 and hook["bg_color"] == "#112233" and hook["start"] == pytest.approx(1.0)
    assert [o["text"] for o in a["overlays"] if o["kind"] == "watermark"] == ["@my_channel"]
    # 같은 템플릿을 또 입혀도 쌓이지 않음
    a2 = c.post("/api/template/apply", json={"project": a, "template_id": tpl["id"]}).json()
    assert len(a2["clips"]) == len(a["clips"]) and len(a2["overlays"]) == len(a["overlays"])
    assert next(x for x in a2["clips"] if x["id"] == "c1")["start"] == pytest.approx(1.0)
    # 내보내면 길이가 인트로+본편+아웃트로+엔딩
    r = c.post("/api/export", json=a2)
    assert r.status_code == 200, r.text
    assert probe(ws.root / "exports" / r.json()["file"])["duration"] == pytest.approx(6.5, abs=0.3)
    # 엔딩 영상은 보관함 목록에서 숨김 표시
    assert any(x.get("hidden") for x in c.get("/api/media").json())


def test_batch_apply_to_saved_projects_and_media_then_export(api):
    c, tmp, ws = api
    tpl, intro, outro = _setup_template(c, tmp)
    vids = [upload(c, make_video(tmp / f"v{i}.mp4", 2, size="270x480")) for i in range(2)]
    base = {"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": vids[0]["id"], "src_in": 0, "src_out": 2, "start": 0}]}
    saved = c.post("/api/projects", json={"name": "첫 번째", "project": base}).json()
    assert c.get("/api/projects").json()[0]["name"] == "첫 번째"
    r = c.post("/api/template/batch", json={"template_id": tpl["id"], "project_ids": [saved["id"]], "media_ids": [v["id"] for v in vids], "name_prefix": "쇼츠 "})
    assert r.status_code == 200, r.text
    res = wait(c, r.json()["job"])
    assert res["status"] == "done", res
    results = res["result"]["results"]
    assert len(results) == 3 and all(x["ok"] for x in results), results
    listing = c.get("/api/projects").json()
    assert len(listing) == 3 and sum(1 for x in listing if x["name"].startswith("쇼츠 ")) == 2
    assert all(x["length"] > 4 for x in listing)
    proj = c.get(f"/api/projects/{saved['id']}").json()["project"]
    assert any(x["tpl"] == "intro" for x in proj["clips"]) and proj["overlays"]
    ex = wait(c, c.post("/api/batch/export", json={"project_ids": [x["id"] for x in listing], "format": "shorts"}).json()["job"], 120)
    assert ex["status"] == "done" and all(x["ok"] for x in ex["result"]["results"]), ex
    assert c.post("/api/template/batch", json={"template_id": "nope", "project_ids": [saved["id"]]}).status_code == 404
    c.delete(f"/api/projects/{saved['id']}")
    assert len(c.get("/api/projects").json()) == 2


def test_endings_and_templates_crud(api):
    c, tmp, ws = api
    kinds = c.get("/api/endings").json()
    assert set(kinds["kinds"]) == {"subscribe", "heart", "bell", "thanks", "soft_logo", "soft_card", "soft_hearts", "soft_text"}
    assert set(kinds["soft"]) == {"soft_logo", "soft_card", "soft_hearts", "soft_text"}
    assert c.post("/api/endings", json={"name": "x", "kind": "nope"}).status_code == 400
    e = c.post("/api/endings", json={"name": "하트", "kind": "heart", "text": "좋아요"}).json()
    assert c.get("/api/endings").json()["items"][0]["text"] == "좋아요"
    prev = wait(c, c.post("/api/endings/preview", json={"name": "p", "kind": "bell", "duration": 1.5}).json()["job"])
    assert prev["status"] == "done" and c.get(prev["result"]["url"]).status_code == 200
    assert c.post("/api/templates", json={"name": "t", "ending": "zzz"}).status_code == 400
    t = c.post("/api/templates", json={"name": "t", "ending": e["id"]}).json()
    assert [x["id"] for x in c.get("/api/templates").json() if not x["builtin"]] == [t["id"]]
    c.delete(f"/api/templates/{t['id']}")
    assert [x for x in c.get("/api/templates").json() if not x["builtin"]] == []


def test_soft_ending_with_image(api):
    """부드러운 엔딩: 이미지를 올려 미리보기·프리셋 저장·영상에 붙이기까지."""
    import io
    from PIL import Image
    c, tmp, ws = api
    buf = io.BytesIO(); Image.new("RGB", (300, 200), "#f0a070").save(buf, "PNG")
    assert c.post("/api/endings/image", files={"file": ("a.txt", b"nope")}).status_code == 400
    up = c.post("/api/endings/image", files={"file": ("logo.png", buf.getvalue(), "image/png")}).json()
    assert c.get(up["url"]).status_code == 200
    for kind in ("soft_logo", "soft_card", "soft_hearts", "soft_text"):
        prev = wait(c, c.post("/api/endings/preview", json={"name": "p", "kind": kind, "duration": 1.5, "image": up["image"]}).json()["job"])
        assert prev["status"] == "done" and c.get(prev["result"]["url"]).status_code == 200
    # 이미지 없이도 만들어져요
    prev = wait(c, c.post("/api/endings/preview", json={"name": "p", "kind": "soft_logo", "duration": 1.5}).json()["job"])
    assert prev["status"] == "done"
