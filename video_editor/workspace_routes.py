"""프로젝트 선택기의 서버 주소: 목록 · 만들기 · 열기 · 이름 바꾸기 · 복제 · 지우기 · 편집 상태(프로젝트 파일) 저장."""
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
    confirm: str = ""          # 지울 프로젝트 이름을 그대로 적어야 지워져요


def add_routes(router: APIRouter, ws: Workspace, jobs) -> None:
    pr = ws.projects

    @router.get("/workspace")
    def workspace_info():
        return pr.listing()

    @router.post("/workspace/projects")
    def create_project(b: NameReq):
        """새 프로젝트를 만들고 바로 엽니다."""
        if jobs.busy():
            raise HTTPException(409, "오래 걸리는 작업이 아직 돌고 있어요. 끝난 뒤에 바꿔 주세요.")
        return pr.create(b.name or "새 프로젝트")

    @router.post("/workspace/open")
    def open_project(b: OpenReq):
        if jobs.busy() and not b.force and b.id != pr.active:
            raise HTTPException(409, "오래 걸리는 작업(AI 변환·받아쓰기 등)이 아직 돌고 있어요. 끝난 뒤에 바꾸거나, 그래도 바꾸려면 '강제로 열기'를 눌러 주세요.")
        return pr.open(b.id)

    @router.patch("/workspace/projects/{pid}")
    def rename_project(pid: str, b: NameReq):
        return pr.rename(pid, b.name)

    @router.post("/workspace/projects/{pid}/duplicate")
    def duplicate_project(pid: str, b: NameReq):
        return pr.duplicate(pid, b.name)

    @router.post("/workspace/projects/{pid}/delete")
    def delete_project(pid: str, b: DeleteReq):
        if jobs.busy() and pid == pr.active:
            raise HTTPException(409, "이 프로젝트에서 오래 걸리는 작업이 돌고 있어요. 끝난 뒤에 지워 주세요.")
        return pr.delete(pid, b.confirm)

    @router.get("/workspace/timeline")
    def get_timeline(project: str = ""):
        return pr.load_timeline(project or ws.pid) or {}

    @router.put("/workspace/timeline")
    def put_timeline(b: dict, project: str = ""):
        """화면이 보내는 프로젝트 id에 저장해요 (다른 탭에서 프로젝트를 바꿔도 엉뚱한 곳에 덮어쓰지 않게)."""
        pid = project or ws.pid
        pr.info(pid)
        if not isinstance(b.get("project"), dict):
            raise HTTPException(400, "편집 상태 모양이 올바르지 않아요.")
        pr.save_timeline(pid, {"project": b["project"], "ui": b.get("ui") if isinstance(b.get("ui"), dict) else {}})
        return {"ok": True}
