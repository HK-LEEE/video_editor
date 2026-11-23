import subprocess

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from video_editor.editor import Workspace, make_router, probe
from video_editor.engine import SAMPLE_RATE as SR


@pytest.fixture()
def api(tmp_path):
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def make_video(path, seconds=3, color="red", tone=1000, audio=True, size="320x240"):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tone}:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def make_voice(path, seconds=1.0):
    t = np.arange(int(SR * seconds)) / SR
    x = sum(np.sin(k * 2 * np.pi * 140 * t) / k for k in range(1, 20))
    sf.write(path, (0.3 * x / np.abs(x).max()).astype(np.float32), SR)
    return path


def upload(c, path, **form):
    r = c.post("/api/media", files={"file": (path.name, path.read_bytes())}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def tracks(**over):
    base = [
        {"id": "v1", "kind": "video"}, {"id": "v2", "kind": "video"},
        {"id": "a1", "kind": "audio"}, {"id": "a2", "kind": "audio", "voice": True},
    ]
    for t in base:
        t.update(over.get(t["id"], {}))
    return base


def decode(path, video_frame_at=None):
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(pcm, np.float32)


def frame_rgb(path, t):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=8:8", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)


def rms(a, s, e):
    return float(np.sqrt(np.mean(a[int(s * SR):int(e * SR)] ** 2)))


def test_media_upload_kinds(api):
    c, tmp = api
    v = upload(c, make_video(tmp / "a.mp4"))
    assert v["kind"] == "video" and v["has_audio"] and v["width"] == 320
    a = upload(c, make_voice(tmp / "v.wav"))
    assert a["kind"] == "audio" and not a["has_video"]
    rec = upload(c, make_voice(tmp / "r.wav"), recorded="true", name="녹음 1")
    assert rec["recorded"] and rec["name"] == "녹음 1" and rec["file"].endswith(".wav")
    assert {m["id"] for m in c.get("/api/media").json()} == {v["id"], a["id"], rec["id"]}
    pk = c.get(f"/api/media/{a['id']}/peaks", params={"voice": "robot"}).json()
    assert pk["rate"] == 100 and len(pk["peaks"]) == pytest.approx(100, abs=2)
    bad = tmp / "x.txt"; bad.write_text("hello")
    assert c.post("/api/media", files={"file": ("x.txt", b"hello")}).status_code == 400
    assert c.get("/api/media/zz/info").status_code == 400


def test_export_stacks_video_tracks_and_fills_gaps(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 3, "red", 500))
    blue = upload(c, make_video(tmp / "b.mp4", 2, "blue", 2000))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v2", "media": blue["id"], "src_in": 0, "src_out": 1, "start": 1},   # 1~2초 위에 파랑
        {"id": "c3", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 1, "start": 4},   # 3~4초는 빈 곳
        {"id": "c4", "track": "a1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
    ]
    out = c.post("/api/export", json={"tracks": tracks(), "clips": clips}).json()
    mp4 = tmp / "o.mp4"; mp4.write_bytes(c.get(out["url"]).content)
    info = probe(mp4)
    assert info["duration"] == pytest.approx(5, abs=0.15) and info["width"] == 320
    r, g, b = frame_rgb(mp4, 0.5); assert r > 150 and b < 80          # 빨강
    r, g, b = frame_rgb(mp4, 1.5); assert b > 150 and r < 80          # 위 트랙 파랑
    assert frame_rgb(mp4, 3.5).max() < 30                             # 빈 곳 검정
    r, g, b = frame_rgb(mp4, 4.5); assert r > 150                     # 다시 빨강
    a = decode(mp4)
    assert rms(a, 0.2, 2.8) > 0.03 and rms(a, 3.2, 4.8) < 0.003      # 소리는 a1 클립 구간에만

    # 위 트랙을 숨기면 아래 빨강이 보임
    hidden = c.post("/api/export", json={"tracks": tracks(v2={"mute": True}), "clips": clips}).json()
    mp4b = tmp / "h.mp4"; mp4b.write_bytes(c.get(hidden["url"]).content)
    r, g, b = frame_rgb(mp4b, 1.5); assert r > 150 and b < 80


