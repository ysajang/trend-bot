"""
텔레그램 리포트 발송 (v2 — 규칙기반)

ReportData → 텔레그램 포맷 → 분할 전송
"""

import logging
from datetime import datetime, timezone, timedelta

import telegram

import config
from analyzer.report_builder import ReportData

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MAX_MESSAGE_LENGTH = 4096  # 텔레그램 메시지 최대 길이

# 트렌드 태그 → 이모지 매핑
TAG_EMOJI = {
    "급상승": "🔺",
    "지속상승": "📈",
    "하락세": "📉",
    "신규": "🆕",
    "유지": "➖",
    "—": "·",
}


def format_report(report: ReportData) -> str:
    """
    ReportData → 텔레그램 메시지 포맷

    섹션 구조:
      헤더 → TOP 10 키워드 → 주요 뉴스 클러스터 → 7일 추이 → 어제 대비 변화
    """
    now = datetime.now(KST)
    date_str = now.strftime("%Y.%m.%d %H:%M")
    total = report.total_rss + report.total_news + report.total_datalab

    lines: list[str] = []

    # ── 헤더 ──
    lines.append(f"📊 TrendRadar v2 | 뉴스 {total}건 + 검색트렌드 분석")
    lines.append(f"📅 {date_str}")
    lines.append(f"📝 RSS {report.total_rss} + 뉴스 {report.total_news} + DataLab {report.total_datalab}")
    lines.append("")

    # ── 섹션 0: ⭐ 조사할 가치 (행동 트리거 충족) ──
    if report.action_signals:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("⭐ 조사할 가치 (빈도5+ / 검색수요 / 3일연속)")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for kw in report.action_signals:
            lines.append(f"⭐ {kw.keyword} ({kw.count}회) {kw.source_tag}")
        lines.append("")

    # ── 섹션 1: 급상승 키워드 TOP 10 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🔥 급상승 키워드 TOP 10")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    for i, kw in enumerate(report.top_keywords, 1):
        emoji = TAG_EMOJI.get(kw.trend_tag, "·")
        source = f" {kw.source_tag}" if kw.source_tag else ""
        lines.append(f"{i}. {kw.keyword} ({kw.count}회) {emoji} {kw.trend_tag}{source}")
    lines.append("")

    # ── 섹션 2: 주요 뉴스 클러스터 ──
    news_keywords = [kw for kw in report.top_keywords if kw.representative_news_title]
    if news_keywords:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📰 주요 뉴스 클러스터")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for kw in news_keywords:
            title = kw.representative_news_title
            # 너무 긴 제목 자르기
            if len(title) > 60:
                title = title[:57] + "..."
            lines.append(f"▸ [{kw.keyword}] {title}")
        lines.append("")

    # ── 섹션 3: 엔터/문화/게임 트렌드 ──
    if report.entertainment_keywords:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🎮 엔터/문화/게임 트렌드 TOP 5")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for i, kw in enumerate(report.entertainment_keywords, 1):
            emoji = TAG_EMOJI.get(kw.trend_tag, "·")
            source = f" {kw.source_tag}" if kw.source_tag else ""
            lines.append(f"{i}. {kw.keyword} ({kw.count}회) {emoji} {kw.trend_tag}{source}")
        # 엔터 대표 기사
        ent_news = [kw for kw in report.entertainment_keywords if kw.representative_news_title]
        if ent_news:
            lines.append("")
            for kw in ent_news:
                title = kw.representative_news_title
                if len(title) > 60:
                    title = title[:57] + "..."
                lines.append(f"▸ [{kw.keyword}] {title}")
        lines.append("")

    # ── 섹션 3.5: 🎯 Steam 선행지표 ──
    if report.steam_games:
        most_played = [g for g in report.steam_games if g.get("chart") == "most_played"]
        top_sellers = [g for g in report.steam_games if g.get("chart") == "top_sellers_kr"]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🎯 Steam 선행지표")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        def _change_mark(g: dict) -> str:
            ch = g.get("rank_change")
            if ch is None:
                return " 🆕"
            if ch >= 3:
                return f" 🚀+{ch}"
            if ch > 0:
                return f" ↑{ch}"
            if ch < 0:
                return f" ↓{abs(ch)}"
            return ""

        if most_played:
            lines.append("🌐 글로벌 동접 TOP 5")
            for g in most_played[:5]:
                name = g.get("name") or f"appid {g.get('appid')}"
                genres = "/".join(g.get("genres", [])[:2])
                genre_str = f" [{genres}]" if genres else ""
                lines.append(f"{g.get('rank')}. {name}{genre_str}{_change_mark(g)}")

        if top_sellers:
            lines.append("")
            lines.append("🇰🇷 한국 판매 TOP 5")
            for g in top_sellers[:5]:
                name = g.get("name") or f"appid {g.get('appid')}"
                lines.append(f"{g.get('rank')}. {name}{_change_mark(g)}")

        # 급상승 게임 별도 강조 (선행지표 핵심)
        risers = [
            g for g in report.steam_games
            if g.get("rank_change") is not None and g.get("rank_change") >= 3
        ]
        if risers:
            lines.append("")
            riser_names = [g.get("name") or str(g.get("appid")) for g in risers]
            lines.append(f"🚀 급상승 주목: {' / '.join(riser_names)}")
        lines.append("")

    # ── 섹션 4: 7일 추이 요약 (종합 + 엔터 통합) ──
    if report.trend_highlights:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 7일 추이 요약")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # 우선순위 순서로 표시
        tag_order = ["급상승", "신규", "지속상승", "하락세", "유지"]
        for tag in tag_order:
            keywords = report.trend_highlights.get(tag, [])
            if keywords:
                emoji = TAG_EMOJI.get(tag, "·")
                kw_str = " / ".join(keywords[:8])  # 최대 8개
                lines.append(f"{emoji} {tag}: {kw_str}")
        lines.append("")

    # ── 섹션 5: 어제 대비 변화 (종합 + 엔터 통합) ──
    if report.yesterday_diff is not None:
        diff = report.yesterday_diff
        if diff.new_keywords or diff.dropped_keywords:
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("🆚 어제 대비 변화")
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            if diff.new_keywords:
                lines.append(f"🆕 신규: {' / '.join(diff.new_keywords)}")
            if diff.dropped_keywords:
                lines.append(f"📤 이탈: {' / '.join(diff.dropped_keywords)}")
            lines.append("")

    # ── 푸터 ──
    lines.append("🤖 TrendRadar Bot v2")

    return "\n".join(lines)


