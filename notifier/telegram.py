"""
텔레그램 알림 발송
- 트렌드 리포트를 텔레그램 메시지로 전송
- 긴 메시지는 분할 전송
"""

import logging
from datetime import datetime, timezone, timedelta

import telegram

import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
MAX_MESSAGE_LENGTH = 4096  # 텔레그램 메시지 최대 길이


def build_report(
    insight: str,
    keyword_summary: str,
    post_count: int,
) -> str:
    """텔레그램 리포트 메시지 생성"""
    now = datetime.now(KST)
    date_str = now.strftime("%Y.%m.%d %H:%M")

    report = f"""📊 커뮤니티 트렌드 리포트
📅 {date_str}
📝 분석 대상: 디시인사이드 실시간베스트 {post_count}개

━━━━━━━━━━━━━━━━━━━━

{insight}

━━━━━━━━━━━━━━━━━━━━

📈 키워드 빈도 요약
{keyword_summary}

━━━━━━━━━━━━━━━━━━━━
🤖 TrendRadar Bot"""

    return report


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


async def send_report(report: str) -> bool:
    """텔레그램으로 리포트 전송"""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("텔레그램 설정이 없습니다 (.env 확인)")
        return False

    bot = telegram.Bot(token=config.TELEGRAM_BOT_TOKEN)

    messages = split_message(report)

    try:
        for msg in messages:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode=None,  # 마크다운 파싱 오류 방지
            )
        logger.info(f"텔레그램 전송 완료 ({len(messages)}개 메시지)")
        return True
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")
        return False
