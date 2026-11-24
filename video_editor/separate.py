"""보컬 분리: 영상·녹음에서 목소리와 배경음(음악·효과음)을 나눕니다 (Demucs, 내 컴퓨터에서 실행).

Demucs는 PyTorch가 커서 전용 가상환경 `.venv-demucs`에 설치하고 하위 프로세스로 돌립니다.
설치: `bash scripts/setup_ai_studio.sh`   (환경변수: DEMUCS_PYTHON)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name("demucs_worker.py")
SETUP_CMD = "bash scripts/setup_ai_studio.sh"


def demucs_python() -> Path:
    return Path(os.environ.get("DEMUCS_PYTHON") or ROOT / ".venv-demucs" / "bin" / "python")


def is_installed() -> bool:
    return demucs_python().exists() and (demucs_python().parent.parent / ".demucs-ready").exists()


def separate(src: Path, vocals: Path, background: Path, timeout: float | None = None) -> None:
    """src(소리가 있는 아무 파일)를 vocals·background(wav)로 나눕니다."""
    if not is_installed():
        raise RuntimeError(f"보컬 분리가 설치되지 않았습니다. 터미널에서 `{SETUP_CMD}`를 실행하세요.")
    env = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    r = subprocess.run([str(demucs_python()), "-I", str(WORKER)],
                       input=json.dumps({"src": str(src), "vocals": str(vocals), "background": str(background)}),
                       capture_output=True, text=True, env=env, timeout=timeout)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    try:
        res = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        raise RuntimeError(f"보컬 분리 실패: {(r.stderr or r.stdout)[-600:]}") from None
    if not res.get("ok"):
        raise RuntimeError(f"보컬 분리 실패: {res.get('error', '알 수 없는 오류')}")
