"""쇼츠 도구 모음의 서버 주소: 효과음·음악 보관함, 박자 컷, Claude 문구, 썸네일, 템플릿·엔딩, 보관한 프로젝트, 일괄 적용."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import beats as beats_mod
from . import claude_copy, endings as endings_mod, library, shorts, thumbnail
from .editor import Project, Workspace, _check_id, canvas_size, clip_len, export_video
from .templates import EndingPreset, ProjectStore, Store, Template, apply_ending, apply_template, project_from_media


class BeatsReq(BaseModel):
    media: str
    bpm: float | None = Field(None, ge=40, le=240)     # 알고 있으면 (또는 절반·두 배로 다시 잡을 때)


class BeatsPlanReq(BaseModel):
    project: Project
    beats: list[float] = Field(max_length=5000)        # 타임라인 초
    mode: str = Field("snap", pattern="^(snap|split)$")
    ids: list[str] = []                                # 대상 영상 클립 (비우면 모든 영상 클립, split은 필수)
    every: int = Field(4, ge=1, le=32)
    min_beats: int = Field(1, ge=1, le=16)
    align_start: bool = False
    transition: str = "none"
    transition_dur: float = Field(0.2, ge=0.1, le=1.0)


class SettingsReq(BaseModel):
    api_key: str | None = None
    model: str | None = None
    engine: str | None = None


class CueIn(BaseModel):
    start: float = 0
    text: str = ""


class CopyReq(BaseModel):
    cues: list[CueIn]
    duration: float = 0
    topic: str = ""
    note: str = ""
    model: str | None = None


class ThumbReq(BaseModel):
    project: Project
    t: float = Field(0, ge=0)
    text: str = Field("", max_length=60)
    color: str = "#ffe135"
    position: float = Field(0.2, ge=0, le=0.8)


class ApplyReq(BaseModel):
    project: Project
    template_id: str


class BatchReq(BaseModel):
    template_id: str
    project_ids: list[str] = []
    media_ids: list[str] = []          # 보관함 영상마다 새 프로젝트를 만들어 입힘
    name_prefix: str = ""


class EndingApplyReq(BaseModel):
    project: Project
    ending_id: str


class BatchExportReq(BaseModel):
    project_ids: list[str]
    format: str = Field("full", pattern="^(full|shorts|reels)$")


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    # 템플릿·엔딩 프리셋은 모든 프로젝트가 같이 쓰고, '보관한 프로젝트'는 프로젝트마다 따로 둡니다 (목록·일괄 작업은 전체를 봐요)
    templates = Store(ws.home, "templates", Template)
    endings = Store(ws.home, "endings", EndingPreset)

    def store(w: Workspace = ws) -> ProjectStore:
        return ProjectStore(w.root / "saved")

    def locate(sid: str) -> Workspace:
        """보관한 프로젝트가 들어 있는 작업 폴더 (열린 프로젝트부터 찾고, 없으면 다른 프로젝트들)."""
        order = [ws.pid] + [p["id"] for p in ws.projects.list() if p["id"] != ws.pid]
        for pid in order:
            w = ws.at(pid)
            if ProjectStore(w.root / "saved")._p(sid).exists():
                return w
        raise HTTPException(404, "보관한 프로젝트를 찾을 수 없어요.")

    def launch(work):
        job = jobs.submit("studio", uuid.uuid4().hex, work)
        return {"status": job.status, "job": job.id}

    # ---- 효과음·음악 보관함
    @router.get("/library")
    def library_catalog():
        return library.catalog(ws)

    @router.post("/library/ensure")
    def library_ensure():
        """기본 소리를 (아직이면) 만들어 보관함에 등록합니다. 처음 한 번만 몇 초 걸려요."""
        media = library.ensure_all(ws)
        return {"items": library.catalog(ws), "media": media}

    @router.post("/library/upload")
    def library_upload(file: UploadFile = File(...), kind: str = Form("sfx"), name: str = Form(""), tags: str = Form(""),
                       mood: str = Form(""), bpm: float = Form(0.0)):
        tmp = ws.root / "cache" / f"upload_{uuid.uuid4().hex}{Path(file.filename or '').suffix[:8]}"
        with tmp.open("wb") as f:
            f.write(file.file.read())
        try:
            return library.add_user(ws, tmp, name or Path(file.filename or "소리").stem, kind,
                                    [t for t in tags.replace("#", " ").replace(",", " ").split()], mood, bpm)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            tmp.unlink(missing_ok=True)

    @router.delete("/library/{mid}")
    def library_delete(mid: str):
        try:
            library.remove_user(ws, mid)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    # ---- 박자
    @router.post("/beats")
    def beats(b: BeatsReq):
        _check_id(b.media)
        ws.meta(b.media)
        try:
            return library.beats_for(ws, b.media, b.bpm)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/beats/plan")
    def beats_plan(b: BeatsPlanReq):
        try:
            if b.mode == "snap":
                lens = {c.media: float(ws.meta(c.media)["duration"]) for c in b.project.clips}
                out = beats_mod.snap_cuts(b.project, b.beats, lens, set(b.ids) or None, b.min_beats, b.align_start,
                                          b.transition, b.transition_dur)
            else:
                if not b.ids:
                    raise ValueError("자를 영상 클립을 먼저 골라 주세요.")
                out = beats_mod.split_on_beats(b.project, b.beats, set(b.ids), b.every, b.transition, b.transition_dur)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"clips": [c.model_dump() for c in out.clips], "subtitles": [c.model_dump() for c in out.subtitles]}

    # ---- Claude 문구
    @router.get("/settings")
    def get_settings():
        return claude_copy.public_settings(ws.home)

    @router.put("/settings")
    def put_settings(b: SettingsReq):
        try:
            return claude_copy.save_settings(ws.home, b.api_key, b.model, b.engine)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/ai/copy")
    def ai_copy(b: CopyReq):
        st = claude_copy.public_settings(ws.home)
        if not st["ready"]:
            raise HTTPException(503, st["help"])

        def work():
            return claude_copy.generate(ws.home, [c.model_dump() for c in b.cues], b.duration, b.topic, b.model, b.note)

        return launch(work)

    @router.post("/thumbnail")
    def make_thumbnail(b: ThumbReq):
        p = b.project
        order = {t.id: i for i, t in enumerate(p.tracks) if t.kind == "video" and not t.mute}
        hit = [c for c in p.clips if c.track in order and c.layout == "full" and c.start - 1e-6 <= b.t < c.start + clip_len(c)]
        if not hit:
            raise HTTPException(400, "그 시각에는 보이는 영상이 없어요.")
        c = max(hit, key=lambda c: order[c.track])
        m = ws.meta(c.media)
        if not m["has_video"]:
            raise HTTPException(400, "그 시각의 클립에 화면이 없어요.")
        name = f"thumb_{uuid.uuid4().hex[:8]}"
        frame = ws.root / "cache" / f"{name}_f.png"
        try:
            thumbnail.grab_frame(ws.media_file(c.media), c.src_in + (b.t - c.start) * c.speed, frame)
            out = thumbnail.render(frame, b.text, ws.root / "exports" / f"{name}.png", b.color, b.position)
        except RuntimeError as e:
            raise HTTPException(500, str(e)) from e
        finally:
            frame.unlink(missing_ok=True)
        return {"file": out.name, "url": f"/api/exports/{out.name}"}

    # ---- 템플릿 · 엔딩 프리셋
    @router.get("/templates")
    def list_templates():
        return [t.model_dump() for t in templates.all()]

    @router.post("/templates")
    def save_template(t: Template):
        for mid in (t.intro, t.outro):
            if mid:
                ws.meta(mid)
                ws.promote(mid)    # 템플릿은 모든 프로젝트에서 쓰니 인트로·아웃트로 파일도 공유 보관함에 둡니다
        if t.ending and endings.get(t.ending) is None:
            raise HTTPException(400, "없는 엔딩 프리셋이에요.")
        return templates.save(t).model_dump()

    @router.delete("/templates/{tid}")
    def delete_template(tid: str):
        templates.delete(tid)
        return {"ok": True}

    @router.get("/endings")
    def list_endings():
        return {"kinds": endings_mod.KINDS, "defaults": endings_mod.DEFAULTS, "items": [e.model_dump() for e in endings.all()]}

    @router.post("/endings")
    def save_ending(e: EndingPreset):
        if e.kind not in endings_mod.KINDS:
            raise HTTPException(400, "모르는 엔딩 종류예요.")
        return endings.save(e).model_dump()

    @router.delete("/endings/{eid}")
    def delete_ending(eid: str):
        endings.delete(eid)
        return {"ok": True}

    @router.post("/endings/preview")
    def preview_ending(e: EndingPreset):
        if e.kind not in endings_mod.KINDS:
            raise HTTPException(400, "모르는 엔딩 종류예요.")

        def work():
            name = f"ending_prev_{uuid.uuid4().hex[:8]}.mp4"
            endings_mod.render(ws.root / "exports" / name, e.model_dump(exclude={"id", "name"}), 360, 640)
            return {"url": f"/api/exports/{name}"}

        return launch(work)

    @router.post("/endings/apply")
    def endings_apply(b: EndingApplyReq):
        e = endings.get(b.ending_id)
        if e is None:
            raise HTTPException(404, "엔딩 프리셋을 찾을 수 없어요.")
        return apply_ending(ws, b.project, e).model_dump()

    def _apply(p: Project, tid: str, w: Workspace = ws) -> Project:
        t = templates.get(tid)
        if t is None:
            raise HTTPException(404, "템플릿을 찾을 수 없어요.")
        try:
            return apply_template(w, p, t, endings)
        except RuntimeError as e:
            raise HTTPException(500, str(e)) from e

    @router.post("/template/apply")
    def template_apply(b: ApplyReq):
        """현재 프로젝트에 템플릿을 입힌 새 프로젝트 (화면이 받아서 바꿉니다)."""
        return _apply(b.project, b.template_id).model_dump()

    # ---- 보관한 프로젝트 · 일괄 적용
    @router.get("/projects")
    def list_projects():
        out = []
        for info in ws.projects.list():
            w = ws.at(info["id"])
            for d in store(w).list():
                out.append({**d, "workspace": info["id"], "workspace_name": info["name"], "current": info["id"] == ws.pid})
        return sorted(out, key=lambda x: (not x["current"], -x["saved_at"]))

    @router.post("/projects")
    def save_project(b: dict):
        try:
            p = Project(**b["project"])
        except (KeyError, ValueError) as e:
            raise HTTPException(400, "프로젝트 모양이 올바르지 않아요.") from e
        sid = b.get("id")
        w = locate(sid) if sid else ws        # 이미 있는 보관본을 덮어쓸 땐 그 보관본이 있던 프로젝트에 둡니다
        return store(w).save(str(b.get("name", "")), p, sid)

    @router.get("/projects/{pid}")
    def get_project(pid: str):
        w = locate(pid)
        d, p = store(w).load(pid)
        return {"id": d["id"], "name": d["name"], "project": p.model_dump(), "workspace": w.pid, "current": w.pid == ws.pid}

    @router.delete("/projects/{pid}")
    def delete_project(pid: str):
        store(locate(pid)).delete(pid)
        return {"ok": True}

    @router.post("/template/batch")
    def template_batch(b: BatchReq):
        if templates.get(b.template_id) is None:
            raise HTTPException(404, "템플릿을 찾을 수 없어요.")
        if not b.project_ids and not b.media_ids:
            raise HTTPException(400, "입힐 프로젝트나 영상을 골라 주세요.")

        def work():
            results = []
            for pid in b.project_ids:
                try:
                    w = locate(pid)       # 다른 프로젝트에 있는 보관본도 그 프로젝트의 미디어로 입힙니다
                    d, p = store(w).load(pid)
                    store(w).save(d["name"], _apply(p, b.template_id, w), pid)
                    results.append({"id": pid, "name": d["name"], "ok": True, "workspace": w.pid})
                except HTTPException as e:
                    results.append({"id": pid, "name": pid, "ok": False, "error": str(e.detail)})
            for mid in b.media_ids:
                try:
                    _check_id(mid)
                    m = ws.meta(mid)
                    saved = store().save(f"{b.name_prefix}{Path(m['name']).stem}", _apply(project_from_media(ws, mid), b.template_id))
                    results.append({"id": saved["id"], "name": saved["name"], "ok": True})
                except HTTPException as e:
                    results.append({"id": mid, "name": mid, "ok": False, "error": str(e.detail)})
            return {"results": results}

        return launch(work)

    @router.post("/batch/export")
    def batch_export(b: BatchExportReq):
        if not b.project_ids:
            raise HTTPException(400, "내보낼 프로젝트를 골라 주세요.")

        def work():
            results = []
            for pid in b.project_ids:
                try:
                    w = locate(pid)
                    d, p = store(w).load(pid)
                    spec = None if b.format == "full" else shorts.ShortSpec(format=b.format)
                    out = export_video(w, p, spec)
                    results.append({"id": pid, "name": d["name"], "ok": True, "workspace": w.pid,
                                    "url": f"/api/exports/{out.name}?project={w.pid}"})
                except HTTPException as e:
                    results.append({"id": pid, "name": pid, "ok": False, "error": str(e.detail)})
                except Exception as e:  # noqa: BLE001
                    results.append({"id": pid, "name": pid, "ok": False, "error": str(e)})
            return {"results": results}

        return launch(work)
