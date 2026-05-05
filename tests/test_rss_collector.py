"""
RSS 수집기 단위 테스트
실행: pytest tests/test_rss_collector.py -v
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collector.rss_collector import (
    RSSFeed,
    RSS_FEEDS,
    _clean_html,
    _parse_published_date,
    _is_within_hours,
    _entry_to_trend_item,
    fetch_all_feeds,
)
from collector.trend_item import TrendItem


# ────────────────────────────────────────────────────────────
# _clean_html
# ────────────────────────────────────────────────────────────

class TestCleanHtml:
    def test_removes_tags(self):
        assert _clean_html("<b>제목</b>") == "제목"

    def test_decodes_entities(self):
        assert _clean_html("&amp; &lt; &gt;") == "& < >"

    def test_normalizes_whitespace(self):
        assert _clean_html("  공백   많은   텍스트  ") == "공백 많은 텍스트"

    def test_empty_string(self):
        assert _clean_html("") == ""

    def test_none_returns_empty(self):
        assert _clean_html(None) == ""  # type: ignore

    def test_nested_tags(self):
        assert _clean_html("<div><p>내용</p></div>") == "내용"


# ────────────────────────────────────────────────────────────
# _parse_published_date
# ────────────────────────────────────────────────────────────

class TestParsePublishedDate:
    def test_published_parsed(self):
        entry = {"published_parsed": (2026, 5, 6, 3, 0, 0, 0, 0, 0)}
        dt = _parse_published_date(entry)
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 5
        assert dt.day == 6

    def test_updated_parsed_fallback(self):
        entry = {"updated_parsed": (2026, 5, 5, 12, 0, 0, 0, 0, 0)}
        dt = _parse_published_date(entry)
        assert dt is not None
        assert dt.day == 5

    def test_rfc2822_string(self):
        entry = {"published": "Mon, 05 May 2026 14:30:00 +0900"}
        dt = _parse_published_date(entry)
        assert dt is not None
        assert dt.year == 2026

    def test_no_date_returns_none(self):
        entry = {"title": "제목만"}
        assert _parse_published_date(entry) is None

    def test_malformed_date_returns_none(self):
        entry = {"published": "invalid-date", "published_parsed": None}
        assert _parse_published_date(entry) is None


# ────────────────────────────────────────────────────────────
# _is_within_hours
# ────────────────────────────────────────────────────────────

class TestIsWithinHours:
    def test_recent_is_within(self):
        now = datetime.now(timezone.utc)
        assert _is_within_hours(now - timedelta(hours=1), 24) is True

    def test_old_is_not_within(self):
        now = datetime.now(timezone.utc)
        assert _is_within_hours(now - timedelta(hours=48), 24) is False

    def test_none_returns_true(self):
        """날짜 없으면 포함 (과수집 > 누락)"""
        assert _is_within_hours(None, 24) is True

    def test_naive_datetime_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        naive = (now - timedelta(hours=1)).replace(tzinfo=None)
        assert _is_within_hours(naive, 24) is True

    def test_boundary_exact(self):
        now = datetime.now(timezone.utc)
        boundary = now - timedelta(hours=24)
        assert _is_within_hours(boundary, 24) is True  # >= cutoff


# ────────────────────────────────────────────────────────────
# _entry_to_trend_item
# ────────────────────────────────────────────────────────────

MOCK_FEED = RSSFeed(
    name="test",
    label="테스트",
    url="https://example.com/rss",
    category="종합",
)


class TestEntryToTrendItem:
    def test_valid_entry(self):
        now = datetime.now(timezone.utc)
        entry = {
            "title": "테스트 뉴스 제목",
            "summary": "<p>뉴스 요약 <b>내용</b></p>",
            "link": "https://example.com/news/1",
            "published_parsed": now.timetuple()[:9],
        }
        item = _entry_to_trend_item(entry, MOCK_FEED)
        assert item is not None
        assert item.title == "테스트 뉴스 제목"
        assert item.summary == "뉴스 요약 내용"
        assert item.source == "rss:test"
        assert item.source_type == "rss"
        assert item.category == "종합"
        assert item.signal_score == 1.0
        assert item.url == "https://example.com/news/1"

    def test_empty_title_returns_none(self):
        entry = {"title": "", "link": "https://example.com"}
        assert _entry_to_trend_item(entry, MOCK_FEED) is None

    def test_old_entry_filtered(self):
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        entry = {
            "title": "오래된 뉴스",
            "published_parsed": old.timetuple()[:9],
        }
        assert _entry_to_trend_item(entry, MOCK_FEED) is None

    def test_long_summary_truncated(self):
        entry = {
            "title": "제목",
            "summary": "A" * 600,
            "published_parsed": datetime.now(timezone.utc).timetuple()[:9],
        }
        item = _entry_to_trend_item(entry, MOCK_FEED)
        assert item is not None
        assert len(item.summary) <= 500
        assert item.summary.endswith("...")

    def test_no_date_still_included(self):
        """날짜 없는 엔트리도 포함"""
        entry = {"title": "날짜 없는 뉴스", "link": "https://example.com"}
        item = _entry_to_trend_item(entry, MOCK_FEED)
        assert item is not None
        assert item.published_at == ""

    def test_raw_meta_contains_feed_info(self):
        entry = {
            "title": "테스트",
            "published_parsed": datetime.now(timezone.utc).timetuple()[:9],
        }
        item = _entry_to_trend_item(entry, MOCK_FEED)
        assert item is not None
        assert item.raw_meta["feed_name"] == "test"
        assert item.raw_meta["feed_label"] == "테스트"

    def test_html_in_title_cleaned(self):
        entry = {
            "title": "<b>볼드 제목</b> &amp; 특수문자",
            "published_parsed": datetime.now(timezone.utc).timetuple()[:9],
        }
        item = _entry_to_trend_item(entry, MOCK_FEED)
        assert item is not None
        assert item.title == "볼드 제목 & 특수문자"


# ────────────────────────────────────────────────────────────
# fetch_all_feeds (통합 테스트 - mock HTTP)
# ────────────────────────────────────────────────────────────

SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>테스트 피드</title>
<item>
  <title>첫 번째 뉴스</title>
  <link>https://example.com/1</link>
  <description>첫 번째 뉴스 요약</description>
  <pubDate>{pub_date}</pubDate>
</item>
<item>
  <title>두 번째 뉴스</title>
  <link>https://example.com/2</link>
  <description>두 번째 뉴스 요약</description>
  <pubDate>{pub_date}</pubDate>
</item>
</channel>
</rss>"""


