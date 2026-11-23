"""멀티트랙 편집기 서버.

프로젝트 = 트랙 목록 + 클립 목록. 클립은 보관함의 미디어(영상·오디오·녹음) 중
[src_in, src_out] 구간을 타임라인 start 초에 놓은 것입니다.

- 영상 트랙: 아래 트랙부터 쌓이고, 같은 시각에 여러 클립이 있으면 위(목록 뒤쪽) 트랙이 보입니다.
- 오디오 트랙: 모두 섞입니다. 트랙마다 볼륨·음소거·솔로, 녹이기 효과, 덕킹이 있습니다.
- 보이스 트랙(voice=True)의 소리가 나올 때 다른 트랙은 duck 만큼 줄어듭니다.

미리듣기(/api/mix)와 내보내기(/api/export)는 같은 render_audio를 씁니다.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
import pedalboard as pb
import soundfile as sf
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import ai_voice
from .engine import SAMPLE_RATE as SR, VoiceSettings, load_audio, peak_limit, process, save_wav
from .presets import load_presets

ORIGINAL = "original"
VOICE_TARGET_DB = -20.0
DUCK_MAX_DB = 15.0
FPS = 30
PEAKS_PER_S = 100


# ================================================================ 보관함

class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        for sub in ("media", "cache", "exports"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def meta_path(self, mid: str) -> Path:
        _check_id(mid)
        return self.root / "media" / f"{mid}.json"

    def meta(self, mid: str) -> dict:
        p = self.meta_path(mid)
        if not p.exists():
            raise HTTPException(404, "미디어를 찾을 수 없습니다.")
        return json.loads(p.read_text())

    def media_file(self, mid: str) -> Path:
        return self.root / "media" / self.meta(mid)["file"]


def _check_id(x: str) -> None:
    if not x or len(x) > 64 or not all(c in "0123456789abcdef" for c in x):
        raise HTTPException(400, "잘못된 id")


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)], capture_output=True, text=True).stdout
    try:
        info = json.loads(out)
    except json.JSONDecodeError:
        info = {}
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("width")), None)
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        duration = 0.0
    return {
        "duration": duration,
        "has_video": v is not None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
    }


def add_media(ws: Workspace, src: Path, name: str, recorded: bool = False) -> dict:
    mid = uuid.uuid4().hex
    ext = Path(name).suffix.lower()
    if not ext[1:].isalnum():
        ext = ""
    if recorded:
        # 브라우저 녹음(webm/mp4)은 바로 wav로 바꿔 둡니다
        audio = load_audio(src)
        save_wav(ws.root / "media" / f"{mid}.wav", audio)
        src.unlink(missing_ok=True)
        ext = ".wav"
    else:
        shutil.move(str(src), ws.root / "media" / f"{mid}{ext}")
    file = f"{mid}{ext}"
    info = probe(ws.root / "media" / file)
    if info["duration"] <= 0 or not (info["has_audio"] or info["has_video"]):
        (ws.root / "media" / file).unlink(missing_ok=True)
        raise HTTPException(400, "영상이나 소리를 읽을 수 없는 파일입니다.")
    meta = {"id": mid, "name": name, "file": file, "recorded": recorded,
            "kind": "video" if info["has_video"] else "audio", **info}
    ws.meta_path(mid).write_text(json.dumps(meta, ensure_ascii=False))
    return meta


def media_audio(ws: Workspace, mid: str) -> np.ndarray:
    """미디어의 소리를 48kHz 모노로 (캐시). 소리가 없으면 빈 배열."""
    meta = ws.meta(mid)
    if not meta["has_audio"]:
        return np.zeros(0, dtype=np.float32)
    cache = ws.root / "cache" / f"{mid}_raw.wav"
    if not cache.exists():
        save_wav(cache, load_audio(ws.media_file(mid)))
    a, _ = sf.read(cache, dtype="float32")
    return a


def voice_for(name: str) -> VoiceSettings:
    presets = load_presets()
    if name not in presets:
        raise HTTPException(400, f"없는 목소리: {name}")
    return presets[name]


def ai_cache(ws: Workspace, mid: str, ref: str) -> Path:
    return ws.root / "cache" / ai_voice.cache_name(mid, ref)


def convert_ai(ws: Workspace, mid: str, ref: str) -> None:
    """AI 음성 변환을 돌려 캐시에 저장 (오래 걸립니다). 길이는 원본과 같게 맞춥니다."""
    src = media_audio(ws, mid)
    sample = media_audio(ws, ref)
    if not src.size or not sample.size:
        raise HTTPException(400, "소리가 없는 미디어입니다.")
    src_wav = ws.root / "cache" / f"{mid}_raw.wav"
    ref_wav = ws.root / "cache" / f"{ref}_raw.wav"
    raw_out = ws.root / "cache" / f"ai_{uuid.uuid4().hex}.wav"
    try:
        ai_voice.run_seedvc(src_wav, ref_wav, raw_out)
        y = load_audio(raw_out)
    finally:
        raw_out.unlink(missing_ok=True)
    y = np.pad(y, (0, max(0, src.size - y.size)))[: src.size]
    save_wav(ai_cache(ws, mid, ref), peak_limit(y, SR))


def clip_audio(ws: Workspace, mid: str, voice: str = ORIGINAL, intensity: float = 1.0) -> np.ndarray:
    """미디어 소리에 목소리 변조를 적용한 결과 (캐시). 시간축은 원본과 같습니다."""
    if voice == ORIGINAL:
        return media_audio(ws, mid)
    if ai_voice.is_ai_voice(voice):
        cache = ai_cache(ws, mid, ai_voice.ref_id(voice))
        if not cache.exists():  # 아직 변환 전이면 원본 소리 (내보내기는 막습니다)
            return media_audio(ws, mid)
        a, _ = sf.read(cache, dtype="float32")
        return a
    intensity = round(float(intensity), 2)
    cache = ws.root / "cache" / f"{mid}_{voice}_{intensity}.wav"
    if not cache.exists():
        raw = media_audio(ws, mid)
        y = process(raw, voice_for(voice), trim=False, intensity=intensity) if raw.size else raw
        save_wav(cache, y)
    a, _ = sf.read(cache, dtype="float32")
    return a


def peaks(audio: np.ndarray) -> list[float]:
    hop = SR // PEAKS_PER_S
    n = int(np.ceil(audio.size / hop))
    if n == 0:
        return []
    p = np.pad(np.abs(audio), (0, n * hop - audio.size)).reshape(n, hop).max(axis=1)
    return [round(float(v), 3) for v in p]


# ================================================================ 프로젝트

class Track(BaseModel):
    id: str
    kind: Literal["video", "audio"]
    name: str = ""
    mute: bool = False          # 영상 트랙에서는 '숨기기'
    solo: bool = False
    volume: float = Field(1.0, ge=0.0, le=4.0)
    voice: bool = False         # 보이스 트랙: 덕킹의 기준 + 녹이기 기본 켜짐
    room: float = Field(0.0, ge=0.0, le=1.0)
    level: bool = False
    gate: bool = False
    tone: bool = False
    duck: float = Field(0.0, ge=0.0, le=1.0)   # 보이스가 나올 때 이 트랙을 줄이는 정도


class Clip(BaseModel):
    id: str
    track: str
    media: str
    src_in: float = Field(ge=0)
    src_out: float = Field(gt=0)
    start: float = Field(ge=0)
    voice: str = ORIGINAL
    intensity: float = Field(1.0, ge=0.1, le=5.0)
    gain: float = Field(1.0, ge=0.0, le=4.0)
    link: str | None = None


class Project(BaseModel):
    tracks: list[Track]
    clips: list[Clip]


def project_length(p: Project) -> float:
    return max([c.start + (c.src_out - c.src_in) for c in p.clips] + [0.0])


def _fade(x: np.ndarray, n: int = int(SR * 0.008)) -> np.ndarray:
    n = min(n, x.size // 2)
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def _active_rms(x: np.ndarray, floor_db: float = -45.0) -> float:
    f = int(SR * 0.02)
    n = x.size // f
    if n == 0:
        return 0.0
    r = np.sqrt(np.mean(x[: n * f].reshape(n, f) ** 2, axis=1) + 1e-12)
    act = r[20 * np.log10(r) > floor_db]
    return float(np.sqrt(np.mean(act ** 2))) if act.size else 0.0


def _envelope(x: np.ndarray, attack_s: float, release_s: float) -> np.ndarray:
    hop = int(SR * 0.01)
    n = int(np.ceil(x.size / hop))
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    blocks = np.pad(np.abs(x), (0, n * hop - x.size)).reshape(n, hop).max(axis=1)
    a = np.exp(-0.01 / max(attack_s, 1e-3))
    r = np.exp(-0.01 / max(release_s, 1e-3))
    env = np.empty(n, dtype=np.float32)
    level = 0.0
    for i, v in enumerate(blocks):
        coef = a if v > level else r
        level = coef * level + (1 - coef) * v
        env[i] = level
    return np.interp(np.arange(x.size) / hop, np.arange(n), env).astype(np.float32)


def track_fx(x: np.ndarray, t: Track) -> np.ndarray:
    """녹이기: 잡음 게이트 → 톤 → 레벨 → 공간감."""
    if not np.any(x):
        return x
    chain = []
    if t.gate:
        chain.append(pb.NoiseGate(threshold_db=-48, ratio=6, attack_ms=2, release_ms=150))
    if t.tone:
        chain += [pb.HighpassFilter(cutoff_frequency_hz=90),
                  pb.LowShelfFilter(cutoff_frequency_hz=250, gain_db=-2),
                  pb.Compressor(threshold_db=-24, ratio=3, attack_ms=8, release_ms=150)]
    if chain:
        x = pb.Pedalboard(chain)(x.astype(np.float32), SR)
    if t.level:
        rms = _active_rms(x)
        if rms > 0:
            x = x * (10 ** (VOICE_TARGET_DB / 20) / rms)
    if t.room > 0:
        x = pb.Pedalboard([pb.Reverb(room_size=0.15 + 0.45 * t.room, damping=0.6, wet_level=0.35 * t.room,
                                     dry_level=1.0 - 0.25 * t.room, width=0.6)])(x.astype(np.float32), SR)
    return x.astype(np.float32)


def render_audio(ws: Workspace, p: Project, length: float | None = None) -> np.ndarray:
    length = project_length(p) if length is None else length
    n = int(round(length * SR)) + 1
    audio_tracks = [t for t in p.tracks if t.kind == "audio"]
    any_solo = any(t.solo for t in audio_tracks)
    buses: dict[str, np.ndarray] = {}
    for t in audio_tracks:
        if t.mute or (any_solo and not t.solo):
            continue
        bus = np.zeros(n, dtype=np.float32)
        for c in p.clips:
            if c.track != t.id or c.src_out <= c.src_in:
                continue
            a = clip_audio(ws, c.media, c.voice, c.intensity)
            seg = a[int(c.src_in * SR): int(c.src_out * SR)].copy()
            at = int(round(c.start * SR))
            seg = seg[: max(0, n - at)]
            if seg.size:
                bus[at: at + seg.size] += _fade(seg) * c.gain
        buses[t.id] = track_fx(bus, t) * t.volume

    voice_key = sum((buses[t.id] for t in audio_tracks if t.voice and t.id in buses), np.zeros(n, np.float32))
    talking = None
    if np.any(voice_key):
        fast = _envelope(voice_key, 0.005, 0.05)
        talking = _envelope((fast > 0.01).astype(np.float32), 0.05, 0.3)

    mix = np.zeros(n, dtype=np.float32)
    for t in audio_tracks:
        if t.id not in buses:
            continue
        bus = buses[t.id]
        if not t.voice and t.duck > 0 and talking is not None:
            bus = bus * (1 - (1 - 10 ** (-DUCK_MAX_DB * t.duck / 20)) * talking)
        mix += bus
    return peak_limit(mix, SR)


def project_key(p: Project, ws: Workspace | None = None) -> str:
    raw = p.model_dump_json()
    if ws is not None:  # AI 변환이 끝나면 같은 프로젝트라도 소리가 달라집니다
        raw += "".join("1" if ai_cache(ws, c.media, ai_voice.ref_id(c.voice)).exists() else "0"
                       for c in p.clips if ai_voice.is_ai_voice(c.voice))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def mix_file(ws: Workspace, p: Project) -> Path:
    out = ws.root / "cache" / f"mix_{project_key(p, ws)}.wav"
    if not out.exists():
        save_wav(out, render_audio(ws, p))
    return out


# ================================================================ 영상 합치기

def video_segments(p: Project, length: float) -> list[tuple[float, float, Clip | None]]:
    """타임라인을 '이 시간에 보이는 클립' 단위로 나눕니다 (빈 곳은 None = 검은 화면)."""
    order = [t.id for t in p.tracks if t.kind == "video" and not t.mute]
    rank = {tid: i for i, tid in enumerate(order)}
    vclips = [c for c in p.clips if c.track in rank and c.src_out > c.src_in]
    cuts = sorted({0.0, length, *[c.start for c in vclips], *[c.start + c.src_out - c.src_in for c in vclips]})
    cuts = [x for x in cuts if 0 <= x <= length]
    segs: list[tuple[float, float, Clip | None]] = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-3:
            continue
        mid = (a + b) / 2
        covering = [c for c in vclips if c.start <= mid < c.start + c.src_out - c.src_in]
        # 위 트랙이 이기고, 같은 트랙에서 겹치면 나중에 시작한 클립이 이김
        top = max(covering, key=lambda c: (rank[c.track], c.start)) if covering else None
        if segs and segs[-1][2] is top and abs(segs[-1][1] - a) < 1e-6:
            segs[-1] = (segs[-1][0], b, top)
        else:
            segs.append((a, b, top))
    return segs


def canvas_size(ws: Workspace, p: Project) -> tuple[int, int]:
    for t in p.tracks:
        for c in p.clips:
            if c.track == t.id and t.kind == "video":
                m = ws.meta(c.media)
                if m.get("width") and m.get("height"):
                    w, h = m["width"], m["height"]
                    scale = min(1.0, 1920 / max(w, h))  # 너무 크면 1920 기준으로
                    return int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    return 1280, 720


def export_video(ws: Workspace, p: Project) -> Path:
    length = project_length(p)
    if length <= 0:
        raise HTTPException(400, "타임라인이 비어 있습니다.")
    pending = [c for c in p.clips if ai_voice.is_ai_voice(c.voice)
               and not ai_cache(ws, c.media, ai_voice.ref_id(c.voice)).exists()]
    if pending:
        raise HTTPException(409, f"AI 변환이 아직 안 된 클립이 {len(pending)}개 있어요. 속성 패널에서 'AI 변환 만들기'를 먼저 눌러 주세요.")
    wav = mix_file(ws, p)
    W, H = canvas_size(ws, p)
    segs = video_segments(p, length)

    inputs: list[str] = []
    parts: list[str] = []
    n_in = 0
    for i, (a, b, c) in enumerate(segs):
        dur = b - a
        if c is None:
            parts.append(f"color=c=black:s={W}x{H}:r={FPS}:d={dur:.4f},format=yuv420p,setsar=1[s{i}]")
        else:
            k = n_in
            n_in += 1
            src = c.src_in + (a - c.start)
            m = ws.meta(c.media)
            inputs += ["-ss", f"{src:.4f}", "-t", f"{dur:.4f}"]
            if m["kind"] == "audio":  # 영상 트랙에 소리 파일만 있으면 검은 화면
                inputs += ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
            else:
                inputs += ["-i", str(ws.media_file(c.media))]
            parts.append(
                f"[{k}:v]trim=duration={dur:.4f},setpts=PTS-STARTPTS,"
                f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[s{i}]")
    audio_idx = n_in
    graph = ";".join(parts) + ";" + "".join(f"[s{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]"

    out = ws.root / "exports" / f"video_editor_{uuid.uuid4().hex[:8]}.mp4"
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs, "-i", str(wav),
           "-filter_complex", graph, "-map", "[v]", "-map", f"{audio_idx}:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{length:.4f}", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"내보내기 실패: {r.stderr[-800:]}")
    return out


# ================================================================ API

class AIConvert(BaseModel):
    media: str
    ref: str


def make_router(ws: Workspace) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/voices")
    def voices():
        return [{"name": ORIGINAL, "label": "원본 (변조 없음)"}] + \
               [{"name": n, "label": s.label} for n, s in load_presets().items()]

    # ---- AI 음성 변환 (Seed-VC)
    jobs = ai_voice.JobRunner()

    @router.get("/ai/status")
    def ai_status():
        return {"installed": ai_voice.is_installed(), "setup": "bash scripts/setup_ai_voice.sh"}

    @router.get("/ai/ready")
    def ai_ready(media: str, ref: str):
        _check_id(media), _check_id(ref)
        job = jobs.active(media, ref)
        return {"ready": ai_cache(ws, media, ref).exists(), "job": job.id if job else None}

    @router.post("/ai/convert")
    def ai_convert(b: AIConvert):
        _check_id(b.media), _check_id(b.ref)
        ws.meta(b.media), ws.meta(b.ref)
        if ai_cache(ws, b.media, b.ref).exists():
            return {"status": "done", "job": None}
        if not ai_voice.is_installed():
            raise HTTPException(503, "AI 음성 변환이 설치되지 않았습니다. 터미널에서 `bash scripts/setup_ai_voice.sh`를 실행하세요.")
        job = jobs.active(b.media, b.ref) or jobs.submit(b.media, b.ref, lambda: convert_ai(ws, b.media, b.ref))
        return {"status": job.status, "job": job.id}

    @router.get("/ai/jobs/{jid}")
    def ai_job(jid: str):
        job = jobs.jobs.get(jid)
        if not job:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        return {"status": job.status, "error": job.error}

    @router.post("/media")
    def upload_media(file: UploadFile = File(...), recorded: bool = Form(False), name: str = Form("")):
        tmp = ws.root / "cache" / f"upload_{uuid.uuid4().hex}{Path(file.filename or '').suffix[:8]}"
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            return add_media(ws, tmp, name or file.filename or "media", recorded=recorded)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"파일을 읽지 못했습니다: {e}") from e
        finally:
            tmp.unlink(missing_ok=True)

    @router.get("/media")
    def list_media():
        metas = [json.loads(p.read_text()) for p in (ws.root / "media").glob("*.json")]
        return sorted(metas, key=lambda m: m["name"])

    @router.get("/media/{mid}/info")
    def media_info(mid: str):
        return ws.meta(mid)

    @router.get("/media/{mid}/file")
    def media_file(mid: str):
        return FileResponse(ws.media_file(mid))

    @router.get("/media/{mid}/peaks")
    def media_peaks(mid: str, voice: str = ORIGINAL, intensity: float = 1.0):
        return {"rate": PEAKS_PER_S, "peaks": peaks(clip_audio(ws, mid, voice, intensity))}

    @router.post("/mix")
    def mix(p: Project):
        return FileResponse(mix_file(ws, p), media_type="audio/wav")

    @router.post("/export")
    def export(p: Project):
        out = export_video(ws, p)
        return {"file": out.name, "url": f"/api/exports/{out.name}"}

    @router.get("/exports/{name}")
    def get_export(name: str):
        p = ws.root / "exports" / Path(name).name
        if not p.exists() or p.suffix != ".mp4":
            raise HTTPException(404)
        return FileResponse(p, filename=p.name)

    return router
