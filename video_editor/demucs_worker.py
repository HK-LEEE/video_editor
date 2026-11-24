"""보컬 분리 작업자: .venv-demucs 안에서만 실행되는 독립 스크립트 (표준입력 JSON → 표준출력 마지막 줄 JSON).

torchaudio의 저장 기능(최근 버전에서 별도 패키지가 필요)을 쓰지 않고, ffmpeg로 읽고 soundfile로 씁니다.
"""
import json
import subprocess
import sys

MODEL = "htdemucs"


def load_stereo(path, sr):
    import numpy as np
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-vn", "-ac", "2", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="float32").reshape(-1, 2).T.copy()


def run(a):
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model(MODEL)
    model.eval()
    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    wav = torch.from_numpy(load_stereo(a["src"], model.samplerate))
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    try:
        src = apply_model(model, wav[None], device=dev, split=True, overlap=0.25, progress=False)[0]
    except Exception:  # noqa: BLE001  (MPS에서 안 되는 연산이 있으면 CPU로 다시)
        if dev == "cpu":
            raise
        src = apply_model(model, wav[None], device="cpu", split=True, overlap=0.25, progress=False)[0]
    src = src * (ref.std() + 1e-8) + ref.mean()
    names = list(model.sources)
    voc = src[names.index("vocals")]
    bg = sum(src[i] for i, n in enumerate(names) if n != "vocals")
    sf.write(a["vocals"], voc.numpy().T, model.samplerate)
    sf.write(a["background"], bg.numpy().T, model.samplerate)
    return {}


if __name__ == "__main__":
    try:
        res = run(json.loads(sys.stdin.read()))
        print("\n" + json.dumps({"ok": True, **res}))
    except Exception as e:  # noqa: BLE001
        print("\n" + json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        sys.exit(1)
