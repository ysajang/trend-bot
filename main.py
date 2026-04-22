"""
TrendRadar Bot - 메인 실행 스크립트
cron으로 매일 실행 → 크롤링 → 분석 → AI 인사이트 → 텔레그램 전송
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

from crawler.dcinside import crawl_realtime_best
from analyzer.keyword_extractor import analyze_keywords, format_keywords_for_ai
from analyzer.ai_insight import generate_full_insight
from notifier.telegram import build_report, send_report
from storage.json_store import save_daily_data, cleanup_old_data

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


async def run():
    logger.info("=" * 50)
    logger.info("TrendRadar Bot 실행 시작")
    logger.info("=" * 50)

    # 1. 크롤링
    logger.info("[1/5] 디시인사이드 실시간베스트 크롤링...")
    try:
        posts = await crawl_realtime_best(max_pages=3)
    except Exception as e:
        logger.error(f"크롤링 실패: {e}")
        await send_report(f"⚠️ TrendRadar 크롤링 실패\n{e}")
        return

    if not posts:
        logger.warning("수집된 게시글이 없습니다")
        await send_report("⚠️ TrendRadar: 수집된 게시글 0개 - 크롤러 점검 필요")
        return

    posts_data = [p.to_dict() for p in posts]
    logger.info(f"수집 완료: {len(posts_data)}개")

    # 2. 키워드 추출
    logger.info("[2/5] 키워드 추출 중...")
    try:
        keywords = analyze_keywords(posts_data)
        keywords_data = [kw.to_dict() for kw in keywords]
    except Exception as e:
        logger.error(f"키워드 추출 실패: {e}")
        keywords = []
        keywords_data = []

    # 3. 데이터 저장
    logger.info("[3/5] 데이터 저장 중...")
    save_daily_data(posts_data, keywords_data)
    cleanup_old_data(keep_days=14)

    # 4. AI 인사이트 생성
    logger.info("[4/5] AI 인사이트 생성 중...")
    try:
        raw_data = format_keywords_for_ai(keywords, posts_data)
        insight = generate_full_insight(raw_data)
    except Exception as e:
        logger.error(f"AI 인사이트 생성 실패: {e}")
        insight = "⚠️ AI 인사이트 생성 실패 - 키워드 데이터만 전송합니다"

    # 5. 텔레그램 전송
    logger.info("[5/5] 텔레그램 전송 중...")
    keyword_summary = "\n".join(
        f"  {i}. {kw.keyword} ({kw.count}회)"
        for i, kw in enumerate(keywords[:10], 1)
    ) if keywords else "(키워드 추출 실패)"

    report = build_report(
        insight=insight,
        keyword_summary=keyword_summary,
        post_count=len(posts_data),
    )
    success = await send_report(report)

    if success:
        logger.info("✅ TrendRadar 실행 완료 - 리포트 전송 성공")
    else:
        logger.error("❌ TrendRadar 실행 완료 - 리포트 전송 실패")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
