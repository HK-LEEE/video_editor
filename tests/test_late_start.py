"""첫 영상 클립이 0초가 아닌 곳에서 시작해도 내보내기가 멈추지 않고, 앞은 검은 화면·무음으로 채워요."""
import pytest

from tests.test_editor import api, decode, frame_rgb, make_video, rms, tracks, upload  # noqa: F401
from video_editor.editor import probe


@pytest.mark.parametrize("start", [0.7167, 1.0])
def test_export_single_clip_starting_late(api, start):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 2, "red", 500))
    clips = [{"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 2, "start": start},
             {"id": "c2", "track": "a1", "media": red["id"], "src_in": 0, "src_out": 2, "start": start}]
    r = c.post("/api/export", json={"tracks": tracks(), "clips": clips})
    assert r.status_code == 200, r.text
    mp4 = tmp / "o.mp4"; mp4.write_bytes(c.get(r.json()["url"]).content)
    assert probe(mp4)["duration"] == pytest.approx(start + 2, abs=0.2)
    assert frame_rgb(mp4, start / 2).max() < 30                       # 앞은 검정
    assert frame_rgb(mp4, start + 1.0)[0] > 150                        # 클립 구간은 빨강
    assert rms(decode(mp4), 0.0, start - 0.1) < 0.003                  # 앞은 무음


def test_export_timeout_is_reported(api, monkeypatch):
    from video_editor import editor
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 2, "red", 500))
    monkeypatch.setattr(editor, "EXPORT_TIMEOUT", lambda s: 0.001)
    r = c.post("/api/export", json={"tracks": tracks(), "clips": [{"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 2, "start": 0}]})
    assert r.status_code == 500 and "너무 오래 걸려" in r.json()["detail"]
    assert not list((tmp / "ws").rglob("video_editor_*.mp4"))
