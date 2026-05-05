"""
TrendRadar Bot v2 - 메인 실행 스크립트

v2 아키텍처 (3-Layer):
  Layer 1: RSS 피드 수집 (트렌드 발견)
  Layer 2: Naver News API (트렌드 심화)
  Layer 3: Naver DataLab API (트렌드 검증)

현재 빌드 단계: D (Layer 1 + 2 + 3 배선 완료)
  - analyzer/notifier 재배선: 단계 E/F에서 구현
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

from collector import TrendItem
from collector.rss_collector import fetch_all_feeds
from collector.naver_news import search_by_keywords
from collector.naver_datalab import verify_trends
from analyzer.keyword_extractor import analyze_keywords

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


async def run() -> int:
    """
    메인 파이프라인

    Returns:
        종료 코드 (0=정상 / 1=수집 실패 / 2=처리 실패)
    """
    logger.info("=" * 50)
    logger.info("TrendRadar Bot v2 실행 시작")
    logger.info("=" * 50)

    # ── Layer 1: 트렌드 발견 (RSS) ──
    layer1_items = await fetch_all_feeds()

    if not layer1_items:
        logger.warning("Layer 1 수집 실패 (RSS 피드 전체 실패 가능성)")
        return 1

    logger.info(f"Layer 1 완료: {len(layer1_items)}건")

    # ── 키워드 추출 (Layer 1 → Layer 2/3 브릿지) ──
    layer1_dicts = [item.to_dict() for item in layer1_items]
    keyword_results = analyze_keywords(layer1_dicts)
    seed_keywords = [kw.keyword for kw in keyword_results]
    logger.info(f"키워드 추출: TOP {len(seed_keywords)}개 → {seed_keywords[:10]}")

    # ── Layer 2: 트렌드 심화 (Naver News) ──
    layer2_items: list[TrendItem] = []
    if seed_keywords:
        layer2_items = await search_by_keywords(
            keywords=seed_keywords[:10],
            include_categories=True,
        )
        logger.info(f"Layer 2 완료: {len(layer2_items)}건")
    else:
        logger.warning("추출된 키워드 없음 → Layer 2 건너뜀")

    # ── Layer 3: 트렌드 검증 (DataLab) ──
    layer3_items: list[TrendItem] = []
    if seed_keywords:
        layer3_items = await verify_trends(
            keywords=seed_keywords[:20],  # TOP 20 키워드 검증 (4 API calls)
        )
        logger.info(f"Layer 3 완료: {len(layer3_items)}건")
    else:
        logger.warning("추출된 키워드 없음 → Layer 3 건너뜀")

    # ── 통합 통계 ──
    logger.info(
        f"전체 수집 완료: "
        f"L1 {len(layer1_items)}건 + L2 {len(layer2_items)}건 + L3 {len(layer3_items)}건 = "
        f"{len(layer1_items) + len(layer2_items) + len(layer3_items)}건"
    )

    # 카테고리별 통계
    cat_counts: dict[str, int] = {}
    for item in layer1_items:
        cat_counts[item.category] = cat_counts.get(item.category, 0) + 1
    for cat, cnt in sorted(cat_counts.items()):
        logger.info(f"  [RSS:{cat}] {cnt}건")

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
