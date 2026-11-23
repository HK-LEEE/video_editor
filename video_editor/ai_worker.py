"""AI 도구 작업자: .venv-aitools 안에서만 실행됩니다 (받아쓰기·번역). 편집기 코드와 따로 도는 독립 스크립트예요.

사용: python -I ai_worker.py <transcribe|translate|prepare>  ← 요청 JSON을 표준입력으로 받고,
결과 JSON을 표준출력 마지막 줄에 씁니다 (라이브러리의 진행 로그는 앞쪽에 섞일 수 있음).
"""
import json
import os
import sys

DEFAULT_MODEL = "large-v3-turbo"
# 번역 쌍은 영어를 거쳐 갑니다 (예: ko→ja = ko→en→ja)
PIVOT = "en"


def transcribe(a):
    from faster_whisper import WhisperModel

    model = WhisperModel(a.get("model") or os.environ.get("AITOOLS_WHISPER") or DEFAULT_MODEL,
                         device="cpu", compute_type="int8")
    lang = a.get("lang") or None
    prompt = a.get("prompt") or None
    segs, info = model.transcribe(a["audio"], language=lang, word_timestamps=True, vad_filter=True,
                                  initial_prompt=prompt, condition_on_previous_text=False)
    out = []
    for s in segs:
        words = [{"s": round(w.start, 3), "e": round(w.end, 3), "w": w.word.strip()} for w in (s.words or [])]
        out.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "words": words})
    return {"lang": info.language, "segments": out}


def _argos():
    import argostranslate.package as pkg
    import argostranslate.translate as tr
    return pkg, tr


def _ensure_pair(src, dst):
    pkg, tr = _argos()
    have = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
    need = [(src, dst)] if PIVOT in (src, dst) else [(src, PIVOT), (PIVOT, dst)]
    missing = [p for p in need if p not in have]
    if missing:
        pkg.update_package_index()
        avail = pkg.get_available_packages()
        for f, t in missing:
            cand = next((p for p in avail if p.from_code == f and p.to_code == t), None)
            if cand is None:
                raise RuntimeError(f"번역 모델이 없습니다: {f}→{t}")
            pkg.install_from_path(cand.download())


def translate(a):
    _, tr = _argos()
    src, dst = a["src"], a["dst"]
    _ensure_pair(src, dst)
    return {"texts": [tr.translate(t, src, dst) if t.strip() else "" for t in a["texts"]]}


def prepare(a):
    """설치할 때 모델을 미리 내려받습니다."""
    from faster_whisper import WhisperModel
    WhisperModel(a.get("model") or os.environ.get("AITOOLS_WHISPER") or DEFAULT_MODEL, device="cpu", compute_type="int8")
    for src, dst in a.get("pairs", []):
        _ensure_pair(src, dst)
    return {"ok": True}


if __name__ == "__main__":
    cmd = sys.argv[1]
    req = json.loads(sys.stdin.read() or "{}")
    try:
        res = {"transcribe": transcribe, "translate": translate, "prepare": prepare}[cmd](req)
        print("\n" + json.dumps({"ok": True, **res}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print("\n" + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)
