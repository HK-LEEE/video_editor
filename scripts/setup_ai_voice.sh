#!/usr/bin/env bash
# AI 음성 변환(Seed-VC) 설치: 별도 가상환경(.venv-seedvc)에 PyTorch와 Seed-VC를 받습니다.
# 처음 변환할 때 모델(수 GB)이 자동으로 내려받아집니다. 맥(Apple Silicon)은 MPS로 가속됩니다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3.10}"
command -v "$PY" >/dev/null || PY=python3
mkdir -p third_party
[ -d third_party/seed-vc ] || git clone --depth 1 https://github.com/Plachtaa/seed-vc third_party/seed-vc

[ -d .venv-seedvc ] || "$PY" -m venv .venv-seedvc
. .venv-seedvc/bin/activate
pip install --upgrade pip
if [ "$(uname)" = "Darwin" ]; then
  pip install -r third_party/seed-vc/requirements-mac.txt
else
  pip install -r third_party/seed-vc/requirements.txt
fi
echo "설치 완료. python app.py 를 다시 실행하세요."
