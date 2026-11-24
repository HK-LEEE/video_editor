"""효과음·배경 음악 보관함.

기본 소리는 `synth.py`가 직접 합성한 것이라 저작권이 없습니다. 처음 쓸 때 한 번 만들어서 작업 폴더의 보관함(media)에
일반 미디어와 똑같이 등록하고(`lib` = sfx | bgm 표시), 그래서 타임라인에 끌어다 놓는 방식이 파일과 같습니다.
내가 가진 소리는 `add_user`로 올려서 이름·태그·BPM을 붙여 같은 보관함에 둡니다 (내가 올린 것만 지울 수 있어요).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from . import beats as beats_mod
from . import synth
from .editor import SR, Workspace, _check_id, add_media, media_audio, remove_media

KINDS = ("sfx", "bgm")
_lock = threading.Lock()


def lib_id(key: str) -> str:
    return hashlib.md5(f"lib:{synth.VERSION}:{key}".encode()).hexdigest()


def _specs() -> list[dict]:
    out = [{"key": f"sfx_{s.key}", "kind": "sfx", "name": s.name, "tags": s.tags, "mood": "", "bpm": 0, "fn": s.fn} for s in synth.SFX]
    out += [{"key": f"bgm_{b.key}", "kind": "bgm", "name": b.name, "tags": b.tags, "mood": b.mood, "bpm": b.bpm, "fn": b.fn} for b in synth.BGM]
    return out


def ensure_item(ws: Workspace, spec: dict) -> dict:
    mid = lib_id(spec["key"])
    meta_p = ws.global_shared / f"{mid}.json"   # 기본 효과음·음악은 모든 워크스페이스가 같이 쓰는 전역 보관함에 둡니다
    if meta_p.exists():
        return json.loads(meta_p.read_text())
    with _lock:
        if meta_p.exists():
            return json.loads(meta_p.read_text())
        x = spec["fn"]()
        file = f"{mid}.wav"
        sf.write(ws.global_shared / file, x, SR, subtype="PCM_16")
        meta = {"id": mid, "name": spec["name"], "file": file, "recorded": False, "kind": "audio", "duration": round(x.size / SR, 4),
                "has_video": False, "has_audio": True, "width": 0, "height": 0,
                "lib": spec["kind"], "key": spec["key"], "tags": spec["tags"], "mood": spec["mood"], "bpm": spec["bpm"], "user": False}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False))
        return meta


def ensure_all(ws: Workspace, kind: str | None = None) -> list[dict]:
    return [ensure_item(ws, s) for s in _specs() if kind in (None, s["kind"])]


def warm(ws: Workspace) -> threading.Thread:
    """서버를 켜면 뒤에서 기본 소리를 미리 만들어 둡니다 (처음 열 때 기다리지 않게)."""
    t = threading.Thread(target=lambda: ensure_all(ws), daemon=True)
    t.start()
    return t


def catalog(ws: Workspace) -> list[dict]:
    """보관함 목록: 기본 소리(아직 안 만들었으면 ready=False)와 내가 올린 소리."""
    items = []
    for s in _specs():
        mid = lib_id(s["key"])
        p = ws.global_shared / f"{mid}.json"
        meta = json.loads(p.read_text()) if p.exists() else None
        items.append({"id": mid, "key": s["key"], "kind": s["kind"], "name": s["name"], "tags": s["tags"], "mood": s["mood"], "bpm": s["bpm"],
                      "duration": meta["duration"] if meta else None, "ready": meta is not None, "user": False})
    for p in sorted([*ws.global_shared.glob("*.json"), *ws.shared.glob("*.json")]):    # 전역 + 이 워크스페이스에 올린 것
        m = json.loads(p.read_text())
        if m.get("lib") and m.get("user"):
            items.append({"id": m["id"], "key": m.get("key", ""), "kind": m["lib"], "name": m["name"], "tags": m.get("tags", []),
                          "mood": m.get("mood", ""), "bpm": m.get("bpm", 0), "duration": m["duration"], "ready": True, "user": True})
    return items


def add_user(ws: Workspace, src: Path, name: str, kind: str, tags: list[str] | None = None, mood: str = "", bpm: float = 0.0) -> dict:
    """내 소리를 보관함에 올립니다. 음악이고 BPM을 안 적으면 박자를 찾아 채웁니다."""
    if kind not in KINDS:
        raise ValueError("효과음(sfx)이나 배경 음악(bgm)이어야 해요.")
    meta = add_media(ws, src, name, shared=True)
    if not meta["has_audio"]:
        remove_media(ws, meta["id"])
        raise ValueError("소리가 있는 파일만 올릴 수 있어요.")
    if kind == "bgm" and not bpm:
        try:
            bpm = round(beats_mod.detect(media_audio(ws, meta["id"]), SR)["bpm"])
        except Exception:  # noqa: BLE001  박자 찾기가 실패해도 올리기는 됩니다
            bpm = 0
    meta.update({"lib": kind, "tags": [t.strip() for t in (tags or []) if t.strip()], "mood": mood, "bpm": bpm, "user": True,
                 "name": name or meta["name"]})
    ws.meta_path(meta["id"]).write_text(json.dumps(meta, ensure_ascii=False))
    return meta


def remove_user(ws: Workspace, mid: str) -> None:
    _check_id(mid)
    m = ws.meta(mid)
    if not m.get("user") or not m.get("lib"):
        raise ValueError("내가 올린 소리만 지울 수 있어요.")
    remove_media(ws, mid)


def beats_for(ws: Workspace, mid: str, hint_bpm: float | None = None) -> dict:
    """미디어의 박자 (캐시). 음악 보관함 곡은 알려진 BPM을 힌트로 씁니다."""
    meta = ws.meta(mid)
    hint = hint_bpm or (meta.get("bpm") or None)
    cache = ws.root / "cache" / f"{mid}_beats_{int(hint or 0)}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    audio = media_audio(ws, mid)
    if audio.size == 0:
        raise ValueError("소리가 없는 파일이에요.")
    res = beats_mod.detect(audio, SR, hint)
    res["duration"] = round(audio.size / SR, 3)
    cache.write_text(json.dumps(res))
    return res
