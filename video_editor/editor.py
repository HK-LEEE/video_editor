"""멀티트랙 편집기 서버.

프로젝트 = 트랙 목록 + 클립 목록. 클립은 보관함의 미디어(영상·오디오·녹음) 중
[src_in, src_out] 구간을 타임라인 start 초에 놓은 것입니다.

- 영상 트랙: 아래 트랙부터 쌓이고, 같은 시각에 여러 클립이 있으면 위(목록 뒤쪽) 트랙이 보입니다.
- 오디오 트랙: 모두 섞입니다. 트랙마다 볼륨·음소거·솔로, 녹이기 효과, 덕킹이 있습니다.
- 보이스 트랙(voice=True)의 소리가 나올 때 다른 트랙은 duck 만큼 줄어듭니다.
- 영상 클립은 전환 효과(transition)를 가질 수 있고, layout="pip"이면 아래 영상 위에 작게 겹쳐 보입니다.
- 클립은 속도(speed)를 가질 수 있습니다. 소리는 높이를 유지한 채 늘이고 줄이며, 타임라인에서 차지하는 길이는 (src_out - src_in) / speed 입니다.
- 볼륨 키프레임(vol_keys)은 클립(클립 시작 기준 초)과 트랙(타임라인 기준 초)에 둘 수 있고, 점 사이는 직선으로 이어집니다.
- 영상 클립은 색 보정(color: 밝기·대비·채도·색온도)을 가질 수 있습니다.
- 자막은 프로젝트의 자막 트랙(subtitles)에 구간별로 들어가고 내보낼 때 영상에 입히거나 SRT로 내보냅니다.

미리듣기(/api/mix)와 내보내기(/api/export)는 같은 render_audio를 씁니다.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
import pedalboard as pb
import soundfile as sf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import ai_voice, characters, overlays as ovl_mod, shorts, workspaces
from .overlays import Overlay
from .subtitles import Cue, SubTrack, SubtitleStyle, active_cues, to_ass, to_srt
from .engine import SAMPLE_RATE as SR, VoiceSettings, load_audio, peak_limit, process, save_wav
from .presets import load_presets

class RevealReq(BaseModel):
    path: str


TRANSITIONS = ("none", "fade", "dip", "wipeleft", "wiperight", "slideleft", "slideright")
ORIGINAL = "original"
VOICE_TARGET_DB = -20.0
DUCK_MAX_DB = 15.0
DUCK_LOOKAHEAD_S = 0.08
FPS = 30
ENABLE_EPS = 0.002          # 겹쳐 놓는 구간 경계의 소수점 오차(0.0001초)에 한 프레임이 밀리거나 빠지지 않게 하는 여유
PEAKS_PER_S = 100


# ================================================================ 보관함

# 요청마다 달라지는 '이 창이 보고 있는 워크스페이스·프로젝트' (딥링크 /editor/w/<워크스페이스>/p/<프로젝트> 에서 온 요청이 여기 묶여요).
# 안 묶여 있으면 예전처럼 '열려 있는' 것을 써요.
BOUND: contextvars.ContextVar = contextvars.ContextVar("workspace_bound", default=None)
_LINK = re.compile(r"^/editor/w/([^/?#]+)/p/([^/?#]+)")


def link_path(sid: str, pid: str, tab: str = "") -> str:
    q = "?tab=" + tab if tab else ""
    return f"/editor/w/{urllib.parse.quote(sid, safe='')}/p/{urllib.parse.quote(pid, safe='')}{q}"


class Workspace:
    """작업 폴더: 워크스페이스 > 프로젝트. 프로젝트별 파일(media·cache·exports…)은 `root`(지금 열린 프로젝트 폴더) 아래에,
    워크스페이스 전용 자료(템플릿·엔딩 프리셋, 내가 올린 효과음·음악)는 `space_dir` 아래에,
    모든 워크스페이스가 같이 쓰는 것(보이스 캐릭터·설정, 기본 효과음·음악, 전역 공용 템플릿·엔딩)은 `home` 아래에 있습니다.
    `at(프로젝트 id)`는 그 프로젝트에 고정된 같은 작업 폴더를 돌려줘요 (같은 워크스페이스 안의 일괄 작업용)."""

    def __init__(self, home: Path, project: str | None = None, _spaces: workspaces.Spaces | None = None, space: str | None = None):
        self.home = Path(home)
        self.spaces = _spaces or workspaces.Spaces(self.home)
        self._sid = space
        self._pid = project
        self.global_shared = self.home / "library" / "media"   # 전역 공용 미디어 (기본 효과음·음악, 캐릭터 샘플, 전역 템플릿 인트로·아웃트로)
        self.global_shared.mkdir(parents=True, exist_ok=True)

    @property
    def sid(self) -> str:
        b = BOUND.get()
        return self._sid or (b[0] if b else None) or self.spaces.active

    @property
    def space_dir(self) -> Path:
        return self.spaces.dir(self.sid)

    @property
    def shared(self) -> Path:
        """이 워크스페이스 전용 미디어 보관함 (내가 올린 효과음·음악, 전용 템플릿 인트로·아웃트로)."""
        p = self.space_dir / "library" / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def projects(self) -> workspaces.Projects:
        return self.spaces.projects(self.sid)

    @property
    def pid(self) -> str:
        b = BOUND.get()
        return self._pid or (b[1] if b and b[0] == self.sid else None) or self.projects.active

    @property
    def root(self) -> Path:
        return self.projects.dir(self.pid)

    def resolve_link(self, referer: str) -> tuple[str, str] | None:
        """Referer(=딥링크 주소를 보고 있는 창)에서 (워크스페이스, 프로젝트)를 읽어요. 없는 곳이면 None."""
        m = _LINK.match(urllib.parse.urlparse(referer or "").path)
        if not m:
            return None
        sid, pid = (urllib.parse.unquote(g) for g in m.groups())
        try:
            if self.spaces.exists(sid) and self.spaces.projects(sid).exists(pid):
                return sid, pid
        except Exception:  # noqa: BLE001
            pass
        return None

    def at(self, pid: str) -> "Workspace":
        """같은 워크스페이스 안의 다른 프로젝트에 고정된 작업 폴더."""
        self.projects.info(pid)
        return Workspace(self.home, pid, self.spaces, self.sid)

    def _dirs(self) -> list[Path]:
        return [self.root / "media", self.shared, self.global_shared]    # 찾는 순서: 프로젝트 → 워크스페이스 → 전역

    def meta_path(self, mid: str) -> Path:
        _check_id(mid)
        for d in self._dirs():
            if (d / f"{mid}.json").exists():
                return d / f"{mid}.json"
        return self.root / "media" / f"{mid}.json"

    def meta(self, mid: str) -> dict:
        p = self.meta_path(mid)
        if not p.exists():
            raise HTTPException(404, "미디어를 찾을 수 없습니다.")
        return json.loads(p.read_text())

    def media_file(self, mid: str) -> Path:
        return self.meta_path(mid).parent / self.meta(mid)["file"]

    def all_meta(self) -> list[dict]:
        """이 프로젝트의 미디어 + 워크스페이스·전역 공유 미디어 (같은 id면 프로젝트 것이 우선)."""
        out = {}
        for d in reversed(self._dirs()):
            for p in d.glob("*.json"):
                try:
                    m = json.loads(p.read_text())
                except (OSError, ValueError):
                    continue
                try:
                    m.setdefault("added", p.stat().st_mtime)   # '추가순' 정렬용 (옛 파일은 만든 시각으로)
                except OSError:
                    pass
                out[m["id"]] = m
        return list(out.values())

    def promote(self, mid: str, scope: str = "space") -> bool:
        """이 프로젝트의 미디어를 공유 보관함에도 복사합니다 (원본은 그대로). scope: 'space'=이 워크스페이스, 'global'=모든 워크스페이스.
        다른 프로젝트에서도 쓰는 템플릿·캐릭터가 가리키는 파일이에요."""
        return workspaces.promote_media(self.root / "media", self.global_shared if scope == "global" else self.shared, mid)


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


def add_media(ws: Workspace, src: Path, name: str, recorded: bool = False, shared: bool = False) -> dict:
    mid = uuid.uuid4().hex
    mdir = ws.shared if shared else ws.root / "media"
    ext = Path(name).suffix.lower()
    if not ext[1:].isalnum():
        ext = ""
    if recorded:
        # 브라우저 녹음(webm/mp4)은 바로 wav로 바꿔 둡니다
        audio = load_audio(src)
        save_wav(mdir / f"{mid}.wav", audio)
        src.unlink(missing_ok=True)
        ext = ".wav"
    else:
        shutil.move(str(src), mdir / f"{mid}{ext}")
    file = f"{mid}{ext}"
    info = probe(mdir / file)
    if info["duration"] <= 0 or not (info["has_audio"] or info["has_video"]):
        (mdir / file).unlink(missing_ok=True)
        raise HTTPException(400, "영상이나 소리를 읽을 수 없는 파일입니다.")
    meta = {"id": mid, "name": name, "file": file, "recorded": recorded,
            "kind": "video" if info["has_video"] else "audio", "added": time.time(), **info}
    (mdir / f"{mid}.json").write_text(json.dumps(meta, ensure_ascii=False))
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


def voice_ref(ws: Workspace, voice: str) -> str | None:
    """이 목소리가 AI 변환(샘플 목소리)을 필요로 하면 그 샘플 미디어 id, 아니면 None."""
    if ai_voice.is_ai_voice(voice):
        return ai_voice.ref_id(voice)
    if characters.is_char(voice):
        ch = characters.get(ws.home, characters.char_id(voice))
        return ch.ref or None if ch else None
    return None


def character_audio(ws: Workspace, mid: str, ch: characters.Character, *, convert: bool = False) -> np.ndarray | None:
    """미디어 소리에 캐릭터 목소리를 입힌 결과 (캐시). AI 변환이 아직이면 convert=True일 때만 (오래 걸리게) 만들고,
    아니면 None을 돌려줍니다. 시간축은 원본과 같습니다."""
    if ch.ref:
        cache = ai_cache(ws, mid, ch.ref)
        if not cache.exists():
            if not convert:
                return None
            convert_ai(ws, mid, ch.ref)
        a, _ = sf.read(cache, dtype="float32")
    else:
        a = media_audio(ws, mid)
    if ch.preset == ORIGINAL or not a.size:
        return a
    cache2 = ws.root / "cache" / f"{mid}_char_{ch.id}_{ch.signature()}.wav"
    if not cache2.exists():
        save_wav(cache2, process(a, voice_for(ch.preset), trim=False, intensity=ch.intensity))
    a, _ = sf.read(cache2, dtype="float32")
    return a


def clip_audio(ws: Workspace, mid: str, voice: str = ORIGINAL, intensity: float = 1.0) -> np.ndarray:
    """미디어 소리에 목소리 변조를 적용한 결과 (캐시). 시간축은 원본과 같습니다."""
    if voice == ORIGINAL:
        return media_audio(ws, mid)
    if characters.is_char(voice):
        ch = characters.get(ws.home, characters.char_id(voice))
        out = character_audio(ws, mid, ch) if ch else None
        return media_audio(ws, mid) if out is None else out   # 캐릭터가 없어졌거나 AI 변환 전이면 원본 소리
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


def remove_media(ws: Workspace, mid: str) -> None:
    """작업 중간에 만든 임시 미디어를 보관함에서 지웁니다."""
    meta = ws.meta(mid)
    mp = ws.meta_path(mid)
    (mp.parent / meta["file"]).unlink(missing_ok=True)
    mp.unlink(missing_ok=True)
    for f in (ws.root / "cache").glob(f"{mid}_*"):
        f.unlink(missing_ok=True)


def peaks(audio: np.ndarray) -> list[float]:
    hop = SR // PEAKS_PER_S
    n = int(np.ceil(audio.size / hop))
    if n == 0:
        return []
    p = np.pad(np.abs(audio), (0, n * hop - audio.size)).reshape(n, hop).max(axis=1)
    return [round(float(v), 3) for v in p]


# ================================================================ 프로젝트

class VolKey(BaseModel):
    t: float = Field(ge=0)                   # 클립 키프레임은 클립 시작부터, 트랙 키프레임은 타임라인 처음부터 초
    v: float = Field(1.0, ge=0.0, le=4.0)    # 이 시점의 볼륨 배율


class Color(BaseModel):
    brightness: float = Field(0.0, ge=-1.0, le=1.0)
    contrast: float = Field(1.0, ge=0.0, le=3.0)
    saturation: float = Field(1.0, ge=0.0, le=3.0)
    temperature: float = Field(0.0, ge=-1.0, le=1.0)   # -1 차갑게(푸르게) ~ +1 따뜻하게(붉게)

    def is_neutral(self) -> bool:
        return not self.brightness and self.contrast == 1 and self.saturation == 1 and not self.temperature


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
    vol_keys: list[VolKey] = []                 # 트랙 볼륨 자동화 (타임라인 기준 초)


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
    speed: float = Field(1.0, ge=0.25, le=4.0)   # 재생 속도 (소리 높이는 유지)
    vol_keys: list[VolKey] = []                  # 클립 볼륨 자동화 (클립 시작 기준 초)
    # 영상 클립 전용: 색 보정
    color: Color = Color()
    # 영상 클립 전용: 이 클립이 시작할 때 아래 화면에서 이 클립으로 넘어가는 효과
    transition: Literal[TRANSITIONS] = "none"
    transition_dur: float = Field(0.5, ge=0.1, le=3.0)
    # 영상 클립 전용: 전환 때 이 클립 뒤쪽 원본(잘라낸 불량 프레임일 수 있음)을 이어 쓰지 않고 마지막 화면을 붙잡습니다
    tail_hold: bool = False
    # 영상 클립 전용: pip이면 아래 영상을 가리지 않고 작은 화면으로 위에 겹칩니다 (x, y는 화면 비율의 가운데 위치)
    layout: Literal["full", "pip"] = "full"
    pip_x: float = Field(0.75, ge=0.0, le=1.0)
    pip_y: float = Field(0.75, ge=0.0, le=1.0)
    pip_w: float = Field(0.3, ge=0.05, le=1.0)
    pip_opacity: float = Field(1.0, ge=0.1, le=1.0)
    pip_border: bool = True
    disabled: bool = False   # 클립 끄기: 타임라인에는 남지만 소리·화면·길이에 넣지 않음
    tpl: str = ""            # 템플릿이 넣은 클립의 표시 (intro·outro·ending). 다시 적용할 때 먼저 지웁니다


class Project(BaseModel):
    tracks: list[Track]
    clips: list[Clip]
    subtitles: list[Cue] = []
    subtitle_style: SubtitleStyle = SubtitleStyle()
    subtitle_tracks: list[SubTrack] = []   # 자막 트랙(줄)별 이름·스타일 (lane 번호 = 순서). 없으면 전체 스타일을 따름
    subtitle_lang: str = ""     # 비우면 모든 자막, 지정하면 그 언어(와 언어 없음) 자막만 입힘
    overlays: list[Overlay] = []   # 화면 위 글자 (훅 제목·워터마크·자유 글자)


def clip_len(c: Clip) -> float:
    """타임라인에서 클립이 차지하는 길이 (속도 반영)."""
    return (c.src_out - c.src_in) / c.speed


def project_length(p: Project) -> float:
    return max([c.start + clip_len(c) for c in p.clips if not c.disabled] + [0.0])


def stretch(x: np.ndarray, speed: float) -> np.ndarray:
    """높이는 그대로 두고 speed배 빠르게(>1) 또는 느리게(<1). 길이는 정확히 len/speed로 맞춥니다."""
    if abs(speed - 1.0) < 1e-6 or x.size < 2:
        return x
    y = pb.time_stretch(x.astype(np.float32), SR, stretch_factor=float(speed))
    y = np.asarray(y, dtype=np.float32).reshape(-1)
    want = int(round(x.size / speed))
    return np.pad(y, (0, max(0, want - y.size)))[:want]


def gain_envelope(keys: list[VolKey], n: int, t0: float = 0.0) -> np.ndarray:
    """키프레임 사이를 직선으로 이은 볼륨 배율 (첫/끝 점 바깥은 그 값 그대로). 키가 없으면 1."""
    if not keys:
        return np.ones(n, dtype=np.float32)
    ks = sorted(keys, key=lambda k: k.t)
    return np.interp(t0 + np.arange(n) / SR, [k.t for k in ks], [k.v for k in ks]).astype(np.float32)


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
            if c.track != t.id or c.src_out <= c.src_in or c.disabled:
                continue
            a = clip_audio(ws, c.media, c.voice, c.intensity)
            seg = stretch(a[int(c.src_in * SR): int(c.src_out * SR)].copy(), c.speed)
            at = int(round(c.start * SR))
            if c.vol_keys:
                seg = seg * gain_envelope(c.vol_keys, seg.size)
            seg = seg[: max(0, n - at)]
            if seg.size:
                bus[at: at + seg.size] += _fade(seg) * c.gain
        buses[t.id] = track_fx(bus, t) * t.volume
        if t.vol_keys:
            buses[t.id] *= gain_envelope(t.vol_keys, n)

    voice_key = sum((buses[t.id] for t in audio_tracks if t.voice and t.id in buses), np.zeros(n, np.float32))
    talking = None
    if np.any(voice_key):
        fast = _envelope(voice_key, 0.005, 0.05)
        talking = _envelope((fast > 0.01).astype(np.float32), 0.05, 0.3)
        k = int(SR * DUCK_LOOKAHEAD_S)     # 말이 시작하기 조금 전부터 음악을 내려서, 첫 마디가 음악에 묻히지 않게
        talking = np.concatenate([talking[k:], np.zeros(k, dtype=talking.dtype)])

    mix = np.zeros(n, dtype=np.float32)
    for t in audio_tracks:
        if t.id not in buses:
            continue
        bus = buses[t.id]
        if not t.voice and t.duck > 0 and talking is not None:
            bus = bus * (1 - (1 - 10 ** (-DUCK_MAX_DB * t.duck / 20)) * talking)
        mix += bus
    return peak_limit(mix, SR)


VIDEO_ONLY_CLIP_FIELDS = {"tpl", "transition", "transition_dur", "tail_hold", "layout", "pip_x", "pip_y", "pip_w", "pip_opacity", "pip_border", "color"}


def project_key(p: Project, ws: Workspace | None = None) -> str:
    """소리 믹스의 캐시 키. 소리에 영향 없는 자막·전환·PiP 설정은 빼서, 그걸 바꿔도 믹스를 다시 만들지 않습니다."""
    raw = p.model_dump_json(exclude={"subtitles": True, "subtitle_style": True, "subtitle_tracks": True, "subtitle_lang": True, "overlays": True,
                                     "clips": {"__all__": VIDEO_ONLY_CLIP_FIELDS}})
    if ws is not None:  # AI 변환이 끝나거나 캐릭터를 고치면 같은 프로젝트라도 소리가 달라집니다
        for c in p.clips:
            ref = voice_ref(ws, c.voice)
            if ref:
                raw += "1" if ai_cache(ws, c.media, ref).exists() else "0"
            if characters.is_char(c.voice):
                ch = characters.get(ws.home, characters.char_id(c.voice))
                raw += ch.signature() if ch else "-"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def mix_file(ws: Workspace, p: Project) -> Path:
    out = ws.root / "cache" / f"mix_{project_key(p, ws)}.wav"
    if not out.exists():
        save_wav(out, render_audio(ws, p))
    return out


# ================================================================ 영상 합치기
# 검은 바탕 위에 영상 클립을 아래 트랙부터 차례로 겹쳐 올립니다 (overlay).
# 위 트랙이 아래를 가리고, 같은 트랙에서 겹치면 나중에 시작한 클립이 위입니다. PiP 클립은 항상 맨 위에 올라갑니다.
# 전환 효과는 새 클립이 시작하는 처음 transition_dur 초 동안 그 클립만 알파(투명도)·위치·가림막을 움직이는 방식이라
# '그 순간 아래에 보이던 화면'과 자연스럽게 섞입니다.

def canvas_size(ws: Workspace, p: Project) -> tuple[int, int]:
    for want in ("full", "pip"):
        for t in p.tracks:
            for c in p.clips:
                if c.track == t.id and t.kind == "video" and c.layout == want:
                    m = ws.meta(c.media)
                    if m.get("width") and m.get("height"):
                        w, h = m["width"], m["height"]
                        scale = min(1.0, 1920 / max(w, h))  # 너무 크면 1920 기준으로
                        return int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    return 1280, 720


def _f(x: float) -> str:
    return f"{x:.4f}"


def _tb(x: float) -> str:
    """setpts에 넣는 '초 → 프레임 칸' 값. ffmpeg은 소수를 반올림이 아니라 버려서(8.4333초 = 252.999칸 → 252칸) 한 칸 일찍 시작해 끝에 빈 프레임이 생기므로 0.5를 더해 반올림합니다."""
    return f"({x:.4f}/TB+0.5)"


TEMP_GAIN = 0.2   # 색온도 ±1일 때 빨강·파랑을 ±20% 키우고 줄입니다 (미리보기도 같은 값)


def color_filters(col: Color) -> list[str]:
    """색 보정 ffmpeg 필터. 밝기는 더하기, 대비는 중간 회색 기준, 색온도는 빨강/파랑 균형입니다."""
    out = []
    if col.brightness or col.contrast != 1 or col.saturation != 1:
        out.append(f"eq=brightness={col.brightness:.4f}:contrast={col.contrast:.4f}:saturation={col.saturation:.4f}")
    if col.temperature:
        out.append(f"colorchannelmixer=rr={1 + TEMP_GAIN * col.temperature:.4f}:bb={1 - TEMP_GAIN * col.temperature:.4f}")
    return out


def video_layers(ws: Workspace, p: Project) -> list[Clip]:
    """아래에서 위로 그릴 순서의 영상 클립."""
    rank = {t.id: i for i, t in enumerate(p.tracks) if t.kind == "video" and not t.mute}
    clips = [c for c in p.clips if c.track in rank and c.src_out > c.src_in and not c.disabled]
    return sorted(clips, key=lambda c: (c.layout == "pip", rank[c.track], c.start))


def _tail_extension(p: Project, c: Clip, layers: list[Clip]) -> float:
    """같은 트랙에서 바로 이어 붙은 다음 클립이 전환 효과를 가지면, 이 클립 끝을 그만큼 늘려 겹쳐 줍니다."""
    if c.layout != "full":
        return 0.0
    e = c.start + clip_len(c)
    nxt = [k for k in layers if k.track == c.track and k.layout == "full" and k.transition != "none"
           and abs(k.start - e) <= 0.05]
    return max((min(k.transition_dur, clip_len(k)) for k in nxt), default=0.0)


def build_video_graph(ws: Workspace, p: Project, length: float, W: int, H: int) -> tuple[list[str], str]:
    """(ffmpeg 입력 인자, filter_complex 그래프). 마지막 출력 라벨은 [vbase]."""
    layers = video_layers(ws, p)
    inputs: list[str] = []
    parts = [f"color=c=black:s={W}x{H}:r={FPS}:d={_f(length)},format=yuv420p[cv0]"]
    cur, n_in, n_lbl = "cv0", 0, 0

    def lbl(prefix: str) -> str:
        nonlocal n_lbl
        n_lbl += 1
        return f"{prefix}{n_lbl}"

    def overlay(top: str, start: float, end: float, x: str = "0", y: str = "0") -> None:
        nonlocal cur
        out = lbl("cv")
        parts.append(f"[{cur}][{top}]overlay=x='{x}':y='{y}':format=auto:eof_action=pass:"
                     f"enable='gte(t,{_f(start - ENABLE_EPS)})*lt(t,{_f(end - ENABLE_EPS)})'[{out}]")
        cur = out

    for c in layers:
        m = ws.meta(c.media)
        if c.layout == "pip" and m["kind"] == "audio":
            continue
        dur = clip_len(c)           # 타임라인에서의 길이 (속도 반영)
        ext = _tail_extension(p, c, layers)
        real = min(ext, max(0.0, (m["duration"] - c.src_out) / c.speed)) if m["kind"] != "audio" else ext
        if c.tail_hold:
            real = 0.0
        k, n_in = n_in, n_in + 1
        inputs += ["-ss", f"{c.src_in:.4f}", "-t", _f((dur + real) * c.speed)]   # 원본에서 읽는 길이
        if m["kind"] == "audio":  # 영상 트랙에 소리 파일만 있으면 검은 화면
            inputs += ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
        else:
            inputs += ["-i", str(ws.media_file(c.media))]
        chain = [f"trim=duration={_f((dur + real) * c.speed)}", f"setpts=(PTS-STARTPTS)/{c.speed:.6f}", f"fps={FPS}"]
        chain += color_filters(c.color)
        if c.layout == "pip":
            pw = max(16, int(W * c.pip_w) // 2 * 2)
            chain.append(f"scale={pw}:-2")
            if c.pip_border:
                chain.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t={max(2, W // 240)}")
            chain.append("format=rgba")
            if c.pip_opacity < 1:
                chain.append(f"colorchannelmixer=aa={c.pip_opacity:.3f}")
            chain.append("setsar=1")
        else:
            chain += [f"scale={W}:{H}:force_original_aspect_ratio=decrease",
                      f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black", "setsar=1", "format=yuva420p"]
        if ext - real > 1e-3:  # 이어질 원본이 모자라면 마지막 화면을 붙잡고 있습니다
            chain.append(f"tpad=stop_mode=clone:stop_duration={_f(ext - real + 0.05)}")
        s, e = c.start, c.start + dur + ext
        d = min(c.transition_dur, dur) if c.transition != "none" else 0.0
        kind = c.transition if c.layout == "full" else ("fade" if d else "none")
        src = lbl("s")
        pos0 = _pip_xy(c) if c.layout == "pip" else ("0", "0")

        place = f"setpts=PTS+{_tb(s)}"

        if kind == "none":
            parts.append(f"[{k}:v]{','.join(chain)},{place}[{src}]")
            overlay(src, s, e, *pos0)
        elif kind == "fade":
            chain.append(f"fade=t=in:st=0:d={_f(d)}:alpha=1")
            parts.append(f"[{k}:v]{','.join(chain)},{place}[{src}]")
            overlay(src, s, e, *pos0)
        elif kind == "dip":
            chain.append(f"fade=t=in:st={_f(d / 2)}:d={_f(d / 2)}:alpha=1")
            parts.append(f"[{k}:v]{','.join(chain)},setpts=PTS+{_tb(s)}[{src}]")
            blk = lbl("b")  # 아래 화면이 검게 어두워졌다가 새 화면이 떠오릅니다
            parts.append(f"color=c=black:s={W}x{H}:r={FPS}:d={_f(d)},format=yuva420p,"
                         f"fade=t=in:st=0:d={_f(d / 2)}:alpha=1,setpts=PTS-STARTPTS+{_tb(s)}[{blk}]")
            overlay(blk, s, s + d)
            overlay(src, s, e)
        else:  # 가림막(wipe)·밀기(slide): 처음 d초만 따로 떼서 움직이고, 나머지는 그대로 얹습니다
            a, b, head, tail = lbl("a"), lbl("b"), lbl("h"), lbl("t")
            parts.append(f"[{k}:v]{','.join(chain)},split[{a}][{b}]")
            head_chain = f"[{a}]trim=duration={_f(d)},setpts=PTS-STARTPTS"
            if kind.startswith("wipe"):
                x = f"W*(1-clip(T/{_f(d)},0,1))"
                cond = f"gt(X,{x})" if kind == "wipeleft" else f"lt(X,W-{x})"
                head_chain += f",geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if({cond},255,0)'"
                ox = "0"
            else:
                sign = "" if kind == "slideleft" else "-"
                ox = f"{sign}W*(1-min(1,(t-{_f(s)})/{_f(d)}))"
            parts.append(f"{head_chain},setpts=PTS+{_tb(s)}[{head}]")
            parts.append(f"[{b}]trim=start={_f(d)},setpts=PTS-STARTPTS+{_tb(s + d)}[{tail}]")
            overlay(head, s, s + d, ox)
            overlay(tail, s + d, e)
    return inputs, ";".join(parts) + f";[{cur}]format=yuv420p[vbase]"


def _pip_xy(c: Clip) -> tuple[str, str]:
    """PiP 화면의 위치 (가운데 기준, 화면 밖으로 나가지 않게)."""
    return (f"max(0,min(main_w-overlay_w,main_w*{c.pip_x:.4f}-overlay_w/2))",
            f"max(0,min(main_h-overlay_h,main_h*{c.pip_y:.4f}-overlay_h/2))")


def EXPORT_TIMEOUT(seconds: float) -> float:
    """내보내기 최대 시간 (영상 길이의 20배, 최소 3분, 최대 2시간). 넘으면 멈춘 것으로 보고 끝내요."""
    return min(7200.0, max(180.0, seconds * 20))


def export_video(ws: Workspace, p: Project, short: shorts.ShortSpec | None = None, max_side: int | None = None) -> Path:
    """short를 주면 9:16(1080x1920)로 자르고·구간을 잘라 내보냅니다. max_side를 주면 긴 변을 그 이하로 줄입니다 (미리보기용)."""
    length = project_length(p)
    if length <= 0:
        raise HTTPException(400, "타임라인이 비어 있습니다.")
    t0, t1 = 0.0, length
    if short:
        try:
            t0, t1 = shorts.span(short, length)
        except ValueError as e:
            raise HTTPException(400, str(e))
    pending = [c for c in p.clips if voice_ref(ws, c.voice) and not ai_cache(ws, c.media, voice_ref(ws, c.voice)).exists()]
    if pending:
        raise HTTPException(409, f"AI 변환이 아직 안 된 클립이 {len(pending)}개 있어요. 속성 패널에서 'AI 변환 만들기'를 먼저 눌러 주세요.")
    wav = mix_file(ws, p)
    W, H = canvas_size(ws, p)
    if max_side and max(W, H) > max_side:
        k = max_side / max(W, H)
        W, H = int(W * k) // 2 * 2, int(H * k) // 2 * 2

    # PiP 클립에서도 모양이 안 틀어지게: PiP는 layout이 pip인 클립만 겹쳐지고 나머지는 위 규칙대로 쌓입니다
    inputs, graph = build_video_graph(ws, p, t1 if short else length, W, H)
    audio_idx = sum(1 for x in inputs if x == "-ss")
    out_label = "vbase"
    uid8 = uuid.uuid4().hex[:8]
    exports = ws.root / "exports"
    ass_name = ov_name = None
    sub_cues, sub_style, sub_w, sub_h, out_len = p.subtitles, p.subtitle_style, W, H, length
    sub_adjust = None
    if short:
        graph += ";" + shorts.video_filter(W, H, short, t0, t1)
        out_label, out_len = "vshort", t1 - t0
        sub_cues = shorts.shift_cues(p.subtitles, t0, t1)
        sub_w, sub_h = shorts.OUT_W, shorts.OUT_H
        if short.safe_subs:
            sub_adjust = lambda st: shorts.safe_style(st, short.format)
            sub_style = sub_adjust(sub_style)
    if active_cues(sub_cues, p.subtitle_lang, out_len):
        ass_name = f"subs_{uid8}.ass"
        (exports / ass_name).write_text(to_ass(sub_cues, sub_style, sub_w, sub_h, p.subtitle_lang, out_len, p.subtitle_tracks, sub_adjust), encoding="utf-8")
        graph += f";[{out_label}]ass=filename={ass_name}[vout]"   # ffmpeg를 exports 폴더에서 돌려서 경로 이스케이프가 필요 없습니다
        out_label = "vout"
    ov_ass = ovl_mod.to_ass(p.overlays, sub_w, sub_h, t1 if short else length, t0,
                            shorts.FORMATS[short.format]["safe"] if short and short.safe_subs else None)
    if ov_ass:   # 훅 제목·워터마크는 자막 위에 입힙니다
        ov_name = f"ovl_{uid8}.ass"
        (exports / ov_name).write_text(ov_ass, encoding="utf-8")
        graph += f";[{out_label}]ass=filename={ov_name}[vov]"
        out_label = "vov"

    out = exports / (f"video_editor_{short.format}_{uid8}.mp4" if short else f"video_editor_{uid8}.mp4")
    audio_in = ["-ss", f"{t0:.4f}", "-t", f"{out_len:.4f}", "-i", str(wav.resolve())] if short else ["-i", str(wav.resolve())]
    # -aac_coder fast: ffmpeg 9.x 의 기본 AAC 인코더는 '앞이 무음인 소리 + 192k'에서 멈춰 버려요 (첫 클립이 0초보다 늦게 시작하면 앞이 무음)
    cmd = ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *inputs, *audio_in,
           "-filter_complex", graph, "-map", f"[{out_label}]", "-map", f"{audio_idx}:a",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-aac_coder", "fast", "-b:a", "192k", "-t", f"{out_len:.4f}", "-movflags", "+faststart", str(out.resolve())]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=exports, timeout=EXPORT_TIMEOUT(out_len))
    except subprocess.TimeoutExpired:
        out.unlink(missing_ok=True)
        raise HTTPException(500, "내보내기가 너무 오래 걸려서 멈췄어요 (ffmpeg가 응답하지 않았어요). 클립 위치를 0초 근처로 옮기거나, 해상도를 낮춰 다시 해 보세요.")
    finally:
        for name in (ass_name, ov_name):
            if name:
                (exports / name).unlink(missing_ok=True)
    if r.returncode != 0:
        raise HTTPException(500, f"내보내기 실패: {r.stderr[-800:]}")
    if p.subtitles:   # 자막은 프로젝트의 subtitles 폴더에도 SRT로 남겨 둡니다 (내보낸 영상과 같은 이름)
        try:
            (ws.root / "subtitles" / f"{out.stem}.srt").write_text(to_srt(sub_cues, p.subtitle_lang), encoding="utf-8")
        except OSError:
            pass
    ws.projects.touch(ws.pid)
    return out


# ================================================================ API

class ShortExport(BaseModel):
    project: Project
    spec: shorts.ShortSpec


class AIConvert(BaseModel):
    media: str
    ref: str


def make_router(ws: Workspace) -> APIRouter:
    async def bind(request: Request):
        """이 요청을 보낸 창의 딥링크(Referer)가 가리키는 프로젝트에 묶어요 — 창마다 다른 프로젝트를 동시에 열 수 있게요."""
        BOUND.set(ws.resolve_link(request.headers.get("referer", "")))

    router = APIRouter(prefix="/api", dependencies=[Depends(bind)])

    @router.get("/voices")
    def voices():
        return [{"name": ORIGINAL, "label": "원본 (변조 없음)"}] + \
               [{"name": n, "label": s.label} for n, s in load_presets().items()] + \
               [{"name": characters.PREFIX + c.id, "label": "🎭 " + c.name, "character": True, "ref": c.ref}
                for c in characters.load(ws.home)]

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
        return {"status": job.status, "error": job.error, "result": job.result}

    from .workspace_routes import add_routes as add_workspace_routes
    add_workspace_routes(router, ws, jobs)
    from .ai_routes import add_routes
    add_routes(router, ws, jobs)
    from .autojoin import add_routes as add_join_routes
    add_join_routes(router, ws, jobs)
    from .studio_routes import add_routes as add_studio_routes
    add_studio_routes(router, ws, jobs)

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
        return sorted(ws.all_meta(), key=lambda m: m["name"])

    @router.get("/media/{mid}/info")
    def media_info(mid: str):
        return ws.meta(mid)

    @router.get("/media/{mid}/file")
    def media_file(mid: str):
        return FileResponse(ws.media_file(mid))

    @router.delete("/media/{mid}")
    def media_delete(mid: str):
        ws.meta(mid)
        remove_media(ws, mid)
        return {"ok": True}

    @router.get("/media/{mid}/peaks")
    def media_peaks(mid: str, voice: str = ORIGINAL, intensity: float = 1.0):
        return {"rate": PEAKS_PER_S, "peaks": peaks(clip_audio(ws, mid, voice, intensity))}

    @router.post("/mix")
    def mix(p: Project):
        return FileResponse(mix_file(ws, p), media_type="audio/wav")

    @router.post("/export")
    def export(p: Project):
        out = export_video(ws, p)
        return {"file": out.name, "url": f"/api/exports/{out.name}", "path": str(out.resolve())}

    @router.get("/short/formats")
    def short_formats():
        return {"out": [shorts.OUT_W, shorts.OUT_H], "formats": shorts.FORMATS}

    @router.post("/export/short")
    def export_short(b: ShortExport):
        out = export_video(ws, b.project, b.spec)
        return {"file": out.name, "url": f"/api/exports/{out.name}", "format": b.spec.format, "path": str(out.resolve())}

    @router.post("/subtitles/srt")
    def subtitles_srt(p: Project):
        return Response(to_srt(p.subtitles, p.subtitle_lang), media_type="application/x-subrip; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})

    @router.post("/subtitles/srt/save")
    def subtitles_srt_save(p: Project):
        """현재 자막을 프로젝트의 subtitles 폴더에도 SRT로 저장하고 경로를 알려 줘요 (화면의 'Finder에서 열기'용)."""
        import datetime
        d = ws.root / "subtitles"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"subtitles_{datetime.datetime.now():%Y%m%d-%H%M%S}.srt"
        f.write_text(to_srt(p.subtitles, p.subtitle_lang), encoding="utf-8")
        return {"file": f.name, "path": str(f.resolve())}

    @router.get("/export/list")
    def export_list(project: str = "", limit: int = 30):
        """이 프로젝트에서 내보낸 영상 (최근 것부터). 같은 이름의 SRT가 있으면 함께."""
        w = ws.at(project) if project else ws
        d = w.root / "exports"
        out = []
        for f in sorted(d.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)[: max(1, min(limit, 200))] if d.is_dir() else []:
            st = f.stat()
            srt = w.root / "subtitles" / f"{f.stem}.srt"
            out.append({"file": f.name, "path": str(f.resolve()), "size": st.st_size, "mtime": st.st_mtime,
                        "url": f"/api/exports/{f.name}" + (f"?project={w.pid}" if project else ""),
                        "srt": str(srt.resolve()) if srt.exists() else None})
        return {"dir": str(d.resolve()), "items": out}

    @router.post("/exports/reveal")
    def exports_reveal(b: RevealReq):
        """내보낸 파일을 macOS Finder에서 보여줘요 (`open -R`). 작업 폴더 안의 exports·subtitles 파일만 열어요."""
        import sys
        try:
            f = Path(b.path).resolve(strict=True)
            f.relative_to(ws.home.resolve())
        except (OSError, ValueError):
            raise HTTPException(400, "작업 폴더 안의 파일만 열 수 있어요.")
        if not f.is_file() or f.parent.name not in ("exports", "subtitles"):
            raise HTTPException(400, "내보낸 영상이나 자막 파일만 열 수 있어요.")
        if sys.platform != "darwin":
            return {"opened": False, "path": str(f), "reason": "Finder 열기는 macOS에서만 돼요. 경로를 복사해서 찾아 주세요."}
        r = subprocess.run(["open", "-R", str(f)], capture_output=True, text=True)
        return {"opened": r.returncode == 0, "path": str(f), "reason": r.stderr.strip()[:200] if r.returncode else ""}

    @router.post("/subtitles/parse")
    async def subtitles_parse(request: Request, name: str = ""):
        """자막 파일(SRT·VTT·ASS)의 바이트를 그대로 받아 구간 목록으로 돌려줘요 (넣는 건 화면이 해요)."""
        from .subtitle_import import SubtitleParseError, parse_subtitles
        try:
            return parse_subtitles(await request.body(), name)
        except SubtitleParseError as e:
            raise HTTPException(400, str(e))

    @router.get("/exports/{name}")
    def get_export(name: str, project: str = ""):
        p = (ws.at(project) if project else ws).root / "exports" / Path(name).name
        if not p.exists() or p.suffix not in (".mp4", ".png"):
            raise HTTPException(404)
        return FileResponse(p, filename=p.name)

    return router
