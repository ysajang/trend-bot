"""
analyzer/report_builder.py 테스트

테스트 대상:
  - _map_datalab_tags: L3 → 태그 매핑
  - _find_best_news: 키워드별 대표 뉴스 선정
  - _compute_category_distribution: RSS 카테고리 분포
  - _compute_trend_highlights: 태그별 키워드 그룹핑
  - compute_yesterday_diff: 어제 대비 변화
  - build_report: 전체 파이프라인
"""

import pytest

from collector.trend_item import TrendItem
from analyzer.keyword_extractor import KeywordResult
from analyzer.report_builder import (
    TopKeyword,
    TrendChange,
    ReportData,
    _map_datalab_tags,
    _find_best_news,
    _compute_category_distribution,
    _compute_trend_highlights,
    compute_yesterday_diff,
    build_report,
)


# ── 테스트 픽스처 ──

def _make_l1_item(title: str, category: str = "종합") -> TrendItem:
    return TrendItem(
        source="rss:test",
        source_type="rss",
        title=title,
        category=category,
    )


def _make_l2_item(
    title: str,
    summary: str = "",
    url: str = "",
    published_at: str = "",
    search_keyword: str = "",
) -> TrendItem:
    return TrendItem(
        source="naver_news",
        source_type="naver_news",
        title=title,
        summary=summary,
        url=url,
        published_at=published_at,
        keywords=[search_keyword] if search_keyword else [],
        raw_meta={"search_keyword": search_keyword},
    )


def _make_l3_item(keyword: str, trend_tag: str, ratios: list[float] = None) -> TrendItem:
    return TrendItem(
        source="datalab",
        source_type="datalab",
        title=f"검색 트렌드: {keyword}",
        keywords=[keyword],
        raw_meta={
            "trend_tag": trend_tag,
            "ratios": ratios or [],
        },
    )


def _make_keyword_result(keyword: str, count: int) -> KeywordResult:
    return KeywordResult(
        keyword=keyword,
        count=count,
        related_titles=[f"{keyword} 관련 제목"],
    )


# ── _map_datalab_tags 테스트 ──

class TestMapDatalabTags:
    def test_empty_input(self):
        assert _map_datalab_tags([]) == {}

    def test_single_keyword(self):
        items = [_make_l3_item("삼성전자", "급상승")]
        result = _map_datalab_tags(items)
        assert result == {"삼성전자": "급상승"}

    def test_multiple_keywords(self):
        items = [
            _make_l3_item("삼성전자", "급상승"),
            _make_l3_item("비트코인", "지속상승"),
            _make_l3_item("AI", "신규"),
        ]
        result = _map_datalab_tags(items)
        assert len(result) == 3
        assert result["삼성전자"] == "급상승"
        assert result["비트코인"] == "지속상승"
        assert result["AI"] == "신규"

    def test_ignores_non_datalab_items(self):
        items = [
            _make_l1_item("삼성전자 뉴스"),
            _make_l3_item("삼성전자", "급상승"),
        ]
        result = _map_datalab_tags(items)
        assert len(result) == 1
        assert "삼성전자" in result

    def test_missing_trend_tag_defaults_to_유지(self):
        item = TrendItem(
            source="datalab",
            source_type="datalab",
            title="검색 트렌드: 테스트",
            keywords=["테스트"],
            raw_meta={},
        )
        result = _map_datalab_tags([item])
        assert result["테스트"] == "유지"


# ── _find_best_news 테스트 ──

class TestFindBestNews:
    def test_no_matching_news(self):
        items = [_make_l2_item("전혀 다른 뉴스", search_keyword="다른키워드")]
        assert _find_best_news("삼성전자", items) is None

    def test_match_by_title(self):
        items = [
            _make_l2_item("삼성전자 주가 급등", summary="짧은요약", url="http://a.com"),
        ]
        result = _find_best_news("삼성전자", items)
        assert result is not None
        assert result.url == "http://a.com"

    def test_match_by_keywords_field(self):
        items = [
            _make_l2_item("반도체 투자", summary="상세설명", search_keyword="삼성전자"),
        ]
        result = _find_best_news("삼성전자", items)
        assert result is not None

    def test_prefers_longer_summary(self):
        items = [
            _make_l2_item("삼성전자 A", summary="짧음", url="http://short.com"),
            _make_l2_item("삼성전자 B", summary="이것은 매우 긴 요약입니다 " * 10, url="http://long.com"),
        ]
        result = _find_best_news("삼성전자", items)
        assert result is not None
        assert result.url == "http://long.com"

    def test_case_insensitive_match(self):
        items = [_make_l2_item("AI 반도체 혁신", summary="상세", search_keyword="ai")]
        result = _find_best_news("AI", items)
        assert result is not None

    def test_ignores_non_naver_news(self):
        l1_item = _make_l1_item("삼성전자 뉴스")
        assert _find_best_news("삼성전자", [l1_item]) is None

    def test_empty_items(self):
        assert _find_best_news("삼성전자", []) is None


