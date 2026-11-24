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

from . import ai_tools, characters, emotion, highlights, reframe, separate, voice_sample
from .editor import SR, Workspace, _check_id, add_media, character_audio, media_audio, remove_media, stretch
from .engine import peak_limit, save_wav
from .presets import load_presets

MAX_DUB_SPEEDUP = 1.6      # 번역 더빙이 자막 시간보다 길 때 최대 이만큼만 빠르게 합니다


class TranscribeReq(BaseModel):
    media: str
    lang: str = "ko"          # "" = 자동 감지
    verbatim: bool = False    # 군말도 받아적기
    max_chars: int = Field(28, ge=6, le=80)   # 자막 한 줄 길이 (쇼츠식 낱말 강조 자막은 짧게)


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
    emotion: str = "neutral"      # neutral | auto(글에서 알아챔) | happy ... — 문장 앞의 [화남] 표시가 가장 우선
    level: float = Field(1.0, ge=0.0, le=2.0)   # 감정의 세기
    character: str = ""           # 보이스 캐릭터 id (비우면 읽는 엔진의 목소리 그대로)
    sample: str = ""              # 캐릭터 없이 샘플 목소리(보관함 소리 파일 id)로 바로 바꿔 읽기 (AI 음성 변환)


class VoiceAnalyzeReq(BaseModel):
    media: str


class VoicePrepareReq(BaseModel):
    media: str
    start: float | None = Field(None, ge=0)     # 비우면 좋은 구간을 자동으로 고릅니다
    end: float | None = Field(None, gt=0)
    denoise: bool = True
    name: str = Field("목소리", min_length=1, max_length=40)


class TranslateReq(BaseModel):
    texts: list[str] = Field(max_length=2000)
    src: str
    dst: str


class DubCue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    emotion: str = ""             # 비우면 DubReq.emotion을 따릅니다


class DubReq(BaseModel):
    cues: list[DubCue] = Field(min_length=1, max_length=2000)
    lang: str
    rate: float = Field(1.0, ge=0.5, le=2.0)
    emotion: str = "neutral"
    level: float = Field(1.0, ge=0.0, le=2.0)
    character: str = ""
    sample: str = ""


class HighlightsReq(BaseModel):
    media: str
    lang: str = "ko"
    n: int = Field(5, ge=1, le=20)
    min_len: float = Field(30.0, ge=5, le=120)
    max_len: float = Field(60.0, ge=10, le=180)
    use_stt: bool = True      # 받아쓰기가 설치돼 있으면 문장 단위로 (없으면 말소리 덩어리로)


class ReframeReq(BaseModel):
    media: str
    start: float = Field(0.0, ge=0)
    end: float = Field(gt=0)
    step: float = Field(0.5, ge=0.2, le=2.0)


class SeparateReq(BaseModel):
    media: str


