"""더빙 스튜디오 서버.

영상을 올리고, 영상에 맞춰 녹음한 테이크를 저장하고, 타임라인의 구간(클립)마다
다른 목소리로 변조해 영상에 입혀 내보냅니다.

클립 = 테이크의 [src_in, src_out] 구간을 타임라인 start 초에 놓은 것 + 목소리 설정.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .engine import SAMPLE_RATE as SR, VoiceSettings, load_audio, process, save_wav
from .presets import load_presets

ORIGINAL = "original"  # 변조 없음


class Workspace:
    """영상·테이크·변조 결과·내보낸 파일을 한 폴더에 보관합니다."""

    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ("videos", "takes", "processed", "exports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def video_path(self, vid: str) -> Path:
        _check_id(vid)
        hits = list((self.root / "videos").glob(f"{vid}.*"))
        if not hits:
            raise HTTPException(404, "영상을 찾을 수 없습니다.")
        return hits[0]

    def take_path(self, tid: str) -> Path:
        _check_id(tid)
        p = self.root / "takes" / f"{tid}.wav"
        if not p.exists():
            raise HTTPException(404, "녹음을 찾을 수 없습니다.")
        return p


def _check_id(x: str) -> None:
    if not x or not all(c in "0123456789abcdef" for c in x):
        raise HTTPException(400, "잘못된 id")


def media_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True,
    ).stdout
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout
    return bool(out.strip())


# ---------------------------------------------------------------- 변조·믹스

def voice_for(name: str) -> VoiceSettings:
    if name == ORIGINAL:
        return VoiceSettings(name=ORIGINAL, label="원본")
    presets = load_presets()
    if name not in presets:
        raise HTTPException(400, f"없는 목소리: {name}")
    return presets[name]


def processed_take(ws: Workspace, tid: str, voice: str, intensity: float) -> Path:
    """테이크 전체를 해당 목소리로 변조한 파일 (캐시). 시간축은 원본과 같습니다."""
    intensity = round(float(intensity), 2)
    out = ws.root / "processed" / f"{tid}_{voice}_{intensity}.wav"
    if not out.exists():
        audio, _ = sf.read(ws.take_path(tid), dtype="float32")
        y = process(audio, voice_for(voice), trim=False, intensity=intensity)
        save_wav(out, y)
    return out


class Clip(BaseModel):
    take_id: str
    src_in: float = Field(ge=0)
    src_out: float = Field(gt=0)
    start: float = Field(ge=0)
    voice: str = ORIGINAL
    intensity: float = Field(1.0, ge=0.1, le=5.0)
    gain: float = Field(1.0, ge=0.0, le=4.0)


class ExportRequest(BaseModel):
    video_id: str
    clips: list[Clip]
    original_volume: float = Field(0.0, ge=0.0, le=2.0)


def _fade(x: np.ndarray, n: int = int(SR * 0.008)) -> np.ndarray:
    n = min(n, x.size // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def mix_clips(ws: Workspace, clips: list[Clip], duration: float) -> np.ndarray:
    total = np.zeros(int(round(duration * SR)) + 1, dtype=np.float32)
    for c in clips:
        if c.src_out <= c.src_in:
            continue
        audio, _ = sf.read(processed_take(ws, c.take_id, c.voice, c.intensity), dtype="float32")
        seg = audio[int(c.src_in * SR): int(c.src_out * SR)].copy()
        at = int(round(c.start * SR))
        seg = seg[: max(0, total.size - at)]
        if seg.size == 0:
            continue
        total[at: at + seg.size] += _fade(seg) * c.gain
    return total


def export_video(ws: Workspace, req: ExportRequest) -> Path:
    video = ws.video_path(req.video_id)
    duration = media_duration(video)
    if duration <= 0:
        raise HTTPException(400, "영상 길이를 읽지 못했습니다.")
    mix = mix_clips(ws, req.clips, duration)
    if req.original_volume > 0 and has_audio(video):
        orig = load_audio(video)[: mix.size]
        mix[: orig.size] += orig * req.original_volume
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.99:
        mix *= 0.99 / peak

    name = f"{video.stem}_dub_{uuid.uuid4().hex[:6]}"
    wav = ws.root / "exports" / f"{name}.wav"
    save_wav(wav, mix)
    out = ws.root / "exports" / f"{name}.mp4"
    base = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav),
            "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    r = subprocess.run(base + ["-c:v", "copy", str(out)], capture_output=True, text=True)
    if r.returncode != 0:  # 영상 코덱이 mp4에 그대로 안 들어가면 다시 인코딩
        r = subprocess.run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                   "-pix_fmt", "yuv420p", str(out)], capture_output=True, text=True)
        if r.returncode != 0:
            raise HTTPException(500, f"내보내기 실패: {r.stderr[-500:]}")
    return out


# ---------------------------------------------------------------- API

def make_router(ws: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/voices")
    def voices():
        items = [{"name": ORIGINAL, "label": "원본 (변조 없음)"}]
        items += [{"name": n, "label": s.label} for n, s in load_presets().items()]
        return items

    @router.post("/videos")
    def upload_video(file: UploadFile = File(...)):
        vid = uuid.uuid4().hex
        ext = Path(file.filename or "video.mp4").suffix.lower() or ".mp4"
        if not ext[1:].isalnum():
            ext = ".mp4"
        path = ws.root / "videos" / f"{vid}{ext}"
        with path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"id": vid, "name": file.filename, "duration": media_duration(path), "has_audio": has_audio(path)}

    @router.get("/videos/{vid}/info")
    def video_info(vid: str):
        path = ws.video_path(vid)
        return {"id": vid, "duration": media_duration(path), "has_audio": has_audio(path)}

    @router.get("/videos/{vid}")
    def get_video(vid: str):
        return FileResponse(ws.video_path(vid))

    @router.post("/takes")
    def upload_take(file: UploadFile = File(...)):
        tid = uuid.uuid4().hex
        raw = ws.root / "takes" / f"{tid}.upload"
        with raw.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            audio = load_audio(raw)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"녹음을 읽지 못했습니다: {e}") from e
        finally:
            raw.unlink(missing_ok=True)
        save_wav(ws.root / "takes" / f"{tid}.wav", audio)
        return {"id": tid, "duration": audio.size / SR}

    @router.get("/takes/{tid}/audio")
    def take_audio(tid: str, voice: str = ORIGINAL, intensity: float = 1.0):
        # 미리듣기와 내보내기가 같은 소리가 되도록 항상 같은 변조 결과를 씁니다
        return FileResponse(processed_take(ws, tid, voice, intensity), media_type="audio/wav")

    @router.post("/export")
    def export(req: ExportRequest):
        out = export_video(ws, req)
        return {"file": out.name, "url": f"/api/exports/{out.name}"}

    @router.get("/exports/{name}")
    def get_export(name: str):
        p = ws.root / "exports" / Path(name).name
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name)

    return router
