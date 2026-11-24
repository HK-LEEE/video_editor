"""워크스페이스·프로젝트 선택기의 서버 주소: 목록 · 만들기 · 열기 · 이름 바꾸기 · 복제 · 지우기 · 옮기기 · 편집 상태(프로젝트 파일) 저장."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .editor import Workspace


class NameReq(BaseModel):
    name: str = Field("", max_length=60)


class OpenReq(BaseModel):
    id: str
    force: bool = False        # 오래 걸리는 작업이 돌고 있어도 바꾸기


class DeleteReq(BaseModel):
    confirm: str = ""          # 지울 이름을 그대로 적어야 지워져요


class MoveReq(BaseModel):
    space: str                 # 옮겨 갈 워크스페이스 id


BUSY = "오래 걸리는 작업(AI 변환·받아쓰기 등)이 아직 돌고 있어요. 끝난 뒤에 바꾸거나, 그래도 바꾸려면 '강제로 열기'를 눌러 주세요."


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    sp = ws.spaces

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
        return sp.open(b.id)

    @router.patch("/workspace/spaces/{sid}")
    def rename_space(sid: str, b: NameReq):
        return sp.rename(sid, b.name)

    @router.post("/workspace/spaces/{sid}/delete")
    def delete_space(sid: str, b: DeleteReq):
        if jobs.busy() and sid == sp.active:
            raise HTTPException(409, "이 워크스페이스에서 오래 걸리는 작업이 돌고 있어요. 끝난 뒤에 지워 주세요.")
        return sp.delete(sid, b.confirm)

    # ---- 프로젝트 (열려 있는 워크스페이스 안)
    @router.post("/workspace/projects")
    def create_project(b: NameReq):
        """새 프로젝트를 만들고 바로 엽니다."""
        if jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 바꿔 주세요.")
        return ws.projects.create(b.name or "새 프로젝트")

    @router.post("/workspace/open")
    def open_project(b: OpenReq):
        if jobs.busy() and not b.force and b.id != ws.projects.active:
            raise HTTPException(409, BUSY)
        return ws.projects.open(b.id)

    @router.patch("/workspace/projects/{pid}")
    def rename_project(pid: str, b: NameReq):
        return ws.projects.rename(pid, b.name)

    @router.post("/workspace/projects/{pid}/duplicate")
    def duplicate_project(pid: str, b: NameReq):
        return ws.projects.duplicate(pid, b.name)

    @router.post("/workspace/projects/{pid}/move")
    def move_project(pid: str, b: MoveReq):
        """프로젝트를 다른 워크스페이스로 옮깁니다 (열린 프로젝트면 옮긴 뒤 그쪽에서 열려요)."""
        if jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 옮겨 주세요.")
        was_active = pid == ws.projects.active
        out = sp.move_project(pid, ws.sid, b.space)
        if was_active:
            sp.open(b.space)
        return out

    @router.post("/workspace/projects/{pid}/delete")
    def delete_project(pid: str, b: DeleteReq):
        if jobs.busy() and pid == ws.projects.active:
            raise HTTPException(409, "이 프로젝트에서 오래 걸리는 작업이 돌고 있어요. 끝난 뒤에 지워 주세요.")
        return ws.projects.delete(pid, b.confirm)

    @router.get("/workspace/timeline")
    def get_timeline(project: str = "", space: str = ""):
        pr = sp.projects(space) if space else ws.projects
        return pr.load_timeline(project or pr.active) or {}

    @router.put("/workspace/timeline")
    def put_timeline(b: dict, project: str = "", space: str = ""):
        """화면이 보내는 워크스페이스·프로젝트 id에 저장해요 (다른 탭에서 바꿔도 엉뚱한 곳에 덮어쓰지 않게)."""
        pr = sp.projects(space) if space else ws.projects
        pid = project or pr.active
        pr.info(pid)
        if not isinstance(b.get("project"), dict):
            raise HTTPException(400, "편집 상태 모양이 올바르지 않아요.")
        pr.save_timeline(pid, {"project": b["project"], "ui": b.get("ui") if isinstance(b.get("ui"), dict) else {}})
        return {"ok": True}
