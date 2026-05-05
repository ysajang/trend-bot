"""
notifier/telegram.py 테스트

테스트 대상:
  - format_report: ReportData → 텔레그램 메시지 포맷
  - split_message: 긴 메시지 분할
  - TAG_EMOJI 매핑
"""

import pytest

from analyzer.report_builder import ReportData, TopKeyword, TrendChange
from notifier.telegram import format_report, split_message, TAG_EMOJI


# ── 픽스처 ──

def _make_report(
    top_keywords=None,
    trend_highlights=None,
    yesterday_diff=None,
    category_distribution=None,
) -> ReportData:
    return ReportData(
        total_rss=500,
        total_news=80,
        total_datalab=20,
        top_keywords=top_keywords or [],
        category_distribution=category_distribution or {"경제": 200, "IT": 150},
        trend_highlights=trend_highlights or {},
        yesterday_diff=yesterday_diff,
    )


def _make_top_kw(
    keyword: str,
    count: int = 10,
    trend_tag: str = "유지",
    news_title: str = "",
    news_url: str = "",
) -> TopKeyword:
    return TopKeyword(
        keyword=keyword,
        count=count,
        trend_tag=trend_tag,
        representative_news_title=news_title,
        representative_news_url=news_url,
    )


# ── format_report 테스트 ──

class TestFormatReport:
    def test_header_contains_total_count(self):
        report = _make_report()
        result = format_report(report)
        assert "600건" in result  # 500 + 80 + 20

    def test_header_contains_v2(self):
        report = _make_report()
        result = format_report(report)
        assert "TrendRadar v2" in result

    def test_top_keywords_section(self):
        keywords = [
            _make_top_kw("삼성전자", 15, "급상승"),
            _make_top_kw("비트코인", 12, "지속상승"),
        ]
        report = _make_report(top_keywords=keywords)
        result = format_report(report)

        assert "1. 삼성전자 (15회)" in result
        assert "2. 비트코인 (12회)" in result
        assert "🔺 급상승" in result
        assert "📈 지속상승" in result

    def test_news_cluster_section(self):
        keywords = [
            _make_top_kw("삼성전자", 15, news_title="삼성 실적 발표", news_url="http://a.com"),
        ]
        report = _make_report(top_keywords=keywords)
        result = format_report(report)

        assert "📰 주요 뉴스 클러스터" in result
        assert "[삼성전자] 삼성 실적 발표" in result

    def test_news_cluster_omitted_when_no_news(self):
        keywords = [_make_top_kw("삼성전자", 15)]  # news_title 없음
        report = _make_report(top_keywords=keywords)
        result = format_report(report)
        assert "📰 주요 뉴스 클러스터" not in result

    def test_long_news_title_truncated(self):
        long_title = "A" * 100
        keywords = [_make_top_kw("테스트", news_title=long_title)]
        report = _make_report(top_keywords=keywords)
        result = format_report(report)
        assert "..." in result

    def test_trend_highlights_section(self):
        highlights = {
            "급상승": ["삼성전자", "테슬라"],
            "신규": ["AI"],
        }
        report = _make_report(trend_highlights=highlights)
        result = format_report(report)

        assert "📈 7일 추이 요약" in result
        assert "🔺 급상승: 삼성전자 / 테슬라" in result
        assert "🆕 신규: AI" in result

    def test_trend_highlights_tag_order(self):
        highlights = {
            "유지": ["A"],
            "급상승": ["B"],
            "신규": ["C"],
        }
        report = _make_report(trend_highlights=highlights)
        result = format_report(report)

        # 급상승이 유지보다 먼저 나와야 함
        pos_surge = result.index("급상승")
        pos_new = result.index("🆕 신규")
        pos_steady = result.index("➖ 유지")
        assert pos_surge < pos_new < pos_steady

    def test_yesterday_diff_section(self):
        diff = TrendChange(
            new_keywords=["AI", "엔비디아"],
            dropped_keywords=["테슬라"],
        )
        report = _make_report(yesterday_diff=diff)
        result = format_report(report)

        assert "🆚 어제 대비 변화" in result
        assert "🆕 신규: AI / 엔비디아" in result
        assert "📤 이탈: 테슬라" in result

    def test_yesterday_diff_omitted_when_none(self):
        report = _make_report(yesterday_diff=None)
        result = format_report(report)
        assert "🆚 어제 대비 변화" not in result

    def test_yesterday_diff_omitted_when_no_changes(self):
        diff = TrendChange(new_keywords=[], dropped_keywords=[])
        report = _make_report(yesterday_diff=diff)
        result = format_report(report)
        assert "🆚 어제 대비 변화" not in result

    def test_footer_present(self):
        report = _make_report()
        result = format_report(report)
        assert "🤖 TrendRadar Bot v2" in result

    def test_empty_report(self):
        report = ReportData()
        result = format_report(report)
        assert "TrendRadar v2" in result
        assert "🤖 TrendRadar Bot v2" in result


# ── split_message 테스트 ──

class TestSplitMessage:
    def test_short_message_no_split(self):
        msg = "짧은 메시지"
        assert split_message(msg) == [msg]

    def test_long_message_splits(self):
        msg = "라인\n" * 2000  # ~6000자
        parts = split_message(msg)
        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 4096

    def test_split_at_newline(self):
        # 4090자 첫 파트 + 나머지
        first_part = "A" * 4000 + "\n"
        second_part = "B" * 100
        msg = first_part + second_part
        parts = split_message(msg)
        assert len(parts) == 2
        assert parts[0].endswith("A")

    def test_single_char_boundary(self):
        msg = "A" * 4096
        assert split_message(msg) == [msg]

    def test_exact_boundary_plus_one(self):
        msg = "A" * 4097
        parts = split_message(msg)
        assert len(parts) == 2


# ── TAG_EMOJI 테스트 ──

class TestTagEmoji:
    def test_all_tags_have_emoji(self):
        expected_tags = ["급상승", "지속상승", "하락세", "신규", "유지", "—"]
        for tag in expected_tags:
            assert tag in TAG_EMOJI

    def test_emoji_not_empty(self):
        for tag, emoji in TAG_EMOJI.items():
            assert len(emoji) > 0
