"""웹 화면: python app.py 실행 후 브라우저에서 http://127.0.0.1:7860"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import gradio as gr

from voxshift import VoiceSettings, load_audio, load_presets, process, save_wav

PRESETS = load_presets()
CUSTOM = "custom"
CHOICES = [(s.label, name) for name, s in PRESETS.items()] + [("직접 조절 (슬라이더)", CUSTOM)]
OUT_DIR = Path(tempfile.gettempdir()) / "voxshift"


def _settings(preset: str, pitch: float, formant: float) -> VoiceSettings:
    if preset == CUSTOM:
        return VoiceSettings(pitch=pitch, formant=formant)
    return PRESETS[preset]


def _run(path: str, s: VoiceSettings) -> str:
    audio = load_audio(path)
    out = OUT_DIR / f"{Path(path).stem}_{s.name}_{int(time.time() * 1000)}.wav"
    return str(save_wav(out, process(audio, s)))


def convert(path, preset, pitch, formant):
    if not path:
        raise gr.Error("먼저 녹음하거나 파일을 올려 주세요.")
    return _run(path, _settings(preset, pitch, formant))


def convert_all(path):
    if not path:
        raise gr.Error("먼저 녹음하거나 파일을 올려 주세요.")
    return [_run(path, s) for s in PRESETS.values()]


def on_preset(preset):
    s = PRESETS.get(preset)
    if s is None:
        return gr.update(interactive=True), gr.update(interactive=True)
    return gr.update(value=s.pitch, interactive=False), gr.update(value=s.formant, interactive=False)


with gr.Blocks(title="voxshift 보이스 체인저") as demo:
    gr.Markdown("# 🎙️ voxshift 보이스 체인저\n녹음하거나 파일(m4a, wav)을 올리고, 목소리를 골라 변조하세요.")
    with gr.Row():
        with gr.Column():
            inp = gr.Audio(sources=["microphone", "upload"], type="filepath", label="내 목소리")
            preset = gr.Radio(CHOICES, value="deep", label="목소리")
            pitch = gr.Slider(-12, 12, value=PRESETS["deep"].pitch, step=0.5,
                              label="피치 (반음)", interactive=False)
            formant = gr.Slider(0.7, 1.4, value=PRESETS["deep"].formant, step=0.01,
                                label="포먼트 (1.0 = 그대로, 크면 밝고 작으면 굵게)", interactive=False)
            btn = gr.Button("변조하기", variant="primary")
        with gr.Column():
            out = gr.Audio(type="filepath", label="결과", interactive=False)

    with gr.Accordion("모든 프리셋 한 번에 비교", open=False):
        all_btn = gr.Button("전부 만들기")
        all_outs = [gr.Audio(type="filepath", label=s.label, interactive=False) for s in PRESETS.values()]

    preset.change(on_preset, preset, [pitch, formant])
    btn.click(convert, [inp, preset, pitch, formant], out)
    all_btn.click(convert_all, inp, all_outs)


if __name__ == "__main__":
    demo.launch()
