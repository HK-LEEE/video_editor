"""워크스페이스·프로젝트 선택기의 서버 주소: 목록 · 만들기 · 열기 · 이름 바꾸기 · 복제 · 지우기 · 옮기기 · 편집 상태(프로젝트 파일) 저장."""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .editor import Workspace, link_path


class NameReq(BaseModel):
    name: str = Field("", max_length=60)


class OpenReq(BaseModel):
    id: str
    project: str = ""          # 워크스페이스를 열면서 그 안의 이 프로젝트를 같이 엽니다
    force: bool = False        # 오래 걸리는 작업이 돌고 있어도 바꾸기


class DeleteReq(BaseModel):
    confirm: str = ""          # 지울 이름을 그대로 적어야 지워져요


class MoveReq(BaseModel):
    space: str                 # 옮겨 갈 워크스페이스 id


BUSY = "오래 걸리는 작업(AI 변환·받아쓰기 등)이 아직 돌고 있어요. 끝난 뒤에 바꾸거나, 그래도 바꾸려면 '강제로 열기'를 눌러 주세요."


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    sp = ws.spaces

    def P(space: str):
        """대상 워크스페이스의 프로젝트 목록 (비우면 열려 있는 워크스페이스)."""
        return sp.projects(space) if space else ws.projects

    def active_here(space: str, pid: str) -> bool:
        return (not space or space == ws.sid) and pid == ws.pid

    def here(space: str, pr) -> str:
        """프로젝트를 안 정했을 때: 이 창이 보고 있는 프로젝트 (다른 워크스페이스면 거기서 마지막으로 연 것)."""
        return ws.pid if not space or space == ws.sid else pr.active

    @router.get("/workspace")
    def workspace_info():
        """열린 워크스페이스(space)와 그 안의 프로젝트 목록, 전체 워크스페이스 목록."""
        info = ws.projects.listing()
        return {**info, "space": ws.sid, "space_name": sp.info(ws.sid)["name"], "spaces": sp.listing()}

    # ---- 워크스페이스
    @router.post("/workspace/spaces")
    def create_space(b: NameReq):
        if jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 바꿔 주세요.")
        return sp.create(b.name or "새 워크스페이스")

    @router.post("/workspace/spaces/open")
    def open_space(b: OpenReq):
        if jobs.busy() and not b.force and b.id != sp.active:
            raise HTTPException(409, BUSY)
        out = sp.open(b.id)
        if b.project:
            sp.projects(b.id).open(b.project)
        return out

    @router.patch("/workspace/spaces/{sid}")
    def rename_space(sid: str, b: NameReq):
        return sp.rename(sid, b.name)

    @router.post("/workspace/spaces/{sid}/delete")
    def delete_space(sid: str, b: DeleteReq):
        if jobs.busy() and sid == sp.active:
            raise HTTPException(409, "이 워크스페이스에서 오래 걸리는 작업이 돌고 있어요. 끝난 뒤에 지워 주세요.")
        return sp.delete(sid, b.confirm)

    @router.get("/workspace/spaces/{sid}/projects")
    def space_projects(sid: str):
        """표(자세히 보기)용: 그 워크스페이스의 프로젝트들 + 길이·클립·미디어·용량."""
        out = sp.projects(sid).listing(detail=True)
        return {**out, "space": sid, "space_active": sid == ws.sid}

    # ---- 프로젝트 (space 쿼리로 다른 워크스페이스의 프로젝트도 다룰 수 있어요. 비우면 열려 있는 워크스페이스)
    @router.post("/workspace/projects")
    def create_project(b: NameReq, space: str = "", open: bool = True):
        """새 프로젝트를 만들고 (기본은) 바로 엽니다."""
        if open and jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 바꿔 주세요.")
        info = P(space).create(b.name or "새 프로젝트", open_=open)
        if open and space and space != sp.active:
            sp.open(space)
        return info

    @router.post("/workspace/open")
    def open_project(b: OpenReq):
        if jobs.busy() and not b.force and b.id != ws.pid:
            raise HTTPException(409, BUSY)
        return ws.projects.open(b.id)

    @router.patch("/workspace/projects/{pid}")
    def rename_project(pid: str, b: NameReq, space: str = ""):
        return P(space).rename(pid, b.name)

    @router.post("/workspace/projects/{pid}/duplicate")
    def duplicate_project(pid: str, b: NameReq, space: str = ""):
        return P(space).duplicate(pid, b.name)

    @router.post("/workspace/projects/{pid}/move")
    def move_project(pid: str, b: MoveReq, space: str = ""):
        """프로젝트를 다른 워크스페이스로 옮깁니다 (열린 프로젝트면 옮긴 뒤 그쪽에서 열려요)."""
        if jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 옮겨 주세요.")
        src = space or ws.sid
        was_active = active_here(space, pid)
        out = sp.move_project(pid, src, b.space)
        if was_active:
            sp.open(b.space)
        return out

    @router.post("/workspace/projects/{pid}/delete")
    def delete_project(pid: str, b: DeleteReq, space: str = ""):
        if jobs.busy() and active_here(space, pid):
            raise HTTPException(409, "이 프로젝트에서 오래 걸리는 작업이 돌고 있어요. 끝난 뒤에 지워 주세요.")
        return P(space).delete(pid, b.confirm)

    @router.get("/workspace/timeline")
    def get_timeline(project: str = "", space: str = ""):
        pr = sp.projects(space) if space else ws.projects
        return pr.load_timeline(project or here(space, pr)) or {}

    @router.put("/workspace/timeline")
    def put_timeline(b: dict, project: str = "", space: str = ""):
        """화면이 보내는 워크스페이스·프로젝트 id에 저장해요 (다른 탭에서 바꿔도 엉뚱한 곳에 덮어쓰지 않게)."""
        pr = sp.projects(space) if space else ws.projects
        pid = project or here(space, pr)
        pr.info(pid)
        if not isinstance(b.get("project"), dict):
            raise HTTPException(400, "편집 상태 모양이 올바르지 않아요.")
        pr.save_timeline(pid, {"project": b["project"], "ui": b.get("ui") if isinstance(b.get("ui"), dict) else {}})
        return {"ok": True}


