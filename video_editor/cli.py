"""명령줄 사용: python -m video_editor input.m4a --preset deep"""
from __future__ import annotations

import argparse
from pathlib import Path

from .engine import VoiceSettings, load_audio, process, save_wav
from .presets import load_presets


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="video_editor", description="내 녹음을 변조합니다.")
    p.add_argument("input", help="녹음 파일 (m4a, wav 등)")
    p.add_argument("--preset", help="프리셋 이름, 또는 all (모든 프리셋)")
    p.add_argument("--pitch", type=float, help="피치 (반음)")
    p.add_argument("--formant", type=float, help="포먼트 비율")
    p.add_argument("--intensity", type=float, default=1.0, help="프리셋 강도 (1.0 기본, 2.0 두 배)")
    p.add_argument("--out", default="output", help="저장 폴더")
    p.add_argument("--list", action="store_true", help="프리셋 목록 보기")
    args = p.parse_args(argv)

    presets = load_presets()
    if args.list:
        for name, s in presets.items():
            print(f"{name:16} {s.label}")
        return

    if args.preset == "all":
        targets = list(presets.values())
    elif args.preset:
        if args.preset not in presets:
            p.error(f"없는 프리셋: {args.preset} (--list로 확인)")
        targets = [presets[args.preset]]
    else:
        targets = [VoiceSettings()]
    for s in targets:
        if args.pitch is not None:
            s.pitch = args.pitch
        if args.formant is not None:
            s.formant = args.formant

    audio = load_audio(args.input)
    stem = Path(args.input).stem
    for s in targets:
        path = save_wav(Path(args.out) / f"{stem}_{s.name}.wav", process(audio, s, intensity=args.intensity))
        print(f"저장: {path}")


if __name__ == "__main__":
    main()
