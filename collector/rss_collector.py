"""
Layer 1 — RSS 수집기

주요 언론사 RSS 피드에서 최근 N시간 헤드라인을 수집하고
TrendItem 형식으로 반환합니다.

법적 근거:
- RSS는 언론사가 공개 배포 목적으로 제공하는 서비스
- 개인 구독 용도 사용 (헤드라인 + 요약만 / 본문 스크래핑 금지)
"""

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

import aiohttp
import feedparser

from collector.trend_item import TrendItem
import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# HTML 태그 제거 패턴 (summary 정제용)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class RSSFeed:
    """RSS 피드 설정"""
    name: str           # 식별자 (예: "hani")
    label: str          # 표시명 (예: "한겨레")
    url: str            # RSS URL
    category: str       # "종합"|"경제"|"IT"|"속보"|"게임"|"문화"|"연예"


# ──────────────────────────────────────────────
# 확정 피드 목록 (2026-05-06 활성 검증 완료)
# 엔터/문화/게임 소스 추가 (2026-07-17 활성 검증 완료)
# ──────────────────────────────────────────────
RSS_FEEDS: list[RSSFeed] = [
    # 종합
    RSSFeed("hani",     "한겨레",   "https://www.hani.co.kr/rss/",                                     "종합"),
    RSSFeed("khan",     "경향신문", "https://www.khan.co.kr/rss/rssdata/total_news.xml",                "종합"),
    RSSFeed("donga",    "동아일보", "https://rss.donga.com/total.xml",                                  "종합"),
    RSSFeed("sbs",      "SBS",      "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=01",      "종합"),
    RSSFeed("yna",      "연합뉴스", "https://www.yna.co.kr/rss/news.xml",                               "종합"),
    RSSFeed("chosun",   "조선일보", "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",     "종합"),
    # 경제
    RSSFeed("mk",       "매일경제", "https://www.mk.co.kr/rss/30000001/",                               "경제"),
    RSSFeed("hankyung", "한국경제", "https://www.hankyung.com/feed/all-news",                            "경제"),
    # IT
    RSSFeed("zdnet",    "ZDNet",    "https://feeds.feedburner.com/zdkorea",                             "IT"),
    RSSFeed("etnews",   "전자신문", "https://rss.etnews.com/Section901.xml",                             "IT"),
    # 속보
    RSSFeed("newsis",   "뉴시스",   "https://www.newsis.com/RSS/sokbo.xml",                              "속보"),
    # 게임 (엔터 트렌드용)
    RSSFeed("inven",    "인벤",     "http://webzine.inven.co.kr/news/rss.php",                          "게임"),
    RSSFeed("ruliweb",  "루리웹",   "https://bbs.ruliweb.com/news/rss",                                 "게임"),
    # 문화
    RSSFeed("khan_cul", "경향문화", "https://www.khan.co.kr/rss/rssdata/culture_news.xml",              "문화"),
    # 연예
    RSSFeed("hk_ent",   "한경연예", "https://www.hankyung.com/feed/entertainment",                       "연예"),
    # IT소비 (엔터/소비 트렌드용)
    RSSFeed("hk_it",    "한경IT",   "https://www.hankyung.com/feed/it",                                 "IT"),
]

# 엔터/문화 트렌드 섹션에 포함할 카테고리
ENTERTAINMENT_CATEGORIES = {"게임", "문화", "연예"}


def _clean_html(text: str) -> str:
    """HTML 태그 제거 + 엔티티 디코드 + 공백 정규화"""
    if not text:
        return ""
    # 태그 먼저 제거 (unescape 먼저 하면 &lt;X&gt;가 <X>로 변환되어 태그로 오인)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = html.unescape(text)
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def _parse_published_date(entry: dict) -> Optional[datetime]:
    """
    feedparser 엔트리에서 발행일 파싱

    feedparser는 published_parsed / updated_parsed를 struct_time으로 제공하지만
    일부 피드는 비표준 형식이라 실패할 수 있음 -> fallback으로 원본 문자열 파싱
    """
    # 1차: feedparser가 이미 파싱한 struct_time 사용
    for field_name in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field_name)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue

    # 2차: 원본 문자열에서 RFC 2822 파싱
    for field_name in ("published", "updated"):
        raw = entry.get(field_name, "")
        if raw:
            try:
                return parsedate_to_datetime(raw)
            except (ValueError, TypeError):
                continue

    return None


