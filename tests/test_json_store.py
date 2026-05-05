"""
storage/json_store.py 테스트 (v2)

테스트 대상:
  - save_daily_data: TrendItem[] + KeywordResult[] 직렬화 저장
  - load_daily_data: JSON 로드 + 에러 핸들링
  - load_yesterday_keywords: 어제 키워드 로드
  - cleanup_old_data: 보관 기간 초과 파일 삭제
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from collector.trend_item import TrendItem
from analyzer.keyword_extractor import KeywordResult
from storage.json_store import (
    save_daily_data,
    load_daily_data,
    load_yesterday_keywords,
    cleanup_old_data,
    KST,
)


# ── 픽스처 ──

@pytest.fixture
def tmp_data_dir(tmp_path):
    """config.DATA_DIR을 임시 디렉토리로 패치"""
    with patch("storage.json_store.config") as mock_config:
        mock_config.DATA_DIR = tmp_path
        yield tmp_path


def _make_l1(title: str) -> TrendItem:
    return TrendItem(source="rss:test", source_type="rss", title=title, category="경제")


def _make_l2(title: str, keyword: str = "") -> TrendItem:
    return TrendItem(
        source="naver_news", source_type="naver_news",
        title=title, keywords=[keyword] if keyword else [],
    )


def _make_l3(keyword: str, tag: str = "유지") -> TrendItem:
    return TrendItem(
        source="datalab", source_type="datalab",
        title=f"검색 트렌드: {keyword}",
        keywords=[keyword], raw_meta={"trend_tag": tag},
    )


def _make_kw(keyword: str, count: int) -> KeywordResult:
    return KeywordResult(keyword=keyword, count=count, related_titles=[])


# ── save_daily_data 테스트 ──

class TestSaveDailyData:
    def test_creates_json_file(self, tmp_data_dir):
        l1 = [_make_l1("뉴스1")]
        l2 = [_make_l2("네이버뉴스1", "테스트")]
        l3 = [_make_l3("테스트")]
        kw = [_make_kw("테스트", 5)]

        path = save_daily_data(l1, l2, l3, kw)
        assert path.exists()
        assert path.suffix == ".json"

    def test_file_content_structure(self, tmp_data_dir):
        l1 = [_make_l1("A"), _make_l1("B")]
        l2 = [_make_l2("C", "kw1")]
        l3 = [_make_l3("kw1", "급상승")]
        kw = [_make_kw("kw1", 10), _make_kw("kw2", 5)]

        path = save_daily_data(l1, l2, l3, kw)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "date" in data
        assert data["counts"]["rss"] == 2
        assert data["counts"]["news"] == 1
        assert data["counts"]["datalab"] == 1
        assert data["counts"]["keywords"] == 2

        assert len(data["keywords"]) == 2
        assert data["keywords"][0]["keyword"] == "kw1"
        assert data["keywords"][0]["count"] == 10

        assert len(data["layers"]["l1"]) == 2
        assert len(data["layers"]["l2"]) == 1
        assert len(data["layers"]["l3"]) == 1

    def test_empty_data(self, tmp_data_dir):
        path = save_daily_data([], [], [], [])
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["counts"]["rss"] == 0
        assert data["layers"]["l1"] == []

    def test_filename_is_today_date(self, tmp_data_dir):
        path = save_daily_data([], [], [], [])
        today = datetime.now(KST).strftime("%Y-%m-%d")
        assert path.stem == today

    def test_korean_text_preserved(self, tmp_data_dir):
        l1 = [_make_l1("삼성전자 주가 급등")]
        path = save_daily_data(l1, [], [], [])
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["layers"]["l1"][0]["title"] == "삼성전자 주가 급등"


# ── load_daily_data 테스트 ──

class TestLoadDailyData:
    def test_load_existing_file(self, tmp_data_dir):
        path = tmp_data_dir / "2026-05-06.json"
        path.write_text('{"test": true}', encoding="utf-8")
        data = load_daily_data(path)
        assert data == {"test": True}

    def test_load_nonexistent_file(self, tmp_data_dir):
        path = tmp_data_dir / "nonexistent.json"
        assert load_daily_data(path) is None

    def test_load_corrupt_json(self, tmp_data_dir):
        path = tmp_data_dir / "corrupt.json"
        path.write_text("{invalid json", encoding="utf-8")
        assert load_daily_data(path) is None


# ── load_yesterday_keywords 테스트 ──

class TestLoadYesterdayKeywords:
    def test_no_yesterday_file(self, tmp_data_dir):
        assert load_yesterday_keywords() is None

    def test_with_yesterday_data(self, tmp_data_dir):
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        path = tmp_data_dir / f"{yesterday}.json"
        data = {
            "date": "2026-05-05T06:00:00+09:00",
            "keywords": [
                {"keyword": "삼성전자", "count": 15},
                {"keyword": "비트코인", "count": 10},
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = load_yesterday_keywords()
        assert result is not None
        assert len(result) == 2
        assert "삼성전자" in result
        assert "비트코인" in result

    def test_empty_keywords_list(self, tmp_data_dir):
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        path = tmp_data_dir / f"{yesterday}.json"
        data = {"keywords": []}
        path.write_text(json.dumps(data), encoding="utf-8")

        assert load_yesterday_keywords() is None

    def test_keywords_without_keyword_field(self, tmp_data_dir):
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        path = tmp_data_dir / f"{yesterday}.json"
        data = {"keywords": [{"count": 5}, {"keyword": "유효", "count": 3}]}
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = load_yesterday_keywords()
        assert result == ["유효"]

    def test_roundtrip_save_then_load(self, tmp_data_dir):
        """save → load_yesterday_keywords 라운드트립"""
        # 어제 날짜로 저장
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        kw = [_make_kw("테스트A", 10), _make_kw("테스트B", 5)]

        # save_daily_data는 today 기준이므로 직접 파일 작성
        path = tmp_data_dir / f"{yesterday}.json"
        data = {
            "date": "2026-05-05T06:00:00+09:00",
            "keywords": [k.to_dict() for k in kw],
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        result = load_yesterday_keywords()
        assert result == ["테스트A", "테스트B"]


# ── cleanup_old_data 테스트 ──

class TestCleanupOldData:
    def test_removes_old_files(self, tmp_data_dir):
        # 10일 전 파일 생성
        old_date = (datetime.now(KST) - timedelta(days=10)).strftime("%Y-%m-%d")
        old_file = tmp_data_dir / f"{old_date}.json"
        old_file.write_text("{}", encoding="utf-8")

        # 오늘 파일 생성
        today = datetime.now(KST).strftime("%Y-%m-%d")
        today_file = tmp_data_dir / f"{today}.json"
        today_file.write_text("{}", encoding="utf-8")

        cleanup_old_data(keep_days=7)

        assert not old_file.exists()
        assert today_file.exists()

    def test_keeps_recent_files(self, tmp_data_dir):
        # 3일 전 파일 (보관 기간 내)
        recent = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
        recent_file = tmp_data_dir / f"{recent}.json"
        recent_file.write_text("{}", encoding="utf-8")

        cleanup_old_data(keep_days=7)
        assert recent_file.exists()

    def test_ignores_non_date_files(self, tmp_data_dir):
        non_date = tmp_data_dir / "readme.json"
        non_date.write_text("{}", encoding="utf-8")

        cleanup_old_data(keep_days=7)
        assert non_date.exists()