class EmotionAnalyzeReq(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    lang: str = "ko"
    emotion: str = "auto"
    level: float = Field(1.0, ge=0.0, le=2.0)


class EmotionApplyReq(BaseModel):
    media: str
    lang: str = "ko"
    emotion: str = "auto"         # auto = 받아쓰기로 문장마다 글에서 알아챔, 아니면 모든 문장에 같은 감정
    level: float = Field(1.0, ge=0.0, le=2.0)


class CharacterRenderReq(BaseModel):
    media: str
    character: str


def ai_voice_ready() -> bool:
    from . import ai_voice
    return ai_voice.is_installed()


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

    def synth_to_array(text: str, lang: str, rate: float, voice: str = "") -> np.ndarray:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "s.wav"
            ai_tools.synthesize(text, lang, wav, rate, voice)
            x, _ = sf.read(wav, dtype="float32")
        return x

    def get_character(cid: str, sample: str = "") -> characters.Character | None:
        if not cid and sample:   # 저장한 캐릭터가 없어도 샘플 소리만 고르면 그 목소리로 바꿔 줍니다 (학습 없이, Seed-VC 제로샷)
            audio_media(sample)
            if not ai_voice_ready():
                raise HTTPException(503, "샘플 목소리로 바꾸려면 AI 음성 변환이 필요해요. 터미널에서 `bash scripts/setup_ai_voice.sh`를 실행하세요.")
            return characters.Character(id="sample", name="샘플 목소리", ref=sample)
        if not cid:
            return None
        _check_id(cid)
        ch = characters.get(ws.home, cid)
        if ch is None:
            raise HTTPException(404, "보이스 캐릭터를 찾을 수 없어요.")
        if ch.ref and not ai_voice_ready():
            raise HTTPException(503, "이 캐릭터는 AI 음성 변환이 필요해요. 터미널에서 `bash scripts/setup_ai_voice.sh`를 실행하세요.")
        return ch

    def through_character(ch: characters.Character):
        """소리 배열 → 캐릭터 목소리로 바꾼 소리 (같은 길이). 보관함에 잠깐 올렸다가 지웁니다."""
        def post(audio: np.ndarray) -> np.ndarray:
            tmp = ws.root / "cache" / f"chr_{uuid.uuid4().hex}.wav"
            save_wav(tmp, audio)
            meta = add_media(ws, tmp, "임시.wav")
            try:
                out = character_audio(ws, meta["id"], ch, convert=True)
            finally:
                remove_media(ws, meta["id"])
            return out if out is not None else audio
        return post

    @router.get("/ai/tools")
    def tools_status():
        return {**ai_tools.status(), "emotions": emotion.EMOTIONS, "separate": separate.is_installed(),
                "separate_setup": separate.SETUP_CMD, "yunet": ai_tools.yunet_model().exists(),
                "voice_ai": ai_voice_ready()}

    @router.post("/ai/emotion/analyze")
    def emotion_analyze(b: EmotionAnalyzeReq):
        _lang(b.lang)
        try:
            items = emotion.plan(b.text, b.lang, b.emotion, b.level)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"items": [{**i, "label": emotion.EMOTIONS[i["emotion"]]} for i in items]}

    @router.post("/ai/emotion/apply")
    def emotion_apply(b: EmotionApplyReq):
        audio_media(b.media), _lang(b.lang)
        auto = b.emotion in ("", "auto")
        if not auto and emotion.normalize(b.emotion) is None:
            raise HTTPException(400, f"모르는 감정: {b.emotion}")
        if auto:
            need_installed()

        def work():
            x = media_audio(ws, b.media)
            if auto:       # 문장마다 글에서 감정을 알아챔
                units = highlights.sentence_units(stt(b.media, b.lang, False)["segments"])
                spans = []
                for u in units:
                    emo, strength = emotion.detect(u["text"], b.lang)
                    spans.append({"start": u["start"], "end": u["end"], "emotion": emo, "level": b.level * (0.6 + 0.4 * strength),
                                  "question": u["text"].rstrip().endswith(("?", "？"))})
            else:          # 모든 말 덩어리에 같은 감정
                emo = emotion.normalize(b.emotion)
                spans = [{"start": u["start"], "end": u["end"], "emotion": emo, "level": b.level, "question": False}
                         for u in highlights.units_from_audio(x, SR)]
            y = emotion.apply_spans(x, spans)
            tmp = ws.root / "cache" / f"emo_{uuid.uuid4().hex}.wav"
            save_wav(tmp, peak_limit(y, SR))
            used = sorted({sp["emotion"] for sp in spans if sp["emotion"] != "neutral"})
            tag = "·".join(emotion.EMOTIONS[e] for e in used) or "보통"
            meta = add_media(ws, tmp, f"{Path(ws.meta(b.media)['name']).stem} (감정 {tag}).wav")
            return {"media": meta, "spans": [{**sp, "label": emotion.EMOTIONS[sp["emotion"]]} for sp in spans]}

        return launch(b.media, f"emo:{b.lang}:{b.emotion}:{b.level}", work)

    # ---- 글로 편집(낱말 시각) · 하이라이트 · 리프레임 · 보컬 분리
    @router.post("/ai/highlights")
    def highlights_route(b: HighlightsReq):
        audio_media(b.media), _lang(b.lang)
        if b.max_len < b.min_len:
            raise HTTPException(400, "최대 길이는 최소 길이보다 길어야 해요.")

        def work():
            data = stt(b.media, b.lang, False) if b.use_stt and ai_tools.is_installed() else None
            audio = media_audio(ws, b.media)
            units = highlights.sentence_units(data["segments"]) if data else highlights.units_from_audio(audio, SR)
            items = highlights.find_highlights(units, audio, SR, data["lang"] if data else b.lang, b.n, b.min_len,
                                               b.max_len, ws.meta(b.media)["duration"])
            return {"items": items, "stt": data is not None}

        return launch(b.media, f"hl:{b.lang}:{b.n}:{b.min_len}:{b.max_len}:{int(b.use_stt)}", work)

    @router.post("/ai/reframe")
    def reframe_route(b: ReframeReq):
        _check_id(b.media)
        meta = ws.meta(b.media)
        if not meta["has_video"]:
            raise HTTPException(400, "영상이 아닌 미디어입니다.")
        if b.end <= b.start + 0.5 or b.end - b.start > 900:
            raise HTTPException(400, "구간은 0.5초 이상 15분 이하로 정해 주세요.")
        need_installed()

        def work():
            try:
                frames = ai_tools.detect_subjects(ws.media_file(b.media), b.start, b.end, b.step)
            except RuntimeError as e:
                if "No module named" in str(e) and "cv2" in str(e):
                    raise RuntimeError(f"자동 리프레임에는 OpenCV가 필요해요. 터미널에서 `{separate.SETUP_CMD}`를 실행하세요. (원문: {str(e)[-200:]})") from None
                raise
            found = sum(1 for f in frames if f.get("boxes"))
            return {"track": reframe.build_track(frames, b.start, b.end, max(b.step, 0.25)) if frames else [],
                    "found": found, "total": len(frames)}

        return launch(b.media, f"rf:{b.start}:{b.end}:{b.step}", work)

    @router.post("/ai/separate")
    def separate_route(b: SeparateReq):
        audio_media(b.media)
        if not separate.is_installed():
            raise HTTPException(503, f"보컬 분리가 설치되지 않았습니다. 터미널에서 `{separate.SETUP_CMD}`를 실행하세요.")

        def work():
            name = Path(ws.meta(b.media)["name"]).stem
            v, g = (ws.root / "cache" / f"sep_{uuid.uuid4().hex}_{k}.wav" for k in ("v", "b"))
            try:
                separate.separate(ws.media_file(b.media), v, g)
                return {"vocals": add_media(ws, v, f"{name} (목소리).wav"), "background": add_media(ws, g, f"{name} (배경음).wav")}
            finally:
                v.unlink(missing_ok=True), g.unlink(missing_ok=True)

        return launch(b.media, "sep", work)

    # ---- 보이스 캐릭터
    def check_character(ch: characters.Character) -> None:
        if ch.preset != "original" and ch.preset not in load_presets():
            raise HTTPException(400, f"없는 프리셋: {ch.preset}")
        if ch.ref:
            _check_id(ch.ref)
            if not ws.meta(ch.ref)["has_audio"]:
                raise HTTPException(400, "샘플 목소리로 쓸 미디어에 소리가 없어요.")

    @router.get("/characters")
    def list_characters():
        return [c.model_dump() for c in characters.load(ws.home)]

    @router.post("/characters")
    def create_character(b: characters.Character):
        check_character(b)
        if b.ref:
            ws.promote(b.ref)       # 캐릭터는 모든 프로젝트가 쓰니 샘플 목소리 파일도 공유 보관함에 둡니다
        return characters.create(ws.home, b).model_dump()

    @router.put("/characters/{cid}")
    def update_character(cid: str, b: characters.Character):
        _check_id(cid), check_character(b)
        if b.ref:
            ws.promote(b.ref)
        ch = characters.update(ws.home, cid, b)
        if ch is None:
            raise HTTPException(404, "보이스 캐릭터를 찾을 수 없어요.")
        return ch.model_dump()

    @router.delete("/characters/{cid}")
    def delete_character(cid: str):
        _check_id(cid)
        if not characters.delete(ws.home, cid):
            raise HTTPException(404, "보이스 캐릭터를 찾을 수 없어요.")
        return {"ok": True}

    @router.post("/ai/character/render")
    def character_render(b: CharacterRenderReq):
        audio_media(b.media)
        ch = get_character(b.character)

        def work():
            y = character_audio(ws, b.media, ch, convert=True)
            tmp = ws.root / "cache" / f"chr_{uuid.uuid4().hex}.wav"
            save_wav(tmp, peak_limit(y, SR))
            return {"media": add_media(ws, tmp, f"{Path(ws.meta(b.media)['name']).stem} ({ch.name}).wav")}

        return launch(b.media, f"chr:{ch.id}:{ch.signature()}", work)

    @router.post("/ai/transcribe")
    def transcribe(b: TranscribeReq):
        audio_media(b.media), _lang(b.lang, True), need_installed()

        def work():
            data = stt(b.media, b.lang, b.verbatim)
            return {"lang": data["lang"], "cues": ai_tools.segments_to_cues(data["segments"], data["lang"], b.max_chars),
                    "segments": ai_tools.segment_words(data["segments"])}

        return launch(b.media, f"stt:{b.lang}:{int(b.verbatim)}:{b.max_chars}", work)

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
        if b.emotion not in ("auto", "") and emotion.normalize(b.emotion) is None:
            raise HTTPException(400, f"모르는 감정: {b.emotion}")
        ch = get_character(b.character, b.sample)
        plain = (b.emotion in ("neutral", "") and not emotion.TAG.search(b.text) and ch is None)

        def work():
            tmp = ws.root / "cache" / f"tts_{uuid.uuid4().hex}.wav"
            label = b.text.strip().replace("\n", " ")[:14]
            spans = []
            if plain:
                ai_tools.synthesize(b.text, b.lang, tmp, b.rate, b.voice)
            else:
                audio, spans = emotion.synth_emotional(
                    b.text, b.lang, lambda t, r: synth_to_array(t, b.lang, r, b.voice), b.emotion or "neutral", b.level, b.rate,
                    post=through_character(ch) if ch else None)
                save_wav(tmp, audio)
            suffix = (f" · {ch.name}" if ch else "")
            return {"media": add_media(ws, tmp, f"읽기 {b.lang} · {label}{suffix}.wav"),
                    "spans": [{**sp, "label": emotion.EMOTIONS[sp["emotion"]]} for sp in spans]}

        return launch("tts", f"{b.lang}:{b.rate}:{b.voice}:{b.emotion}:{b.level}:{b.character}:{b.sample}:{hash(b.text)}", work)

    @router.post("/voice/analyze")
    def voice_analyze(b: VoiceAnalyzeReq):
        audio_media(b.media)
        return voice_sample.analyze(media_audio(ws, b.media), SR)

    @router.post("/voice/prepare")
    def voice_prepare(b: VoicePrepareReq):
        """샘플 구간을 잘라 (선택: 잡음 제거) 보관함에 '목소리 샘플'로 올립니다."""
        audio_media(b.media)

        def work():
            x = media_audio(ws, b.media)
            info = voice_sample.analyze(x, SR)
            start = info["start"] if b.start is None else b.start
            end = info["end"] if b.end is None else b.end
            end = min(end, x.size / SR, start + voice_sample.MAX_S + 10)
            if end - start < 1.0:
                raise RuntimeError("고른 구간이 너무 짧아요 (1초 이상).")
            seg = x[int(start * SR): int(end * SR)].copy()
            peak = float(np.abs(seg).max()) or 1.0
            seg *= min(4.0, 0.9 / peak)
            tmp = ws.root / "cache" / f"vs_{uuid.uuid4().hex}.wav"
            save_wav(tmp, seg)
            engine = "none"
            if b.denoise:
                tmp2 = ws.root / "cache" / f"vs_{uuid.uuid4().hex}.wav"
                engine = ai_tools.denoise(tmp, tmp2)
                tmp.unlink(missing_ok=True)
                tmp = tmp2
            meta = add_media(ws, tmp, f"목소리 샘플 · {b.name}.wav")
            return {"media": meta, "start": round(start, 2), "end": round(end, 2), "engine": engine, "analysis": info}

        return launch("voice", f"{b.media}:{b.start}:{b.end}:{b.denoise}:{b.name}", work)

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
        for e in [b.emotion, *(c.emotion for c in b.cues)]:
            if e not in ("", "auto") and emotion.normalize(e) is None:
                raise HTTPException(400, f"모르는 감정: {e}")
        ch = get_character(b.character, b.sample)

        def work():
            return {"media": dub_media(ws, b, synth_to_array, through_character(ch) if ch else None, ch.name if ch else "")}

        key = hash(tuple((c.start, c.end, c.text, c.emotion) for c in b.cues))
        return launch("dub", f"{b.lang}:{b.rate}:{b.emotion}:{b.level}:{b.character}:{b.sample}:{key}", work)


