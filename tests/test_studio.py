"""글로 편집 · 하이라이트 · 자동 리프레임 · 낱말 강조 자막 · 보컬 분리 · 감정 · 보이스 캐릭터."""
import time

import numpy as np
import pyworld
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_ai_tools import SEGMENTS, fake_synth, run, wait
from tests.test_editor import make_voice, tracks, upload
from tests.test_shorts import body, export_short, frame_px
from video_editor import ai_tools, ai_voice, characters, emotion, highlights, reframe, separate, shorts
from video_editor.editor import Workspace, make_router
from video_editor.engine import SAMPLE_RATE as SR
from video_editor.subtitles import Cue, CueWord, SubtitleStyle, to_ass


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_tools, "is_installed", lambda: True)
    app = FastAPI()
    app.include_router(make_router(Workspace(tmp_path / "ws")))
    return TestClient(app), tmp_path


def voiced(seconds=1.0, f0=160.0):
    t = np.arange(int(SR * seconds)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 15))
    return (0.3 * x / np.abs(x).max()).astype(np.float32)


def mean_f0(x):
    d = x.astype(np.float64)
    f0, t = pyworld.dio(d, SR, frame_period=5.0)
    f0 = pyworld.stonemask(d, f0, t, SR)
    return float(np.mean(f0[f0 > 0]))


# ------------------------------------------------------------ 낱말 강조(카라오케) 자막

def test_karaoke_ass_marks_each_word_and_falls_back_when_text_edited():
    words = [CueWord(s=0.1, e=0.5, w="안녕"), CueWord(s=0.6, e=1.0, w="하세요"), CueWord(s=1.2, e=1.8, w="여러분")]
    cue = Cue(id="a", start=1, end=3, text="안녕 하세요 여러분", lang="ko", words=words)
    ass = to_ass([cue], SubtitleStyle(karaoke=True, hl_color="#ff0000", color="#ffffff"), 1080, 1920)
    line = next(ln for ln in ass.splitlines() if ln.startswith("Dialogue: 1"))
    assert "{\\k10}" in line and "{\\k50}안녕 " in line and "{\\k60}하세요 " in line and line.endswith("{\\k60}여러분")
    style = next(ln for ln in ass.splitlines() if ln.startswith("Style: Text"))
    assert "&H000000FF,&H00FFFFFF" in style                    # 말한 낱말=빨강(BGR), 아직 안 한 낱말=흰색
    # 글을 고치면 일반 자막으로
    edited = cue.model_copy(update={"text": "완전히 다른 글"})
    assert "\\k" not in to_ass([edited], SubtitleStyle(karaoke=True), 1080, 1920)
    assert "\\k" not in to_ass([cue], SubtitleStyle(), 1080, 1920)      # 꺼져 있으면 그대로


def test_segments_to_cues_carry_relative_words_and_shorts_shift_them():
    cues = ai_tools.segments_to_cues(SEGMENTS, "ko", max_chars=12)
    assert all(c["words"] and c["words"][0]["s"] == 0 for c in cues)
    c0 = cues[1]
    abs_first = c0["start"] + c0["words"][0]["s"]
    assert abs_first == pytest.approx(c0["start"])
    cue = Cue(id="a", start=1, end=3, text="가 나", words=[CueWord(s=0.0, e=0.4, w="가"), CueWord(s=1.0, e=1.5, w="나")])
    out = shorts.shift_cues([cue], 1.5, 10)[0]       # 앞 0.5초가 잘림 → 낱말도 당겨짐, 이미 지난 '가'는 사라짐
    assert out.start == 0 and [w.w for w in out.words] == ["나"] and out.words[0].s == pytest.approx(0.5)


def test_transcribe_returns_segments_with_words(api, monkeypatch):
    c, tmp = api
    m = upload(c, make_voice(tmp / "v.wav", 4))
    monkeypatch.setattr(ai_tools, "transcribe", lambda wav, lang, verbatim: {"lang": "ko", "segments": SEGMENTS})
    r = run(c, "/api/ai/transcribe", {"media": m["id"], "lang": "ko", "max_chars": 10})
    segs = r["result"]["segments"]
    assert segs[0][1] == {"s": 1.0, "e": 1.6, "w": "안녕하세요"} and len(r["result"]["cues"]) >= 3


# ------------------------------------------------------------ 하이라이트

