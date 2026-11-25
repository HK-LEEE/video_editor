"""부드러운 인트로 · 영상 중간 '좋아요 톡톡' 정지 화면."""
import pytest

from tests.test_editor import make_video, tracks, upload
from tests.test_shorts_tools import api, wait  # noqa: F401  (fixture)
from video_editor import endings
from video_editor.editor import probe


def project(c, tmp, seconds=6, subs=None):
    m = upload(c, make_video(tmp / "v.mp4", seconds, size="270x480"))
    return m, {"tracks": tracks(), "clips": [
        {"id": "c1", "track": "v1", "media": m["id"], "src_in": 0, "src_out": seconds, "start": 0, "link": "L"},
        {"id": "c1a", "track": "a1", "media": m["id"], "src_in": 0, "src_out": seconds, "start": 0, "link": "L"}],
        "subtitles": subs or [], "overlays": [{"id": "wm", "kind": "watermark", "text": "워터마크", "start": 0}]}


def length(p):
    return max(x["start"] + (x["src_out"] - x["src_in"]) / x.get("speed", 1) for x in p["clips"])


def test_intro_kinds_listed_apart_from_endings(api):
    c, tmp, ws = api
    r = c.get("/api/endings").json()
    assert set(r["intro_kinds"]) == {"soft_open_logo", "soft_open_card", "soft_open_hearts", "soft_open_text"} == set(r["intro"])
    assert not set(r["intro_kinds"]) & set(r["kinds"])
    assert r["like"]["text"]


@pytest.mark.parametrize("kind", sorted(endings.INTRO_KINDS))
def test_soft_intro_previews(api, kind):
    c, tmp, ws = api
    prev = wait(c, c.post("/api/endings/preview", json={"name": "p", "kind": kind, "duration": 1.5}).json()["job"])
    assert prev["status"] == "done" and c.get(prev["result"]["url"]).status_code == 200


def test_apply_intro_goes_to_front_and_replaces_itself(api):
    c, tmp, ws = api
    m, p = project(c, tmp, 3, [{"id": "s", "start": 0.5, "end": 1.5, "text": "안녕"}])
    e = c.post("/api/endings", json={"name": "크림", "kind": "soft_open_card", "duration": 1.5}).json()
    a = c.post("/api/endings/apply", json={"project": p, "ending_id": e["id"]}).json()
    intro = [x for x in a["clips"] if x["tpl"] == "intro"]
    assert len(intro) >= 1 and all(x["start"] == 0 for x in intro)
    main = next(x for x in a["clips"] if x["id"] == "c1")
    assert main["start"] == pytest.approx(1.5) and a["subtitles"][0]["start"] == pytest.approx(2.0)
    a2 = c.post("/api/endings/apply", json={"project": a, "ending_id": e["id"]}).json()      # 다시 붙여도 안 쌓임
    assert main["start"] == pytest.approx(next(x for x in a2["clips"] if x["id"] == "c1")["start"])
    assert len(a2["clips"]) == len(a["clips"])