def dub_media(ws: Workspace, b: DubReq, synth_to_array, post=None, who: str = "") -> dict:
    """자막 구간마다 읽은 소리를 제 시각에 얹어 한 파일로 만듭니다. 자막 시간보다 길면 (최대 1.6배까지) 빠르게 합니다.
    감정은 자막마다(자막에 지정한 감정 > 전체 설정 > 자동), 말투는 캐릭터 목소리로 바꾼 *뒤에* 입힙니다."""
    cues = sorted((c for c in b.cues if c.text.strip()), key=lambda c: c.start)
    if not cues:
        raise RuntimeError("읽을 자막이 없어요.")
    total = max(c.end for c in cues) + 0.5
    bus = np.zeros(int(total * SR) + 1, dtype=np.float32)
    spans: list[dict] = []
    for i, c in enumerate(cues):
        x, cs = emotion_synth(c, b, lambda t, r: synth_to_array(t, b.lang, r))
        slot = c.end - c.start
        need = x.size / SR / slot
        k = 1.0
        if need > 1.05:
            k = min(need, MAX_DUB_SPEEDUP)
            x = stretch(x, k)
        nxt = cues[i + 1].start if i + 1 < len(cues) else total
        keep = max(0, int((nxt - c.start) * SR))
        x = x[:keep]                                            # 다음 자막과 겹치지 않게
        if x.size > 64:
            x = x.copy()
            x[-64:] *= np.linspace(1, 0, 64, dtype=np.float32)
        at = int(c.start * SR)
        bus[at: at + x.size] += x[: bus.size - at]
        for sp in cs:
            spans.append({**sp, "start": c.start + sp["start"] / k, "end": min(c.start + sp["end"] / k, c.start + keep / SR)})
    if post is not None:
        y = post(bus)
        bus = np.pad(y, (0, max(0, bus.size - y.size)))[: bus.size]
    bus = emotion.apply_spans(bus, spans)
    out = ws.root / "cache" / f"dub_{uuid.uuid4().hex}.wav"
    save_wav(out, peak_limit(bus, SR))
    return add_media(ws, out, f"더빙 {b.lang}{' · ' + who if who else ''}.wav")


def emotion_synth(c: DubCue, b: DubReq, synth) -> tuple[np.ndarray, list[dict]]:
    """자막 한 칸을 (감정 없이) 읽은 소리와, 나중에 입힐 감정 구간 목록 (이 소리의 0초 기준)."""
    emo = c.emotion or b.emotion or "neutral"
    items = emotion.plan(c.text, b.lang, emo, b.level)
    if not items:
        raise RuntimeError("읽을 글이 없어요.")
    parts, spans, t = [], [], 0.0
    for i, it in enumerate(items):
        pr = emotion.profile_for(it["emotion"], it["level"])
        x = synth(it["text"], float(np.clip(b.rate * pr.rate, 0.4, 2.5)))
        if i:
            gap = np.zeros(int(SR * pr.pause), dtype=np.float32)
            parts.append(gap)
            t += gap.size / SR
        spans.append({**it, "start": t, "end": t + x.size / SR})
        parts.append(x)
        t += x.size / SR
    return np.concatenate(parts), spans
