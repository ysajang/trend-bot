"""
Naver DataLab 검증기 단위 테스트
실행: pytest tests/test_naver_datalab.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from collector.naver_datalab import (
    classify_trend,
    _build_request_body,
    _parse_response,
    verify_trends,
    TREND_SURGE,
    TREND_RISING,
    TREND_FALLING,
    TREND_NEW,
    TREND_STEADY,
    MAX_GROUPS_PER_REQUEST,
)
from collector.trend_item import TrendItem


# ────────────────────────────────────────────────────────────
# classify_trend
# ────────────────────────────────────────────────────────────

class TestClassifyTrend:
    def test_surge_double_average(self):
        """오늘이 이전 평균의 2배 이상 → 급상승"""
        ratios = [10, 12, 11, 13, 10, 12, 50]
        assert classify_trend(ratios) == TREND_SURGE

    def test_surge_from_near_zero(self):
        """이전 거의 0에서 오늘 30+ → 급상승"""
        ratios = [0, 0, 1, 2, 0, 1, 35]
        assert classify_trend(ratios) == TREND_SURGE

    def test_rising_3_consecutive(self):
        """최근 3일 연속 상승 → 지속상승"""
        ratios = [50, 45, 40, 42, 45, 55, 65]
        assert classify_trend(ratios) == TREND_RISING

    def test_falling_3_consecutive(self):
        """최근 3일 연속 하락 → 하락세"""
        ratios = [30, 40, 50, 60, 55, 45, 35]
        assert classify_trend(ratios) == TREND_FALLING

    def test_falling_below_half_max(self):
        """오늘이 기간 최대의 50% 미만 → 하락세"""
        ratios = [100, 80, 60, 50, 40, 45, 40]
        assert classify_trend(ratios) == TREND_FALLING

    def test_new_keyword(self):
        """초반 4일 0이고 최근 등장 → 신규"""
        ratios = [0, 0, 0, 0, 20, 45, 60]
        assert classify_trend(ratios) == TREND_NEW

    def test_steady_no_change(self):
        """큰 변동 없음 → 유지"""
        ratios = [50, 52, 48, 51, 50, 49, 51]
        assert classify_trend(ratios) == TREND_STEADY

    def test_all_zeros(self):
        """전부 0 → 유지"""
        ratios = [0, 0, 0, 0, 0, 0, 0]
        assert classify_trend(ratios) == TREND_STEADY

    def test_empty_list(self):
        ratios: list[float] = []
        assert classify_trend(ratios) == TREND_STEADY

    def test_single_value(self):
        assert classify_trend([50]) == TREND_STEADY

    def test_two_values_surge(self):
        """2일 데이터에서 급상승"""
        assert classify_trend([10, 50]) == TREND_SURGE


# ────────────────────────────────────────────────────────────
# _build_request_body
# ────────────────────────────────────────────────────────────

class TestBuildRequestBody:
    def test_basic_structure(self):
        body = _build_request_body(["AI", "반도체"], days=7)
        assert "startDate" in body
        assert "endDate" in body
        assert body["timeUnit"] == "date"
        assert len(body["keywordGroups"]) == 2

    def test_max_5_groups(self):
        """6개 넘겨도 5개만"""
        keywords = ["a", "b", "c", "d", "e", "f"]
        body = _build_request_body(keywords)
        assert len(body["keywordGroups"]) == MAX_GROUPS_PER_REQUEST

    def test_keyword_group_format(self):
        body = _build_request_body(["AI"])
        group = body["keywordGroups"][0]
        assert group["groupName"] == "AI"
        assert group["keywords"] == ["AI"]


# ────────────────────────────────────────────────────────────
# _parse_response
# ────────────────────────────────────────────────────────────

class TestParseResponse:
    def test_normal_response(self):
        data = {
            "results": [
                {
                    "title": "AI",
                    "keywords": ["AI"],
                    "data": [
                        {"period": "2026-05-01", "ratio": 50.0},
                        {"period": "2026-05-02", "ratio": 60.0},
                        {"period": "2026-05-03", "ratio": 70.0},
                    ],
                }
            ]
        }
        result = _parse_response(data)
        assert "AI" in result
        assert result["AI"] == [50.0, 60.0, 70.0]

    def test_multiple_keywords(self):
        data = {
            "results": [
                {"title": "A", "data": [{"period": "2026-05-01", "ratio": 10}]},
                {"title": "B", "data": [{"period": "2026-05-01", "ratio": 20}]},
            ]
        }
        result = _parse_response(data)
        assert len(result) == 2

    def test_empty_results(self):
        assert _parse_response({"results": []}) == {}
        assert _parse_response({}) == {}

    def test_data_sorted_by_period(self):
        """날짜 역순으로 와도 정렬됨"""
        data = {
            "results": [
                {
                    "title": "X",
                    "data": [
                        {"period": "2026-05-03", "ratio": 30},
                        {"period": "2026-05-01", "ratio": 10},
                        {"period": "2026-05-02", "ratio": 20},
                    ],
                }
            ]
        }
        result = _parse_response(data)
        assert result["X"] == [10, 20, 30]


# ────────────────────────────────────────────────────────────
# verify_trends (통합 테스트 - mock HTTP)
# ────────────────────────────────────────────────────────────

SAMPLE_DATALAB_RESPONSE = {
    "startDate": "2026-04-30",
    "endDate": "2026-05-06",
    "timeUnit": "date",
    "results": [
        {
            "title": "AI",
            "keywords": ["AI"],
            "data": [
                {"period": "2026-04-30", "ratio": 10},
                {"period": "2026-05-01", "ratio": 12},
                {"period": "2026-05-02", "ratio": 11},
                {"period": "2026-05-03", "ratio": 13},
                {"period": "2026-05-04", "ratio": 10},
                {"period": "2026-05-05", "ratio": 12},
                {"period": "2026-05-06", "ratio": 50},
            ],
        },
        {
            "title": "트럼프",
            "keywords": ["트럼프"],
            "data": [
                {"period": "2026-04-30", "ratio": 50},
                {"period": "2026-05-01", "ratio": 52},
                {"period": "2026-05-02", "ratio": 48},
                {"period": "2026-05-03", "ratio": 51},
                {"period": "2026-05-04", "ratio": 50},
                {"period": "2026-05-05", "ratio": 49},
                {"period": "2026-05-06", "ratio": 51},
            ],
        },
    ],
}


class TestVerifyTrends:
    def test_empty_keywords(self):
        result = asyncio.get_event_loop().run_until_complete(
            verify_trends([])
        )
        assert result == []

    @patch("collector.naver_datalab.config")
    @patch("collector.naver_datalab.aiohttp.ClientSession")
    def test_successful_verification(self, mock_session_cls, mock_config):
        mock_config.NAVER_CLIENT_ID = "test_id"
        mock_config.NAVER_CLIENT_SECRET = "test_secret"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=SAMPLE_DATALAB_RESPONSE)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = asyncio.get_event_loop().run_until_complete(
            verify_trends(["AI", "트럼프"])
        )
        assert len(result) == 2
        assert all(isinstance(item, TrendItem) for item in result)
        assert all(item.source_type == "datalab" for item in result)

        # AI: 평균 ~11에서 오늘 50 → 급상승
        ai_item = [r for r in result if "AI" in r.keywords][0]
        assert ai_item.raw_meta["trend_tag"] == TREND_SURGE

        # 트럼프: 변동 없음 → 유지
        trump_item = [r for r in result if "트럼프" in r.keywords][0]
        assert trump_item.raw_meta["trend_tag"] == TREND_STEADY

    @patch("collector.naver_datalab.config")
    @patch("collector.naver_datalab.aiohttp.ClientSession")
    def test_missing_keyword_in_response(self, mock_session_cls, mock_config):
        """API 응답에 없는 키워드는 유지 태그"""
        mock_config.NAVER_CLIENT_ID = "test_id"
        mock_config.NAVER_CLIENT_SECRET = "test_secret"

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"results": []})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        result = asyncio.get_event_loop().run_until_complete(
            verify_trends(["없는키워드"])
        )
        assert len(result) == 1
        assert result[0].raw_meta["trend_tag"] == TREND_STEADY

    @patch("collector.naver_datalab.config")
    def test_missing_api_key(self, mock_config):
        mock_config.NAVER_CLIENT_ID = ""
        mock_config.NAVER_CLIENT_SECRET = ""

        result = asyncio.get_event_loop().run_until_complete(
            verify_trends(["테스트"])
        )
        # 키 없어도 crash 안 하고 빈 추이 + 유지 태그
        assert len(result) == 1
        assert result[0].raw_meta["trend_tag"] == TREND_STEADY


# ────────────────────────────────────────────────────────────
# 설정 검증
# ────────────────────────────────────────────────────────────

class TestDatalabConfig:
    def test_max_groups_is_5(self):
        assert MAX_GROUPS_PER_REQUEST == 5
