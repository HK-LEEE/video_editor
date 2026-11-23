from __future__ import annotations

from pathlib import Path

import yaml

from .engine import VoiceSettings

DEFAULT_PRESETS = Path(__file__).with_name("presets.yaml")


def load_presets(path: str | Path = DEFAULT_PRESETS) -> dict[str, VoiceSettings]:
    """presets.yaml을 읽어 {이름: VoiceSettings}로 돌려줍니다."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {name: VoiceSettings.from_dict(cfg, name=name) for name, cfg in raw.items()}
