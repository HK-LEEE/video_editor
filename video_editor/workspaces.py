"""작업 폴더(workspace)를 프로젝트별로 나눠 두는 곳.

    workspace/
      workspace.json                 지금 열린 프로젝트 · 최근 목록
      characters.json  templates.json  endings.json  settings.json     ← 모든 프로젝트가 같이 쓰는 것
      library/media/                 효과음·음악 보관함, 템플릿 인트로·아웃트로, 캐릭터 샘플 목소리 (공유 미디어)
      projects/<프로젝트>/
        project.json                 이름 · 만든 때 · 고친 때
        timeline.json                마지막 편집 상태 (타임라인 · 화면 설정)
        media/  cache/  exports/     이 프로젝트의 파일 · AI 결과 · 내보낸 영상
        subtitles/                   내보낼 때 같이 남기는 SRT
        saved/                       '보관한 프로젝트' (템플릿 일괄 적용·일괄 내보내기 대상)
      _trash/                        지운 프로젝트 (폴더째 옮겨 두기만 해요)
      _migration/                    예전 구조에서 옮길 때의 기록 · 메타데이터 백업

AI 모델·가상환경(.venv-*)은 작업 폴더 밖(저장소 폴더)에 있어서 그대로 공유됩니다.

예전(프로젝트 구분 없이 workspace/media, cache, exports, projects/*.json)에서 처음 켜면 `migrate_legacy`가
기존 내용을 '기존 작업' 프로젝트로 옮깁니다. 파일은 같은 폴더 안에서 이름만 바꿔 옮기고(복사·삭제 없음),
옮기기 전에 목록과 메타데이터 백업을 남기며, 옮긴 뒤 개수·크기를 대조합니다. 여러 번 실행해도 안전해요.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import HTTPException

SUBDIRS = ("media", "cache", "exports", "subtitles", "saved")
LEGACY_NAME = "기존 작업"
FRESH_NAME = "기본 프로젝트"
SHARED_FILES = ("characters.json", "templates.json", "endings.json", "settings.json")
_ID_OK = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_\-]{1,48}$")
_lock = threading.RLock()


def _write_json(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def slug(name: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ]+", "-", name.strip()).strip("-")[:30]
    return s or "project"


def check_id(pid: str) -> str:
    if not isinstance(pid, str) or not _ID_OK.match(pid):
        raise HTTPException(400, "잘못된 프로젝트 id")
    return pid


def _dir_stats(p: Path) -> tuple[int, int]:
    n = size = 0
    for f in p.rglob("*"):
        if f.is_file():
            n += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return n, size


class Projects:
    """프로젝트 목록과 '지금 열린 프로젝트'."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.base = self.home / "projects"
        self.reg = self.home / "workspace.json"
        self.base.mkdir(parents=True, exist_ok=True)
        with _lock:
            migrate_legacy(self.home)
            if not self.list():
                self._create(FRESH_NAME, origin="fresh")
            if self.active not in {p["id"] for p in self.list()}:
                self._set_active(self.list()[0]["id"])

    # ---- 파일 위치
    def dir(self, pid: str) -> Path:
        return self.base / check_id(pid)

    def exists(self, pid: str) -> bool:
        try:
            return (self.dir(pid) / "project.json").exists()
        except HTTPException:
            return False

    # ---- 읽기
    def _reg(self) -> dict:
        d = _read_json(self.reg, {})
        return d if isinstance(d, dict) else {}

    @property
    def active(self) -> str:
        return self._reg().get("active", "")

    def _set_active(self, pid: str) -> None:
        d = self._reg()
        recent = [x for x in d.get("recent", []) if x != pid and self.exists(x)]
        _write_json(self.reg, {"version": 2, "active": pid, "recent": [pid, *recent][:12]})

    def info(self, pid: str) -> dict:
        d = _read_json(self.dir(pid) / "project.json", None)
        if not isinstance(d, dict):
            raise HTTPException(404, "프로젝트를 찾을 수 없어요.")
        return d

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.base.iterdir()) if self.base.exists() else []:
            d = _read_json(p / "project.json", None) if p.is_dir() else None
            if isinstance(d, dict) and d.get("id") == p.name:
                out.append(d)
        return out

    def listing(self) -> dict:
        recent = self._reg().get("recent", [])
        rank = {pid: i for i, pid in enumerate(recent)}
        items = []
        for d in self.list():
            pd = self.dir(d["id"])
            media = [m for m in (pd / "media").glob("*.json")
                     if not (_read_json(m, {}).get("hidden"))]
            items.append({**d, "media": len(media), "saved": len(list((pd / "saved").glob("*.json"))),
                          "active": d["id"] == self.active, "recent": rank.get(d["id"], 99)})
        items.sort(key=lambda x: (x["recent"], -x.get("updated", 0)))
        return {"active": self.active, "projects": items}

    # ---- 쓰기
    def _unique_id(self, name: str) -> str:
        base = slug(name)
        pid, n = base, 2
        while (self.base / pid).exists():
            pid = f"{base}-{n}"
            n += 1
        return pid

    def _create(self, name: str, origin: str = "created") -> dict:
        name = name.strip()[:60] or "새 프로젝트"
        pid = self._unique_id(name)
        d = self.base / pid
        for s in SUBDIRS:
            (d / s).mkdir(parents=True, exist_ok=True)
        now = time.time()
        info = {"id": pid, "name": name, "created": now, "updated": now, "origin": origin, "version": 1}
        _write_json(d / "project.json", info)
        if not self._reg().get("active"):
            self._set_active(pid)
        return info

    def create(self, name: str, open_: bool = True) -> dict:
        with _lock:
            info = self._create(name)
            if open_:
                self._set_active(info["id"])
            return info

    def open(self, pid: str) -> dict:
        with _lock:
            info = self.info(pid)
            for s in SUBDIRS:
                (self.dir(pid) / s).mkdir(parents=True, exist_ok=True)
            self._set_active(pid)
            return info

    def touch(self, pid: str) -> None:
        with _lock:
            try:
                info = self.info(pid)
            except HTTPException:
                return
            info["updated"] = time.time()
            _write_json(self.dir(pid) / "project.json", info)

    def rename(self, pid: str, name: str) -> dict:
        name = name.strip()[:60]
        if not name:
            raise HTTPException(400, "이름을 적어 주세요.")
        with _lock:
            info = self.info(pid)
            info["name"] = name
            info["updated"] = time.time()
            _write_json(self.dir(pid) / "project.json", info)
            return info

    def duplicate(self, pid: str, name: str = "") -> dict:
        """파일은 하드링크로 이어 붙여(같은 디스크 안에서 용량을 거의 안 늡니다), 못 하면 복사해요. 내보낸 영상(exports)은 빼요."""
        with _lock:
            src = self.dir(pid)
            old = self.info(pid)
            new = self._create(name.strip() or f"{old['name']} 복사본", origin="duplicated")
            dst = self.dir(new["id"])

            def cp(s, d):
                if str(s).endswith(".json"):
                    shutil.copy2(s, d)            # 나중에 덮어써 쓰는 파일은 따로 복사 (하드링크면 원본도 바뀌어요)
                else:
                    try:
                        os.link(s, d)
                    except OSError:
                        shutil.copy2(s, d)

            for sub in ("media", "cache", "subtitles", "saved"):
                if (src / sub).exists():
                    shutil.copytree(src / sub, dst / sub, copy_function=cp, dirs_exist_ok=True)
            if (src / "timeline.json").exists():
                shutil.copy2(src / "timeline.json", dst / "timeline.json")
            return new

    def delete(self, pid: str, confirm: str) -> dict:
        """폴더째 _trash로 옮깁니다 (완전히 지우지 않아요). 이름을 똑같이 적어야 지워져요."""
        with _lock:
            info = self.info(pid)
            if confirm.strip() != info["name"]:
                raise HTTPException(400, "확인용 이름이 달라요. 프로젝트 이름을 그대로 적어 주세요.")
            rest = [p for p in self.list() if p["id"] != pid]
            if not rest:
                raise HTTPException(400, "마지막 프로젝트는 지울 수 없어요. 먼저 새 프로젝트를 만들어 주세요.")
            trash = self.home / "_trash"
            trash.mkdir(exist_ok=True)
            dest = trash / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
            self.dir(pid).rename(dest)
            if self.active == pid:
                recent = [x for x in self._reg().get("recent", []) if x != pid and self.exists(x)]
                self._set_active(recent[0] if recent else rest[0]["id"])
            return {"ok": True, "trash": str(dest)}

    # ---- 프로젝트 파일(편집 상태)
    def load_timeline(self, pid: str) -> dict | None:
        d = _read_json(self.dir(pid) / "timeline.json", None)
        return d if isinstance(d, dict) else None

    def save_timeline(self, pid: str, data: dict) -> None:
        with _lock:
            _write_json(self.dir(pid) / "timeline.json", data)
        self.touch(pid)


