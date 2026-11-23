"""AI 편집 도구의 서버 주소 (/api/ai/...). 오래 걸리는 작업은 AI 음성 변환과 같은 작업 목록(JobRunner)으로
한 번에 하나씩 돌리고, 화면은 /api/ai/jobs/{id}로 결과를 받아 갑니다 (result에 결과가 들어 있음).

시각은 모두 '미디어 기준 초'입니다. 타임라인 위치로 바꾸는 일은 화면(클립의 start·src_in·speed)이 합니다.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import ai_tools
from .editor import SR, Workspace, _check_id, add_media, media_audio, stretch
from .engine import peak_limit, save_wav

MAX_DUB_SPEEDUP = 1.6      # 번역 더빙이 자막 시간보다 길 때 최대 이만큼만 빠르게 합니다


class TranscribeReq(BaseModel):
    media: str
    lang: str = "ko"          # "" = 자동 감지
    verbatim: bool = False    # 군말도 받아적기


class CutsReq(BaseModel):
    media: str
    lang: str = "ko"
    silence: bool = True
    fillers: bool = False
    min_silence: float = Field(0.5, ge=0.1, le=5.0)
    threshold_db: float | None = Field(None, ge=-80, le=-5)
    pad: float = Field(0.08, ge=0.0, le=0.5)
    words: list[str] = []     # 군말 목록에 더할 낱말


class ScenesReq(BaseModel):
    media: str
    threshold: float = Field(0.3, ge=0.05, le=0.9)


class DenoiseReq(BaseModel):
    media: str


class TtsReq(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    lang: str = "ko"
    rate: float = Field(1.0, ge=0.5, le=2.0)
    voice: str = ""


class TranslateReq(BaseModel):
    texts: list[str] = Field(max_length=2000)
    src: str
    dst: str


class DubCue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str


class DubReq(BaseModel):
    cues: list[DubCue] = Field(min_length=1, max_length=2000)
    lang: str
    rate: float = Field(1.0, ge=0.5, le=2.0)


def _lang(x: str, allow_empty: bool = False) -> str:
    if (allow_empty and not x) or x in ai_tools.LANGS:
        return x
    raise HTTPException(400, f"지원하지 않는 언어: {x}")


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    def launch(media: str, ref: str, work):
        job = jobs.active(media, ref) or jobs.submit(media, ref, work)
        return {"status": job.status, "job": job.id}

    def need_installed():
        if not ai_tools.is_installed():
            raise HTTPException(503, f"AI 도구가 설치되지 않았습니다. 터미널에서 `{ai_tools.SETUP_CMD}`를 실행하세요.")

    def audio_media(mid: str) -> None:
        _check_id(mid)
        if not ws.meta(mid)["has_audio"]:
            raise HTTPException(400, "소리가 없는 미디어입니다.")

    def stt(mid: str, lang: str, verbatim: bool) -> dict:
        """받아쓰기 (미디어·언어별로 캐시)."""
        cache = ws.root / "cache" / f"{mid}_stt_{lang or 'auto'}_{int(verbatim)}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        media_audio(ws, mid)
        res = ai_tools.transcribe(ws.root / "cache" / f"{mid}_raw.wav", lang, verbatim)
        data = {"lang": res["lang"], "segments": res["segments"]}
        cache.write_text(json.dumps(data, ensure_ascii=False))
        return data

    @router.get("/ai/tools")
    def tools_status():
        return ai_tools.status()

    @router.post("/ai/transcribe")
    def transcribe(b: TranscribeReq):
        audio_media(b.media), _lang(b.lang, True), need_installed()

        def work():
            data = stt(b.media, b.lang, b.verbatim)
            return {"lang": data["lang"], "cues": ai_tools.segments_to_cues(data["segments"], data["lang"])}

        return launch(b.media, f"stt:{b.lang}:{int(b.verbatim)}", work)

    @router.post("/ai/cuts")
    def cuts(b: CutsReq):
        audio_media(b.media), _lang(b.lang)
        if not (b.silence or b.fillers):
            raise HTTPException(400, "무음이나 군말 중 하나는 골라야 해요.")
        if b.fillers:
            need_installed()

        def work():
            found = []
            if b.silence:
                found += ai_tools.silence_ranges(media_audio(ws, b.media), SR, b.min_silence, b.threshold_db, b.pad)
            if b.fillers:
                found += ai_tools.find_fillers(stt(b.media, b.lang, True)["segments"], b.lang, b.words)
            merged = ai_tools.merge_cuts(found)
            return {"cuts": merged, "removed": round(sum(c["end"] - c["start"] for c in merged), 3),
                    "duration": ws.meta(b.media)["duration"]}

        key = f"cuts:{b.lang}:{int(b.silence)}{int(b.fillers)}:{b.min_silence}:{b.threshold_db}:{b.pad}:{','.join(b.words)}"
        return launch(b.media, key, work)

    @router.post("/ai/scenes")
    def scenes(b: ScenesReq):
        _check_id(b.media)
        if not ws.meta(b.media)["has_video"]:
            raise HTTPException(400, "영상이 아닌 미디어입니다.")
        return launch(b.media, f"scenes:{b.threshold}",
                      lambda: {"times": ai_tools.scene_changes(ws.media_file(b.media), b.threshold)})

    @router.post("/ai/denoise")
    def denoise(b: DenoiseReq):
        audio_media(b.media)

        def work():
            meta = ws.meta(b.media)
            tmp = ws.root / "cache" / f"dn_{uuid.uuid4().hex}.wav"
            engine = ai_tools.denoise(ws.media_file(b.media), tmp)
            new = add_media(ws, tmp, f"{Path(meta['name']).stem} (노이즈 제거).wav")
            return {"media": new, "engine": engine}

        return launch(b.media, "denoise", work)

    @router.post("/ai/tts")
    def tts(b: TtsReq):
        _lang(b.lang)
        if ai_tools.tts_engine() is None:
            raise HTTPException(503, "읽어 주는 엔진이 없어요 (맥은 기본 내장, 리눅스는 espeak-ng 설치).")

        def work():
            tmp = ws.root / "cache" / f"tts_{uuid.uuid4().hex}.wav"
            ai_tools.synthesize(b.text, b.lang, tmp, b.rate, b.voice)
            label = b.text.strip().replace("\n", " ")[:14]
            return {"media": add_media(ws, tmp, f"읽기 {b.lang} · {label}.wav")}

        return launch("tts", f"{b.lang}:{b.rate}:{b.voice}:{hash(b.text)}", work)

    @router.post("/ai/translate")
    def translate(b: TranslateReq):
        _lang(b.src), _lang(b.dst), need_installed()
        if b.src == b.dst:
            raise HTTPException(400, "원본 언어와 번역할 언어가 같아요.")
        return launch("translate", f"{b.src}:{b.dst}:{hash(tuple(b.texts))}",
                      lambda: {"texts": ai_tools.translate(b.texts, b.src, b.dst)})

    @router.post("/ai/dub")
    def dub(b: DubReq):
        _lang(b.lang)
        if ai_tools.tts_engine() is None:
            raise HTTPException(503, "읽어 주는 엔진이 없어요 (맥은 기본 내장, 리눅스는 espeak-ng 설치).")

        def work():
            return {"media": dub_media(ws, b)}

        return launch("dub", f"{b.lang}:{b.rate}:{hash(tuple((c.start, c.end, c.text) for c in b.cues))}", work)


def dub_media(ws: Workspace, b: DubReq) -> dict:
    """자막 구간마다 읽은 소리를 제 시각에 얹어 한 파일로 만듭니다. 자막 시간보다 길면 (최대 1.6배까지) 빠르게 합니다."""
    cues = sorted((c for c in b.cues if c.text.strip()), key=lambda c: c.start)
    if not cues:
        raise RuntimeError("읽을 자막이 없어요.")
    total = max(c.end for c in cues) + 0.5
    bus = np.zeros(int(total * SR) + 1, dtype=np.float32)
    with tempfile.TemporaryDirectory() as tmp:
        for i, c in enumerate(cues):
            wav = Path(tmp) / f"{i}.wav"
            ai_tools.synthesize(c.text, b.lang, wav, b.rate)
            x, _ = sf.read(wav, dtype="float32")
            slot = c.end - c.start
            need = x.size / SR / slot
            if need > 1.05:
                x = stretch(x, min(need, MAX_DUB_SPEEDUP))
            nxt = cues[i + 1].start if i + 1 < len(cues) else total
            x = x[: max(0, int((nxt - c.start) * SR))]          # 다음 자막과 겹치지 않게
            if x.size > 64:
                x = x.copy()
                x[-64:] *= np.linspace(1, 0, 64, dtype=np.float32)
            at = int(c.start * SR)
            bus[at: at + x.size] += x[: bus.size - at]
    out = ws.root / "cache" / f"dub_{uuid.uuid4().hex}.wav"
    save_wav(out, peak_limit(bus, SR))
    return add_media(ws, out, f"더빙 {b.lang}.wav")