def test_mix_mute_solo_volume_and_duck(api):
    c, tmp = api
    music = upload(c, make_video(tmp / "m.mp4", 4, tone=440))
    voice = upload(c, make_voice(tmp / "v.wav", 1.0))
    clips = [
        {"id": "m", "track": "a1", "media": music["id"], "src_in": 0, "src_out": 4, "start": 0},
        {"id": "v", "track": "a2", "media": voice["id"], "src_in": 0, "src_out": 1, "start": 1},
    ]
    def mix(**over):
        r = c.post("/api/mix", json={"tracks": tracks(**over), "clips": clips})
        p = tmp / "mix.wav"; p.write_bytes(r.content)
        return sf.read(p, dtype="float32")[0]

    base = mix()
    assert rms(base, 0.2, 0.8) > 0.03 and rms(base, 1.2, 1.8) > rms(base, 0.2, 0.8)
    assert rms(mix(a1={"mute": True}), 0.2, 0.8) < 1e-4                     # 음소거
    assert rms(mix(a2={"solo": True}), 0.2, 0.8) < 1e-4                     # 솔로면 다른 트랙 안 들림
    assert rms(mix(a1={"volume": 0.5}), 0.2, 0.8) == pytest.approx(rms(base, 0.2, 0.8) * 0.5, rel=0.05)
    ducked = mix(a1={"duck": 1.0}, a2={"mute": False})
    music_only = mix(a2={"mute": True})
    ducked_music_only = c.post("/api/mix", json={"tracks": tracks(a1={"duck": 1.0}), "clips": clips})
    # 덕킹: 보이스가 나오는 동안 a1이 줄어듦 → 전체에서 a1 몫이 줄어든 만큼 확인
    assert rms(ducked, 3.0, 3.8) == pytest.approx(rms(music_only, 3.0, 3.8), rel=0.05)   # 보이스 끝난 뒤엔 그대로
    assert rms(ducked, 1.2, 1.8) < rms(base, 1.2, 1.8)


def test_voice_change_and_track_fx(api):
    c, tmp = api
    voice = upload(c, make_voice(tmp / "v.wav", 1.0))
    clip = {"id": "v", "track": "a2", "media": voice["id"], "src_in": 0, "src_out": 1, "start": 0.5, "gain": 0.2}
    def mix(clip_over=None, **over):
        pad = {**clip, "id": "pad", "start": 2.0, "gain": 0.0}   # 타임라인 길이를 2.6초로
        r = c.post("/api/mix", json={"tracks": tracks(**over), "clips": [{**clip, **(clip_over or {})}, pad]})
        p = tmp / "mix.wav"; p.write_bytes(r.content)
        return sf.read(p, dtype="float32")[0]
    plain = mix()
    leveled = mix(a2={"voice": True, "level": True})
    assert -24 < 20 * np.log10(rms(leveled, 0.55, 1.45)) < -16
    wet = mix(a2={"voice": True, "level": True, "room": 1.0})
    assert rms(wet, 1.52, 1.8) > rms(leveled, 1.52, 1.8) * 3
    robot = mix({"voice": "robot"})
    assert not np.allclose(robot[: plain.size], plain[: robot.size], atol=1e-3)


def test_same_track_overlap_later_clip_wins(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 3, "red"))
    blue = upload(c, make_video(tmp / "b.mp4", 1, "blue"))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v1", "media": blue["id"], "src_in": 0, "src_out": 1, "start": 1},
    ]
    out = c.post("/api/export", json={"tracks": tracks(), "clips": clips}).json()
    mp4 = tmp / "o.mp4"; mp4.write_bytes(c.get(out["url"]).content)
    r, g, b = frame_rgb(mp4, 0.5); assert r > 150 and b < 80
    r, g, b = frame_rgb(mp4, 1.5); assert b > 150 and r < 80
    r, g, b = frame_rgb(mp4, 2.5); assert r > 150 and b < 80


