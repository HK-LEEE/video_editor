"""작업 폴더(workspace)를 '워크스페이스 > 프로젝트'로 나눠 두는 곳. (워크스페이스 = 채널·클라이언트·시리즈 같은 큰 묶음)

    workspace/
      workspace.json                 지금 열린 워크스페이스 · 최근 목록
      characters.json  settings.json 전역: 보이스 캐릭터(목소리)·설정. AI 모델·가상환경(.venv-*)은 작업 폴더 밖이라 원래 전역
      templates.json  endings.json   전역 공용 템플릿·엔딩 프리셋 (모든 워크스페이스에 같이 보여요)
      library/media/                 전역 공용 미디어: 기본 효과음·음악, 전역 템플릿 인트로·아웃트로, 캐릭터 샘플 목소리
      spaces/<워크스페이스>/
        space.json                   이름 · 만든 때 · 열린 프로젝트 · 최근 목록
        templates.json endings.json  이 워크스페이스 전용 템플릿·엔딩 프리셋(워터마크 포함) — 채널별 브랜딩
        library/media/               이 워크스페이스 전용 미디어: 내가 올린 효과음·음악, 전용 템플릿의 인트로·아웃트로
        projects/<프로젝트>/
          project.json  timeline.json  media/  cache/  exports/  subtitles/  saved/
      _trash/                        지운 워크스페이스 (폴더째 옮겨 두기만 해요). 지운 프로젝트는 spaces/<워크스페이스>/_trash/
      _migration/                    예전 구조에서 옮길 때의 기록 · 메타데이터 백업

옛 구조에서는 처음 켤 때 자동으로 옮깁니다 (둘 다 파일을 복사·삭제하지 않고 같은 디스크 안에서 이름만 바꿔 옮기며,
백업·목록·개수/크기 대조 기록을 남기고, 중간에 멈춰도 이어서 끝나며, 여러 번 실행해도 안전해요):
  1) 프로젝트 구분 없던 구조(workspace/media·cache·exports·projects/*.json) → '기존 작업' 프로젝트
  2) workspace/projects/<프로젝트> → '기본 워크스페이스'의 projects/
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
DEFAULT_SPACE = "기본 워크스페이스"
SHARED_FILES = ("characters.json", "templates.json", "endings.json", "settings.json")
_ID_OK = re.compile(r"^[0-9A-Za-z가-힣ㄱ-ㅎㅏ-ㅣ_\-]{1,48}$")
_lock = threading.RLock()


HISTORY_DIR = "timeline_history"
HISTORY_KEEP = 20      # 타임라인 이전 버전을 몇 개까지 둘지
HISTORY_GAP = 90       # 초 — 이 간격보다 촘촘하게는 스냅샷을 새로 만들지 않음 (클립이 확 줄 때는 예외)
_HIST_RE = re.compile(r"^(\d{15})_(\d+)\.json$")


def _clip_count(d) -> int:
    c = (d.get("project") or {}).get("clips") if isinstance(d, dict) and isinstance(d.get("project"), dict) else None
    return len(c) if isinstance(c, list) else 0


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
    """한 워크스페이스 안의 프로젝트 목록과 '지금 열린 프로젝트' (space.json에 기억해요)."""

    def __init__(self, space_dir: Path):
        self.home = Path(space_dir)
        self.base = self.home / "projects"
        self.reg = self.home / "space.json"
        self.base.mkdir(parents=True, exist_ok=True)
        with _lock:
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
        _write_json(self.reg, {**d, "active": pid, "recent": [pid, *recent][:12]})

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

    def stats(self, pid: str) -> dict:
        """표에 보여 줄 값: 타임라인 길이·클립 수(timeline.json), 미디어 수, 폴더 용량."""
        pd = self.dir(pid)
        tl = _read_json(pd / "timeline.json", {})
        clips = (tl.get("project") or {}).get("clips") or [] if isinstance(tl, dict) else []
        length = 0.0
        for c in clips:
            try:
                length = max(length, float(c.get("start", 0)) + (float(c["src_out"]) - float(c["src_in"])) / (float(c.get("speed") or 1) or 1))
            except (KeyError, TypeError, ValueError):
                pass
        media = [m for m in (pd / "media").glob("*.json") if not _read_json(m, {}).get("hidden")]
        return {"length": round(length, 2), "clips": len(clips), "media": len(media), "saved": len(list((pd / "saved").glob("*.json"))),
                "bytes": _dir_stats(pd)[1]}

    def listing(self, detail: bool = False) -> dict:
        recent = self._reg().get("recent", [])
        rank = {pid: i for i, pid in enumerate(recent)}
        items = []
        for d in self.list():
            pd = self.dir(d["id"])
            if detail:
                extra = self.stats(d["id"])
            else:
                media = [m for m in (pd / "media").glob("*.json") if not (_read_json(m, {}).get("hidden"))]
                extra = {"media": len(media), "saved": len(list((pd / "saved").glob("*.json")))}
            items.append({**d, **extra, "active": d["id"] == self.active, "recent": rank.get(d["id"], 99)})
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

    def save_timeline(self, pid: str, data: dict, allow_empty: bool = False) -> None:
        """편집 상태를 저장합니다. 클립이 있던 파일을 클립 0개로 덮어쓰는 건 사용자가 확인했을 때(allow_empty)만 받아요.
        덮어쓰기 직전의 이전 내용은 timeline_history/ 에 최근 HISTORY_KEEP개까지 돌려가며 남겨 둡니다."""
        path = self.dir(pid) / "timeline.json"
        with _lock:
            old = _read_json(path, None) if path.exists() else None
            old_n, new_n = _clip_count(old), _clip_count(data)
            if old_n > 0 and new_n == 0 and not allow_empty:
                raise HTTPException(409, f"클립 {old_n}개가 있던 프로젝트를 빈 타임라인으로 저장하려고 해서 막았어요. 의도한 게 아니라면 '이전 버전'에서 되돌릴 수 있어요.")
            if isinstance(old, dict):
                self._snapshot(pid, old, old_n, new_n)
            _write_json(path, data)
        self.touch(pid)

    # ---- 타임라인 이전 버전 (자동 스냅샷)
    def _hist_dir(self, pid: str) -> Path:
        return self.dir(pid) / HISTORY_DIR

    def _snapshot(self, pid: str, old: dict, old_n: int, new_n: int, force: bool = False) -> None:
        d = self._hist_dir(pid)
        files = sorted(d.glob("*.json")) if d.exists() else []
        last = files[-1] if files else None
        shrink = old_n > 0 and (new_n == 0 or new_n <= old_n * 0.5)   # 클립이 확 줄어드는 저장 직전은 꼭 남김
        if not (force or shrink or last is None or time.time() - last.stat().st_mtime >= HISTORY_GAP):
            return
        if last is not None and _read_json(last, None) == old:
            return
        d.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000):015d}_{old_n}.json"
        _write_json(d / name, old)
        for f in sorted(d.glob("*.json"))[:-HISTORY_KEEP]:
            f.unlink(missing_ok=True)

    def timeline_history(self, pid: str) -> list[dict]:
        d = self._hist_dir(pid); out = []
        for f in sorted(d.glob("*.json"), reverse=True) if d.exists() else []:
            m = _HIST_RE.match(f.name)
            if not m:
                continue
            data = _read_json(f, {})
            clips = (data.get("project") or {}).get("clips") if isinstance(data, dict) else None
            length = max([(c.get("start", 0) or 0) + ((c.get("src_out", 0) or 0) - (c.get("src_in", 0) or 0)) / (c.get("speed") or 1)
                          for c in clips if isinstance(c, dict)] or [0]) if isinstance(clips, list) else 0
            out.append({"name": f.name, "ts": int(m.group(1)) / 1000, "clips": int(m.group(2)), "length": round(length, 2), "bytes": f.stat().st_size})
        return out

    def timeline_version(self, pid: str, name: str) -> dict:
        if not _HIST_RE.match(name):
            raise HTTPException(400, "버전 이름이 올바르지 않아요.")
        d = _read_json(self._hist_dir(pid) / name, None)
        if not isinstance(d, dict):
            raise HTTPException(404, "그 버전을 찾을 수 없어요.")
        return d

    def restore_timeline(self, pid: str, name: str) -> dict:
        """고른 이전 버전으로 되돌려요. 지금 내용은 먼저 스냅샷으로 남겨서 되돌리기를 되돌릴 수도 있어요."""
        snap = self.timeline_version(pid, name)
        path = self.dir(pid) / "timeline.json"
        with _lock:
            cur = _read_json(path, None) if path.exists() else None
            if isinstance(cur, dict) and cur != snap:
                self._snapshot(pid, cur, _clip_count(cur), _clip_count(snap), force=True)
            _write_json(path, snap)
        self.touch(pid)
        return snap


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
        def hack(base: Path, reg: Path) -> Projects:
            x = Projects.__new__(Projects)
            x.home, x.base, x.reg = base.parent, base, reg
            return x

        # 이미 워크스페이스 구조로 옮겨진 뒤에 예전 파일이 다시 생겼다면(옛 버전을 잠깐 썼을 때 등) 그 '기존 작업'에 합칩니다
        pr = target = None
        for base, reg in [(projects, home / "workspace.json"), *[(d / "projects", d / "space.json") for d in sorted((home / "spaces").glob("*"))
                                                                 if (home / "spaces").is_dir()]]:
            cand = hack(base, reg)
            target = next((p for p in cand.list() if p.get("origin") == "migrated"), None) if base.is_dir() else None
            if target:
                pr = cand
                break
        if pr is None:
            projects.mkdir(exist_ok=True)
            pr = hack(projects, home / "workspace.json")
            target = pr._create(LEGACY_NAME, origin="migrated")
        pid = target["id"]
        pdir = pr.base / pid
        shared = home / "library" / "media"
        shared.mkdir(parents=True, exist_ok=True)

        # 2) 백업: 옮기기 전 목록과 메타데이터(json)만 따로 복사해 둡니다 (큰 영상·소리 파일은 이름만 바꿔 옮기니 그대로예요)
        bak = mig / f"backup-{stamp}"
        before = {}
        for d in ("media", "cache", "exports"):
            if (home / d).is_dir():
                n, size = _dir_stats(home / d)
                before[d] = {"files": n, "bytes": size}
        loose = [f for f in projects.glob("*.json") if f.is_file()] if projects.is_dir() else []
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


# ================================================================ 워크스페이스 (프로젝트 위의 묶음)

class Spaces:
    """워크스페이스 목록과 '지금 열린 워크스페이스'. 각 워크스페이스 안에 Projects가 있어요."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.base = self.home / "spaces"
        self.reg = self.home / "workspace.json"
        self._projects: dict[str, Projects] = {}
        self.base.mkdir(parents=True, exist_ok=True)
        with _lock:
            migrate_legacy(self.home)
            migrate_spaces(self.home)
            if not self.list():
                self._create(DEFAULT_SPACE, origin="fresh")
            if self.active not in {p["id"] for p in self.list()}:
                self._set_active(self.list()[0]["id"])

    # ---- 위치
    def dir(self, sid: str) -> Path:
        return self.base / check_id(sid)

    def exists(self, sid: str) -> bool:
        try:
            return (self.dir(sid) / "space.json").exists()
        except HTTPException:
            return False

    def projects(self, sid: str) -> Projects:
        if sid not in self._projects:
            if not self.exists(sid):
                raise HTTPException(404, "워크스페이스를 찾을 수 없어요.")
            self._projects[sid] = Projects(self.dir(sid))
        return self._projects[sid]

    # ---- 읽기
    def _reg(self) -> dict:
        d = _read_json(self.reg, {})
        return d if isinstance(d, dict) else {}

    @property
    def active(self) -> str:
        return self._reg().get("active_space", "")

    def _set_active(self, sid: str) -> None:
        d = self._reg()
        recent = [x for x in d.get("recent", []) if x != sid and self.exists(x)]
        _write_json(self.reg, {"version": 3, "active_space": sid, "recent": [sid, *recent][:12]})

    def info(self, sid: str) -> dict:
        d = _read_json(self.dir(sid) / "space.json", None)
        if not isinstance(d, dict):
            raise HTTPException(404, "워크스페이스를 찾을 수 없어요.")
        return {k: d[k] for k in ("id", "name", "created", "updated", "origin", "version") if k in d}

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.base.iterdir()) if self.base.exists() else []:
            d = _read_json(p / "space.json", None) if p.is_dir() else None
            if isinstance(d, dict) and d.get("id") == p.name:
                out.append(self.info(p.name))
        return out

    def listing(self) -> list[dict]:
        rank = {s: i for i, s in enumerate(self._reg().get("recent", []))}
        items = []
        for d in self.list():
            n = len([p for p in (self.dir(d["id"]) / "projects").iterdir() if (p / "project.json").exists()]) if (self.dir(d["id"]) / "projects").exists() else 0
            items.append({**d, "projects": n, "active": d["id"] == self.active, "recent": rank.get(d["id"], 99)})
        items.sort(key=lambda x: (x["recent"], -x.get("updated", 0)))
        return items

    # ---- 쓰기
    def _unique_id(self, name: str) -> str:
        base = slug(name)
        sid, n = base, 2
        while (self.base / sid).exists():
            sid = f"{base}-{n}"
            n += 1
        return sid

    def _create(self, name: str, origin: str = "created") -> dict:
        name = name.strip()[:60] or "새 워크스페이스"
        sid = self._unique_id(name)
        d = self.base / sid
        (d / "library" / "media").mkdir(parents=True, exist_ok=True)
        now = time.time()
        _write_json(d / "space.json", {"id": sid, "name": name, "created": now, "updated": now, "origin": origin, "version": 1})
        Projects(d)                       # 첫 프로젝트 하나를 같이 만들어 둬요
        if not self._reg().get("active_space"):
            self._set_active(sid)
        return self.info(sid)

    def create(self, name: str, open_: bool = True) -> dict:
        with _lock:
            info = self._create(name)
            if open_:
                self._set_active(info["id"])
            return info

    def open(self, sid: str) -> dict:
        with _lock:
            info = self.info(sid)
            self.projects(sid)
            self._set_active(sid)
            return info

    def rename(self, sid: str, name: str) -> dict:
        name = name.strip()[:60]
        if not name:
            raise HTTPException(400, "이름을 적어 주세요.")
        with _lock:
            d = _read_json(self.dir(sid) / "space.json", None)
            if not isinstance(d, dict):
                raise HTTPException(404, "워크스페이스를 찾을 수 없어요.")
            d.update(name=name, updated=time.time())
            _write_json(self.dir(sid) / "space.json", d)
            return self.info(sid)

    def delete(self, sid: str, confirm: str) -> dict:
        """폴더째 _trash로 옮깁니다 (안의 프로젝트도 모두). 이름을 똑같이 적어야 하고, 마지막 하나는 못 지워요."""
        with _lock:
            info = self.info(sid)
            if confirm.strip() != info["name"]:
                raise HTTPException(400, "확인용 이름이 달라요. 워크스페이스 이름을 그대로 적어 주세요.")
            rest = [s for s in self.list() if s["id"] != sid]
            if not rest:
                raise HTTPException(400, "마지막 워크스페이스는 지울 수 없어요. 먼저 새 워크스페이스를 만들어 주세요.")
            trash = self.home / "_trash"
            trash.mkdir(exist_ok=True)
            dest = trash / f"space-{sid}-{time.strftime('%Y%m%d-%H%M%S')}"
            self.dir(sid).rename(dest)
            self._projects.pop(sid, None)
            if self.active == sid:
                recent = [x for x in self._reg().get("recent", []) if x != sid and self.exists(x)]
                self._set_active(recent[0] if recent else rest[0]["id"])
            return {"ok": True, "trash": str(dest)}

    def move_project(self, pid: str, src: str, dst: str) -> dict:
        """프로젝트를 다른 워크스페이스로 옮깁니다 (폴더 이름 바꾸기). 그 프로젝트가 쓰는 '출발 워크스페이스 전용 미디어'
        (템플릿 인트로·아웃트로 등)는 도착 워크스페이스 보관함에 복사해 둬서 편집 상태·보관본이 그대로 열려요."""
        if src == dst:
            raise HTTPException(400, "같은 워크스페이스예요.")
        with _lock:
            sp, dp = self.projects(src), self.projects(dst)
            info = sp.info(pid)
            if len(sp.list()) <= 1:
                # 마지막 프로젝트를 옮기면 출발 워크스페이스가 비니, 빈 프로젝트를 하나 새로 만들어 둡니다
                sp._create(FRESH_NAME, origin="fresh")
            pdir = sp.dir(pid)
            # 이 프로젝트가 가리키는 미디어 id 중 출발 워크스페이스 보관함에 있는 것
            ids = set()
            for f in [pdir / "timeline.json", *(pdir / "saved").glob("*.json")]:
                try:
                    ids.update(re.findall(r"[0-9a-f]{32}", f.read_text(encoding="utf-8")))
                except OSError:
                    pass
            src_lib, dst_lib = self.dir(src) / "library" / "media", self.dir(dst) / "library" / "media"
            copied = [m for m in sorted(ids) if promote_media(src_lib, dst_lib, m)]
            new_id = dp._unique_id(info["name"])
            dest = dp.base / new_id
            pdir.rename(dest)
            info = _read_json(dest / "project.json", info)
            info.update(id=new_id, updated=time.time())
            _write_json(dest / "project.json", info)
            for st in (sp, dp):
                st._set_active(st.active if st.exists(st.active) else st.list()[0]["id"])
            dp._set_active(new_id)
            return {**info, "space": dst, "copied_media": copied}


