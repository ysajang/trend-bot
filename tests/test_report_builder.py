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
    _map_datalab_ratios,
    _find_best_news,
    _compute_relevance_score,
    _compute_source_tag,
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
            source_tag="🔥 진짜트렌드",
            representative_news_title="삼성 뉴스",
            representative_news_url="http://example.com",
        )
        d = tk.to_dict()
        assert d["keyword"] == "삼성전자"
        assert d["count"] == 15
        assert d["trend_tag"] == "급상승"
        assert d["source_tag"] == "🔥 진짜트렌드"
        assert d["news_title"] == "삼성 뉴스"
        assert d["news_url"] == "http://example.com"


# ── P0: relevance score 기반 매칭 테스트 ──

class TestRelevanceScore:
    def test_keyword_in_title_front_scores_higher(self):
        """키워드가 제목 앞쪽에 있으면 점수가 높아야 함"""
        front_item = _make_l2_item("트럼프 대북 정책 발표", summary="상세설명")
        back_item = _make_l2_item("미국 대북 정책에서 트럼프 입장", summary="상세설명")

        front_score = _compute_relevance_score("트럼프", front_item)
        back_score = _compute_relevance_score("트럼프", back_item)
        assert front_score > back_score

    def test_no_match_returns_zero(self):
        """키워드가 전혀 없으면 0점"""
        item = _make_l2_item("전혀 다른 뉴스 제목", summary="관련없는 내용")
        score = _compute_relevance_score("삼성전자", item)
        assert score == 0.0

    def test_search_keyword_match_bonus(self):
        """search_keyword 직접 매치 시 보너스"""
        item = _make_l2_item("반도체 투자 뉴스", summary="상세", search_keyword="삼성전자")
        score = _compute_relevance_score("삼성전자", item)
        assert score >= 15.0  # search_keyword 보너스

    def test_co_occurrence_boosts_score(self):
        """다른 TOP 키워드 동시 출현 시 점수 상승"""
        item = _make_l2_item("이란 트럼프 협상 진행", summary="이란과 트럼프가 협상")
        score_without = _compute_relevance_score("이란", item, all_top_keywords=[])
        score_with = _compute_relevance_score("이란", item, all_top_keywords=["트럼프", "협상"])
        assert score_with > score_without


class TestContextualNewsMatching:
    """P0: 동음이의어 뉴스 매칭 오류 방지 테스트"""

    def test_구속_법률_vs_야구(self):
        """'구속' 키워드에 법률 기사가 야구 기사보다 높은 점수여야 함"""
        law_article = _make_l2_item(
            "검찰 구속영장 청구 피의자 출석",
            summary="검찰이 혐의자에 대해 구속영장을 청구했다",
            url="http://law.com",
            search_keyword="구속",
        )
        baseball_article = _make_l2_item(
            "투수 구속 150km 돌파 경기 결과",
            summary="선발 투수가 직구 구속 150km를 기록하며 삼진을 잡았다",
            url="http://baseball.com",
            search_keyword="구속",
        )

        # "구속"이 제목 앞쪽에 있는 기사에 더 높은 점수
        # 법률 기사: "검찰 구속영장" → 구속이 4번째 글자 (앞쪽)
        # 야구 기사: "투수 구속" → 구속이 3번째 글자 (비슷)
        # → 맥락 차이는 co-occurrence로 분류
        # all_top_keywords에 "검찰"이 있으면 법률 기사가 유리
        law_score = _compute_relevance_score(
            "구속", law_article, all_top_keywords=["검찰", "혐의"]
        )
        baseball_score = _compute_relevance_score(
            "구속", baseball_article, all_top_keywords=["검찰", "혐의"]
        )
        assert law_score > baseball_score

    def test_스타벅스_관련_기사_선정(self):
        """'스타벅스' 키워드에 관련 기사가 무관한 기사보다 우선"""
        relevant = _make_l2_item(
            "스타벅스 신메뉴 출시 반응",
            summary="스타벅스가 여름 신메뉴를 출시했다",
            url="http://starbucks.com",
            search_keyword="스타벅스",
        )
        irrelevant = _make_l2_item(
            "카페 혐오 논란 속 스타벅스 언급",
            summary="온라인 커뮤니티에서 카페 이용 매너 논란이 일었다",
            url="http://hate.com",
            search_keyword="스타벅스",
        )

        rel_score = _compute_relevance_score("스타벅스", relevant)
        irr_score = _compute_relevance_score("스타벅스", irrelevant)
        assert rel_score > irr_score


