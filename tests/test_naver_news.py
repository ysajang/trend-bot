"""
Naver News API 수집기 단위 테스트
실행: pytest tests/test_naver_news.py -v
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collector.naver_news import (
    _clean_naver_text,
    _parse_naver_date,
    _news_item_to_trend_item,
    search_by_keywords,
    CATEGORY_SEARCH_TERMS,
    NAVER_NEWS_API_URL,
)
from collector.trend_item import TrendItem


# ────────────────────────────────────────────────────────────
# _clean_naver_text
# ────────────────────────────────────────────────────────────

class TestCleanNaverText:
    def test_removes_bold_highlight(self):
        assert _clean_naver_text("<b>삼성</b>전자 신제품") == "삼성전자 신제품"

    def test_removes_nested_tags(self):
        assert _clean_naver_text("<div><b>제목</b></div>") == "제목"

    def test_decodes_entities(self):
        assert _clean_naver_text("A &amp; B") == "A & B"

    def test_normalizes_whitespace(self):
        assert _clean_naver_text("  여러   공백  ") == "여러 공백"

    def test_empty_returns_empty(self):
        assert _clean_naver_text("") == ""
        assert _clean_naver_text(None) == ""  # type: ignore


# ────────────────────────────────────────────────────────────
# _parse_naver_date
# ────────────────────────────────────────────────────────────

class TestParseNaverDate:
    def test_valid_rfc2822(self):
        result = _parse_naver_date("Mon, 05 May 2026 14:30:00 +0900")
        assert result != ""
        assert "2026-05-05" in result
        assert "+09:00" in result

    def test_empty_returns_empty(self):
        assert _parse_naver_date("") == ""

    def test_invalid_returns_empty(self):
        assert _parse_naver_date("not-a-date") == ""


# ────────────────────────────────────────────────────────────
# _news_item_to_trend_item
# ────────────────────────────────────────────────────────────

class TestNewsItemToTrendItem:
    def test_valid_item(self):
        api_item = {
            "title": "<b>AI</b> 반도체 수출 급증",
            "description": "한국 <b>AI</b> 반도체 수출이 전년 대비 50% 증가했다",
            "originallink": "https://example.com/news/1",
            "link": "https://n.news.naver.com/123",
            "pubDate": "Mon, 05 May 2026 10:00:00 +0900",
        }
        item = _news_item_to_trend_item(api_item, "AI 반도체")
        assert item is not None
        assert item.title == "AI 반도체 수출 급증"
        assert item.source == "naver_news"
        assert item.source_type == "naver_news"
        assert item.url == "https://example.com/news/1"
        assert item.keywords == ["AI 반도체"]
        assert item.raw_meta["search_keyword"] == "AI 반도체"

    def test_empty_title_returns_none(self):
        api_item = {"title": "", "link": "https://example.com"}
        assert _news_item_to_trend_item(api_item, "test") is None

    def test_html_cleaned_in_title_and_description(self):
        api_item = {
            "title": "<b>테스트</b> &amp; 제목",
            "description": "<b>요약</b> 내용 &lt;태그&gt;",
            "link": "https://example.com",
            "pubDate": "Mon, 05 May 2026 10:00:00 +0900",
        }
        item = _news_item_to_trend_item(api_item, "테스트")
        assert item is not None
        assert item.title == "테스트 & 제목"
        assert "<" not in item.summary

    def test_long_description_truncated(self):
        api_item = {
            "title": "제목",
            "description": "A" * 600,
            "link": "https://example.com",
        }
        item = _news_item_to_trend_item(api_item, "test")
        assert item is not None
        assert len(item.summary) <= 500
        assert item.summary.endswith("...")

    def test_fallback_to_link_when_no_originallink(self):
        api_item = {
            "title": "제목",
            "link": "https://n.news.naver.com/456",
        }
        item = _news_item_to_trend_item(api_item, "test")
        assert item is not None
        assert item.url == "https://n.news.naver.com/456"

    def test_naver_link_in_raw_meta(self):
        api_item = {
            "title": "제목",
            "originallink": "https://example.com/1",
            "link": "https://n.news.naver.com/1",
        }
        item = _news_item_to_trend_item(api_item, "test")
        assert item is not None
        assert item.raw_meta["naver_link"] == "https://n.news.naver.com/1"


# ────────────────────────────────────────────────────────────
# search_by_keywords (통합 테스트 - mock HTTP)
# ────────────────────────────────────────────────────────────

SAMPLE_API_RESPONSE = {
    "lastBuildDate": "Mon, 05 May 2026 10:00:00 +0900",
    "total": 2,
    "start": 1,
    "display": 2,
    "items": [
        {
            "title": "<b>삼성</b>전자 신제품 발표",
            "originallink": "https://example.com/1",
            "link": "https://n.news.naver.com/1",
            "description": "<b>삼성</b>전자가 새 갤럭시를 공개",
            "pubDate": "Mon, 05 May 2026 09:00:00 +0900",
        },
        {
            "title": "<b>삼성</b> 반도체 투자 확대",
            "originallink": "https://example.com/2",
            "link": "https://n.news.naver.com/2",
            "description": "반도체 시설 투자 10조원 규모",
            "pubDate": "Mon, 05 May 2026 08:00:00 +0900",
        },
    ],
}


class TestSearchByKeywords:
    def test_empty_keywords_returns_empty(self):
        result = asyncio.get_event_loop().run_until_complete(
            search_by_keywords([], include_categories=False)
        )
        assert result == []

    @patch("collector.naver_news.config")
    @patch("collector.naver_news.aiohttp.ClientSession")
    def test_successful_search(self, mock_session_cls, mock_config):
        """정상 검색 시나리오"""
        mock_config.NAVER_CLIENT_ID = "test_id"
        mock_config.NAVER_CLIENT_SECRET = "test_secret"
        mock_config.NAVER_NEWS_PER_QUERY = 10
        mock_config.NAVER_QPS_LIMIT = 9

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=SAMPLE_API_RESPONSE)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = asyncio.get_event_loop().run_until_complete(
            search_by_keywords(["삼성"], include_categories=False)
        )
        assert len(result) == 2
        assert all(isinstance(item, TrendItem) for item in result)
        assert result[0].source_type == "naver_news"

    @patch("collector.naver_news.config")
    @patch("collector.naver_news.aiohttp.ClientSession")
    def test_429_skips_keyword(self, mock_session_cls, mock_config):
        """429 Too Many Requests 시 해당 키워드 건너뜀"""
        mock_config.NAVER_CLIENT_ID = "test_id"
        mock_config.NAVER_CLIENT_SECRET = "test_secret"
        mock_config.NAVER_NEWS_PER_QUERY = 10
        mock_config.NAVER_QPS_LIMIT = 9

        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = asyncio.get_event_loop().run_until_complete(
            search_by_keywords(["AI"], include_categories=False)
        )
        assert result == []

    @patch("collector.naver_news.config")
    @patch("collector.naver_news.aiohttp.ClientSession")
    def test_dedup_by_title(self, mock_session_cls, mock_config):
        """동일 제목 다른 키워드 검색 결과 중복 제거"""
        mock_config.NAVER_CLIENT_ID = "test_id"
        mock_config.NAVER_CLIENT_SECRET = "test_secret"
        mock_config.NAVER_NEWS_PER_QUERY = 10
        mock_config.NAVER_QPS_LIMIT = 9

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=SAMPLE_API_RESPONSE)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        # 2개 키워드 검색 → 같은 결과 반환 → 중복 제거
        result = asyncio.get_event_loop().run_until_complete(
            search_by_keywords(["삼성", "반도체"], include_categories=False)
        )
        assert len(result) == 2  # 4건이 아닌 2건 (제목 중복 제거)

    @patch("collector.naver_news.config")
    def test_missing_api_key_returns_empty(self, mock_config):
        """API 키 미설정 시 빈 리스트"""
        mock_config.NAVER_CLIENT_ID = ""
        mock_config.NAVER_CLIENT_SECRET = ""
        mock_config.NAVER_NEWS_PER_QUERY = 10
        mock_config.NAVER_QPS_LIMIT = 9

        result = asyncio.get_event_loop().run_until_complete(
            search_by_keywords(["테스트"], include_categories=False)
        )
        assert result == []


# ────────────────────────────────────────────────────────────
# 설정 검증
# ────────────────────────────────────────────────────────────

class TestNaverNewsConfig:
    def test_category_terms_exist(self):
        assert len(CATEGORY_SEARCH_TERMS) >= 3

    def test_api_url_valid(self):
        assert NAVER_NEWS_API_URL.startswith("https://")
