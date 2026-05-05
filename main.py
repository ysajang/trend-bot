"""
TrendRadar Bot v2 - 메인 실행 스크립트

v2 아키텍처 (3-Layer):
  Layer 1: RSS 피드 수집 (트렌드 발견)
  Layer 2: Naver News API (트렌드 심화)
  Layer 3: Naver DataLab API (트렌드 검증)

현재 빌드 단계: B (Layer 1 RSS 수집기 배선 완료)
  - Layer 2/3: 단계 C/D에서 구현
  - analyzer/notifier 재배선: 단계 E/F에서 구현
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

from collector import TrendItem
from collector.rss_collector import fetch_all_feeds

KST = timezone(timedelta(hours=9))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"trend-bot_{datetime.now(KST).strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("main")


async def collect_layer2_naver_news(seed_keywords: list[str]) -> list[TrendItem]:
    """Layer 2 Naver News API - 단계 C에서 구현"""
    logger.info("[Layer 2] Naver News 수집기 미구현 (단계 C 대기)")
    return []


async def collect_layer3_datalab(keywords: list[str]) -> list[TrendItem]:
    """Layer 3 DataLab 검증 - 단계 D에서 구현"""
    logger.info("[Layer 3] DataLab 검증기 미구현 (단계 D 대기)")
    return []


async def run() -> int:
    """
    메인 파이프라인

    Returns:
        종료 코드 (0=정상 / 1=수집 실패 / 2=처리 실패)
    """
    logger.info("=" * 50)
    logger.info("TrendRadar Bot v2 실행 시작")
    logger.info("=" * 50)

    # Layer 1: 트렌드 발견 (RSS)
    layer1_items = await fetch_all_feeds()

    # Layer 2/3는 단계 C/D에서 배선
    # layer2_items = await collect_layer2_naver_news([...])
    # layer3_items = await collect_layer3_datalab([...])

    if not layer1_items:
        logger.warning("수집된 TrendItem이 없습니다 (RSS 피드 전체 실패 가능성)")
        return 1

    logger.info(f"수집 완료: Layer 1 {len(layer1_items)}건")

    # 카테고리별 통계
    cat_counts: dict[str, int] = {}
    for item in layer1_items:
        cat_counts[item.category] = cat_counts.get(item.category, 0) + 1
    for cat, cnt in sorted(cat_counts.items()):
        logger.info(f"  [{cat}] {cnt}건")

    # analyzer / notifier 재배선은 단계 E/F에서 구현
    logger.info("analyzer / notifier 재배선 단계 E/F 대기 중")
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(run())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("사용자 중단")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"예상치 못한 오류: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