def split_message(text: str) -> list[str]:
    """긴 메시지를 텔레그램 제한에 맞게 분할"""
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    messages: list[str] = []
    while text:
        if len(text) <= MAX_MESSAGE_LENGTH:
            messages.append(text)
            break

        # 줄바꿈 기준으로 분할
        cut_pos = text.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if cut_pos == -1:
            cut_pos = MAX_MESSAGE_LENGTH

        messages.append(text[:cut_pos])
        text = text[cut_pos:].lstrip("\n")

    return messages


async def send_message(text: str) -> bool:
    """
    임의 텍스트를 텔레그램으로 전송 (길이 초과 시 자동 분할)

    Args:
        text: 보낼 메시지

    Returns:
        전송 성공 여부
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("텔레그램 설정이 없습니다 (.env 확인)")
        return False

    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)
    messages = split_message(text)

    try:
        for msg in messages:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=None,  # 마크다운 파싱 오류 방지
            )
        logger.info(f"텔레그램 전송 완료 ({len(messages)}개 메시지 / {len(text)}자)")
        return True
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")
        return False


async def send_report(report: ReportData) -> bool:
    """
    텔레그램으로 리포트 전송

    Args:
        report: ReportData 구조체

    Returns:
        전송 성공 여부
    """
    return await send_message(format_report(report))