def test_like_pause_splits_shifts_and_removes(api):
    c, tmp, ws = api
    m, p = project(c, tmp, 6, [{"id": "s1", "start": 0.5, "end": 1.5, "text": "앞"}, {"id": "s2", "start": 3.5, "end": 5.0, "text": "뒤"}])
    r = c.post("/api/likepause/apply", json={"project": p, "at": 3.0, "spec": {"duration": 2.0}})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["at"] == pytest.approx(3.0)
    pause = [x for x in a["clips"] if x["tpl"] == "likepause"]
    assert pause and all(x["start"] == pytest.approx(3.0) for x in pause)
    assert length(a) == pytest.approx(6 + 2.0, abs=0.1)
    v = sorted([x for x in a["clips"] if x["track"] == "v1" and not x["tpl"]], key=lambda x: x["start"])
    assert [round(x["start"], 2) for x in v] == [0, 5.0] and v[0]["src_out"] == pytest.approx(3.0) and v[1]["src_in"] == pytest.approx(3.0)
    au = sorted([x for x in a["clips"] if x["track"] == "a1" and not x["tpl"]], key=lambda x: x["start"])
    assert [round(x["start"], 2) for x in au] == [0, 5.0] and au[1]["src_in"] == pytest.approx(3.0)
    assert v[1]["link"] == au[1]["link"] != v[0]["link"]                      # 뒤쪽 영상·소리는 새로 묶임
    assert [round(s["start"], 2) for s in a["subtitles"]] == [0.5, 5.5]        # 뒤 자막은 멈춘 길이만큼 밀림
    assert next(o for o in a["overlays"] if o["kind"] == "watermark").get("end") is None
    ex = c.post("/api/export", json=a)
    assert ex.status_code == 200, ex.text
    assert probe(ws.root / "exports" / ex.json()["file"])["duration"] == pytest.approx(8.0, abs=0.4)
    # 빼면 원래 길이·자막 위치로
    pid = pause[0]["id"]
    b = c.post("/api/likepause/remove", json={"project": a, "clip_id": pid}).json()
    assert not [x for x in b["clips"] if x["tpl"]] and length(b) == pytest.approx(6.0, abs=0.1)
    assert [round(s["start"], 2) for s in b["subtitles"]] == [0.5, 3.5]
    assert c.post("/api/likepause/remove", json={"project": b}).status_code == 404


def test_like_pause_snaps_out_of_speech_and_rejects_bad_spots(api):
    c, tmp, ws = api
    m, p = project(c, tmp, 6, [{"id": "s", "start": 2.0, "end": 4.0, "text": "길게 말하는 중"}])
    a = c.post("/api/likepause/apply", json={"project": p, "at": 2.3}).json()
    assert a["at"] == pytest.approx(2.0)                                       # 말 시작 앞으로 옮겨짐
    sub = a["subtitles"][0]
    assert sub["start"] == pytest.approx(2.0 + 2.4) and sub["end"] == pytest.approx(4.0 + 2.4)
    exact = c.post("/api/likepause/apply", json={"project": p, "at": 3.0, "snap": False}).json()
    assert exact["at"] == pytest.approx(3.0)
    assert exact["subtitles"][0]["start"] == pytest.approx(2.0) and exact["subtitles"][0]["end"] == pytest.approx(4.0 + 2.4)   # 말 도중이면 끝만 늘어남
    assert c.post("/api/likepause/apply", json={"project": p, "at": 99}).status_code in (200, 400)
    empty = {"tracks": tracks(), "clips": []}
    assert c.post("/api/likepause/apply", json={"project": empty, "at": 1}).status_code == 400
    prev = wait(c, c.post("/api/likepause/preview", json={}).json()["job"])
    assert prev["status"] == "done" and c.get(prev["result"]["url"]).status_code == 200


def test_template_with_like_pause_and_soft_intro_is_idempotent(api):
    c, tmp, ws = api
    m, p = project(c, tmp, 8, [{"id": "s", "start": 0.5, "end": 1.5, "text": "안녕"}])
    a = c.post("/api/template/apply", json={"project": p, "template_id": "builtin-emotional", "channel": "하루"})
    assert a.status_code == 200, a.text
    a = a.json()
    tags = sorted(x["tpl"] for x in a["clips"] if x["track"] == "v1" and x["tpl"])
    assert tags == ["ending", "intro", "likepause", "outro"]
    pause = next(x for x in a["clips"] if x["tpl"] == "likepause" and x["track"] == "v1")
    intro = next(x for x in a["clips"] if x["tpl"] == "intro" and x["track"] == "v1")
    assert pause["start"] == pytest.approx(intro["src_out"] + 4.0, abs=1.0)     # 본 영상 가운데쯤
    a2 = c.post("/api/template/apply", json={"project": a, "template_id": "builtin-emotional", "channel": "하루"}).json()
    assert len(a2["clips"]) == len(a["clips"]) and length(a2) == pytest.approx(length(a), abs=0.05)
    ex = c.post("/api/export", json=a2)
    assert ex.status_code == 200, ex.text
    # 템플릿에 정지 화면이 없으면 직접 넣은 것은 그대로 남음
    plain = c.post("/api/template/apply", json={"project": a, "template_id": "builtin-news"}).json()
    assert any(x["tpl"] == "likepause" for x in plain["clips"])
