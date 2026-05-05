"""
TrendItem 단위 테스트
실행: pytest tests/test_trend_item.py -v
"""

import pytest

from collector import TrendItem, ALLOWED_SOURCE_TYPES


# ────────────────────────────────────────────────────────────
# 정상 생성 케이스
# ────────────────────────────────────────────────────────────

class TestTrendItemCreation:
    """TrendItem 정상 생성 테스트"""

    def test_minimal_rss_item(self):
        """RSS 최소 필드 생성"""
        item = TrendItem(
            source="rss:hani",
            source_type="rss",
            title="오늘의 뉴스 헤드라인",
        )
        assert item.title == "오늘의 뉴스 헤드라인"
        assert item.source == "rss:hani"
        assert item.source_type == "rss"
        assert item.summary == ""
        assert item.signal_score == 0.0
        assert item.keywords == []
        assert item.raw_meta == {}

    def test_full_naver_news_item(self):
        """네이버 뉴스 전체 필드 생성"""
        item = TrendItem(
            source="naver_news",
            source_type="naver_news",
            title="삼성전자 신제품 발표",
            summary="삼성전자가 새로운 갤럭시 시리즈를 공개했다",
            url="https://news.naver.com/article/123",
            published_at="2026-05-05T14:30:00+09:00",
            category="IT",
            signal_score=0.85,
            raw_meta={"originallink": "https://example.com/news/123"},
        )
        assert item.category == "IT"
        assert item.signal_score == 0.85
        assert item.raw_meta["originallink"] == "https://example.com/news/123"

    def test_datalab_item_with_keywords(self):
        """DataLab 키워드 검증 결과"""
        item = TrendItem(
            source="datalab",
            source_type="datalab",
            title="검색 트렌드: 챗GPT",
            signal_score=2.34,
            keywords=["챗GPT", "AI", "OpenAI"],
        )
        assert item.keywords == ["챗GPT", "AI", "OpenAI"]
        assert len(item.keywords) == 3


# ────────────────────────────────────────────────────────────
# 입력값 검증
# ────────────────────────────────────────────────────────────

class TestTrendItemValidation:
    """입력값 검증 테스트 (sanitization)"""

    def test_empty_title_raises(self):
        with pytest.raises(ValueError, match="title"):
            TrendItem(source="rss:test", source_type="rss", title="")

    def test_whitespace_only_title_raises(self):
        with pytest.raises(ValueError, match="title"):
            TrendItem(source="rss:test", source_type="rss", title="   ")

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValueError, match="source_type"):
            TrendItem(source="x", source_type="dcinside", title="t")

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="source"):
            TrendItem(source="", source_type="rss", title="t")

    def test_title_stripped(self):
        item = TrendItem(
            source="rss:hani",
            source_type="rss",
            title="  여백 있는 제목  ",
        )
        assert item.title == "여백 있는 제목"

    def test_url_stripped(self):
        item = TrendItem(
            source="rss:hani",
            source_type="rss",
            title="제목",
            url="  https://example.com  ",
        )
        assert item.url == "https://example.com"

    def test_summary_none_becomes_empty(self):
        # None 들어와도 빈 문자열로 정규화
        item = TrendItem(
            source="rss:hani",
            source_type="rss",
            title="제목",
            summary=None,  # type: ignore
        )
        assert item.summary == ""


# ────────────────────────────────────────────────────────────
# 직렬화 / 역직렬화
# ────────────────────────────────────────────────────────────

class TestTrendItemSerialization:
    """to_dict / from_dict 라운드트립"""

    def test_to_dict_contains_all_fields(self):
        item = TrendItem(
            source="rss:hani",
            source_type="rss",
            title="제목",
        )
        d = item.to_dict()
        assert "source" in d
        assert "source_type" in d
        assert "title" in d
        assert "summary" in d
        assert "url" in d
        assert "published_at" in d
        assert "category" in d
        assert "signal_score" in d
        assert "keywords" in d
        assert "raw_meta" in d

    def test_roundtrip(self):
        """to_dict → from_dict 라운드트립"""
        original = TrendItem(
            source="naver_news",
            source_type="naver_news",
            title="원본 제목",
            summary="요약",
            url="https://example.com/123",
            published_at="2026-05-05T14:30:00+09:00",
            category="경제",
            signal_score=0.5,
            keywords=["키워드1", "키워드2"],
            raw_meta={"foo": "bar"},
        )
        restored = TrendItem.from_dict(original.to_dict())
        assert restored.source == original.source
        assert restored.title == original.title
        assert restored.signal_score == original.signal_score
        assert restored.keywords == original.keywords
        assert restored.raw_meta == original.raw_meta

    def test_from_dict_with_missing_optional_fields(self):
        """필수 필드만 있는 dict에서 복원"""
        data = {
            "source": "rss:hani",
            "source_type": "rss",
            "title": "제목",
        }
        item = TrendItem.from_dict(data)
        assert item.title == "제목"
        assert item.summary == ""
        assert item.signal_score == 0.0


# ────────────────────────────────────────────────────────────
# 허용된 source_type 화이트리스트
# ────────────────────────────────────────────────────────────

class TestSourceTypeWhitelist:
    """source_type 화이트리스트 검증"""

    def test_allowed_types(self):
        assert "rss" in ALLOWED_SOURCE_TYPES
        assert "naver_news" in ALLOWED_SOURCE_TYPES
        assert "datalab" in ALLOWED_SOURCE_TYPES

    def test_dcinside_not_allowed(self):
        """레거시 dcinside는 허용 안 됨 (Legal by Design)"""
        assert "dcinside" not in ALLOWED_SOURCE_TYPES
        assert "fmkorea" not in ALLOWED_SOURCE_TYPES

    @pytest.mark.parametrize("source_type", ["rss", "naver_news", "datalab"])
    def test_all_allowed_types_create_successfully(self, source_type: str):
        """허용된 모든 타입으로 생성 가능"""
        item = TrendItem(
            source=f"test:{source_type}",
            source_type=source_type,
            title="테스트 제목",
        )
        assert item.source_type == source_type