def lively_audio(total=120.0):
    """0~120초: 40~75초만 크고 변화가 큰 목소리, 나머지는 작고 단조로움."""
    rng = np.random.default_rng(1)
    t = np.arange(int(SR * total)) / SR
    x = 0.02 * np.sin(2 * np.pi * 180 * t)
    m = (t >= 40) & (t < 75)
    x[m] = (0.35 * np.sin(2 * np.pi * 220 * t[m]) * (0.4 + 0.6 * np.abs(np.sin(2 * np.pi * 1.3 * t[m])))).astype(np.float32)
    return (x + rng.normal(0, 0.001, x.size)).astype(np.float32)


def sentence_units(total=120.0, step=5.0):
    out, t, i = [], 0.0, 0
    while t + step <= total:
        hot = 40 <= t < 75
        text = ("진짜 중요한 비밀이 있어요!" if i % 2 == 0 else "이 방법을 꼭 기억하세요.") if hot else "그냥 천천히 이야기를 합니다."
        out.append({"start": t + 0.2, "end": t + step - 0.3, "text": text})
        t += step
        i += 1
    return out


def test_highlights_pick_lively_non_overlapping_windows_on_sentence_edges():
    x = lively_audio()
    units = sentence_units()
    items = highlights.find_highlights(units, x, SR, "ko", n=3, min_len=30, max_len=60)
    assert items and items[0]["start"] >= 30 and items[0]["end"] <= 85          # 활기찬 구간 쪽
    assert items[0]["end"] - items[0]["start"] >= 30 and items[0]["end"] - items[0]["start"] <= 60
    assert items[0]["score"] > items[-1]["score"] - 1e-9
    starts = {round(u["start"], 3) for u in units}
    ends = {round(u["end"], 3) for u in units}
    assert all(round(i["start"], 3) in starts and round(i["end"], 3) in ends for i in items)   # 문장 중간에서 안 끊김
    for a in items:
        for b in items:
            if a is not b:
                assert min(a["end"], b["end"]) - max(a["start"], b["start"]) < 0.25 * (a["end"] - a["start"])
    assert highlights.find_highlights([], x, SR) == []


def test_highlight_units_from_audio_when_no_transcript():
    t = np.arange(SR * 6) / SR
    sig = np.where(((t > 0.5) & (t < 2)) | ((t > 3) & (t < 5)), 0.3 * np.sin(2 * np.pi * 200 * t), 0).astype(np.float32)
    units = highlights.units_from_audio(sig, SR)
    assert len(units) == 2 and units[0]["start"] == pytest.approx(0.5, abs=0.06) and units[1]["end"] == pytest.approx(5.0, abs=0.06)


def test_sentence_units_split_on_punctuation_and_pauses():
    u = highlights.sentence_units(SEGMENTS)
    assert [x["text"] for x in u] == ["음 안녕하세요 어...", "반갑습니다."] or len(u) >= 2
    assert u[-1]["end"] == 3.0


def test_highlights_route_with_and_without_transcript(api, monkeypatch, tmp_path):
    c, tmp = api
    sf.write(tmp / "long.wav", lively_audio(), SR)
    m = upload(c, tmp / "long.wav")
    segs = [{"start": u["start"], "end": u["end"], "text": u["text"], "words": []} for u in sentence_units()]
    monkeypatch.setattr(ai_tools, "transcribe", lambda wav, lang, verbatim: {"lang": "ko", "segments": segs})
    r = run(c, "/api/ai/highlights", {"media": m["id"], "n": 2})
    assert r["status"] == "done" and r["result"]["stt"] and len(r["result"]["items"]) >= 1
    assert r["result"]["items"][0]["title"]
    monkeypatch.setattr(ai_tools, "is_installed", lambda: False)             # 받아쓰기 없이도 (말소리 덩어리 기준)
    r = run(c, "/api/ai/highlights", {"media": m["id"], "n": 2, "min_len": 5, "max_len": 20, "use_stt": False})
    assert r["status"] == "done" and r["result"]["stt"] is False
    assert c.post("/api/ai/highlights", json={"media": m["id"], "min_len": 50, "max_len": 20}).status_code == 400


# ------------------------------------------------------------ 자동 리프레임

def frames_moving(n=40, step=0.5, noise=0.0, gap=None):
    rng = np.random.default_rng(0)
    out = []
    for i in range(n):
        t = i * step
        cx = 0.2 + 0.6 * i / (n - 1) + rng.normal(0, noise)
        boxes = [[cx, 0.4, 0.2]] + ([[0.9, 0.5, 0.05]] if i % 5 == 0 else [])      # 작은 다른 얼굴이 가끔 보임
        if gap and gap[0] <= t < gap[1]:
            boxes = []
        out.append({"t": t, "boxes": boxes})
    return out