def _is_within_hours(dt: Optional[datetime], hours: int) -> bool:
    """발행일이 최근 N시간 이내인지 확인 (1초 마진 포함)"""
    if dt is None:
        # 날짜 정보 없으면 포함 (누락보다 과수집이 안전)
        return True
    now = datetime.now(timezone.utc)
    # 1초 마진: RSS pubDate 정밀도가 초 단위이므로 경계값 안전 처리
    cutoff = now - timedelta(hours=hours, seconds=1)
    # timezone-naive인 경우 UTC로 간주
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def _entry_to_trend_item(entry: dict, feed: RSSFeed) -> Optional[TrendItem]:
    """feedparser 엔트리 → TrendItem 변환"""
    title = _clean_html(entry.get("title", ""))
    if not title:
        return None

    # 발행일 파싱 + 필터링
    pub_dt = _parse_published_date(entry)
    if not _is_within_hours(pub_dt, config.RSS_RECENT_HOURS):
        return None

    # published_at ISO8601 KST 변환
    published_at = ""
    if pub_dt:
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        published_at = pub_dt.astimezone(KST).isoformat()

    # summary (본문 X / 피드 제공 요약만)
    summary = _clean_html(entry.get("summary", entry.get("description", "")))
    # 너무 긴 summary 자르기 (트렌드 분석에 500자 이상 불필요)
    if len(summary) > 500:
        summary = summary[:497] + "..."

    url = entry.get("link", "")

    return TrendItem(
        source=f"rss:{feed.name}",
        source_type="rss",
        title=title,
        summary=summary,
        url=url,
        published_at=published_at,
        category=feed.category,
        signal_score=1.0,  # RSS는 고정 1.0 (Layer 2/3에서 재조정)
        raw_meta={
            "feed_label": feed.label,
            "feed_name": feed.name,
        },
    )


async def _fetch_single_feed(
    session: aiohttp.ClientSession,
    feed: RSSFeed,
) -> list[TrendItem]:
    """단일 RSS 피드 수집 (에러 시 빈 리스트 반환 / 나머지 피드 계속 진행)"""
    items: list[TrendItem] = []
    try:
        async with session.get(
            feed.url,
            timeout=aiohttp.ClientTimeout(total=config.RSS_FETCH_TIMEOUT_SEC),
            headers={"User-Agent": "TrendRadar/2.0 (RSS Reader)"},
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[{feed.name}] HTTP {resp.status} - 건너뜀")
                return items

            raw_bytes = await resp.read()

            # feedparser는 bytes도 받음 (인코딩 자동 감지)
            parsed = feedparser.parse(raw_bytes)

            if parsed.bozo and not parsed.entries:
                logger.warning(
                    f"[{feed.name}] RSS 파싱 실패: {parsed.bozo_exception} - 건너뜀"
                )
                return items

            for entry in parsed.entries:
                item = _entry_to_trend_item(entry, feed)
                if item is not None:
                    items.append(item)

            logger.info(f"[{feed.name}] {len(items)}건 수집 (전체 {len(parsed.entries)}건 중)")

    except asyncio.TimeoutError:
        logger.warning(f"[{feed.name}] 타임아웃 ({config.RSS_FETCH_TIMEOUT_SEC}s) - 건너뜀")
    except aiohttp.ClientError as e:
        logger.warning(f"[{feed.name}] 연결 오류: {e} - 건너뜀")
    except Exception as e:
        logger.exception(f"[{feed.name}] 예상치 못한 오류: {e}")

    return items


async def fetch_all_feeds(
    feeds: Optional[list[RSSFeed]] = None,
) -> list[TrendItem]:
    """
    모든 RSS 피드 병렬 수집

    Args:
        feeds: 수집할 피드 리스트 (None이면 기본 RSS_FEEDS 전체)

    Returns:
        TrendItem 리스트 (발행일 최신순 정렬)
    """
    if feeds is None:
        feeds = RSS_FEEDS

    if not feeds:
        logger.warning("수집할 RSS 피드가 없습니다")
        return []

    logger.info(f"RSS 수집 시작: {len(feeds)}개 피드")

    all_items: list[TrendItem] = []

    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_single_feed(session, feed) for feed in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.exception(f"[{feeds[i].name}] gather 예외: {result}")
            elif isinstance(result, list):
                all_items.extend(result)

    # 제목 기반 단순 중복 제거 (동일 제목 다른 피드 재배포 대응)
    seen_titles: set[str] = set()
    deduped: list[TrendItem] = []
    for item in all_items:
        # 정규화: 공백 제거 후 비교
        normalized = item.title.replace(" ", "").lower()
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            deduped.append(item)

    # 최신순 정렬 (published_at이 없으면 뒤로)
    deduped.sort(key=lambda x: x.published_at or "", reverse=True)

    logger.info(
        f"RSS 수집 완료: 총 {len(all_items)}건 → 중복 제거 후 {len(deduped)}건"
    )
    return deduped
