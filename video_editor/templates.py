"""템플릿(자막 스타일·인트로·아웃트로·워터마크·훅 제목 모양·엔딩)과 엔딩 프리셋, 보관한 프로젝트, 그리고 일괄 적용.

- 템플릿과 엔딩 프리셋은 작업 폴더의 json 파일에 이름을 붙여 저장합니다.
- `apply_template`은 프로젝트에 템플릿을 입혀 새 프로젝트를 돌려줍니다. 템플릿이 넣은 클립·글자에는 `tpl` 표시를 붙여 두어서
  같은 템플릿(또는 다른 템플릿)을 다시 입혀도 겹쳐 쌓이지 않고 먼저 걷어낸 뒤 새로 넣습니다.
- 보관한 프로젝트 여러 개, 또는 보관함의 영상 여러 개에 한 번에 입힐 수 있습니다 (`batch_apply`, `projects_from_media`).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import endings as endings_mod
from .editor import Clip, Project, Track, Workspace, canvas_size, clip_len, project_length
from .overlays import Overlay
from .subtitles import SubtitleStyle

TRANSITIONS_OK = ("none", "fade", "dip", "slideleft", "slideright", "wipeleft", "wiperight")


class Watermark(BaseModel):
    text: str = Field(min_length=1, max_length=60)
    x: float = Field(0.16, ge=0, le=1)
    y: float = Field(0.06, ge=0, le=1)
    size: float = Field(0.028, ge=0.01, le=0.2)
    opacity: float = Field(0.55, ge=0.05, le=1)
    color: str = "#ffffff"


class HookStyle(BaseModel):
    """훅 제목(맨 위 큰 제목 바)의 모양. 글은 영상마다 다르니 모양만 템플릿에 담습니다."""
    y: float = Field(0.14, ge=0, le=1)
    size: float = Field(0.062, ge=0.01, le=0.3)
    color: str = "#ffffff"
    bg_color: str = "#e11d48"
    bg_opacity: float = Field(0.92, ge=0, le=1)
    outline: float = Field(0.0, ge=0, le=0.5)
    start: float = 0.0
    end: float | None = 3.0


class Anim(BaseModel):
    """템플릿 안에 바로 들어 있는 짧은 애니메이션 카드 (인트로·아웃트로 카드, 엔딩). `{channel}`은 채널 이름으로 바뀝니다."""
    kind: str = "pop"
    text: str = Field("", max_length=40)
    sub: str = Field("", max_length=40)
    accent: str = "#ffd23f"
    bg1: str = "#1b1030"
    bg2: str = "#3a1c5e"
    duration: float = Field(1.6, ge=0.6, le=8.0)


class Template(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=40)
    desc: str = ""                           # 한 줄 설명 (샘플 템플릿)
    builtin: bool = False                    # 앱에 들어 있는 샘플 템플릿 (지울 수 없어요)
    intro_card: Anim | None = None           # 직접 그리는 인트로 카드 (intro 미디어가 없을 때)
    outro_card: Anim | None = None
    ending_card: Anim | None = None          # 직접 그리는 엔딩 (ending 프리셋이 없을 때)
    subtitle_style: SubtitleStyle | None = None
    hook: HookStyle | None = None
    intro: str | None = None                 # 보관함 미디어 id
    outro: str | None = None
    intro_transition: str = "none"
    outro_transition: str = "fade"
    watermark: Watermark | None = None
    ending: str | None = None                # 엔딩 프리셋 id
    scope: str = ""                          # "space"=이 워크스페이스 전용 (기본), "global"=모든 워크스페이스에 공용


class EndingPreset(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=40)
    kind: str = "subscribe"
    text: str = Field("구독 & 좋아요", max_length=30)
    sub: str = Field("알림 설정까지!", max_length=30)
    accent: str = "#ff3b3b"
    bg1: str = "#1b1030"
    bg2: str = "#3a1c5e"
    duration: float = Field(3.0, ge=1.5, le=8.0)
    scope: str = ""                          # "space" | "global" (템플릿과 같아요)


# ---------------------------------------------------------------- 저장소 (json 파일)

class Store:
    def __init__(self, root: Path, name: str, model):
        self.path = Path(root) / f"{name}.json"
        self.model = model

    def all(self) -> list:
        try:
            return [self.model(**d) for d in json.loads(self.path.read_text())] if self.path.exists() else []
        except (OSError, json.JSONDecodeError, ValueError):
            return []

    def get(self, id_: str):
        return next((x for x in self.all() if x.id == id_), None)

    def save(self, item):
        items = self.all()
        if not item.id:
            item.id = uuid.uuid4().hex[:10]
        items = [x for x in items if x.id != item.id] + [item]
        self.path.write_text(json.dumps([x.model_dump() for x in items], ensure_ascii=False))
        return item

    def delete(self, id_: str) -> None:
        self.path.write_text(json.dumps([x.model_dump() for x in self.all() if x.id != id_], ensure_ascii=False))


class ScopedStore:
    """템플릿·엔딩 프리셋 저장소: 이 워크스페이스 전용(채널별 브랜딩)과 전역 공용을 함께 보여 줍니다.
    저장할 때 scope가 'global'이면 전역 파일에, 아니면 이 워크스페이스에 둬요 (같은 id가 다른 쪽에 있으면 옮겨져요)."""

    def __init__(self, ws, name: str, model):
        self.ws, self.name, self.model = ws, name, model

    def _store(self, scope: str) -> Store:
        return Store(self.ws.home if scope == "global" else self.ws.space_dir, self.name, self.model)

    def all(self) -> list:
        out = []
        for scope in ("space", "global"):
            for x in self._store(scope).all():
                x.scope = scope
                out.append(x)
        return out

    def get(self, id_: str):
        return next((x for x in self.all() if x.id == id_), None)

    def save(self, item):
        scope = "global" if item.scope == "global" else "space"
        if item.id:
            other = self._store("space" if scope == "global" else "global")
            if other.get(item.id):
                other.delete(item.id)
        item.scope = scope
        return self._store(scope).save(item)

    def delete(self, id_: str) -> None:
        for scope in ("space", "global"):
            if self._store(scope).get(id_):
                self._store(scope).delete(id_)


class ProjectStore:
    def __init__(self, dir_: Path):
        self.dir = Path(dir_)            # 프로젝트 폴더의 saved/ — 프로젝트마다 따로 보관해요
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, pid: str) -> Path:
        if not pid or not all(c in "0123456789abcdef" for c in pid) or len(pid) > 32:
            raise HTTPException(400, "잘못된 id")
        return self.dir / f"{pid}.json"

    def list(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            d = json.loads(p.read_text())
            pr = Project(**d["project"])
            out.append({"id": d["id"], "name": d["name"], "saved_at": d["saved_at"], "clips": len(pr.clips), "length": round(project_length(pr), 2)})
        return sorted(out, key=lambda x: -x["saved_at"])

    def save(self, name: str, project: Project, pid: str | None = None) -> dict:
        pid = pid or uuid.uuid4().hex[:12]
        d = {"id": pid, "name": name.strip() or "이름 없음", "saved_at": time.time(), "project": project.model_dump()}
        self._p(pid).write_text(json.dumps(d, ensure_ascii=False))
        return {k: d[k] for k in ("id", "name", "saved_at")}

    def load(self, pid: str) -> tuple[dict, Project]:
        p = self._p(pid)
        if not p.exists():
            raise HTTPException(404, "보관한 프로젝트를 찾을 수 없어요.")
        d = json.loads(p.read_text())
        return d, Project(**d["project"])

    def delete(self, pid: str) -> None:
        self._p(pid).unlink(missing_ok=True)


# ---------------------------------------------------------------- 클립 넣기·걷어내기

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _shift(p: Project, d: float) -> None:
    """모든 클립·자막·글자를 d초 뒤로 (d<0이면 앞으로)."""
    for c in p.clips:
        c.start = round(max(0.0, c.start + d), 4)
    for q in p.subtitles:
        q.start = round(max(0.0, q.start + d), 4)
        q.end = round(max(q.start + 0.05, q.end + d), 4)
    for o in p.overlays:
        o.start = round(max(0.0, o.start + d), 4)
        if o.end is not None:
            o.end = round(max(o.start + 0.05, o.end + d), 4)
    for t in p.tracks:
        for k in t.vol_keys:
            k.t = round(max(0.0, k.t + d), 4)


def _strip(p: Project) -> None:
    """이전 템플릿이 넣은 인트로·아웃트로·엔딩과 글자를 걷어냅니다 (인트로를 걷으면 나머지를 앞으로 당김)."""
    intro_len = max([c.start + clip_len(c) for c in p.clips if c.tpl == "intro"] + [0.0])
    p.clips = [c for c in p.clips if not c.tpl]
    p.overlays = [o for o in p.overlays if not o.tpl]
    if intro_len > 0:
        _shift(p, -intro_len)


def _audio_track(p: Project, t0: float, t1: float) -> Track:
    free = lambda t: not any(c.track == t.id and c.start < t1 - 1e-3 and c.start + clip_len(c) > t0 + 1e-3 for c in p.clips)
    cands = [t for t in p.tracks if t.kind == "audio" and not t.voice and free(t)] or [t for t in p.tracks if t.kind == "audio" and free(t)]
    if cands:
        return cands[0]
    t = Track(id=_uid(), kind="audio", name=f"A{sum(1 for x in p.tracks if x.kind == 'audio') + 1}")
    p.tracks.append(t)
    return t


def _video_track(p: Project) -> Track:
    vt = next((t for t in p.tracks if t.kind == "video"), None)
    if vt:
        return vt
    vt = Track(id=_uid(), kind="video", name="V1")
    p.tracks.insert(0, vt)
    return vt


def _place(ws: Workspace, p: Project, mid: str, at: float, tag: str, transition: str = "none") -> float:
    m = ws.meta(mid)
    dur = float(m["duration"])
    vt = _video_track(p)
    link = _uid() if m["has_audio"] else None
    if m["has_video"]:
        p.clips.append(Clip(id=_uid(), track=vt.id, media=mid, src_in=0, src_out=dur, start=at, link=link, tpl=tag,
                            transition=transition if transition in TRANSITIONS_OK else "none"))
    if m["has_audio"]:
        at_t = _audio_track(p, at, at + dur)
        p.clips.append(Clip(id=_uid(), track=at_t.id, media=mid, src_in=0, src_out=dur, start=at, link=link, tpl=tag))
    return dur


def _anim_media(ws: Workspace, params: dict, label: str, W: int, H: int) -> str:
    """애니메이션 카드를 (같은 설정은 한 번만) 만들어 보관함에 숨겨 두고 그 미디어 id를 돌려줍니다."""
    mid = endings_mod.ending_key(params, W, H)
    meta_p = ws.meta_path(mid)
    if not meta_p.exists():
        out = ws.root / "media" / f"{mid}.mp4"
        endings_mod.render(out, params, W, H)
        from .editor import probe
        info = probe(out)
        meta_p.write_text(json.dumps({"id": mid, "name": label, "file": out.name, "recorded": False,
                                      "kind": "video", "hidden": True, **info}, ensure_ascii=False))
    return mid


def _ending_media(ws: Workspace, preset: EndingPreset, W: int, H: int) -> str:
    return _anim_media(ws, preset.model_dump(exclude={"id", "name"}), f"엔딩 · {preset.name}", W, H)


DEFAULT_CHANNEL = "내 채널"
HOOK_PLACEHOLDER = "여기에 훅 제목"


def _fill(text: str, channel: str) -> str:
    return text.replace("{channel}", channel or DEFAULT_CHANNEL)


def _card_params(a: Anim, channel: str) -> dict:
    d = a.model_dump()
    d["text"], d["sub"] = _fill(a.text, channel), _fill(a.sub, channel)
    return d


def apply_template(ws: Workspace, p: Project, tpl: Template, endings: Store, canvas: tuple[int, int] | None = None,
                   channel: str = "", hook_text: str | None = None) -> Project:
    """hook_text를 주면(빈 글이어도) 훅 제목이 아직 없을 때 그 글로 훅 제목을 하나 넣습니다 (모양은 템플릿 것)."""
    out = p.model_copy(deep=True)
    _strip(out)
    if tpl.subtitle_style:
        out.subtitle_style = tpl.subtitle_style.model_copy(deep=True)
    if tpl.hook:
        h = tpl.hook
        for o in out.overlays:
            if o.kind == "title":
                o.y, o.size, o.color, o.bg_color, o.bg_opacity, o.outline = h.y, h.size, h.color, h.bg_color, h.bg_opacity, h.outline
        if hook_text is not None and not any(o.kind == "title" for o in out.overlays):
            out.overlays.append(Overlay(id=_uid(), kind="title", text=hook_text.strip() or HOOK_PLACEHOLDER, start=h.start, end=h.end,
                                        y=h.y, size=h.size, color=h.color, bg_color=h.bg_color, bg_opacity=h.bg_opacity, outline=h.outline))
    W, H = canvas or canvas_size(ws, out)
    if tpl.intro:
        _place_front(ws, out, tpl.intro, tpl.intro_transition)
    elif tpl.intro_card:
        _place_front(ws, out, _anim_media(ws, _card_params(tpl.intro_card, channel), f"인트로 · {tpl.name}", W, H), tpl.intro_transition)
    if tpl.outro:
        _place(ws, out, tpl.outro, project_length(out), "outro", tpl.outro_transition)
    elif tpl.outro_card:
        _place(ws, out, _anim_media(ws, _card_params(tpl.outro_card, channel), f"아웃트로 · {tpl.name}", W, H),
               project_length(out), "outro", tpl.outro_transition)
    if tpl.ending:
        preset = endings.get(tpl.ending)
        if preset is None:
            raise HTTPException(404, "엔딩 프리셋을 찾을 수 없어요.")
        _place(ws, out, _ending_media(ws, preset, W, H), project_length(out), "ending")
    elif tpl.ending_card:
        _place(ws, out, _anim_media(ws, _card_params(tpl.ending_card, channel), f"엔딩 · {tpl.name}", W, H), project_length(out), "ending")
    if tpl.watermark:
        w = tpl.watermark
        out.overlays.append(Overlay(id=_uid(), kind="watermark", text=_fill(w.text, channel), x=w.x, y=w.y, size=w.size, opacity=w.opacity,
                                    color=w.color, bold=True, outline=0.08, max_width=0.5, tpl="watermark"))
    return out


def _place_front(ws: Workspace, p: Project, mid: str, transition: str) -> float:
    d = float(ws.meta(mid)["duration"])
    _shift(p, d)
    return _place(ws, p, mid, 0.0, "intro", "none")


def project_from_media(ws: Workspace, mid: str) -> Project:
    """보관함 영상(또는 소리) 하나를 넣은 새 프로젝트: 영상 1·원본 소리·보이스·음악 트랙."""
    m = ws.meta(mid)
    p = Project(tracks=[Track(id=_uid(), kind="video", name="V1"), Track(id=_uid(), kind="audio", name="원본 소리", duck=0.3),
                        Track(id=_uid(), kind="audio", name="보이스", voice=True, room=0.25, level=True, gate=True, tone=True),
                        Track(id=_uid(), kind="audio", name="음악·효과음", duck=0.5)], clips=[])
    dur = float(m["duration"])
    link = _uid() if (m["has_video"] and m["has_audio"]) else None
    if m["has_video"]:
        p.clips.append(Clip(id=_uid(), track=p.tracks[0].id, media=mid, src_in=0, src_out=dur, start=0, link=link))
    if m["has_audio"]:
        p.clips.append(Clip(id=_uid(), track=p.tracks[1].id, media=mid, src_in=0, src_out=dur, start=0, link=link))
    return p


def apply_ending(ws: Workspace, p: Project, preset: EndingPreset) -> Project:
    """엔딩만 (다시) 붙입니다. 이전에 붙인 엔딩은 걷어내고 영상 맨 끝에 새로 붙여요."""
    out = p.model_copy(deep=True)
    out.clips = [c for c in out.clips if c.tpl != "ending"]
    W, H = canvas_size(ws, out)
    _place(ws, out, _ending_media(ws, preset, W, H), project_length(out), "ending")
    return out