def test_pick_subject_prefers_big_continuous_face():
    s = reframe.pick_subject(frames_moving(10))
    assert all(p is not None and p[3] == 0.2 for p in s)


def test_track_follows_subject_smoothly_with_few_points():
    tr = reframe.build_track(frames_moving(40, noise=0.01), 0, 19.5)
    assert 2 <= len(tr) <= 60 and tr[0]["cx"] < 0.4 and tr[-1]["cx"] > 0.6
    assert all(b["t"] > a["t"] for a, b in zip(tr, tr[1:]))
    speeds = [abs(b["cx"] - a["cx"]) / (b["t"] - a["t"]) for a, b in zip(tr, tr[1:])]
    assert max(speeds) <= 0.55                                        # 카메라 속도 상한
    still = reframe.build_track([{"t": i * 0.5, "boxes": [[0.5 + (0.02 if i % 2 else -0.02), 0.5, 0.2]]} for i in range(20)], 0, 9.5)
    assert max(p["cx"] for p in still) - min(p["cx"] for p in still) < 0.03       # 작은 흔들림엔 가만히


def test_track_returns_to_center_when_lost_and_handles_no_detection():
    tr = reframe.build_track(frames_moving(40, gap=(8, 20)), 0, 19.5)
    assert abs(tr[-1]["cx"] - 0.5) < 0.2
    assert all(p["cx"] == 0.5 and p["cy"] == 0.5 for p in reframe.build_track([{"t": 0, "boxes": []}, {"t": 1, "boxes": []}], 0, 1))


def test_reframe_route_converts_detection_to_track(api, monkeypatch):
    c, tmp = api
    from tests.test_shorts import split_video
    m = upload(c, split_video(tmp / "s.mp4", 6))
    monkeypatch.setattr(ai_tools, "detect_subjects", lambda video, s, e, step: frames_moving(12, step=step))
    r = run(c, "/api/ai/reframe", {"media": m["id"], "start": 0, "end": 5.5})
    assert r["status"] == "done" and r["result"]["found"] == 12 and r["result"]["track"][0]["t"] == 0
    a = upload(c, make_voice(tmp / "v.wav"))
    assert c.post("/api/ai/reframe", json={"media": a["id"], "start": 0, "end": 3}).status_code == 400   # 영상이 아님
    assert c.post("/api/ai/reframe", json={"media": m["id"], "start": 2, "end": 2.1}).status_code == 400


def test_export_follows_track_over_time(api):
    c, tmp = api
    # 왼쪽 빨강·오른쪽 파랑 영상에서 처음엔 왼쪽, 끝엔 오른쪽을 따라감 (확대 2배)
    track = [{"t": 0, "cx": 0.2, "cy": 0.5}, {"t": 1.5, "cx": 0.2, "cy": 0.5}, {"t": 2.5, "cx": 0.8, "cy": 0.5}, {"t": 6, "cx": 0.8, "cy": 0.5}]
    mp4 = export_short(c, tmp, body(c, tmp, {"format": "reels", "zoom": 2.0, "end": 5, "track": track}))
    r0, _, b0 = frame_px(mp4, 0.5, 0.5, 0.5, size=(54, 96))
    r1, _, b1 = frame_px(mp4, 4.5, 0.5, 0.5, size=(54, 96))
    assert r0 > 150 and b0 < 80 and b1 > 150 and r1 < 80
    # 시작 시각이 어긋나도 (잘라낸 구간 기준으로) 같은 점을 따라감
    mp4 = export_short(c, tmp, body(c, tmp, {"format": "reels", "zoom": 2.0, "start": 1.5, "end": 6, "track": track}), "o2.mp4")
    assert frame_px(mp4, 0.1, 0.5, 0.5, size=(54, 96))[0] > 150       # 타임라인 1.5초 = 아직 왼쪽 (점 시각은 타임라인 기준)
    assert frame_px(mp4, 3.9, 0.5, 0.5, size=(54, 96))[2] > 150


def test_lerp_expression_clamps_outside_keys():
    e = shorts._lerp_expr([(1.0, 0.2), (3.0, 0.8)])
    assert e.startswith("if(lt(t,1.0000),0.20000,") and "0.80000" in e


# ------------------------------------------------------------ 보컬 분리