# ── D: source_tag 크로스체크 테스트 ──

class TestComputeSourceTag:
    def test_real_trend(self):
        tag = _compute_source_tag(keyword_count=20, median_count=10, change_ratio=2.0)
        assert "진짜트렌드" in tag

    def test_media_driven(self):
        tag = _compute_source_tag(keyword_count=20, median_count=10, change_ratio=0.5)
        assert "미디어주도" in tag

    def test_public_interest(self):
        tag = _compute_source_tag(keyword_count=5, median_count=10, change_ratio=2.0)
        assert "대중관심" in tag

    def test_no_tag(self):
        tag = _compute_source_tag(keyword_count=5, median_count=10, change_ratio=1.0)
        assert tag == ""


class TestMapDatalabRatios:
    def test_empty(self):
        assert _map_datalab_ratios([]) == {}

    def test_extracts_change_ratio(self):
        item = TrendItem(
            source="datalab", source_type="datalab", title="트렌드: 삼성전자",
            keywords=["삼성전자"], raw_meta={"change_ratio": 2.5, "trend_tag": "급상승"},
        )
        result = _map_datalab_ratios([item])
        assert result["삼성전자"] == 2.5


# ── 뉴스 클러스터 중복 배제 테스트 ──

class TestNewsDeduplification:
    def test_same_news_not_assigned_twice(self):
        """같은 뉴스 기사가 여러 키워드에 중복 배정되지 않아야 함"""
        l1 = []
        l2 = [
            _make_l2_item(
                "이란 트럼프 합의 관련 뉴스",
                summary="이란과 트럼프가 합의에 도달했다는 보도",
                url="http://same-news.com",
                search_keyword="이란",
            ),
            _make_l2_item(
                "트럼프 독자 발언",
                summary="트럼프 대통령이 별도 발언을 했다",
                url="http://trump-only.com",
                search_keyword="트럼프",
            ),
        ]
        l3 = []
        keywords = [
            _make_keyword_result("이란", 30),
            _make_keyword_result("트럼프", 25),
        ]

        report = build_report(l1, l2, l3, keywords)

        urls = [kw.representative_news_url for kw in report.top_keywords if kw.representative_news_url]
        # URL 중복 없어야 함
        assert len(urls) == len(set(urls))

    def test_fallback_when_all_news_used(self):
        """모든 뉴스가 사용된 경우 빈 값으로 처리"""
        l2 = [
            _make_l2_item("유일한 뉴스", summary="하나뿐", url="http://only.com", search_keyword="A"),
        ]
        keywords = [
            _make_keyword_result("A", 10),
            _make_keyword_result("B", 5),  # B에 매칭 뉴스 없음
        ]

        report = build_report([], l2, [], keywords)
        assert report.top_keywords[0].representative_news_url == "http://only.com"
        assert report.top_keywords[1].representative_news_url == ""


# ── 7일 추이 TOP 10 한정 테스트 ──

class TestTrendHighlightsTopOnly:
    def test_only_top10_keywords_in_highlights(self):
        """7일 추이에 TOP 10 키워드만 포함되어야 함"""
        # TOP 10에 포함될 키워드 2개
        keywords = [
            _make_keyword_result("삼성전자", 20),
            _make_keyword_result("AI", 15),
        ]
        # DataLab에 TOP 10 밖 키워드도 포함
        l3 = [
            _make_l3_item("삼성전자", "급상승"),
            _make_l3_item("AI", "지속상승"),
            _make_l3_item("선박", "급상승"),    # TOP 10 밖
            _make_l3_item("문제", "유지"),       # TOP 10 밖
        ]

        report = build_report([], [], l3, keywords)

        # 7일 추이에 포함된 모든 키워드 수집
        all_highlight_kws = []
        for kws in report.trend_highlights.values():
            all_highlight_kws.extend(kws)

        assert "삼성전자" in all_highlight_kws
        assert "AI" in all_highlight_kws
        assert "선박" not in all_highlight_kws
        assert "문제" not in all_highlight_kws