# ================================================================ 예전 구조에서 옮기기

def _is_legacy(home: Path) -> bool:
    if any((home / d).is_dir() for d in ("media", "cache", "exports")):
        return True
    p = home / "projects"
    return p.is_dir() and any(f.is_file() for f in p.glob("*.json"))


def _move(src: Path, dst: Path, log: list, leftovers: Path) -> None:
    """src를 dst로 옮깁니다 (같은 디스크 안의 이름 바꾸기). dst에 이미 있으면 덮지 않고 leftovers로 비켜 둡니다."""
    if not src.exists():
        return
    if src.is_dir() and dst.is_dir():
        for c in sorted(src.iterdir()):
            _move(c, dst / c.name, log, leftovers)
        try:
            src.rmdir()
        except OSError:
            pass
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        leftovers.mkdir(parents=True, exist_ok=True)
        dst = leftovers / f"{int(time.time() * 1000)}-{src.name}"
    size = src.stat().st_size if src.is_file() else None
    src.rename(dst)
    log.append({"from": str(src), "to": str(dst), "size": size})


def migrate_legacy(home: Path) -> dict | None:
    """예전 구조(workspace/media·cache·exports·projects/*.json)를 '기존 작업' 프로젝트로 옮깁니다. 할 일이 없으면 None."""
    home = Path(home)
    if not _is_legacy(home):
        return None
    with _lock:
        mig = home / "_migration"
        mig.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        projects = home / "projects"
        projects.mkdir(exist_ok=True)

        # 1) 옮길 프로젝트 폴더 (이미 만들다 만 게 있으면 그걸 이어서 씁니다)
        pr = Projects.__new__(Projects)
        pr.home, pr.base, pr.reg = home, projects, home / "workspace.json"
        target = next((p for p in pr.list() if p.get("origin") == "migrated"), None) or pr._create(LEGACY_NAME, origin="migrated")
        pid = target["id"]
        pdir = projects / pid
        shared = home / "library" / "media"
        shared.mkdir(parents=True, exist_ok=True)

        # 2) 백업: 옮기기 전 목록과 메타데이터(json)만 따로 복사해 둡니다 (큰 영상·소리 파일은 이름만 바꿔 옮기니 그대로예요)
        bak = mig / f"backup-{stamp}"
        before = {}
        for d in ("media", "cache", "exports"):
            if (home / d).is_dir():
                n, size = _dir_stats(home / d)
                before[d] = {"files": n, "bytes": size}
        loose = [f for f in projects.glob("*.json") if f.is_file()]
        before["projects_json"] = {"files": len(loose), "bytes": sum(f.stat().st_size for f in loose)}
        if not any(mig.glob("backup-*")):
            bak.mkdir()
            for f in (home / "media").glob("*.json") if (home / "media").is_dir() else []:
                (bak / "media").mkdir(exist_ok=True)
                shutil.copy2(f, bak / "media" / f.name)
            for f in loose:
                (bak / "projects").mkdir(exist_ok=True)
                shutil.copy2(f, bak / "projects" / f.name)
            for name in SHARED_FILES:
                if (home / name).exists():
                    shutil.copy2(home / name, bak / name)
            _write_json(bak / "before.json", before)

        log: list = []
        leftovers = pdir / "legacy-leftovers"

        # 3) 미디어: 효과음·음악 보관함 항목은 공유 보관함으로, 나머지는 이 프로젝트로
        mdir = home / "media"
        if mdir.is_dir():
            lib_files = set()
            for f in mdir.glob("*.json"):
                m = _read_json(f, {})
                if isinstance(m, dict) and m.get("lib"):
                    lib_files.update({f.name, m.get("file", "")})
            for f in sorted(mdir.iterdir()):
                dest = shared if f.name in lib_files else pdir / "media"
                _move(f, dest / f.name, log, leftovers)
            try:
                mdir.rmdir()
            except OSError:
                _move(mdir, pdir / "media", log, leftovers)
        # 4) 캐시 · 내보낸 영상
        for d in ("cache", "exports"):
            if (home / d).exists():
                _move(home / d, pdir / d, log, leftovers)
        # 5) 보관한 프로젝트(예전엔 projects/<id>.json)
        for f in loose:
            _move(f, pdir / "saved" / f.name, log, leftovers)
        for s in SUBDIRS:
            (pdir / s).mkdir(exist_ok=True)

        # 6) 공유로 써야 하는 미디어(템플릿 인트로·아웃트로, 캐릭터 샘플 목소리)는 공유 보관함에도 복사
        want = set()
        for name, keys in (("templates.json", ("intro", "outro")), ("characters.json", ("ref",))):
            for item in _read_json(home / name, []) if isinstance(_read_json(home / name, []), list) else []:
                for k in keys:
                    if isinstance(item, dict) and item.get(k):
                        want.add(str(item[k]))
        promoted = [mid for mid in sorted(want) if promote_media(pdir / "media", shared, mid)]

        # 7) 대조: 옮긴 뒤 개수·크기가 옮기기 전과 같은지
        after_n = after_b = 0
        for d in (pdir / "media", shared, pdir / "cache", pdir / "exports", pdir / "saved", leftovers):
            if d.exists():
                n, b = _dir_stats(d)
                after_n += n
                after_b += b
        # 승격 복사본은 중복이라 빼고 비교합니다
        dup_n = dup_b = 0
        for mid in promoted:
            for f in shared.glob(f"{mid}*"):
                dup_n += 1
                dup_b += f.stat().st_size
        want_n = sum(before[k]["files"] for k in before)
        want_b = sum(before[k]["bytes"] for k in before)
        ok = (after_n - dup_n >= want_n) and (after_b - dup_b >= want_b)
        manifest = {"time": stamp, "project": pid, "before": before, "after": {"files": after_n - dup_n, "bytes": after_b - dup_b},
                    "verified": ok, "promoted_to_shared": promoted, "moved": log}
        _write_json(mig / f"manifest-{stamp}.json", manifest)
        pr._set_active(pid)
        if not ok:
            raise RuntimeError(f"옮긴 뒤 파일 수·크기가 맞지 않아요. 기록: {mig}")
        return manifest


def promote_media(src_media: Path, shared: Path, mid: str) -> bool:
    """프로젝트 미디어 하나를 공유 보관함에도 복사합니다 (원본은 그대로). 이미 있거나 원본이 없으면 False."""
    meta_p = Path(src_media) / f"{mid}.json"
    dst_meta = Path(shared) / f"{mid}.json"
    if dst_meta.exists():
        return False
    m = _read_json(meta_p, None)
    if not isinstance(m, dict) or not (Path(src_media) / m.get("file", "")).is_file():
        return False
    Path(shared).mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(src_media) / m["file"], Path(shared) / m["file"])
    _write_json(dst_meta, {**m, "shared": True})
    return True