# ---------------------------------------------------------------- 2단계: 전환 · PiP · 자막

def frame_px(path, t, x, y, size=(32, 24)):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={size[0]}:{size[1]}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3)
    return img[int(y * size[1])][int(x * size[0])].astype(int)


def export_to(c, tmp, body, name="o.mp4"):
    r = c.post("/api/export", json=body)
    assert r.status_code == 200, r.text
    mp4 = tmp / name
    mp4.write_bytes(c.get(r.json()["url"]).content)
    return mp4


def two_clips(c, tmp, transition, dur=1.0):
    red = upload(c, make_video(tmp / "r.mp4", 3, "red", 500))
    blue = upload(c, make_video(tmp / "b.mp4", 3, "blue", 900))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v1", "media": blue["id"], "src_in": 0, "src_out": 3, "start": 3,
         "transition": transition, "transition_dur": dur},
        {"id": "a", "track": "a1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
    ]
    return {"tracks": tracks(), "clips": clips}


def test_transition_fade_blends_previous_and_next(api):
    c, tmp = api
    mp4 = export_to(c, tmp, two_clips(c, tmp, "fade"))
    assert probe(mp4)["duration"] == pytest.approx(6, abs=0.15)
    r, g, b = frame_px(mp4, 2.5, .5, .5); assert r > 150 and b < 80          # 전환 전: 빨강
    r, g, b = frame_px(mp4, 3.5, .5, .5); assert r > 60 and b > 60           # 전환 중간: 섞임
    r, g, b = frame_px(mp4, 4.5, .5, .5); assert b > 150 and r < 80          # 전환 뒤: 파랑
    assert rms(decode(mp4), 0.2, 2.8) > 0.03                                 # 소리는 영향 없음


def test_transition_dip_goes_through_black(api):
    c, tmp = api
    mp4 = export_to(c, tmp, two_clips(c, tmp, "dip", 1.0))
    assert frame_px(mp4, 3.5, .5, .5).max() < 60                             # 중간엔 거의 검정
    r, g, b = frame_px(mp4, 4.5, .5, .5); assert b > 150


@pytest.mark.parametrize("kind,left_is_new", [("wipeleft", False), ("wiperight", True)])
def test_transition_wipe_reveals_from_side(api, kind, left_is_new):
    c, tmp = api
    mp4 = export_to(c, tmp, two_clips(c, tmp, kind, 1.0))
    left, right = frame_px(mp4, 3.5, .1, .5), frame_px(mp4, 3.5, .9, .5)
    new, old = (left, right) if left_is_new else (right, left)
    assert new[2] > 150 and new[0] < 80                                      # 새 클립(파랑)이 드러난 쪽
    assert old[0] > 150 and old[2] < 80                                      # 아직 옛 클립(빨강)


@pytest.mark.parametrize("kind,left_is_new", [("slideleft", False), ("slideright", True)])
def test_transition_slide_moves_new_clip_in(api, kind, left_is_new):
    c, tmp = api
    mp4 = export_to(c, tmp, two_clips(c, tmp, kind, 1.0))
    left, right = frame_px(mp4, 3.5, .1, .5), frame_px(mp4, 3.5, .9, .5)
    new, old = (left, right) if left_is_new else (right, left)
    assert new[2] > 150 and old[0] > 150
    r, g, b = frame_px(mp4, 4.5, .5, .5); assert b > 150 and r < 80


def test_transition_without_overlap_fades_from_black(api):
    c, tmp = api
    blue = upload(c, make_video(tmp / "b.mp4", 2, "blue", 900))
    clips = [{"id": "c", "track": "v1", "media": blue["id"], "src_in": 0, "src_out": 2, "start": 0,
              "transition": "fade", "transition_dur": 1.0}]
    mp4 = export_to(c, tmp, {"tracks": tracks(), "clips": clips})
    assert frame_px(mp4, 0.0, .5, .5).max() < 40
    r, g, b = frame_px(mp4, 1.6, .5, .5); assert b > 150


