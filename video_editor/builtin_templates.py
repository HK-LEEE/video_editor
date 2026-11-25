"""앱에 들어 있는 샘플 템플릿 5종: 예능형·정보형·감성형·뉴스형·미니멀.

모두 직접 그린 원본 디자인입니다 (특정 방송사·채널의 로고나 서체를 따라 하지 않아요).
자막 스타일 + 훅 제목 바 + 워터마크 + 인트로·아웃트로 카드 + 엔딩이 한 벌이라 한 번에 입히고, 여러 영상에 일괄로도 입힐 수 있어요.
`{channel}`은 입힐 때 적은 채널 이름으로 바뀝니다 (안 적으면 '내 채널').
"""
from __future__ import annotations

from .subtitles import SubtitleStyle
from .templates import Anim, HookStyle, LikePause, Template, Watermark

BUILTIN_PREFIX = "builtin-"


def _t(id_: str, **kw) -> Template:
    return Template(id=BUILTIN_PREFIX + id_, builtin=True, **kw)


BUILTINS: list[Template] = [
    _t("variety", name="예능형", desc="굵은 외곽선 낱말 강조 자막, 알록달록 훅 제목, 통통 튀는 인트로, 부드러운 하트 엔딩",
       subtitle_style=SubtitleStyle(karaoke=True, color="#ffffff", hl_color="#ffe14d", outline_color="#1a1a1a", outline=0.2,
                                    size=0.082, y=0.66, shadow=0.05),
       hook=HookStyle(y=0.13, size=0.07, color="#ffffff", bg_color="#ff3b6b", bg_opacity=1.0, outline=0.0, start=0.0, end=3.0),
       watermark=Watermark(text="@{channel}", x=0.17, y=0.065, size=0.03, opacity=0.65, color="#ffffff"),
       intro_card=Anim(kind="pop", text="{channel}", sub="오늘도 웃어봐요!", accent="#ffd23f", bg1="#ff3b6b", bg2="#7b2cff", duration=1.4),
       outro_card=Anim(kind="pop", text="또 만나요!", sub="{channel}", accent="#ffd23f", bg1="#7b2cff", bg2="#ff3b6b", duration=1.4),
       ending_card=Anim(kind="soft_hearts", text="함께 웃어 줘서 고마워요", sub="구독하고 다음 영상도 같이 봐요", accent="#f2788a", bg1="#fff1ec", bg2="#fbcdc4", text_color="#6a3a3a", duration=4.0),
       intro_transition="none", outro_transition="fade"),
    _t("info", name="정보형", desc="깔끔한 남색 박스 자막, 파란 훅 제목, 옆에서 밀려오는 인트로, 부드러운 감사 엔딩",
       subtitle_style=SubtitleStyle(color="#ffffff", outline=0.0, bg_color="#0f2a4a", bg_opacity=0.86, size=0.05, y=0.82, box_pad=0.32),
       hook=HookStyle(y=0.12, size=0.058, color="#ffffff", bg_color="#2563eb", bg_opacity=0.96, outline=0.0, start=0.0, end=3.5),
       watermark=Watermark(text="{channel}", x=0.16, y=0.06, size=0.026, opacity=0.6, color="#e6f0ff"),
       intro_card=Anim(kind="slide", text="{channel}", sub="알아두면 쓸모있는 이야기", accent="#38bdf8", bg1="#0b1b33", bg2="#123b6b", duration=1.4),
       outro_card=Anim(kind="slide", text="도움이 됐다면", sub="저장해 두세요", accent="#38bdf8", bg1="#123b6b", bg2="#0b1b33", duration=1.4),
       ending_card=Anim(kind="soft_text", text="끝까지 봐주셔서 감사해요", sub="다음 영상도 확인하세요", accent="#5aa9e6", bg1="#f1f7fd", bg2="#cfe3f6", text_color="#26435f", duration=4.0),
       intro_transition="fade", outro_transition="fade"),
    _t("emotional", name="감성형", desc="은은한 글로우 자막, 투명 배경 훅 제목, 부드러운 크림빛 인트로, 영상 중간 '좋아요 톡톡' 정지, 따뜻한 로고 엔딩",
       subtitle_style=SubtitleStyle(font="AppleMyungjo", bold=False, color="#fff7ec", outline_color="#3b2a2a", outline=0.05, size=0.056,
                                    y=0.8, shadow=0.06, glow=0.35),
       hook=HookStyle(y=0.15, size=0.056, color="#fff7ec", bg_color="#000000", bg_opacity=0.0, outline=0.08, start=0.0, end=3.5),
       watermark=Watermark(text="{channel}", x=0.5, y=0.94, size=0.024, opacity=0.5, color="#fff7ec"),
       intro_card=Anim(kind="soft_open_logo", text="{channel}", sub="오늘의 이야기를 시작해요", accent="#f08a5d", bg1="#fff6ea", bg2="#fcd9bd", text_color="#5b3a29", duration=2.8),
       outro_card=Anim(kind="fade", text="고마워요", sub="{channel}", accent="#f4c9a8", bg1="#5b3a4a", bg2="#2b1d2e", duration=1.8),
       ending_card=Anim(kind="soft_logo", text="마음에 닿았다면", sub="구독으로 다시 만나요", accent="#f08a5d", bg1="#fff6ea", bg2="#fcd9bd", text_color="#5b3a29", duration=4.0),
       like_pause=LikePause(),
       intro_transition="fade", outro_transition="fade"),
    _t("news", name="뉴스형", desc="하단 파란 띠 자막, 빨간 속보 느낌 훅 제목, 띠가 지나가는 인트로, 부드러운 감사 엔딩",
       subtitle_style=SubtitleStyle(color="#ffffff", outline=0.0, bg_color="#0b2a6b", bg_opacity=0.92, size=0.05, y=0.88, box_pad=0.35),
       hook=HookStyle(y=0.1, size=0.055, color="#ffffff", bg_color="#c4161c", bg_opacity=0.96, outline=0.0, start=0.0, end=4.0),
       watermark=Watermark(text="{channel} 뉴스", x=0.2, y=0.05, size=0.026, opacity=0.8, color="#ffffff"),
       intro_card=Anim(kind="wipe", text="{channel} 뉴스", sub="오늘의 소식", accent="#c4161c", bg1="#0b1f4d", bg2="#071233", duration=1.5),
       outro_card=Anim(kind="wipe", text="시청해 주셔서 감사합니다", sub="{channel} 뉴스", accent="#c4161c", bg1="#071233", bg2="#0b1f4d", duration=1.6),
       ending_card=Anim(kind="soft_card", text="시청해 주셔서 감사합니다", sub="구독하고 다음 소식을 받아 보세요", accent="#3b6fc4", bg1="#f1f5fb", bg2="#d3def0", text_color="#1f2f4d", duration=4.0),
       intro_transition="none", outro_transition="fade"),
    _t("minimal", name="미니멀", desc="가는 흰 글씨 자막, 배경 없는 훅 제목, 가는 선 인트로, 조용한 인사 엔딩",
       subtitle_style=SubtitleStyle(bold=False, color="#ffffff", outline_color="#000000", outline=0.07, size=0.05, y=0.86, shadow=0.03),
       hook=HookStyle(y=0.12, size=0.05, color="#ffffff", bg_color="#000000", bg_opacity=0.0, outline=0.1, start=0.0, end=3.0),
       watermark=Watermark(text="{channel}", x=0.14, y=0.055, size=0.022, opacity=0.4, color="#ffffff"),
       intro_card=Anim(kind="line", text="{channel}", sub="", accent="#ffffff", bg1="#111111", bg2="#1f1f1f", duration=1.0),
       ending_card=Anim(kind="soft_text", text="고맙습니다", sub="구독으로 또 만나요", accent="#a89f94", bg1="#faf8f5", bg2="#e8e3dc", text_color="#4a443d", duration=3.5),
       intro_transition="fade"),
]


def all_builtin() -> list[Template]:
    return [t.model_copy(deep=True) for t in BUILTINS]


def get_builtin(tid: str) -> Template | None:
    return next((t.model_copy(deep=True) for t in BUILTINS if t.id == tid), None)
