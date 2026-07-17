"""
TrendRadar Bot v2 - 메인 실행 스크립트

v2 아키텍처 (3-Layer + 규칙기반 리포트):
  Layer 1: RSS 피드 수집 (트렌드 발견)
  Layer 2: Naver News API (트렌드 심화)
  Layer 3: Naver DataLab API (트렌드 검증)
  Report: 규칙기반 리포트 생성 (LLM 미사용)
  Notify: 텔레그램 리포트 발송

빌드 단계: F (전체 파이프라인 완료)
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

import config
from collector import TrendItem
from collector.rss_collector import fetch_all_feeds, ENTERTAINMENT_CATEGORIES
from collector.naver_news import search_by_keywords
from collector.naver_datalab import verify_trends
from analyzer.keyword_extractor import analyze_keywords
from analyzer.report_builder import build_report
from storage.json_store import save_daily_data, load_yesterday_keywords, cleanup_old_data
from notifier.telegram import format_report, send_report

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

# httpx 로그 억제 (텔레그램 API 호출 시 봇 토큰 평문 노출 방지)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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
    # 종합 TOP: 엔터 카테고리 제외 (정치·경제·사회 집중)
    keyword_results = analyze_keywords(
        layer1_dicts, exclude_categories=ENTERTAINMENT_CATEGORIES,
    )
    seed_keywords = [kw.keyword for kw in keyword_results]
    logger.info(f"키워드 추출: TOP {len(seed_keywords)}개 → {seed_keywords[:10]}")

    # 엔터/문화/게임 TOP: 엔터 카테고리만 집계 (엔터 전용 불용어 적용)
    ent_keyword_results = analyze_keywords(
        layer1_dicts,
        include_categories=ENTERTAINMENT_CATEGORIES,
        extra_stopwords=config.ENTERTAINMENT_STOPWORDS,
    )
    ent_seed_keywords = [kw.keyword for kw in ent_keyword_results[:5]]
    logger.info(f"엔터 키워드 추출: TOP {len(ent_seed_keywords)}개 → {ent_seed_keywords}")

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
    # 종합 TOP 20 + 엔터 TOP 5 를 함께 검증 (중복 제거)
    verify_targets = list(dict.fromkeys(seed_keywords[:20] + ent_seed_keywords))
    if verify_targets:
        layer3_items = await verify_trends(keywords=verify_targets)
        logger.info(f"Layer 3 완료: {len(layer3_items)}건")
    else:
        logger.warning("추출된 키워드 없음 → Layer 3 건너뜀")

    # ── 통합 통계 ──
    total = len(layer1_items) + len(layer2_items) + len(layer3_items)
    logger.info(
        f"전체 수집 완료: "
        f"L1 {len(layer1_items)}건 + L2 {len(layer2_items)}건 + L3 {len(layer3_items)}건 = "
        f"{total}건"
    )

    # ── 어제 데이터 로드 (대비 비교용) ──
    yesterday_keywords = load_yesterday_keywords()

    # ── 데이터 저장 (오늘) ──
    save_daily_data(
        layer1_items, layer2_items, layer3_items,
        keyword_results + ent_keyword_results[:5],  # 종합 + 엔터 통합 저장 (diff용)
    )

    # ── 오래된 데이터 정리 ──
    cleanup_old_data(keep_days=7)

    # ── 규칙기반 리포트 생성 ──
    report = build_report(
        layer1_items=layer1_items,
        layer2_items=layer2_items,
        layer3_items=layer3_items,
        keyword_results=keyword_results,
        yesterday_keywords=yesterday_keywords,
        entertainment_results=ent_keyword_results,
    )

    # ── 텔레그램 발송 ──
    formatted = format_report(report)
    logger.info(f"리포트 생성 완료 ({len(formatted)}자)")
    logger.info(f"\n{formatted}")

    sent = await send_report(report)
    if sent:
        logger.info("텔레그램 발송 완료")
    else:
        logger.warning("텔레그램 발송 실패 또는 설정 없음")

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