def test_transition_does_not_change_audio_mix_cache(api):
    c, tmp = api
    body = two_clips(c, tmp, "none")
    a = c.post("/api/mix", json=body).content
    body["clips"][1]["transition"] = "fade"
    assert c.post("/api/mix", json=body).content == a
    assert len(list((tmp / "ws" / "cache").glob("mix_*.wav"))) == 1


def test_pip_overlays_small_window_over_full(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 3, "red", 500))
    blue = upload(c, make_video(tmp / "b.mp4", 2, "blue", 900))
    clips = [
        {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 3, "start": 0},
        {"id": "c2", "track": "v2", "media": blue["id"], "src_in": 0, "src_out": 2, "start": 0.5,
         "layout": "pip", "pip_x": 0.75, "pip_y": 0.75, "pip_w": 0.4, "pip_border": False},
    ]
    mp4 = export_to(c, tmp, {"tracks": tracks(), "clips": clips})
    assert probe(mp4)["width"] == 320
    for t, blue_expected in [(0.25, False), (1.5, True), (2.8, False)]:
        r, g, b = frame_px(mp4, t, .75, .75)
        assert (b > 150 and r < 80) if blue_expected else (r > 150 and b < 80), t
    r, g, b = frame_px(mp4, 1.5, .1, .1); assert r > 150 and b < 80          # 나머지는 큰 화면 그대로
    r, g, b = frame_px(mp4, 1.5, .5, .5); assert r > 150 and b < 80          # PiP 밖(가운데)도 빨강


def test_pip_opacity_and_border(api):
    c, tmp = api
    red = upload(c, make_video(tmp / "r.mp4", 2, "red", 500))
    blue = upload(c, make_video(tmp / "b.mp4", 2, "blue", 900))
    base = {"id": "c2", "track": "v2", "media": blue["id"], "src_in": 0, "src_out": 2, "start": 0,
            "layout": "pip", "pip_x": 0.5, "pip_y": 0.5, "pip_w": 0.5}
    full = {"id": "c1", "track": "v1", "media": red["id"], "src_in": 0, "src_out": 2, "start": 0}
    half = export_to(c, tmp, {"tracks": tracks(), "clips": [full, {**base, "pip_opacity": 0.5, "pip_border": False}]})
    r, g, b = frame_px(half, 1.0, .5, .5); assert r > 60 and b > 60
    bordered = export_to(c, tmp, {"tracks": tracks(), "clips": [full, {**base, "pip_border": True}]}, "b2.mp4")
    r, g, b = frame_px(bordered, 1.0, .252, .5, size=(320, 240)); assert min(r, g, b) > 150   # 흰 테두리


def test_pip_and_transition_do_not_change_canvas_size(api):
    c, tmp = api
    big = upload(c, make_video(tmp / "big.mp4", 2, "red", size="640x360"))
    small = upload(c, make_video(tmp / "s.mp4", 2, "blue", size="160x120"))
    clips = [{"id": "c1", "track": "v1", "media": small["id"], "src_in": 0, "src_out": 2, "start": 0, "layout": "pip"},
             {"id": "c2", "track": "v2", "media": big["id"], "src_in": 0, "src_out": 2, "start": 0}]
    mp4 = export_to(c, tmp, {"tracks": tracks(), "clips": clips})
    assert (probe(mp4)["width"], probe(mp4)["height"]) == (640, 360)          # PiP가 아닌 클립 기준


SUBS = [{"id": "s1", "start": 1.0, "end": 2.0, "text": "HELLO", "lang": "en"},
        {"id": "s2", "start": 2.5, "end": 3.5, "text": "둘째 줄\n자막", "lang": "ko"}]


def bright_pixels(path, t):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(-1, 3)
    return int((img.min(axis=1) > 200).sum())