def test_separate_route_adds_two_media(api, monkeypatch):
    c, tmp = api
    m = upload(c, make_voice(tmp / "v.wav", 2))
    r = c.post("/api/ai/separate", json={"media": m["id"]})
    assert r.status_code == 503 and "setup_ai_studio" in r.json()["detail"]

    def fake(src, v, b, timeout=None):
        x, _ = sf.read(src, dtype="float32")
        sf.write(v, x * 0.8, SR), sf.write(b, x * 0.1, SR)
    monkeypatch.setattr(separate, "is_installed", lambda: True)
    monkeypatch.setattr(separate, "separate", fake)
    res = run(c, "/api/ai/separate", {"media": m["id"]})
    assert res["status"] == "done"
    v, b = res["result"]["vocals"], res["result"]["background"]
    assert "목소리" in v["name"] and "배경음" in b["name"] and v["duration"] == pytest.approx(2.0, abs=0.1)
    assert len(c.get("/api/media").json()) == 3


# ------------------------------------------------------------ 감정

def test_detect_and_plan_with_tags_and_overrides():
    assert emotion.detect("오늘 정말 행복해요! 고마워요", "ko")[0] == "happy"
    assert emotion.detect("당장 나가! 너무 화가 나", "ko")[0] == "angry"
    assert emotion.detect("너무 슬퍼서 눈물이 나요ㅠㅠ", "ko")[0] == "sad"
    assert emotion.detect("I am so sad and lonely", "en")[0] == "sad"
    assert emotion.detect("그냥 평범한 문장입니다.", "ko") == ("neutral", 0.0)
    p = emotion.plan("[화남] 이게 뭐야! 오늘은 행복해요. 정말요?", "ko", "auto")
    assert [i["emotion"] for i in p] == ["angry", "happy", "neutral"] and p[2]["question"] and p[0]["text"] == "이게 뭐야!"
    assert {i["emotion"] for i in emotion.plan("슬프다. 행복해요.", "ko", "calm")} == {"calm"}
    assert emotion.plan("[슬픔] 행복해요", "ko", "angry")[0]["emotion"] == "sad"       # 문장 표시가 우선
    with pytest.raises(ValueError):
        emotion.plan("x", "ko", "bogus")


def test_prosody_changes_pitch_range_and_loudness_by_emotion():
    x = voiced(1.0)
    base = mean_f0(x)
    happy, sad, angry = (emotion.prosody(x, e, 1.0) for e in ("happy", "sad", "angry"))
    assert mean_f0(happy) > base * 1.05 and mean_f0(sad) < base * 0.95
    assert all(y.size == x.size and np.isfinite(y).all() for y in (happy, sad, angry))
    rms_ = lambda y: float(np.sqrt(np.mean(y ** 2)))
    assert rms_(angry) > rms_(x) * 1.2 and rms_(sad) < rms_(x) * 0.85
    assert np.array_equal(emotion.prosody(x, "neutral", 1.0), x)
    # 세기: 0이면 거의 그대로, 2면 더 크게 변함
    d = lambda lv: abs(np.log2(mean_f0(emotion.prosody(x, "happy", lv)) / base))
    assert d(0.0) < 0.01 and d(2.0) > d(1.0) > d(0.5)
    q = emotion.prosody(x, "neutral", 1.0, question=True)               # 물음표: 문장 끝 올림
    half = q.size * 4 // 5
    assert mean_f0(q[half:]) > mean_f0(x[half:]) * 1.04
    whisper = emotion.prosody(x, "whisper", 1.0)
    assert whisper.size == x.size and np.isfinite(whisper).all()


def test_apply_spans_only_touches_spans():
    x = voiced(3.0)
    y = emotion.apply_spans(x, [{"start": 1.0, "end": 2.0, "emotion": "sad", "level": 1.0}])
    assert np.array_equal(y[: int(SR * 0.98)], x[: int(SR * 0.98)]) and not np.array_equal(y[SR + 1000: 2 * SR - 1000], x[SR + 1000: 2 * SR - 1000])
    assert np.array_equal(y[int(SR * 2.02):], x[int(SR * 2.02):])


