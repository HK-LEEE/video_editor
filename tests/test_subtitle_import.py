"""자막 파일 불러오기(SRT·VTT·ASS) 파서와 API."""
import pytest

from video_editor.subtitle_import import SubtitleParseError, parse_subtitles

SRT = "1\n00:00:01,000 --> 00:00:03,500\n안녕하세요\n반가워요\n\n2\n00:00:04,000 --> 00:00:06,000\n<i>두 번째</i> 자막\n"
VTT = "WEBVTT\n\n00:01.000 --> 00:02.500\n첫 줄\n\ncue2\n01:00:00.000 --> 01:00:01.000 align:start\n둘째\n"
ASS = ("[Script Info]\nTitle: x\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
       "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an8}안녕\\N하세요, 반가워요\n")


def test_srt_basic_and_tags():
    r = parse_subtitles(SRT.encode("utf-8"), "a.srt")
    assert r["format"] == "srt" and r["encoding"] == "utf-8"
    assert r["cues"][0] == {"start": 1.0, "end": 3.5, "text": "안녕하세요\n반가워요"}
    assert r["cues"][1]["text"] == "두 번째 자막"


def test_encoding_detection():
    assert parse_subtitles(SRT.encode("cp949"), "a.srt")["encoding"] == "cp949"
    assert parse_subtitles(SRT.encode("utf-8-sig"), "a.srt")["encoding"] == "utf-8"
    r = parse_subtitles(SRT.encode("utf-16"), "a.srt")
    assert r["encoding"] == "utf-16" and r["cues"][0]["text"].startswith("안녕")


def test_vtt_short_times_and_cue_ids():
    r = parse_subtitles(VTT.encode(), "a.vtt")
    assert r["format"] == "vtt"
    assert [(c["start"], c["end"], c["text"]) for c in r["cues"]] == [(1.0, 2.5, "첫 줄"), (3600.0, 3601.0, "둘째")]


def test_ass_dialogue_with_commas_and_breaks():
    r = parse_subtitles(ASS.encode(), "a.ass")
    assert r["format"] == "ass"
    assert r["cues"] == [{"start": 1.0, "end": 2.5, "text": "안녕\n하세요, 반가워요"}]


def test_crlf_and_errors():
    assert len(parse_subtitles(SRT.replace("\n", "\r\n").encode(), "a.srt")["cues"]) == 2
    for bad in (b"", b"   \n", b"hello world"):
        with pytest.raises(SubtitleParseError):
            parse_subtitles(bad, "a.srt")