# ── _compute_category_distribution 테스트 ──

class TestComputeCategoryDistribution:
    def test_empty_input(self):
        assert _compute_category_distribution([]) == {}

    def test_single_category(self):
        items = [_make_l1_item(f"기사{i}", "경제") for i in range(3)]
        result = _compute_category_distribution(items)
        assert result == {"경제": 3}

    def test_multiple_categories(self):
        items = [
            _make_l1_item("A", "경제"),
            _make_l1_item("B", "경제"),
            _make_l1_item("C", "IT"),
            _make_l1_item("D", "사회"),
        ]
        result = _compute_category_distribution(items)
        assert result["경제"] == 2
        assert result["IT"] == 1
        assert result["사회"] == 1

    def test_empty_category_becomes_기타(self):
        item = TrendItem(source="rss:test", source_type="rss", title="테스트", category="")
        result = _compute_category_distribution([item])
        assert result == {"기타": 1}

    def test_sorted_by_count_desc(self):
        items = (
            [_make_l1_item(f"A{i}", "경제") for i in range(5)]
            + [_make_l1_item(f"B{i}", "IT") for i in range(3)]
            + [_make_l1_item(f"C{i}", "사회") for i in range(1)]
        )
        result = _compute_category_distribution(items)
        keys = list(result.keys())
        assert keys[0] == "경제"
        assert keys[1] == "IT"
        assert keys[2] == "사회"


# ── _compute_trend_highlights 테스트 ──

class TestComputeTrendHighlights:
    def test_empty_input(self):
        assert _compute_trend_highlights({}) == {}

    def test_groups_by_tag(self):
        tag_map = {
            "삼성전자": "급상승",
            "비트코인": "급상승",
            "AI": "신규",
        }
        result = _compute_trend_highlights(tag_map)
        assert len(result["급상승"]) == 2
        assert len(result["신규"]) == 1

    def test_single_tag(self):
        tag_map = {"A": "유지", "B": "유지", "C": "유지"}
        result = _compute_trend_highlights(tag_map)
        assert len(result) == 1
        assert len(result["유지"]) == 3


# ── compute_yesterday_diff 테스트 ──

class TestComputeYesterdayDiff:
    def test_identical_lists(self):
        diff = compute_yesterday_diff(["A", "B", "C"], ["A", "B", "C"])
        assert diff.new_keywords == []
        assert diff.dropped_keywords == []

    def test_new_keywords(self):
        diff = compute_yesterday_diff(["A", "B", "C"], ["A", "B"])
        assert diff.new_keywords == ["C"]
        assert diff.dropped_keywords == []

    def test_dropped_keywords(self):
        diff = compute_yesterday_diff(["A"], ["A", "B", "C"])
        assert diff.new_keywords == []
        assert diff.dropped_keywords == ["B", "C"]

    def test_mixed_changes(self):
        diff = compute_yesterday_diff(["A", "D"], ["A", "B", "C"])
        assert diff.new_keywords == ["D"]
        assert diff.dropped_keywords == ["B", "C"]

    def test_both_empty(self):
        diff = compute_yesterday_diff([], [])
        assert diff.new_keywords == []
        assert diff.dropped_keywords == []

    def test_today_empty(self):
        diff = compute_yesterday_diff([], ["A", "B"])
        assert diff.new_keywords == []
        assert diff.dropped_keywords == ["A", "B"]


# ── build_report 통합 테스트 ──