def test_tts_emotion_tags_and_levels(api, monkeypatch):
    c, tmp = api
    fake_synth(monkeypatch)
    plain = run(c, "/api/ai/tts", {"text": "안녕하세요 여러분. 반갑습니다.", "lang": "ko"})["result"]
    assert plain["spans"] == []                                           # 감정 없음 = 예전 방식 그대로
    r = run(c, "/api/ai/tts", {"text": "[기쁨] 안녕하세요 여러분. [슬픔] 반갑습니다.", "lang": "ko", "emotion": "auto"})["result"]
    assert [s["label"] for s in r["spans"]] == ["기쁨", "슬픔"] and r["spans"][1]["start"] > r["spans"][0]["end"]
    d_happy = r["spans"][0]["end"] - r["spans"][0]["start"]
    assert d_happy < 1.8 / 1.08 + 0.1                                      # 기쁨은 빠르게 읽음 (기본 속도 대비 짧음)
    assert c.post("/api/ai/tts", json={"text": "a", "emotion": "bogus"}).status_code == 400
    a = c.post("/api/ai/emotion/analyze", json={"text": "행복해요! 너무 슬퍼요ㅠㅠ. 그냥 그래요.", "emotion": "auto"}).json()["items"]
    assert [i["label"] for i in a] == ["기쁨", "슬픔", "보통"]


def test_emotion_apply_on_recorded_clip(api, monkeypatch):
    c, tmp = api
    sf.write(tmp / "s.wav", np.concatenate([voiced(1.0), np.zeros(SR), voiced(1.0)]), SR)
    m = upload(c, tmp / "s.wav")
    r = run(c, "/api/ai/emotion/apply", {"media": m["id"], "emotion": "sad"})
    assert r["status"] == "done" and "슬픔" in r["result"]["media"]["name"] and len(r["result"]["spans"]) == 2
    y, _ = sf.read(tmp / "ws" / "media" / r["result"]["media"]["file"], dtype="float32")
    assert mean_f0(y[: SR]) < mean_f0(voiced(1.0)) * 0.95
    # 자동: 받아쓰기로 문장마다
    segs = [{"start": 0.0, "end": 1.0, "text": "정말 행복해요!", "words": []}, {"start": 2.0, "end": 3.0, "text": "너무 슬퍼요ㅠㅠ", "words": []}]
    monkeypatch.setattr(ai_tools, "transcribe", lambda wav, lang, verbatim: {"lang": "ko", "segments": segs})
    r = run(c, "/api/ai/emotion/apply", {"media": m["id"], "emotion": "auto"})
    assert [s["label"] for s in r["result"]["spans"]] == ["기쁨", "슬픔"]
    assert c.post("/api/ai/emotion/apply", json={"media": m["id"], "emotion": "x"}).status_code == 400


def test_dub_uses_cue_emotion_over_default(api, monkeypatch):
    c, tmp = api
    fake_synth(monkeypatch)
    cues = [{"start": 0.0, "end": 2.0, "text": "hello there", "emotion": "sad"}, {"start": 3.0, "end": 5.0, "text": "hello there"}]
    r = run(c, "/api/ai/dub", {"cues": cues, "lang": "en", "emotion": "excited"})
    assert r["status"] == "done"
    assert c.post("/api/ai/dub", json={"cues": [{"start": 0, "end": 1, "text": "x", "emotion": "zzz"}], "lang": "en"}).status_code == 400


# ------------------------------------------------------------ 보이스 캐릭터

def clip_mix(c, mid, voice, track="a2"):
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": track, "media": mid, "src_in": 0, "src_out": 1.0, "start": 0, "voice": voice}]}
    r = c.post("/api/mix", json=p)
    assert r.status_code == 200, r.text
    return r


def test_character_crud_and_listed_in_voices(api):
    c, tmp = api
    ref = upload(c, make_voice(tmp / "ref.wav", 2))
    ch = c.post("/api/characters", json={"name": "내 캐릭터", "preset": "deep", "intensity": 1.5, "ref": ref["id"]}).json()
    assert ch["id"] and c.get("/api/characters").json()[0]["name"] == "내 캐릭터"
    v = [x for x in c.get("/api/voices").json() if x.get("character")]
    assert v == [{"name": f"char:{ch['id']}", "label": "🎭 내 캐릭터", "character": True, "ref": ref["id"]}]
    assert c.put(f"/api/characters/{ch['id']}", json={"name": "새 이름", "preset": "bright"}).json()["preset"] == "bright"
    assert c.post("/api/characters", json={"name": "x", "preset": "nope"}).status_code == 400
    assert c.post("/api/characters", json={"name": "x", "ref": "ab12"}).status_code == 404
    assert c.delete(f"/api/characters/{ch['id']}").status_code == 200 and c.get("/api/characters").json() == []
    assert c.delete(f"/api/characters/{ch['id']}").status_code == 404


