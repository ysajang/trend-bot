"""
JSON 파일 기반 데이터 저장
- 일별로 수집 데이터 저장
- 전일 데이터와 비교를 위한 로드
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

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


def save_daily_data(posts: list[dict], keywords: list[dict]) -> Path:
    """오늘 데이터 저장"""
    path = get_today_path()
    data = {
        "date": datetime.now(KST).isoformat(),
        "post_count": len(posts),
        "posts": posts,
        "keywords": keywords,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"데이터 저장: {path} ({len(posts)}개 게시글, {len(keywords)}개 키워드)")
    return path


def load_daily_data(path: Path) -> dict | None:
    """저장된 데이터 로드"""
    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


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
