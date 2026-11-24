"""Claude로 쇼츠의 첫 3초 훅 제목, 썸네일 문구, 업로드용 제목·설명·해시태그를 뽑습니다.

- 기본은 **Claude Code 헤드리스 모드**: 컴퓨터에 설치된 `claude` 명령(`claude -p`)을 불러서 내 Claude 로그인으로 돌립니다. API 키가 필요 없어요.
- 대체 방법은 Claude API: 키는 코드나 저장소에 넣지 않고, 환경변수 `ANTHROPIC_API_KEY` 또는 편집기 설정(작업 폴더의 `settings.json`, git에 안 올라감)에 둡니다.
- 모델은 기본 Sonnet 5.5(`claude-sonnet-5-5`), 더 꼼꼼한 Opus 5.5(`claude-opus-5-5`)를 고를 수 있습니다.
- 자막(대본)을 그대로 보냅니다. 영상이나 소리는 보내지 않습니다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MODELS = {"claude-sonnet-5-5": "Sonnet 5.5 (빠르고 저렴, 기본)", "claude-opus-5-5": "Opus 5.5 (더 꼼꼼)"}
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TRANSCRIPT_CHARS = 24000
ENGINES = {"cli": "Claude Code (내 Claude 로그인, 키 필요 없음)", "api": "Claude API 키"}
CLI_TIMEOUT = 180
NO_CLI = ("Claude Code(`claude` 명령)를 못 찾았어요. 터미널에서 `npm install -g @anthropic-ai/claude-code` 로 설치하고 `claude` 를 한 번 실행해 로그인하거나, "
          "'쇼츠 도구 → 문구' 탭에서 'Claude API 키' 방식으로 바꿔 주세요.")
NO_LOGIN = "Claude Code에 로그인이 안 되어 있어요. 터미널에서 `claude` 를 실행해 로그인한 뒤 다시 눌러 주세요."
NO_KEY = ("Claude API 키가 없어요. 터미널에서 `export ANTHROPIC_API_KEY=sk-ant-...` 로 켠 뒤 앱을 다시 시작하거나, "
          "'쇼츠 도구 → 문구' 탭의 API 키 칸에 붙여 넣으세요. (키는 https://console.anthropic.com/settings/keys 에서 만들어요)")

SCHEMA = {
    "type": "object",
    "properties": {
        "hook_titles": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "why": {"type": "string"}}, "required": ["text", "why"], "additionalProperties": False}},
        "thumbnail": {"type": "object", "properties": {
            "text": {"type": "string"}, "idea": {"type": "string"}, "best_time": {"type": "number"}},
            "required": ["text", "idea", "best_time"], "additionalProperties": False},
        "youtube": {"type": "object", "properties": {
            "title": {"type": "string"}, "description": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "description", "hashtags"], "additionalProperties": False},
        "instagram": {"type": "object", "properties": {
            "caption": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
            "required": ["caption", "hashtags"], "additionalProperties": False},
    },
    "required": ["hook_titles", "thumbnail", "youtube", "instagram"],
    "additionalProperties": False,
}

SYSTEM = """당신은 한국어 숏폼(유튜브 쇼츠·인스타 릴스) 전문 카피라이터입니다. 영상 대본을 읽고 다음을 만듭니다.
- hook_titles: 영상 맨 앞 3초 동안 화면 위쪽에 크게 띄울 제목 3가지. 각 18자 이하, 줄바꿈 없이, 궁금증·반전·숫자·감정 중 하나로 시선을 잡되 대본에 없는 사실은 지어내지 않습니다. why에는 한 문장으로 왜 먹히는지.
- thumbnail: 썸네일에 얹을 짧은 글자(text, 12자 이하), 어떤 장면·표정·구도가 좋은지(idea), 대본 시각 중 썸네일로 가장 좋은 순간(best_time, 초. 모르면 0).
- youtube: 쇼츠 업로드용 title(60자 이하, 끝에 #Shorts는 붙이지 않음), description(2~4문장 + 마지막 줄 구독 유도 한 줄), hashtags(5~8개, '#' 포함, #Shorts 포함).
- instagram: 릴스 caption(첫 줄이 훅, 3~5줄, 이모지 적당히), hashtags(8~12개, '#' 포함).
대본의 언어와 말투를 따르고(한국어면 자연스러운 구어체), 과장·허위·낚시성 거짓 주장은 쓰지 않습니다."""


def settings_path(root: Path) -> Path:
    return Path(root) / "settings.json"


def load_settings(root: Path) -> dict:
    p = settings_path(root)
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(root: Path, api_key: str | None = None, model: str | None = None, engine: str | None = None) -> dict:
    s = load_settings(root)
    if api_key is not None:
        key = api_key.strip()
        if key:
            s["api_key"] = key
        else:
            s.pop("api_key", None)
    if model is not None:
        if model not in MODELS:
            raise ValueError("모르는 모델이에요.")
        s["model"] = model
    if engine is not None:
        if engine not in ENGINES:
            raise ValueError("모르는 방식이에요.")
        s["engine"] = engine
    p = settings_path(root)
    p.write_text(json.dumps(s))
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return public_settings(root)


def api_key(root: Path) -> tuple[str | None, str]:
    """(키, 출처 env|file|none)"""
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        return env, "env"
    k = load_settings(root).get("api_key")
    return (k, "file") if k else (None, "none")


def cli_path() -> str | None:
    p = shutil.which("claude")
    if p:
        return p
    for c in (Path.home() / ".claude/local/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude"), Path.home() / ".local/bin/claude"):
        if c.exists():
            return str(c)
    return None


def engine_for(root: Path) -> str:
    """실제로 쓸 방식. 기본은 Claude Code. 그게 없는데 API 키가 있으면 API로 갑니다."""
    want = load_settings(root).get("engine", "cli")
    if want == "cli" and not cli_path() and api_key(root)[0]:
        return "api"
    return want if want in ENGINES else "cli"


def public_settings(root: Path) -> dict:
    key, src = api_key(root)
    eng = engine_for(root)
    return {"engine": load_settings(root).get("engine", "cli"), "engines": ENGINES, "active_engine": eng,
            "has_cli": bool(cli_path()), "ready": bool(cli_path()) if eng == "cli" else bool(key),
            "no_cli_help": NO_CLI, "help": NO_CLI if eng == "cli" else NO_KEY,
            "has_key": bool(key), "source": src, "masked": (key[:7] + "…" + key[-4:]) if key and len(key) > 14 else ("저장됨" if key else ""),
            "model": load_settings(root).get("model", DEFAULT_MODEL), "models": MODELS, "no_key_help": NO_KEY}


def transcript_text(cues: list[dict], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    lines = [f"[{c['start']:.1f}s] {c['text'].strip()}" for c in sorted(cues, key=lambda c: c["start"]) if c.get("text", "").strip()]
    text = "\n".join(lines)
    return text[:limit]


def _client(key: str):
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("anthropic 패키지가 없어요. 터미널에서 `pip install anthropic` 을 실행하세요.") from e
    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=120.0)


def _prompt(text: str, duration: float, topic: str, platform_note: str) -> str:
    return (f"영상 길이: {duration:.0f}초\n" + (f"주제·채널 설명: {topic}\n" if topic.strip() else "")
            + (f"추가 요청: {platform_note}\n" if platform_note.strip() else "") + f"\n대본(자막):\n{text}")


def _parse_json(out: str) -> dict:
    out = out.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", out, re.S)
    if m:
        out = m.group(1).strip()
    if not out.startswith("{"):
        i, j = out.find("{"), out.rfind("}")
        out = out[i:j + 1] if 0 <= i < j else out
    return json.loads(out)


def run_cli(user: str, model: str, timeout: float = CLI_TIMEOUT, runner=None) -> dict:
    """`claude -p`(헤드리스)로 한 번 물어서 SCHEMA 모양의 dict를 받습니다. 실패하면 RuntimeError(한국어 안내)."""
    exe = cli_path()
    if not exe:
        raise RuntimeError(NO_CLI)
    runner = runner or subprocess.run
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}   # 내 로그인으로 돌립니다
    schema_note = ("\n\n반드시 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력하세요(설명·코드블록 없이).\n" + json.dumps(SCHEMA, ensure_ascii=False))
    base = [exe, "-p", "--output-format", "json", "--model", model]
    rich = base + ["--system-prompt", SYSTEM, "--tools", "", "--no-session-persistence", "--disable-slash-commands"]
    with tempfile.TemporaryDirectory() as cwd:          # 프로젝트 폴더의 설정·메모를 읽지 않게 빈 폴더에서 돌립니다
        for args in (rich, base):
            try:
                r = runner(args, input=(user + schema_note) if args is rich else (SYSTEM + "\n\n" + user + schema_note),
                           capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"Claude Code가 {int(timeout)}초 안에 답하지 않았어요. 잠시 뒤에 다시 해 주세요.") from e
            except OSError as e:
                raise RuntimeError(f"Claude Code를 실행하지 못했어요: {e}") from e
            err = (r.stderr or "") + (r.stdout or "")
            if r.returncode != 0 and args is rich and re.search(r"unknown (option|argument)|unrecognized", err, re.I):
                continue                                  # 오래된 CLI에는 일부 옵션이 없어서, 최소 옵션으로 한 번 더
            break
    raw = (r.stdout or "").strip()
    try:
        env_json = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        env_json = {}
    text = str(env_json.get("result", "")) if isinstance(env_json, dict) else ""
    low = (text + err).lower()
    if r.returncode != 0 or (isinstance(env_json, dict) and env_json.get("is_error")):
        if re.search(r"not logged in|please run /login|login|authentication|credentials|oauth|invalid api key|401", low):
            raise RuntimeError(NO_LOGIN)
        if re.search(r"rate limit|usage limit|limit reached|429|overloaded", low):
            raise RuntimeError("사용 한도에 걸렸어요. 잠시 뒤에 다시 해 주세요. (Claude 구독 한도일 수 있어요)")
        raise RuntimeError("Claude Code 호출에 실패했어요: " + (text or err).strip()[:300])
    if isinstance(env_json, dict) and isinstance(env_json.get("structured_output"), dict):
        return env_json["structured_output"]
    try:
        return _parse_json(text or raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError("Claude의 답을 읽지 못했어요. 한 번 더 눌러 주세요.") from e


def generate(root: Path, cues: list[dict], duration: float = 0.0, topic: str = "", model: str | None = None,
             platform_note: str = "", client=None, runner=None) -> dict:
    """결과(dict, SCHEMA 모양). 준비가 안 됐거나 호출이 실패하면 RuntimeError(한국어 안내)."""
    text = transcript_text(cues)
    if not text.strip():
        raise RuntimeError("대본이 비어 있어요. 먼저 AI 도구로 자동 자막을 만들거나 자막을 넣어 주세요.")
    model = model or load_settings(root).get("model", DEFAULT_MODEL)
    if model not in MODELS:
        model = DEFAULT_MODEL
    user = _prompt(text, duration, topic, platform_note)
    if client is None and engine_for(root) == "cli":
        data = run_cli(user, model, runner=runner)
        data["model"] = model
        return clean(data)
    key, _ = api_key(root)
    if client is None:
        if not key:
            raise RuntimeError(NO_KEY)
        client = _client(key)
    try:
        import anthropic
        errors = (anthropic.APIError,)
    except ImportError:  # 테스트용 가짜 클라이언트만 쓰는 경우
        errors = ()
    try:
        r = client.messages.create(model=model, max_tokens=4000, system=SYSTEM,
                                   output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
                                   messages=[{"role": "user", "content": user}])
    except errors as e:  # type: ignore[misc]
        status = getattr(e, "status_code", None)
        if status == 401:
            raise RuntimeError("API 키가 올바르지 않아요. 키를 다시 확인해 주세요.") from e
        if status == 429:
            raise RuntimeError("요청이 너무 많거나 사용 한도에 걸렸어요. 잠시 뒤에 다시 해 주세요.") from e
        raise RuntimeError(f"Claude 호출에 실패했어요: {getattr(e, 'message', e)}") from e
    if getattr(r, "stop_reason", "") == "refusal":
        raise RuntimeError("Claude가 이 대본으로는 만들기를 거절했어요. 대본을 확인해 주세요.")
    out = next((b.text for b in r.content if getattr(b, "type", "") == "text"), "")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError("Claude의 답을 읽지 못했어요. 한 번 더 눌러 주세요.") from e
    data["model"] = model
    return clean(data)


def clean(d: dict) -> dict:
    """화면에 바로 쓰도록 길이·형식을 다듬습니다."""
    d["hook_titles"] = [{"text": " ".join(str(h.get("text", "")).split()), "why": str(h.get("why", ""))} for h in d.get("hook_titles", [])][:5]
    tag = lambda t: ("#" + str(t).strip().lstrip("#").replace(" ", "")) if str(t).strip().lstrip("#") else ""
    for k in ("youtube", "instagram"):
        d.setdefault(k, {})
        d[k]["hashtags"] = [t for t in (tag(x) for x in d[k].get("hashtags", [])) if t]
    d.setdefault("thumbnail", {"text": "", "idea": "", "best_time": 0})
    return d