def add_page_routes(app: FastAPI, ws: Workspace, html_path: Path) -> None:
    """편집기 화면 주소: /editor (마지막으로 연 프로젝트) · /editor/w/<워크스페이스> · /editor/w/<워크스페이스>/p/<프로젝트> (딥링크)."""
    def render_editor(sid: str, pid: str, missing: str = "") -> HTMLResponse:
        """워크스페이스 sid 의 프로젝트 pid 를 화면에 실어 보냅니다 — 브라우저가 프로젝트별로 편집 상태를 따로 기억하게요."""
        pr = ws.spaces.projects(sid)
        info = pr.info(pid)
        sinfo = ws.spaces.info(sid)
        ws_info = {"id": info["id"], "name": info["name"], "space": sinfo["id"], "space_name": sinfo["name"],
                   "legacy": info.get("origin") == "migrated", "legacy2": sinfo.get("origin") == "migrated",
                   "timeline": pr.load_timeline(info["id"]), "link": link_path(sinfo["id"], info["id"]), "missing": missing}
        tag = "<script>window.__WS=" + json.dumps(ws_info, ensure_ascii=False).replace("<", "\\u003c") + ";</script>"
        html = html_path.read_text(encoding="utf-8").replace("</head>", tag + "</head>", 1)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})


    @app.get("/editor", include_in_schema=False)
    def editor(missing: str = ""):
        """주소 없이 들어오면 마지막으로 연 프로젝트를 보여 주고, 주소창은 그 프로젝트의 딥링크로 바꿔요(화면 쪽에서)."""
        return render_editor(ws.sid, ws.pid, missing)


    @app.get("/editor/w/{sid}", include_in_schema=False)
    def editor_space(sid: str):
        """워크스페이스 주소만 오면 그 워크스페이스에서 마지막으로 연 프로젝트로."""
        try:
            if ws.spaces.exists(sid):
                return RedirectResponse(link_path(sid, ws.spaces.projects(sid).active), status_code=302)
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse("/editor?missing=" + urllib.parse.quote(sid, safe=""), status_code=302)


    @app.get("/editor/w/{sid}/p/{pid}", include_in_schema=False)
    def editor_project(sid: str, pid: str):
        """딥링크: 이 주소의 프로젝트를 엽니다. 없으면 안내와 함께 열려 있던 프로젝트(→ 목록)로."""
        if not ws.resolve_link(f"/editor/w/{urllib.parse.quote(sid, safe='')}/p/{urllib.parse.quote(pid, safe='')}"):
            return RedirectResponse("/editor?missing=" + urllib.parse.quote(f"{sid}/{pid}", safe=""), status_code=302)
        # '마지막으로 연 곳'만 기억해 둡니다 (다른 창이 보던 프로젝트는 그 창의 주소가 정해서 영향 없어요)
        sp = ws.spaces
        if sp.projects(sid).active != pid:
            sp.projects(sid).open(pid)
        if sp.active != sid:
            sp.open(sid)
        return render_editor(sid, pid)
