#!/usr/bin/env bash
# AI 편집 도구 설치: 자동 자막·군말 자르기(faster-whisper 받아쓰기)와 번역(argos)을 별도 가상환경(.venv-aitools)에 받고,
# 노이즈 제거용 RNNoise 모델과 받아쓰기·번역 모델을 미리 내려받습니다 (수백 MB~1.5GB, 한 번만).
# 장면 나누기·무음 자르기·노이즈 제거(FFT)·읽어 주기(TTS)는 설치 없이도 됩니다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.12 python3.11 python3.10 python3; do command -v "$c" >/dev/null && PY="$c" && break; done
fi
[ -n "$PY" ] || { echo "Python 3.10 이상이 필요합니다. 맥: brew install python@3.12" >&2; exit 1; }

[ -d .venv-aitools ] || "$PY" -m venv .venv-aitools
. .venv-aitools/bin/activate
pip install --upgrade pip
pip install -r scripts/requirements-aitools.txt

# RNNoise 모델 (ffmpeg arnndn 필터용, 작은 파일)
mkdir -p third_party/rnnoise
[ -f third_party/rnnoise/bd.rnnn ] || curl -fsSL -o third_party/rnnoise/bd.rnnn \
  https://raw.githubusercontent.com/GregorR/rnnoise-models/master/beguiling-drafter-2018-08-30/bd.rnnn \
  || echo "(RNNoise 모델을 못 받았어요. 노이즈 제거는 FFT 방식으로 대신 동작합니다)"

# 받아쓰기·번역 모델 미리 받기 (한국어↔영어, 일본어, 중국어, 스페인어, 프랑스어, 독일어)
echo '{"pairs": [["ko","en"],["en","ko"],["en","ja"],["ja","en"],["en","zh"],["zh","en"],["en","es"],["en","fr"],["en","de"]]}' \
  | python -I video_editor/ai_worker.py prepare
touch .venv-aitools/.aitools-ready
echo "설치 완료. python app.py 를 다시 실행하세요."