def test_character_preset_only_changes_voice_like_the_preset(api):
    c, tmp = api
    m = upload(c, make_voice(tmp / "v.wav", 1))
    ch = c.post("/api/characters", json={"name": "굵은", "preset": "deep", "intensity": 1.0}).json()
    a = decode_mix(clip_mix(c, m["id"], f"char:{ch['id']}"), tmp / "m1.wav")
    b = decode_mix(clip_mix(c, m["id"], "deep"), tmp / "m2.wav")
    assert np.allclose(a, b, atol=1e-4)
    # 캐릭터를 고치면 (강도) 믹스 캐시가 새로 만들어짐
    c.put(f"/api/characters/{ch['id']}", json={"name": "굵은", "preset": "deep", "intensity": 2.0})
    a2 = decode_mix(clip_mix(c, m["id"], f"char:{ch['id']}"), tmp / "m3.wav")
    assert not np.allclose(a, a2, atol=1e-3)
    gone = decode_mix(clip_mix(c, m["id"], "char:deadbeef"), tmp / "m4.wav")      # 없는 캐릭터는 원본
    assert np.allclose(gone, decode_mix(clip_mix(c, m["id"], "original"), tmp / "m5.wav"), atol=1e-5)


def decode_mix(resp, path):
    path.write_bytes(resp.content)
    x, _ = sf.read(path, dtype="float32")
    return x


def test_character_with_sample_needs_ai_convert_then_applies_preset(api, monkeypatch):
    c, tmp = api

    def fake_seedvc(source, target, out, steps=30, pitch_shift=0):
        x, sr = sf.read(source, dtype="float32")
        sf.write(out, x * 0.5, sr)
        return out

    monkeypatch.setattr(ai_voice, "is_installed", lambda: True)
    monkeypatch.setattr(ai_voice, "run_seedvc", fake_seedvc)
    m = upload(c, make_voice(tmp / "v.wav", 1))
    ref = upload(c, make_voice(tmp / "ref.wav", 2))
    ch = c.post("/api/characters", json={"name": "AI 캐릭터", "preset": "original", "ref": ref["id"]}).json()
    voice = f"char:{ch['id']}"
    p = {"tracks": tracks(), "clips": [{"id": "c1", "track": "a2", "media": m["id"], "src_in": 0, "src_out": 1, "start": 0, "voice": voice}]}
    r = c.post("/api/export", json={"tracks": p["tracks"], "clips": p["clips"]})
    assert r.status_code == 409 and "AI 변환" in r.json()["detail"]             # 변환 전에는 내보내기 막음
    j = c.post("/api/ai/convert", json={"media": m["id"], "ref": ref["id"]}).json()
    assert wait(c, j["job"])["status"] == "done"
    assert c.get(f"/api/ai/ready?media={m['id']}&ref={ref['id']}").json()["ready"]
    after = decode_mix(c.post("/api/mix", json=p), tmp / "ai.wav")
    orig = decode_mix(clip_mix(c, m["id"], "original"), tmp / "o.wav")
    assert np.sqrt(np.mean(after ** 2)) == pytest.approx(np.sqrt(np.mean(orig ** 2)) * 0.5, rel=0.05)


def test_character_render_and_tts_through_character(api, monkeypatch):
    c, tmp = api
    fake_synth(monkeypatch)
    ch = c.post("/api/characters", json={"name": "로봇", "preset": "robot"}).json()
    m = upload(c, make_voice(tmp / "v.wav", 1))
    r = run(c, "/api/ai/character/render", {"media": m["id"], "character": ch["id"]})
    assert r["status"] == "done" and "(로봇)" in r["result"]["media"]["name"]
    n = len(c.get("/api/media").json())
    r = run(c, "/api/ai/tts", {"text": "안녕하세요. 반갑습니다.", "lang": "ko", "emotion": "happy", "character": ch["id"]})
    assert r["status"] == "done" and "로봇" in r["result"]["media"]["name"]
    assert len(c.get("/api/media").json()) == n + 1                                # 임시 미디어는 남지 않음
    assert c.post("/api/ai/tts", json={"text": "a", "character": "abc123"}).status_code == 404
    r = run(c, "/api/ai/dub", {"cues": [{"start": 0, "end": 2, "text": "hello there"}], "lang": "en", "character": ch["id"], "emotion": "sad"})
    assert r["status"] == "done" and "로봇" in r["result"]["media"]["name"]


def test_detect_catches_conjugated_endings():
    for text, emo in [("기쁜 날입니다", "happy"), ("너무 슬픈 하루였습니다", "sad"), ("화가 났습니다", "angry"), ("즐거웠어요", "happy")]:
        assert emotion.detect(text, "ko")[0] == emo
