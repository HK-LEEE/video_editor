"""보이스 캐릭터: 내가 만든 목소리를 이름 붙여 저장해 두고 어디서든 같은 목소리로 바꿉니다.

캐릭터 = (AI 샘플 목소리: 있으면 Seed-VC로 그 사람 음색으로) + (프리셋 변조와 강도: 그 위에 얹는 DSP).
클립의 목소리를 `char:<id>`로 두면 어떻게 말한 소리든 같은 캐릭터 목소리가 됩니다. 정의는 작업 폴더의 characters.json에 저장돼요.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

PREFIX = "char:"
FILE = "characters.json"
_lock = threading.Lock()


class Character(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=40)
    preset: str = "original"                       # 프리셋 이름 ("original" = 변조 없음)
    intensity: float = Field(1.0, ge=0.1, le=5.0)
    ref: str = ""                                  # 샘플 목소리 미디어 id (비우면 AI 변환 없이 프리셋만)
    note: str = Field("", max_length=200)

    def signature(self) -> str:
        """정의가 바뀌면 달라지는 값 (캐시 이름에 씁니다)."""
        import hashlib
        return hashlib.sha1(f"{self.preset}|{self.intensity:.3f}|{self.ref}".encode()).hexdigest()[:10]


def is_char(voice: str) -> bool:
    return voice.startswith(PREFIX)


def char_id(voice: str) -> str:
    return voice[len(PREFIX):]


def _path(root: Path) -> Path:
    return Path(root) / FILE


def load(root: Path) -> list[Character]:
    p = _path(root)
    if not p.exists():
        return []
    try:
        return [Character(**c) for c in json.loads(p.read_text())]
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _save(root: Path, items: list[Character]) -> None:
    p = _path(root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps([c.model_dump() for c in items], ensure_ascii=False, indent=1))
    tmp.replace(p)


def get(root: Path, cid: str) -> Character | None:
    return next((c for c in load(root) if c.id == cid), None)


def create(root: Path, ch: Character) -> Character:
    with _lock:
        items = load(root)
        ch = ch.model_copy(update={"id": uuid.uuid4().hex[:12]})
        _save(root, [*items, ch])
        return ch


def update(root: Path, cid: str, ch: Character) -> Character | None:
    with _lock:
        items = load(root)
        for i, c in enumerate(items):
            if c.id == cid:
                items[i] = ch.model_copy(update={"id": cid})
                _save(root, items)
                return items[i]
    return None


def delete(root: Path, cid: str) -> bool:
    with _lock:
        items = load(root)
        keep = [c for c in items if c.id != cid]
        if len(keep) == len(items):
            return False
        _save(root, keep)
        return True