def migrate_spaces(home: Path) -> dict | None:
    """예전 구조(workspace/projects/<프로젝트>/)를 '기본 워크스페이스'의 projects/로 옮깁니다. 할 일이 없으면 None."""
    home = Path(home)
    old = home / "projects"
    todo = [p for p in sorted(old.iterdir()) if (p / "project.json").exists()] if old.is_dir() else []
    if not todo and not (old.is_dir() and not any(old.iterdir())):
        return None
    with _lock:
        mig = home / "_migration"
        mig.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        spaces = home / "spaces"
        spaces.mkdir(exist_ok=True)
        # 옮길 워크스페이스 (만들다 만 게 있으면 이어서 씁니다)
        sp = Spaces.__new__(Spaces)
        sp.home, sp.base, sp.reg, sp._projects = home, spaces, home / "workspace.json", {}
        target = next((s for s in sp.list() if s.get("origin") == "migrated"), None)
        old_reg = _read_json(home / "workspace.json", {})
        if target is None:
            sid = sp._unique_id(DEFAULT_SPACE)
            d = spaces / sid
            (d / "library" / "media").mkdir(parents=True, exist_ok=True)
            now = time.time()
            _write_json(d / "space.json", {"id": sid, "name": DEFAULT_SPACE, "created": now, "updated": now, "origin": "migrated", "version": 1,
                                           "active": old_reg.get("active", ""), "recent": old_reg.get("recent", [])})
            target = sp.info(sid)
        sid = target["id"]
        sdir = spaces / sid
        # 백업: 프로젝트 메타데이터와 옛 workspace.json
        bak = mig / f"backup-spaces-{stamp}"
        if not any(mig.glob("backup-spaces-*")):
            bak.mkdir()
            for p in todo:
                (bak / "projects" / p.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(p / "project.json", bak / "projects" / p.name / "project.json")
            if (home / "workspace.json").exists():
                shutil.copy2(home / "workspace.json", bak / "workspace.json")
        before_n, before_b = _dir_stats(old) if old.is_dir() else (0, 0)
        log: list = []
        for p in todo:
            _move(p, sdir / "projects" / p.name, log, sdir / "projects" / "legacy-leftovers")
        if old.is_dir():
            try:
                old.rmdir()
            except OSError:
                _move(old, sdir / "projects" / "legacy-leftovers", log, sdir / "projects" / "legacy-leftovers")
        after_n, after_b = _dir_stats(sdir / "projects")
        ok = after_n >= before_n and after_b >= before_b
        _write_json(mig / f"manifest-spaces-{stamp}.json", {"time": stamp, "space": sid, "projects": [p.name for p in todo],
                    "before": {"files": before_n, "bytes": before_b}, "after": {"files": after_n, "bytes": after_b}, "verified": ok, "moved": log})
        if not ok:
            raise RuntimeError(f"옮긴 뒤 파일 수·크기가 맞지 않아요. 기록: {mig}")
        # 열린 프로젝트 기억을 워크스페이스로 옮기고, workspace.json을 새 모양으로
        reg = _read_json(sdir / "space.json", {})
        reg.setdefault("active", old_reg.get("active", ""))
        reg.setdefault("recent", old_reg.get("recent", []))
        _write_json(sdir / "space.json", reg)
        _write_json(home / "workspace.json", {"version": 3, "active_space": sid, "recent": [sid]})
        return {"space": sid, "projects": [p.name for p in todo]}