class TestBuildReport:
    def _setup_data(self):
        l1 = [
            _make_l1_item("삼성전자 주가 급등", "경제"),
            _make_l1_item("AI 반도체 수요 증가", "IT"),
            _make_l1_item("비트코인 10만달러 돌파", "경제"),
        ]
        l2 = [
            _make_l2_item(
                "삼성전자 실적 발표",
                summary="삼성전자가 반도체 호황으로 역대 최대 실적을 기록했다",
                url="http://news1.com",
                published_at="2026-05-06T10:00:00+09:00",
                search_keyword="삼성전자",
            ),
            _make_l2_item(
                "비트코인 급등 분석",
                summary="비트코인이 기관 매수세에 10만달러를 돌파했다",
                url="http://news2.com",
                published_at="2026-05-06T09:00:00+09:00",
                search_keyword="비트코인",
            ),
        ]
        l3 = [
            _make_l3_item("삼성전자", "급상승", [10, 15, 20, 30, 45, 60, 100]),
            _make_l3_item("비트코인", "지속상승", [50, 55, 60, 65, 70, 75, 80]),
            _make_l3_item("AI", "신규", [0, 0, 0, 0, 5, 20, 45]),
        ]
        keywords = [
            _make_keyword_result("삼성전자", 15),
            _make_keyword_result("비트코인", 12),
            _make_keyword_result("AI", 10),
        ]
        return l1, l2, l3, keywords

    def test_basic_report_structure(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        assert isinstance(report, ReportData)
        assert report.total_rss == 3
        assert report.total_news == 2
        assert report.total_datalab == 3
        assert len(report.top_keywords) == 3
        assert report.yesterday_diff is None

    def test_top_keywords_order(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        assert report.top_keywords[0].keyword == "삼성전자"
        assert report.top_keywords[0].count == 15
        assert report.top_keywords[1].keyword == "비트코인"

    def test_datalab_tag_mapping(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        assert report.top_keywords[0].trend_tag == "급상승"
        assert report.top_keywords[1].trend_tag == "지속상승"
        assert report.top_keywords[2].trend_tag == "신규"

    def test_representative_news(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        samsung = report.top_keywords[0]
        assert samsung.representative_news_title == "삼성전자 실적 발표"
        assert samsung.representative_news_url == "http://news1.com"

    def test_category_distribution(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        assert report.category_distribution["경제"] == 2
        assert report.category_distribution["IT"] == 1

    def test_trend_highlights(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)

        assert "급상승" in report.trend_highlights
        assert "삼성전자" in report.trend_highlights["급상승"]

    def test_yesterday_diff_included(self):
        l1, l2, l3, keywords = self._setup_data()
        yesterday_kws = ["삼성전자", "테슬라", "엔비디아"]
        report = build_report(l1, l2, l3, keywords, yesterday_keywords=yesterday_kws)

        assert report.yesterday_diff is not None
        assert "비트코인" in report.yesterday_diff.new_keywords
        assert "AI" in report.yesterday_diff.new_keywords
        assert "테슬라" in report.yesterday_diff.dropped_keywords
        assert "엔비디아" in report.yesterday_diff.dropped_keywords

    def test_yesterday_diff_none_when_not_provided(self):
        l1, l2, l3, keywords = self._setup_data()
        report = build_report(l1, l2, l3, keywords)
        assert report.yesterday_diff is None

    def test_empty_layers(self):
        report = build_report([], [], [], [])
        assert report.total_rss == 0
        assert report.total_news == 0
        assert report.total_datalab == 0
        assert report.top_keywords == []

    def test_keyword_without_matching_news(self):
        l1 = [_make_l1_item("테스트", "IT")]
        l2 = []  # 뉴스 없음
        l3 = [_make_l3_item("테스트키워드", "유지")]
        keywords = [_make_keyword_result("테스트키워드", 5)]

        report = build_report(l1, l2, l3, keywords)
        assert report.top_keywords[0].representative_news_title == ""
        assert report.top_keywords[0].representative_news_url == ""

    def test_keyword_without_datalab_tag(self):
        l1 = [_make_l1_item("테스트", "IT")]
        l2 = []
        l3 = []  # DataLab 결과 없음
        keywords = [_make_keyword_result("미확인키워드", 5)]

        report = build_report(l1, l2, l3, keywords)
        assert report.top_keywords[0].trend_tag == "—"

    def test_top_10_limit(self):
        keywords = [_make_keyword_result(f"kw{i}", 100 - i) for i in range(20)]
        report = build_report([], [], [], keywords)
        assert len(report.top_keywords) == 10
        assert report.top_keywords[0].keyword == "kw0"
        assert report.top_keywords[9].keyword == "kw9"


class TestTopKeywordSerialization:
    def test_to_dict(self):
        tk = TopKeyword(
            keyword="삼성전자",
            count=15,
            trend_tag="급상승",
            representative_news_title="삼성 뉴스",
            representative_news_url="http://example.com",
        )
        d = tk.to_dict()
        assert d["keyword"] == "삼성전자"
        assert d["count"] == 15
        assert d["trend_tag"] == "급상승"
        assert d["news_title"] == "삼성 뉴스"
        assert d["news_url"] == "http://example.com"
