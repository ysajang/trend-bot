"""
Layer 2 — Naver News API 수집기

Layer 1(RSS)에서 추출된 TOP 키워드로 네이버 뉴스 검색 API를 호출하여
트렌드의 맥락과 깊이를 확보합니다.

API 문서: https://developers.naver.com/docs/serviceapi/search/news/news.md
일일 한도: 25,000회 (별도 카운트)
초당 한도: 10 req/s (안전 마진 적용 → 9 req/s)
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp

from collector.trend_item import TrendItem
import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# HTML 태그 + 하이라이트 태그 제거
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")

# API 엔드포인트
NAVER_NEWS_API_URL = "https://openapi.naver.com/v1/search/news.json"

# 카테고리 광역 검색 키워드 (Layer 1 키워드와 병행)
CATEGORY_SEARCH_TERMS = ["경제", "IT 기술", "사회", "정치", "연예"]


def _clean_naver_text(text: str) -> str:
    """네이버 API 응답 텍스트 정제 (<b> 하이라이트 등 제거)"""
    if not text:
        return ""
    # 1차: 실제 HTML 태그 제거 (빈 문자열로 → <b>삼성</b>전자 = 삼성전자)
    text = HTML_TAG_PATTERN.sub("", text)
    # 2차: 엔티티 디코딩 (&amp; → & / &lt; → <)
    text = html.unescape(text)
    # 3차: 엔티티로 인코딩되어 있던 태그가 디코딩 후 드러난 경우 제거
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def _parse_naver_date(date_str: str) -> str:
    """
    네이버 API 날짜 → ISO8601 KST 변환
    입력 형식: "Mon, 05 May 2026 14:30:00 +0900"
    """
    if not date_str:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST).isoformat()
    except (ValueError, TypeError):
        return ""


def _news_item_to_trend_item(
    item: dict,
    search_keyword: str,
) -> Optional[TrendItem]:
    """네이버 뉴스 API 응답 아이템 → TrendItem 변환"""
    title = _clean_naver_text(item.get("title", ""))
    if not title:
        return None

    description = _clean_naver_text(item.get("description", ""))
    # 너무 긴 description 자르기
    if len(description) > 500:
        description = description[:497] + "..."

    return TrendItem(
        source="naver_news",
        source_type="naver_news",
        title=title,
        summary=description,
        url=item.get("originallink", item.get("link", "")),
        published_at=_parse_naver_date(item.get("pubDate", "")),
        category="",  # 네이버 뉴스 API는 카테고리 미제공
        signal_score=0.0,  # 단계 E에서 발행빈도 기반 재계산
        keywords=[search_keyword],
        raw_meta={
            "naver_link": item.get("link", ""),
            "search_keyword": search_keyword,
        },
    )


async def _search_news(
    session: aiohttp.ClientSession,
    keyword: str,
    display: int = 10,
    sort: str = "date",
) -> list[TrendItem]:
    """
    단일 키워드로 네이버 뉴스 검색

    Args:
        session: aiohttp 세션
        keyword: 검색 키워드
        display: 결과 수 (최대 100)
        sort: 정렬 ("date" 최신순 / "sim" 정확도순)

    Returns:
        TrendItem 리스트
    """
    items: list[TrendItem] = []

    if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
        logger.error("NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 설정되지 않았습니다")
        return items

    params = {
        "query": keyword,
        "display": min(display, 100),
        "start": 1,
        "sort": sort,
    }
    headers = {
        "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }

    try:
        async with session.get(
            NAVER_NEWS_API_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 429:
                logger.warning(f"[{keyword}] 429 Too Many Requests - QPS 초과")
                return items
            if resp.status != 200:
                body = await resp.text()
                logger.warning(f"[{keyword}] HTTP {resp.status}: {body[:200]}")
                return items

            data = await resp.json()
            api_items = data.get("items", [])

            for api_item in api_items:
                trend_item = _news_item_to_trend_item(api_item, keyword)
                if trend_item is not None:
                    items.append(trend_item)

            logger.info(f"[naver_news:{keyword}] {len(items)}건 수집")

    except asyncio.TimeoutError:
        logger.warning(f"[{keyword}] 타임아웃")
    except aiohttp.ClientError as e:
        logger.warning(f"[{keyword}] 연결 오류: {e}")
    except Exception as e:
        logger.exception(f"[{keyword}] 예상치 못한 오류: {e}")

    return items


async def search_by_keywords(
    keywords: list[str],
    per_keyword: int = 0,
    include_categories: bool = True,
) -> list[TrendItem]:
    """
    키워드 리스트로 네이버 뉴스 검색 (Layer 2 메인 함수)

    Args:
        keywords: Layer 1에서 추출된 TOP 키워드 리스트
        per_keyword: 키워드당 검색 결과 수 (0이면 config 기본값)
        include_categories: 카테고리 광역 검색 병행 여부

    Returns:
        TrendItem 리스트 (중복 제거 + 최신순 정렬)
    """
    if not keywords:
        logger.warning("검색할 키워드가 없습니다")
        return []

    display = per_keyword if per_keyword > 0 else config.NAVER_NEWS_PER_QUERY

    # 검색 대상 구성: 키워드 + (선택) 카테고리 광역
    search_terms = list(keywords)
    if include_categories:
        search_terms.extend(CATEGORY_SEARCH_TERMS)

    logger.info(
        f"Naver News 검색 시작: 키워드 {len(keywords)}개"
        + (f" + 카테고리 {len(CATEGORY_SEARCH_TERMS)}개" if include_categories else "")
    )

    all_items: list[TrendItem] = []

    async with aiohttp.ClientSession() as session:
        # QPS 제한 준수: 배치로 나눠서 호출
        batch_size = config.NAVER_QPS_LIMIT  # 9
        for i in range(0, len(search_terms), batch_size):
            batch = search_terms[i:i + batch_size]
            tasks = [
                _search_news(session, term, display=display)
                for term in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.exception(f"[{batch[j]}] gather 예외: {result}")
                elif isinstance(result, list):
                    all_items.extend(result)

            # 배치 간 1초 대기 (QPS 준수)
            if i + batch_size < len(search_terms):
                await asyncio.sleep(1.0)

    # 제목 기반 중복 제거
    seen_titles: set[str] = set()
    deduped: list[TrendItem] = []
    for item in all_items:
        normalized = item.title.replace(" ", "").lower()
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            deduped.append(item)

    # 최신순 정렬
    deduped.sort(key=lambda x: x.published_at or "", reverse=True)

    logger.info(
        f"Naver News 수집 완료: 총 {len(all_items)}건 → 중복 제거 후 {len(deduped)}건"
    )
    return deduped