def sub_project(c, tmp, **extra):
    blue = upload(c, make_video(tmp / "b.mp4", 4, "blue", size="640x360"))
    clips = [{"id": "c", "track": "v1", "media": blue["id"], "src_in": 0, "src_out": 4, "start": 0}]
    return {"tracks": tracks(), "clips": clips, "subtitles": SUBS, **extra}


def test_subtitles_are_burned_in_only_during_their_cue(api):
    c, tmp = api
    mp4 = export_to(c, tmp, sub_project(c, tmp, subtitle_style={"size": 0.15, "y": 0.5}))
    assert bright_pixels(mp4, 0.5) == 0
    assert bright_pixels(mp4, 1.5) > 300                                      # 흰 글자
    assert bright_pixels(mp4, 2.2) == 0
    assert bright_pixels(mp4, 3.0) > 100
    assert list((tmp / "ws" / "exports").glob("*.ass")) == []                  # 임시 파일은 지움


def test_subtitle_style_color_outline_background_and_position(api):
    c, tmp = api
    style = {"size": 0.15, "y": 0.2, "color": "#ff0000", "outline": 0, "bg_opacity": 0}
    mp4 = export_to(c, tmp, sub_project(c, tmp, subtitle_style=style))
    r, g, b = frame_px(mp4, 1.5, .5, .5, size=(640, 360)); assert b > 150 and r < 60       # 가운데엔 글자 없음
    top = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "1.5", "-i", str(mp4), "-frames:v", "1", "-vf",
                          "crop=640:120:0:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(top, np.uint8).reshape(-1, 3)
    assert ((img[:, 0] > 200) & (img[:, 2] < 80)).sum() > 200                  # 위쪽에 빨간 글자
    boxed = export_to(c, tmp, sub_project(c, tmp, subtitle_style={"size": 0.15, "y": 0.5, "color": "#ffffff",
                                                                    "bg_color": "#ff0000", "bg_opacity": 1.0}), "bx.mp4")
    img = np.frombuffer(subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "1.5", "-i", str(boxed), "-frames:v", "1",
                                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout,
                        np.uint8).reshape(-1, 3)
    assert ((img[:, 0] > 200) & (img[:, 2] < 80)).sum() > 1500                 # 빨간 배경 상자


def test_subtitle_language_filter(api):
    c, tmp = api
    only_en = export_to(c, tmp, sub_project(c, tmp, subtitle_lang="en", subtitle_style={"size": 0.15}))
    assert bright_pixels(only_en, 1.5) > 300 and bright_pixels(only_en, 3.0) == 0   # ko 자막은 빠짐


def test_srt_export(api):
    c, tmp = api
    body = {"tracks": tracks(), "clips": [], "subtitles": [*SUBS, {"id": "e", "start": 5, "end": 6, "text": "  "}]}
    r = c.post("/api/subtitles/srt", json=body)
    assert r.status_code == 200 and "subrip" in r.headers["content-type"]
    assert r.content.decode("utf-8") == ("1\n00:00:01,000 --> 00:00:02,000\nHELLO\n\n"
                                         "2\n00:00:02,500 --> 00:00:03,500\n둘째 줄\n자막\n")
    ko = c.post("/api/subtitles/srt", json={**body, "subtitle_lang": "ko"}).content.decode()
    assert ko.startswith("1\n00:00:02,500") and "HELLO" not in ko


def test_subtitle_ass_generation():
    from video_editor.subtitles import Cue, SubtitleStyle, to_ass, srt_time, ass_time
    assert srt_time(3661.5) == "01:01:01,500" and ass_time(3661.5) == "1:01:01.50"
    ass = to_ass([Cue(id="a", start=0, end=1, text="a{b}\nc")], SubtitleStyle(bg_opacity=0.5, x=0.5, y=0.8), 1000, 500)
    assert "PlayResX: 1000" in ass and "\\pos(500,400)" in ass and "Style: Box," in ass
    assert "a(b)\\Nc" in ass and ass.count("Dialogue:") == 2
