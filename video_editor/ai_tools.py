"""AI 편집 도구: 자동 자막, 무음·군말 자르기, 장면 나누기, 노이즈 제거, 대본 읽기(TTS), 번역 더빙.

- 받아쓰기(faster-whisper)와 번역(argos)은 별도 가상환경 `.venv-aitools`의 작업자(ai_worker.py)를 하위 프로세스로 부릅니다.
  설치: `bash scripts/setup_ai_tools.sh`   (환경변수: AITOOLS_PYTHON, AITOOLS_WHISPER)
- 무음 찾기, 장면 나누기, 노이즈 제거(RNNoise)는 설치 없이 ffmpeg와 numpy만으로 돌아갑니다
  (RNNoise 모델 파일이 없으면 ffmpeg의 FFT 노이즈 제거로 대신합니다).
- 읽어 주기(TTS)는 맥의 `say`(한국어 Yuna 등)를 쓰고, 없으면 espeak-ng로 대신합니다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name("ai_worker.py")
RNNOISE_MODEL = "bd.rnnn"
SETUP_CMD = "bash scripts/setup_ai_tools.sh"
LANGS = {"ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文", "es": "Español", "fr": "Français", "de": "Deutsch"}
SAY_VOICES = {"ko": "Yuna", "en": "Samantha", "ja": "Kyoko", "zh": "Tingting", "es": "Monica", "fr": "Thomas", "de": "Anna"}
FILLERS = {
    "ko": ["어", "음", "으", "흠", "엄", "에", "으음", "어어", "음음"],
    "en": ["um", "uh", "umm", "uhh", "er", "erm", "hmm", "ah"],
}
FILLER_PROMPT = {"ko": "음... 어, 그, 어... 저기, 음 그러니까 어...", "en": "Um, uh, so, uh, you know, um..."}


def tools_python() -> Path:
    return Path(os.environ.get("AITOOLS_PYTHON") or ROOT / ".venv-aitools" / "bin" / "python")


def ready_marker() -> Path:
    return tools_python().parent.parent / ".aitools-ready"


def is_installed() -> bool:
    return tools_python().exists() and ready_marker().exists()


def rnnoise_model() -> Path:
    return Path(os.environ.get("RNNOISE_MODEL") or ROOT / "third_party" / "rnnoise" / RNNOISE_MODEL)


def tts_engine() -> str | None:
    if shutil.which("say"):
        return "say"
    if shutil.which("espeak-ng"):
        return "espeak-ng"
    return None


def status() -> dict:
    return {"installed": is_installed(), "setup": SETUP_CMD, "tts": tts_engine(),
            "denoise": "rnnoise" if rnnoise_model().exists() else "fft", "langs": LANGS}


# ---------------------------------------------------------------- 작업자 호출 (받아쓰기·번역)

def run_worker(cmd: str, payload: dict, timeout: float | None = None) -> dict:
    if not is_installed():
        raise RuntimeError(f"AI 도구가 설치되지 않았습니다. 터미널에서 `{SETUP_CMD}`를 실행하세요.")
    env = {**os.environ, "HF_HUB_DISABLE_TELEMETRY": "1", "TOKENIZERS_PARALLELISM": "false"}
    r = subprocess.run([str(tools_python()), "-I", str(WORKER), cmd], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=timeout)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    try:
        res = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        raise RuntimeError(f"AI 작업 실패: {(r.stderr or r.stdout)[-600:]}") from None
    if not res.get("ok"):
        raise RuntimeError(f"AI 작업 실패: {res.get('error', '알 수 없는 오류')}")
    return res


def transcribe(wav: Path, lang: str = "", verbatim: bool = False) -> dict:
    """받아쓰기. lang="" 이면 자동 감지. verbatim이면 '음, 어' 같은 군말도 받아적도록 유도합니다."""
    payload = {"audio": str(wav), "lang": lang or None}
    if verbatim and lang in FILLER_PROMPT:
        payload["prompt"] = FILLER_PROMPT[lang]
    return run_worker("transcribe", payload)


def yunet_model() -> Path:
    return Path(os.environ.get("YUNET_MODEL") or ROOT / "third_party" / "yunet" / "face_detection_yunet_2023mar.onnx")


def detect_subjects(video: Path, start: float, end: float, step: float = 0.5) -> list[dict]:
    """영상의 start~end(초)에서 step초마다 사람 얼굴·몸 위치 [{t, boxes:[[cx,cy,size]]}] (화면 비율)."""
    res = run_worker("track", {"video": str(video), "start": start, "end": end, "step": step, "yunet": str(yunet_model())})
    return res["frames"]


def translate(texts: list[str], src: str, dst: str) -> list[str]:
    return run_worker("translate", {"texts": texts, "src": src, "dst": dst})["texts"]


# ---------------------------------------------------------------- 자막 만들기

def segments_to_cues(segments: list[dict], lang: str, max_chars: int = 28, max_dur: float = 6.0) -> list[dict]:
    """받아쓰기 구간을 읽기 편한 길이로 쪼갭니다(단어 시각 기준). 반환 시각은 미디어 기준 초.
    낱말이 있으면 cue["words"]에 자막 시작 기준(상대) 시각으로 같이 담아 낱말별 강조 자막에 씁니다."""
    cues = []
    for s in segments:
        words = s.get("words") or []
        if not words:
            if s["text"].strip():
                cues.append({"start": s["start"], "end": s["end"], "text": s["text"].strip(), "lang": lang, "words": []})
            continue
        cur: list[dict] = []

        def flush():
            if cur:
                t0 = cur[0]["s"]
                cues.append({"start": t0, "end": cur[-1]["e"], "lang": lang,
                             "text": " ".join(w["w"] for w in cur).strip(),
                             "words": [{"s": round(w["s"] - t0, 3), "e": round(max(w["e"], w["s"]) - t0, 3), "w": w["w"]}
                                       for w in cur]})
                cur.clear()

        for w in words:
            if cur and (sum(len(x["w"]) + 1 for x in cur) + len(w["w"]) > max_chars or w["e"] - cur[0]["s"] > max_dur):
                flush()
            cur.append(w)
            if re.search(r"[.!?。？！…]$", w["w"]):
                flush()
        flush()
    return [c for c in cues if c["text"] and c["end"] > c["start"]]


def segment_words(segments: list[dict]) -> list[list[dict]]:
    """글로 편집용: 받아쓰기 구간마다 낱말 목록 [{s,e,w}] (미디어 기준 초)."""
    out = []
    for s in segments:
        words = [{"s": w["s"], "e": w["e"], "w": w["w"]} for w in s.get("words") or [] if w["w"].strip()]
        if words:
            out.append(words)
        elif s["text"].strip():
            out.append([{"s": s["start"], "e": s["end"], "w": s["text"].strip()}])
    return out


# ---------------------------------------------------------------- 무음·군말

def frame_db(x: np.ndarray, sr: int, hop_s: float = 0.02) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = x.size // hop
    if n == 0:
        return np.zeros(0)
    f = x[: n * hop].reshape(n, hop).astype(np.float64)
    return 20 * np.log10(np.sqrt((f * f).mean(axis=1)) + 1e-9)


def silence_ranges(x: np.ndarray, sr: int, min_silence: float = 0.5, threshold_db: float | None = None,
                   pad: float = 0.08, hop_s: float = 0.02) -> list[dict]:
    """조용한 구간 [{start,end}] (초). 문턱은 지정하지 않으면 말소리 크기(상위 5%)보다 30dB 낮은 값을 씁니다.
    양쪽을 pad만큼 남겨서 말 끝이 잘리지 않게 합니다."""
    db = frame_db(x, sr, hop_s)
    if db.size == 0:
        return []
    thr = threshold_db if threshold_db is not None else float(np.clip(np.percentile(db, 95) - 30, -60, -25))
    quiet = db < thr
    out, i, n = [], 0, quiet.size
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        s = i * hop_s + (pad if i > 0 else 0.0)        # 맨 앞·맨 뒤 무음은 통째로 (여유 없이)
        e = j * hop_s - (pad if j < n else 0.0)
        if j * hop_s - i * hop_s >= min_silence and e - s > 0.05:
            out.append({"start": round(s, 3), "end": round(e, 3), "kind": "silence"})
        i = j
    return out


def find_fillers(segments: list[dict], lang: str, extra: list[str] | None = None, max_len: float = 1.2) -> list[dict]:
    targets = set(FILLERS.get(lang, [])) | set(extra or [])
    out = []
    for s in segments:
        for w in s.get("words", []):
            token = re.sub(r"[^\w]", "", w["w"].lower())
            if token in targets and 0 < w["e"] - w["s"] <= max_len:
                out.append({"start": w["s"], "end": w["e"], "kind": "filler", "text": w["w"]})
    return out


def merge_cuts(cuts: list[dict], gap: float = 0.05) -> list[dict]:
    out: list[dict] = []
    for c in sorted(cuts, key=lambda c: c["start"]):
        if out and c["start"] <= out[-1]["end"] + gap:
            out[-1]["end"] = max(out[-1]["end"], c["end"])
            if c["kind"] not in out[-1]["kind"]:
                out[-1]["kind"] += "+" + c["kind"]
        else:
            out.append(dict(c))
    return out


# ---------------------------------------------------------------- 장면 나누기

def scene_changes(video: Path, threshold: float = 0.3, min_gap: float = 1.0) -> list[float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-an",
                        "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)]
    out: list[float] = []
    for t in sorted(times):
        if t >= 0.2 and (not out or t - out[-1] >= min_gap):
            out.append(round(t, 3))
    return out


# ---------------------------------------------------------------- 노이즈 제거

def denoise(src: Path, out: Path, sr: int = 48_000) -> str:
    """src의 소리를 노이즈 제거해 out(wav)에 씁니다. 쓴 방식('rnnoise' | 'fft')을 돌려줍니다."""
    model = rnnoise_model()
    if model.exists():
        flt, engine, cwd = f"highpass=f=70,arnndn=m={model.name}", "rnnoise", model.parent
    else:
        flt, engine, cwd = "highpass=f=70,afftdn=nr=30:nf=-30", "fft", None
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-af", flt,
                        "-ac", "1", "-ar", str(sr), str(out)], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"노이즈 제거 실패: {r.stderr[-400:]}")
    return engine


# ---------------------------------------------------------------- 읽어 주기 (TTS)

def say_voice(lang: str) -> str:
    return SAY_VOICES.get(lang, "")


def synthesize(text: str, lang: str, out: Path, rate: float = 1.0, voice: str = "", sr: int = 48_000) -> Path:
    """text를 lang 언어로 읽어 out(wav, 모노)에 씁니다. rate는 말 속도 배율."""
    text = text.strip()
    if not text:
        raise RuntimeError("읽을 글이 없어요.")
    engine = tts_engine()
    if engine is None:
        raise RuntimeError("읽어 주는 엔진이 없어요. 맥은 기본 내장(say), 리눅스는 espeak-ng를 설치하세요.")
    tmp = out.with_suffix(".tts" + (".aiff" if engine == "say" else ".wav"))
    if engine == "say":
        v = voice or say_voice(lang)
        cmd = ["say"] + (["-v", v] if v else []) + ["-r", str(int(175 * rate)), "-o", str(tmp), "--", text]
    else:
        cmd = ["espeak-ng", "-v", lang, "-s", str(int(160 * rate)), "-w", str(tmp), text]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.exists():
        hint = " 시스템 설정 → 손쉬운 사용 → 음성 콘텐츠에서 해당 언어 목소리를 추가하세요." if engine == "say" else ""
        raise RuntimeError(f"읽기 실패({lang}): {(r.stderr or r.stdout).strip()[-200:]}{hint}")
    c = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tmp), "-ac", "1", "-ar", str(sr), str(out)],
                       capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if c.returncode != 0:
        raise RuntimeError(f"읽기 변환 실패: {c.stderr[-300:]}")
    return out


