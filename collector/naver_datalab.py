"""
Layer 3 — Naver DataLab 검색어트렌드 검증기

Layer 1+2에서 발견된 키워드의 검색량 추이를 확인하여
"급상승 / 지속상승 / 하락세 / 신규 / 유지" 태그를 부여합니다.

API: POST https://openapi.naver.com/v1/datalab/search
일일 한도: 1,000회 (검색 API와 별도 카운트)
제약: 1요청당 최대 5개 키워드 그룹
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp

from collector.trend_item import TrendItem
import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

DATALAB_API_URL = "https://openapi.naver.com/v1/datalab/search"

# 트렌드 분류 태그
TREND_SURGE = "급상승"       # 최근 급등
TREND_RISING = "지속상승"    # 3일 이상 연속 상승
TREND_FALLING = "하락세"     # 하락 추세
TREND_NEW = "신규"           # 최근 새로 등장
TREND_STEADY = "유지"        # 변동 없음

# DataLab 요청당 키워드 그룹 최대 수
MAX_GROUPS_PER_REQUEST = 5


def classify_trend(ratios: list[float]) -> str:
    """
    7일 검색량 비율 데이터로 트렌드 분류

    Args:
        ratios: 7일간 일별 검색량 비율 (0~100, 최대값=100 기준 상대값)
                index 0 = 가장 오래된 날 / index -1 = 가장 최근

    Returns:
        트렌드 태그 문자열
    """
    if not ratios or len(ratios) < 2:
        return TREND_STEADY

    # 모든 값이 0이면 데이터 없음
    if all(v == 0 for v in ratios):
        return TREND_STEADY

    today = ratios[-1]
    yesterday = ratios[-2]

    # ── 신규: 초반 4일 모두 0이고 최근 3일 중 값 존재 ──
    if len(ratios) >= 7:
        early = ratios[:4]
        late = ratios[4:]
        if all(v == 0 for v in early) and any(v > 0 for v in late):
            return TREND_NEW

    # ── 급상승: 오늘이 이전 평균의 2배 이상 ──
    prev_days = ratios[:-1]
    prev_avg = sum(prev_days) / len(prev_days) if prev_days else 0
    if prev_avg > 0 and today >= prev_avg * 2:
        return TREND_SURGE
    # 이전이 거의 0인데 오늘 30 이상이면 급상승
    if prev_avg < 5 and today >= 30:
        return TREND_SURGE

    # ── 지속상승: 최근 3일 연속 상승 ──
    if len(ratios) >= 3:
        last3 = ratios[-3:]
        if last3[0] < last3[1] < last3[2] and last3[2] > 0:
            return TREND_RISING

    # ── 하락세: 최근 3일 연속 하락 또는 오늘이 최대값의 50% 미만 ──
    if len(ratios) >= 3:
        last3 = ratios[-3:]
        if last3[0] > last3[1] > last3[2]:
            return TREND_FALLING
    max_val = max(ratios)
    if max_val > 0 and today < max_val * 0.5:
        return TREND_FALLING

    return TREND_STEADY


def _build_request_body(
    keywords: list[str],
    days: int = 7,
) -> dict:
    """DataLab API 요청 본문 생성"""
    now_kst = datetime.now(KST)
    end_date = now_kst.strftime("%Y-%m-%d")
    start_date = (now_kst - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    keyword_groups = []
    for kw in keywords[:MAX_GROUPS_PER_REQUEST]:
        keyword_groups.append({
            "groupName": kw,
            "keywords": [kw],
        })

    return {
        "startDate": start_date,
        "endDate": end_date,
        "timeUnit": "date",
        "keywordGroups": keyword_groups,
    }


def _parse_response(data: dict) -> dict[str, list[float]]:
    """
    DataLab API 응답 파싱

    Returns:
        {keyword: [ratio_day1, ratio_day2, ..., ratio_day7]}
    """
    result: dict[str, list[float]] = {}
    for group in data.get("results", []):
        keyword = group.get("title", "")
        ratios = [
            item.get("ratio", 0.0)
            for item in sorted(group.get("data", []), key=lambda x: x.get("period", ""))
        ]
        if keyword:
            result[keyword] = ratios
    return result


async def _query_datalab(
    session: aiohttp.ClientSession,
    keywords: list[str],
    days: int = 7,
) -> dict[str, list[float]]:
    """
    DataLab API 단일 호출 (최대 5 키워드)

    Returns:
        {keyword: [ratio_day1, ..., ratio_dayN]}
    """
    if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
        logger.error("NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 설정되지 않았습니다")
        return {}

    body = _build_request_body(keywords, days)
    headers = {
        "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
        "Content-Type": "application/json",
    }

    try:
        async with session.post(
            DATALAB_API_URL,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 429:
                logger.warning("DataLab 429 Too Many Requests")
                return {}
            if resp.status != 200:
                resp_body = await resp.text()
                logger.warning(f"DataLab HTTP {resp.status}: {resp_body[:200]}")
                return {}

            data = await resp.json()
            return _parse_response(data)

    except asyncio.TimeoutError:
        logger.warning("DataLab 타임아웃")
    except aiohttp.ClientError as e:
        logger.warning(f"DataLab 연결 오류: {e}")
    except Exception as e:
        logger.exception(f"DataLab 예상치 못한 오류: {e}")

    return {}


async def verify_trends(
    keywords: list[str],
    days: int = 7,
) -> list[TrendItem]:
    """
    키워드 리스트의 검색 트렌드 검증 (Layer 3 메인 함수)

    Args:
        keywords: 검증할 키워드 리스트 (Layer 1+2에서 추출)
        days: 추이 조회 기간 (기본 7일)

    Returns:
        트렌드 태그가 부여된 TrendItem 리스트
    """
    if not keywords:
        logger.warning("검증할 키워드가 없습니다")
        return []

    logger.info(f"DataLab 트렌드 검증 시작: {len(keywords)}개 키워드 / {days}일")

    # 5개씩 배치로 나눠서 API 호출
    all_trends: dict[str, list[float]] = {}

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(keywords), MAX_GROUPS_PER_REQUEST):
            batch = keywords[i:i + MAX_GROUPS_PER_REQUEST]
            result = await _query_datalab(session, batch, days)
            all_trends.update(result)

            # 배치 간 대기 (QPS 준수)
            if i + MAX_GROUPS_PER_REQUEST < len(keywords):
                await asyncio.sleep(0.5)

    # 트렌드 분류 + TrendItem 생성
    items: list[TrendItem] = []
    for keyword in keywords:
        ratios = all_trends.get(keyword, [])
        trend_tag = classify_trend(ratios)

        # signal_score: 최근값 / 기간평균 비율 (변화 강도)
        avg_val = sum(ratios) / len(ratios) if ratios else 0
        last_val = ratios[-1] if ratios else 0
        change_ratio = (last_val / avg_val) if avg_val > 0 else 0.0

        items.append(TrendItem(
            source="datalab",
            source_type="datalab",
            title=f"검색 트렌드: {keyword}",
            summary=f"{trend_tag} | 7일 추이: {' → '.join(f'{r:.0f}' for r in ratios)}" if ratios else f"{trend_tag} | 데이터 없음",
            signal_score=round(change_ratio, 2),
            keywords=[keyword],
            raw_meta={
                "trend_tag": trend_tag,
                "ratios": ratios,
                "days": days,
                "change_ratio": round(change_ratio, 2),
            },
        ))

        logger.info(
            f"[datalab:{keyword}] {trend_tag} "
            f"(변화율: {change_ratio:.1f}x / "
            f"추이: {[f'{r:.0f}' for r in ratios[-3:]]})"
        )

    # 태그별 통계
    tag_counts: dict[str, int] = {}
    for item in items:
        tag = item.raw_meta.get("trend_tag", TREND_STEADY)
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
    logger.info(f"DataLab 검증 완료: {dict(sorted(tag_counts.items()))}")

    return items