# ── 엔터/문화/게임 분리 집계 + 섹션 테스트 ──

from analyzer.keyword_extractor import analyze_keywords


class TestCategoryFilteredKeywords:
    """analyze_keywords의 category 필터"""

    def _posts(self):
        return [
            {"title": "이란 호르무즈 해상 봉쇄", "category": "종합"},
            {"title": "트럼프 관세 정책 발표", "category": "경제"},
            {"title": "젤다 신작 닌텐도 스위치 출시", "category": "게임"},
            {"title": "엘든링 확장팩 대박 흥행", "category": "게임"},
            {"title": "봉준호 신작 영화 칸 초청", "category": "문화"},
        ]

    def test_exclude_entertainment(self):
        """엔터 제외 시 게임/문화 키워드가 안 잡혀야 함"""
        results = analyze_keywords(
            self._posts(), exclude_categories={"게임", "문화", "연예"}
        )
        kws = {r.keyword for r in results}
        assert "닌텐도" not in kws
        assert "봉준호" not in kws

    def test_include_only_entertainment(self):
        """엔터만 포함 시 정치/경제 키워드가 안 잡혀야 함"""
        results = analyze_keywords(
            self._posts(), include_categories={"게임", "문화", "연예"}
        )
        kws = {r.keyword for r in results}
        assert "이란" not in kws
        assert "트럼프" not in kws
        # 게임/문화 키워드는 포함
        assert any(k in kws for k in ["닌텐도", "엘든링", "봉준호", "스위치"])


class TestEntertainmentSection:
    """리포트 엔터 섹션 생성"""

    def test_entertainment_keywords_populated(self):
        ent_results = [
            KeywordResult(keyword="닌텐도", count=5, related_titles=[]),
            KeywordResult(keyword="엘든링", count=3, related_titles=[]),
        ]
        l1 = [
            _make_l1_item("닌텐도 스위치2 신작 발표", category="게임"),
            _make_l1_item("엘든링 DLC 출시일 공개", category="게임"),
        ]
        report = build_report(
            l1, [], [], [], entertainment_results=ent_results,
        )
        assert len(report.entertainment_keywords) == 2
        assert report.entertainment_keywords[0].keyword == "닌텐도"

    def test_no_entertainment_results(self):
        """엔터 결과 없으면 빈 리스트"""
        report = build_report([], [], [], [])
        assert report.entertainment_keywords == []


# ── ⭐ 행동 트리거 테스트 ──

from analyzer.report_builder import _compute_action_signals


class TestActionSignals:
    def _kw(self, keyword, count, source_tag=""):
        return TopKeyword(keyword=keyword, count=count, source_tag=source_tag)

    def test_all_conditions_met(self):
        kws = [self._kw("호르무즈", 10, "🔥 진짜트렌드")]
        recent = [{"호르무즈"}, {"호르무즈"}]  # 어제 + 그제 모두 등장
        signals = _compute_action_signals(kws, recent)
        assert len(signals) == 1
        assert signals[0].keyword == "호르무즈"

    def test_low_count_excluded(self):
        kws = [self._kw("캐슬바니아", 2, "🔥 진짜트렌드")]
        recent = [{"캐슬바니아"}, {"캐슬바니아"}]
        assert _compute_action_signals(kws, recent) == []

    def test_no_search_demand_excluded(self):
        """📰 미디어주도는 트리거 아님"""
        kws = [self._kw("호우", 50, "📰 미디어주도")]
        recent = [{"호우"}, {"호우"}]
        assert _compute_action_signals(kws, recent) == []

    def test_not_consecutive_excluded(self):
        kws = [self._kw("폭염", 8, "🔍 대중관심")]
        recent = [{"폭염"}, set()]  # 그제 미등장
        assert _compute_action_signals(kws, recent) == []

    def test_insufficient_history_excluded(self):
        """운영 초기(과거 데이터 부족) → 트리거 미충족"""
        kws = [self._kw("폭염", 8, "🔍 대중관심")]
        assert _compute_action_signals(kws, None) == []
        assert _compute_action_signals(kws, [{"폭염"}]) == []
