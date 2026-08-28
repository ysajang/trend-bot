"""
장르 공백 스캐너 주간 실행 스크립트

전체 태그 조합 스캔은 API 호출이 많아(조합당 ~8회) 매일 돌리기 부적합하다.
주 1회 실행해 결과를 캐시에 저장하고, 일일 리포트는 그 캐시를 읽어 표시한다.

사용:
    python3 scan_genre_gaps.py            # 전체 조합 스캔
    python3 scan_genre_gaps.py --limit 30 # 조합 수 제한 (테스트용)

크론 예시 (매주 월요일 새벽 3시):
    0 3 * * 1 cd /home/seungjae/trend-bot && python3 scan_genre_gaps.py >> logs/genre_gap.log 2>&1
"""

import argparse
import asyncio
import logging
import sys

from collector.steam_genre_gap import scan_genre_gaps
from notifier.telegram import send_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def format_gap_report(gaps: list) -> str:
    """장르 공백 스캔 결과를 텔레그램 메시지로 포맷"""
    lines: list[str] = []
    lines.append("🎯 장르 공백 스캔 (주간)")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("1인 개발 관점 진입 기회 TOP 8")
    lines.append("")

    for i, g in enumerate(gaps[:8], 1):
        tags = f"{g.tag_names[0]}+{g.tag_names[1]}"
        lines.append(f"{i}. {tags} (점수 {g.opportunity_score:.0f})")
        lines.append(
            f"   경쟁 {g.supply}개 | 신작 중앙값 {g.median_reviews:.0f}리뷰 "
            f"| 긍정 {g.positive_ratio:.0%}"
        )
        if g.top_title:
            lines.append(f"   1등: {g.top_title} ({g.top_reviews:,}리뷰)")
        if g.recent_titles:
            lines.append(f"   최근작: {', '.join(g.recent_titles[:2])}")
        lines.append("")

    lines.append("※ 신작 중앙값 = 지금 새로 냈을 때의 현실적 기대치")
    lines.append("※ 1등 리뷰수 = 이 장르 시장의 상한선")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="조합 수 제한")
    parser.add_argument("--no-send", action="store_true", help="텔레그램 발송 생략")
    args = parser.parse_args()

    logger.info("장르 공백 스캔 시작")
    gaps = await scan_genre_gaps(max_combos=args.limit)

    if not gaps:
        logger.warning("유효한 조합 없음")
        return 1

    report = format_gap_report(gaps)
    print(report)

    if not args.no_send:
        sent = await send_message(report)
        logger.info(f"텔레그램 발송: {'성공' if sent else '실패'}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