def _make_rss_xml() -> bytes:
    """현재 시각 기준 RSS XML 생성"""
    now = datetime.now(timezone.utc)
    pub_str = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    return SAMPLE_RSS_XML.format(pub_date=pub_str).encode("utf-8")


class TestFetchAllFeeds:
    def test_empty_feeds_returns_empty(self):
        result = asyncio.get_event_loop().run_until_complete(
            fetch_all_feeds(feeds=[])
        )
        assert result == []

    @patch("collector.rss_collector.aiohttp.ClientSession")
    def test_successful_fetch(self, mock_session_cls):
        """정상 수집 시나리오"""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=_make_rss_xml())
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        test_feed = RSSFeed("mock", "모크", "https://mock.com/rss", "종합")
        result = asyncio.get_event_loop().run_until_complete(
            fetch_all_feeds(feeds=[test_feed])
        )
        assert len(result) == 2
        assert all(isinstance(item, TrendItem) for item in result)
        assert result[0].source == "rss:mock"

    @patch("collector.rss_collector.aiohttp.ClientSession")
    def test_http_error_skips_feed(self, mock_session_cls):
        """HTTP 에러 시 해당 피드만 건너뜀"""
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        test_feed = RSSFeed("fail", "실패", "https://fail.com/rss", "종합")
        result = asyncio.get_event_loop().run_until_complete(
            fetch_all_feeds(feeds=[test_feed])
        )
        assert result == []

    @patch("collector.rss_collector.aiohttp.ClientSession")
    def test_dedup_by_title(self, mock_session_cls):
        """동일 제목 다른 피드에서 수집 시 중복 제거"""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=_make_rss_xml())
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        # 동일 피드 2번 수집 (같은 XML 반환)
        feed_a = RSSFeed("a", "A", "https://a.com/rss", "종합")
        feed_b = RSSFeed("b", "B", "https://b.com/rss", "종합")
        result = asyncio.get_event_loop().run_until_complete(
            fetch_all_feeds(feeds=[feed_a, feed_b])
        )
        # 2피드 x 2기사 = 4건이지만 제목 동일하므로 2건
        assert len(result) == 2


# ────────────────────────────────────────────────────────────
# RSS_FEEDS 설정 검증
# ────────────────────────────────────────────────────────────

class TestRSSFeedsConfig:
    def test_minimum_feed_count(self):
        """최소 10개 피드 확보"""
        assert len(RSS_FEEDS) >= 10

    def test_all_feeds_have_required_fields(self):
        for feed in RSS_FEEDS:
            assert feed.name, f"{feed.label} name 비어있음"
            assert feed.url.startswith("http"), f"{feed.label} URL 비정상: {feed.url}"
            assert feed.category in ("종합", "경제", "IT", "속보"), (
                f"{feed.label} 카테고리 비정상: {feed.category}"
            )

    def test_unique_names(self):
        names = [f.name for f in RSS_FEEDS]
        assert len(names) == len(set(names)), "피드 name 중복 존재"

    def test_category_diversity(self):
        """최소 3개 카테고리 커버"""
        categories = {f.category for f in RSS_FEEDS}
        assert len(categories) >= 3
