"""AI 음성 변환 (Seed-VC).

내 녹음(source)을 샘플 목소리(reference, 약 10초)의 음색으로 바꿉니다.
Seed-VC는 PyTorch가 필요해서 편집기 환경과 분리된 가상환경(.venv-seedvc)에 설치하고,
변환할 때만 하위 프로세스로 실행합니다. 설치: `bash scripts/setup_ai_voice.sh`

환경변수로 위치를 바꿀 수 있습니다: SEEDVC_DIR, SEEDVC_PYTHON
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import contextvars
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_PREFIX = "ai:"
DEFAULT_STEPS = 30
MAX_REF_S = 25.0   # Seed-VC가 쓰는 레퍼런스 최대 길이


def seedvc_dir() -> Path:
    return Path(os.environ.get("SEEDVC_DIR") or ROOT / "third_party" / "seed-vc")


def seedvc_python() -> Path:
    return Path(os.environ.get("SEEDVC_PYTHON") or ROOT / ".venv-seedvc" / "bin" / "python")


def ready_marker() -> Path:
    return seedvc_python().parent.parent / ".seedvc-ready"


def is_installed() -> bool:
    """설치 스크립트가 끝까지 성공해야 만들어지는 표식 파일로 판정합니다 (폴더만 있는 반쯤 설치된 상태 제외)."""
    return (seedvc_dir() / "inference.py").exists() and seedvc_python().exists() and ready_marker().exists()


def is_ai_voice(voice: str) -> bool:
    return voice.startswith(AI_PREFIX)


def ref_id(voice: str) -> str:
    return voice[len(AI_PREFIX):]


def cache_name(mid: str, ref: str, steps: int = DEFAULT_STEPS) -> str:
    return f"{mid}_ai_{ref}_{steps}.wav"


def run_seedvc(source: Path, target: Path, out: Path, steps: int = DEFAULT_STEPS, pitch_shift: int = 0) -> Path:
    """Seed-VC inference.py를 실행해 out(wav)에 결과를 씁니다."""
    if not is_installed():
        raise RuntimeError("AI 음성 변환이 설치되지 않았습니다. `bash scripts/setup_ai_voice.sh`를 실행하세요.")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [str(seedvc_python()), "inference.py",
               "--source", str(source), "--target", str(target), "--output", tmp,
               "--diffusion-steps", str(steps), "--semi-tone-shift", str(pitch_shift),
               "--f0-condition", "True" if pitch_shift else "False", "--fp16", "False"]
        env = {**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
        r = subprocess.run(cmd, cwd=seedvc_dir(), capture_output=True, text=True, env=env)
        results = sorted(Path(tmp).glob("*.wav"))
        if r.returncode != 0 or not results:
            raise RuntimeError(f"AI 변환 실패: {(r.stderr or r.stdout)[-600:]}")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(results[0]), out)
    return out


# ---------------------------------------------------------------- 작업 목록 (한 번에 하나씩)

@dataclass
class Job:
    id: str
    media: str
    ref: str
    status: str = "queued"      # queued | running | done | error
    error: str = ""
    result: dict = field(default_factory=dict)


class JobRunner:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()   # 모델이 크니 동시에 하나만 돌립니다

    def submit(self, media: str, ref: str, work) -> Job:
        job = Job(uuid.uuid4().hex[:12], media, ref)
        self.jobs[job.id] = job

        ctx = contextvars.copy_context()   # 요청 때 묶인 워크스페이스·프로젝트를 일꾼 스레드도 그대로 써요

        def go():
            with self._lock:
                job.status = "running"
                try:
                    res = ctx.run(work)
                    if isinstance(res, dict):
                        job.result = res
                    job.status = "done"
                except Exception as e:  # noqa: BLE001
                    job.status, job.error = "error", str(e)

        threading.Thread(target=go, daemon=True).start()
        return job

    def busy(self) -> int:
        """돌고 있거나 기다리는 작업 수 (프로젝트를 바꾸기 전에 확인합니다)."""
        return sum(1 for j in self.jobs.values() if j.status in ("queued", "running"))

    def active(self, media: str, ref: str) -> Job | None:
        return next((j for j in self.jobs.values()
                     if j.media == media and j.ref == ref and j.status in ("queued", "running")), None)
