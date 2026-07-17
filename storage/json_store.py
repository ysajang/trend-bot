"""
JSON 파일 기반 데이터 저장 (v2)
- 일별로 TrendItem[] + KeywordResult[] 저장
- 전일 데이터 로드 → 어제 TOP 키워드 반환
- 7일 보관 후 자동 삭제
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from collector.trend_item import TrendItem
from analyzer.keyword_extractor import KeywordResult
import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


def get_today_path() -> Path:
    """오늘 날짜 기준 저장 파일 경로"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    return config.DATA_DIR / f"{today}.json"


def get_yesterday_path() -> Path:
    """어제 날짜 기준 저장 파일 경로"""
    yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    return config.DATA_DIR / f"{yesterday}.json"


def save_daily_data(
    layer1_items: list[TrendItem],
    layer2_items: list[TrendItem],
    layer3_items: list[TrendItem],
    keyword_results: list[KeywordResult],
) -> Path:
    """
    오늘 수집 데이터 저장

    저장 구조:
      date: ISO8601 KST
      counts: {rss, news, datalab, keywords}
      keywords: [KeywordResult.to_dict(), ...]
      layers: {l1: [...], l2: [...], l3: [...]}
    """
    path = get_today_path()
    data = {
        "date": datetime.now(KST).isoformat(),
        "counts": {
            "rss": len(layer1_items),
            "news": len(layer2_items),
            "datalab": len(layer3_items),
            "keywords": len(keyword_results),
        },
        "keywords": [kw.to_dict() for kw in keyword_results],
        "layers": {
            "l1": [item.to_dict() for item in layer1_items],
            "l2": [item.to_dict() for item in layer2_items],
            "l3": [item.to_dict() for item in layer3_items],
        },
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = len(layer1_items) + len(layer2_items) + len(layer3_items)
    logger.info(
        f"데이터 저장: {path} "
        f"(L1 {len(layer1_items)} + L2 {len(layer2_items)} + L3 {len(layer3_items)} = {total}건 / "
        f"키워드 {len(keyword_results)}개)"
    )
    return path


def load_daily_data(path: Path) -> dict | None:
    """저장된 데이터 로드"""
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"데이터 로드 실패: {path} → {e}")
        return None


def load_yesterday_keywords() -> list[str] | None:
    """
    어제 TOP 키워드 리스트 로드

    Returns:
        어제 키워드 리스트 (없으면 None)
    """
    path = get_yesterday_path()
    data = load_daily_data(path)
    if data is None:
        logger.info("어제 데이터 없음 → 어제 대비 비교 생략")
        return None

    keywords_data = data.get("keywords", [])
    if not keywords_data:
        logger.info("어제 키워드 없음 → 어제 대비 비교 생략")
        return None

    yesterday_keywords = [kw.get("keyword", "") for kw in keywords_data if kw.get("keyword")]
    logger.info(f"어제 키워드 {len(yesterday_keywords)}개 로드 완료")
    return yesterday_keywords


def cleanup_old_data(keep_days: int = 7):
    """오래된 데이터 정리 (기본 7일 보관)"""
    cutoff = datetime.now(KST) - timedelta(days=keep_days)
    removed = 0

    for file in config.DATA_DIR.glob("*.json"):
        try:
            file_date = datetime.strptime(file.stem, "%Y-%m-%d").replace(tzinfo=KST)
            if file_date < cutoff:
                file.unlink()
                removed += 1
        except ValueError:
            continue

    if removed:
        logger.info(f"오래된 데이터 {removed}개 삭제 (보관 기간: {keep_days}일)")


def load_recent_keyword_sets(days: int) -> list[set[str]]:
    """
    최근 N일(오늘 제외) 키워드 세트 로드 (연속 등장 판별용)

    Returns:
        [어제 키워드 세트, 그제 키워드 세트, ...] (없는 날은 빈 세트)
    """
    result: list[set[str]] = []
    for i in range(1, days + 1):
        day = (datetime.now(KST) - timedelta(days=i)).strftime("%Y-%m-%d")
        path = config.DATA_DIR / f"{day}.json"
        data = load_daily_data(path)
        if data is None:
            result.append(set())
            continue
        kws = {
            kw.get("keyword", "")
            for kw in data.get("keywords", [])
            if kw.get("keyword")
        }
        result.append(kws)
    return result
