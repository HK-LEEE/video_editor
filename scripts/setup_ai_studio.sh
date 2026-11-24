#!/usr/bin/env bash
# 쇼츠 스튜디오 기능 설치: 자동 리프레임(OpenCV + 얼굴 검출 모델)과 보컬 분리(Demucs, 별도 가상환경 .venv-demucs).
# 하이라이트·글로 편집·낱말 자막·음악 자동 줄이기·감정·보이스 캐릭터는 이 설치 없이도(받아쓰기만 있으면) 돌아갑니다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.10 python3; do command -v "$c" >/dev/null && PY="$c" && break; done
fi
[ -n "$PY" ] || { echo "Python 3.10 이상이 필요합니다. 맥: brew install python@3.12" >&2; exit 1; }

# 1) 자동 리프레임: .venv-aitools에 OpenCV (받아쓰기·번역 설치가 아직이면 먼저 그것부터)
[ -f .venv-aitools/.aitools-ready ] || bash scripts/setup_ai_tools.sh
.venv-aitools/bin/pip install "opencv-python-headless>=4.9,<5"
mkdir -p third_party/yunet
[ -f third_party/yunet/face_detection_yunet_2023mar.onnx ] || curl -fsSL -o third_party/yunet/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
  || echo "(얼굴 검출 모델을 못 받았어요. 기본 검출기(Haar)로 대신 동작해요)"

# 2) 보컬 분리: Demucs (PyTorch 포함, 수 GB). 최신 torchaudio는 다른 패키지를 요구해서 2.8 이하로 고정
[ -d .venv-demucs ] || "$PY" -m venv .venv-demucs
.venv-demucs/bin/pip install --upgrade pip
.venv-demucs/bin/pip install "torch>=2.1,<2.9" "torchaudio>=2.1,<2.9" demucs soundfile numpy
echo "분리 모델(htdemucs) 내려받는 중…"
.venv-demucs/bin/python -c "from demucs.pretrained import get_model; get_model('htdemucs')"
touch .venv-demucs/.demucs-ready
echo "설치 완료. python app.py 를 다시 실행하세요."
