"""웹 화면: python app.py 실행 후 브라우저에서
- http://127.0.0.1:7860         간단 변조기 (Gradio)
- http://127.0.0.1:7860/editor  멀티트랙 영상 편집기
"""
from __future__ import annotations

import json
import urllib.parse
import os
import tempfile
import time
from pathlib import Path

import gradio as gr
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from video_editor import VoiceSettings, load_audio, load_presets, process, save_wav
from video_editor import library
from video_editor.editor import Workspace, make_router
from video_editor.workspace_routes import add_page_routes

PRESETS = load_presets()
CUSTOM = "custom"
CHOICES = [(s.label, name) for name, s in PRESETS.items()] + [("직접 조절 (슬라이더)", CUSTOM)]
OUT_DIR = Path(tempfile.gettempdir()) / "video_editor"


def _settings(preset: str, pitch: float, formant: float) -> VoiceSettings:
    if preset == CUSTOM:
        return VoiceSettings(pitch=pitch, formant=formant)
    return PRESETS[preset]


def _run(path: str, s: VoiceSettings, intensity: float = 1.0) -> str:
    audio = load_audio(path)
    out = OUT_DIR / f"{Path(path).stem}_{s.name}_{int(time.time() * 1000)}.wav"
    return str(save_wav(out, process(audio, s, intensity=intensity)))


def convert(path, preset, pitch, formant, intensity):
    if not path:
        raise gr.Error("먼저 녹음하거나 파일을 올려 주세요.")
    # 직접 조절은 슬라이더 값 그대로, 프리셋은 강도를 곱합니다
    return _run(path, _settings(preset, pitch, formant), 1.0 if preset == CUSTOM else intensity)


def convert_all(path, intensity):
    if not path:
        raise gr.Error("먼저 녹음하거나 파일을 올려 주세요.")
    return [_run(path, s, intensity) for s in PRESETS.values()]


def on_preset(preset):
    s = PRESETS.get(preset)
    if s is None:
        return gr.update(interactive=True), gr.update(interactive=True)
    return gr.update(value=s.pitch, interactive=False), gr.update(value=s.formant, interactive=False)


with gr.Blocks(title="video_editor 보이스 체인저") as demo:
    gr.Markdown("# 🎙️ video_editor 보이스 체인저\n녹음하거나 파일(m4a, wav)을 올리고, 목소리를 골라 변조하세요.  \n"
                "영상 편집·더빙은 👉 [video_editor](/editor)")
    with gr.Row():
        with gr.Column():
            inp = gr.Audio(sources=["microphone", "upload"], type="filepath", label="내 목소리")
            preset = gr.Radio(CHOICES, value="deep", label="목소리")
            intensity = gr.Slider(0.5, 3.0, value=1.0, step=0.1,
                                  label="강도 (프리셋 변조 세기, 1.0 = 기본, 2.0 = 두 배)")
            pitch = gr.Slider(-24, 24, value=PRESETS["deep"].pitch, step=0.5,
                              label="피치 (반음)", interactive=False)
            formant = gr.Slider(0.5, 2.0, value=PRESETS["deep"].formant, step=0.01,
                                label="포먼트 (1.0 = 그대로, 크면 밝고 작으면 굵게)", interactive=False)
            btn = gr.Button("변조하기", variant="primary")
        with gr.Column():
            out = gr.Audio(type="filepath", label="결과", interactive=False)

    with gr.Accordion("모든 프리셋 한 번에 비교", open=False):
        all_btn = gr.Button("전부 만들기")
        all_outs = [gr.Audio(type="filepath", label=s.label, interactive=False) for s in PRESETS.values()]

    preset.change(on_preset, preset, [pitch, formant])
    btn.click(convert, [inp, preset, pitch, formant, intensity], out)
    all_btn.click(convert_all, [inp, intensity], all_outs)


# 작업 폴더: workspace/projects/<프로젝트>/ 아래에 프로젝트별로 나뉘어 저장됩니다 (예전 구조면 처음 켤 때 '기존 작업'으로 옮겨요).
# 다른 곳을 쓰려면 환경변수 VIDEO_EDITOR_WORKSPACE=/경로
WORKSPACE = Workspace(Path(os.environ.get("VIDEO_EDITOR_WORKSPACE") or Path(__file__).with_name("workspace")))
library.warm(WORKSPACE)   # 효과음·음악을 뒤에서 미리 만들어 둡니다
EDITOR_HTML = Path(__file__).with_name("video_editor") / "static" / "editor.html"

app = FastAPI(title="video_editor")
app.include_router(make_router(WORKSPACE))


add_page_routes(app, WORKSPACE, EDITOR_HTML)


@app.get("/editor-static/{name}", include_in_schema=False)   # 편집기 화면 스크립트 (정해 둔 파일만)
def editor_static(name: str):
    f = EDITOR_HTML.parent / name
    if name not in {"shorts_tools.js", "workspace_picker.js"} or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="text/javascript")


@app.get("/studio", include_in_schema=False)  # 예전 주소
def studio():
    return RedirectResponse("/editor")


app = gr.mount_gradio_app(app, demo, path="/")

if __name__ == "__main__":
    print("간단 변조기:     http://127.0.0.1:7860")
    print("영상 편집기:     http://127.0.0.1:7860/editor")
    uvicorn.run(app, host="127.0.0.1", port=7860)
