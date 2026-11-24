#!/usr/bin/env bash
# 자동 이어 붙이기의 'AI 모핑'(이음새 사이 화면을 AI가 만들어 주는 프레임 보간) 설치: RIFE(rife-ncnn-vulkan)를
# third_party/rife 에 받습니다. 맥(애플 실리콘·인텔)·리눅스·윈도우(Git Bash)용 실행 파일이 들어 있고 GPU(Metal/Vulkan)를 씁니다.
# 압축 파일은 400MB 정도지만 쓰는 모델(rife-v4.6)만 남깁니다. 설치 안 해도 이음새는 ffmpeg 보간으로 대신 동작해요.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=third_party/rife
VER=20221029
case "$(uname -s)" in
  Darwin) OS=macos ;;
  Linux)  OS=ubuntu ;;
  *)      OS=windows ;;
esac
if [ -x "$DEST/rife-ncnn-vulkan" ] && [ -d "$DEST/rife-v4.6" ]; then echo "이미 설치돼 있어요: $DEST"; exit 0; fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/nihui/rife-ncnn-vulkan/releases/download/$VER/rife-ncnn-vulkan-$VER-$OS.zip"
echo "내려받는 중: $URL"
curl -fL --retry 3 -o "$TMP/rife.zip" "$URL"
unzip -q "$TMP/rife.zip" -d "$TMP"
SRC="$TMP/rife-ncnn-vulkan-$VER-$OS"
mkdir -p "$DEST"
cp "$SRC"/rife-ncnn-vulkan* "$DEST/" 2>/dev/null || true
cp "$SRC"/*.dylib "$SRC"/*.so* "$SRC"/*.dll "$DEST/" 2>/dev/null || true
rm -rf "$DEST/rife-v4.6" && cp -R "$SRC/rife-v4.6" "$DEST/rife-v4.6"
chmod +x "$DEST/rife-ncnn-vulkan"
[ "$OS" = macos ] && xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
echo "설치 완료: $DEST (python app.py 를 다시 실행하세요)"
