"""
장르 공백 스캐너 (신호4)

목적: "1인 개발자가 어떤 장르를 만들면 좋은가"에 데이터로 답한다.

원리:
  태그 2개 조합의 시장을 3축으로 평가
    1. 공급 (게임 수): 적을수록 경쟁 낮음
    2. 수요 (상위작 리뷰 수): 많을수록 시장 존재 증명
    3. 만족도 (상위작 평점): 높을수록 장르 자체가 먹힘
  → 공급은 적은데 수요·만족도가 높은 조합 = 빈 시장

사용 API (전부 Steam 공식 / robots.txt 허용):
  - store/search/results  : 태그 조합별 총 게임 수 + 목록
  - store/appreviews      : 게임별 리뷰 수/평점
  - IStoreService/GetTagList : 태그 ID 목록 (검증용)
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from itertools import combinations

import aiohttp

import config

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start=0&count={count}&tags={tags}&category1=998{extra}"
    "&infinite=1&json=1&cc=KR&l=koreana"
)
REVIEWS_URL = (
    "https://store.steampowered.com/appreviews/{appid}"
    "?json=1&num_per_page=0&language=all&purchase_type=all"
)

HEADERS = {"User-Agent": "Mozilla/5.0 (TrendRadar/2.3)"}
TIMEOUT = aiohttp.ClientTimeout(total=20)
REQUEST_DELAY = 0.5  # Steam 부하 배려

GAP_CACHE_PATH = config.DATA_DIR / "steam_genre_gap.json"

# ──────────────────────────────────────────────
# 1인 개발 적합 태그 (2026-08-29 GetTagList 검증 완료)
# 대규모 자본/인력이 필요한 태그(오픈월드, MMO 등)는 제외
# ──────────────────────────────────────────────
SOLO_DEV_TAGS: dict[str, int] = {
    "로그라이크": 1716,
    "로그라이트": 3959,
    "덱빌딩": 32322,
    "카드 게임": 1666,
    "퍼즐": 1664,
    "턴제 전략": 1741,
    "타워 디펜스": 1645,
    "자동화": 255534,
    "자원관리": 8945,
    "경영": 12472,
    "도시 건설": 4328,
    "생존": 1662,
    "크래프팅": 1702,
    "메트로배니아": 1628,
    "2D 플랫폼": 5379,
    "픽셀 그래픽": 3964,
    "탑다운": 4791,
    "비주얼 노벨": 3799,
    "텍스트 기반": 31275,
    "추리": 5613,
    "공포": 1667,
    "귀여운": 4726,
}

# 상위 게임 샘플링 수 (조합당)
SAMPLE_SIZE = 5
# 공급 과잉으로 간주해 제외할 게임 수 상한
MAX_SUPPLY = 4000
# 시장 부재로 간주해 제외할 게임 수 하한
MIN_SUPPLY = 30


@dataclass
class GenreGap:
    """태그 조합 시장 분석 결과"""
    tag_names: tuple[str, str]
    tag_ids: tuple[int, int]
    supply: int = 0                  # 총 게임 수 (경쟁 강도)
    median_reviews: float = 0.0      # 최근 신작 리뷰 수 중앙값 (현실적 기대치)
    top_reviews: int = 0             # 장르 1등 리뷰 수 (시장 상한)
    positive_ratio: float = 0.0      # 최근 신작 평균 긍정 비율 (장르 만족도)
    recent_titles: list[str] = field(default_factory=list)
    top_title: str = ""
    opportunity_score: float = 0.0   # 종합 기회 점수

    def to_dict(self) -> dict:
        return {
            "tags": list(self.tag_names),
            "tag_ids": list(self.tag_ids),
            "supply": self.supply,
            "median_reviews": round(self.median_reviews, 1),
            "top_reviews": self.top_reviews,
            "positive_ratio": round(self.positive_ratio, 3),
            "recent_titles": self.recent_titles,
            "top_title": self.top_title,
            "opportunity_score": round(self.opportunity_score, 2),
        }


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status}: {url[:70]}")
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"요청 실패 ({str(e)[:40]}): {url[:70]}")
        return None


def _parse_appids(results_html: str, limit: int) -> tuple[list[str], list[str]]:
    """검색 결과 HTML에서 appid와 게임명 추출"""
    appids = re.findall(r'data-ds-appid="(\d+)"', results_html)
    titles = re.findall(r'<span class="title">([^<]+)</span>', results_html)
    return appids[:limit], titles[:limit]


async def _fetch_combo(
    session: aiohttp.ClientSession, tag_ids: tuple[int, int], extra: str = "",
) -> tuple[int, list[str], list[str]]:
    """태그 조합의 총 게임 수 + 게임 목록 (extra로 정렬 지정)"""
    url = SEARCH_URL.format(
        count=SAMPLE_SIZE, tags=f"{tag_ids[0]},{tag_ids[1]}", extra=extra,
    )
    data = await _fetch_json(session, url)
    if not data:
        return 0, [], []
    total = data.get("total_count", 0) or 0
    appids, titles = _parse_appids(data.get("results_html", ""), SAMPLE_SIZE)
    return total, appids, titles


async def _fetch_review_stats(
    session: aiohttp.ClientSession, appid: str,
) -> tuple[int, float]:
    """게임의 리뷰 수 + 긍정 비율"""
    data = await _fetch_json(session, REVIEWS_URL.format(appid=appid))
    if not data or not data.get("success"):
        return 0, 0.0
    qs = data.get("query_summary", {})
    total = qs.get("total_reviews", 0) or 0
    positive = qs.get("total_positive", 0) or 0
    ratio = (positive / total) if total > 0 else 0.0
    return total, ratio


def _compute_opportunity_score(gap: GenreGap) -> float:
    """
    기회 점수 = 신작 실적 x 장르 만족도 / 경쟁 강도

    핵심 설계 의도:
      - median_reviews(최근 신작 중앙값)를 쓴다.
        1등의 성적(top_reviews)은 신규 진입자가 재현할 수 없으므로 점수에서 제외.
        "지금 새로 내면 대략 이 정도"가 현실적 기대치.
      - positive_ratio 0.7 미만은 장르 자체가 안 먹히는 신호로 보고 감점.
      - supply가 클수록 경쟁이 심하므로 log로 나눈다.
    """
    import math

    if gap.supply <= 0 or gap.median_reviews <= 0:
        return 0.0

    demand = math.log10(gap.median_reviews + 1)
    satisfaction = (
        gap.positive_ratio if gap.positive_ratio >= 0.7 else gap.positive_ratio * 0.5
    )
    competition = math.log10(gap.supply + 1)

    return (demand * satisfaction * 100) / max(competition, 1.0)


async def scan_genre_gaps(max_combos: int | None = None) -> list[GenreGap]:
    """
    태그 2개 조합을 스캔해 빈 시장 랭킹 산출

    Args:
        max_combos: 스캔할 조합 수 상한 (None이면 전체)

    Returns:
        opportunity_score 내림차순 GenreGap 리스트
    """
    tag_items = list(SOLO_DEV_TAGS.items())
    combos = list(combinations(tag_items, 2))
    if max_combos:
        combos = combos[:max_combos]

    logger.info(f"장르 공백 스캔 시작: {len(combos)}개 조합")
    results: list[GenreGap] = []

    async with aiohttp.ClientSession() as session:
        for (name_a, id_a), (name_b, id_b) in combos:
            # 1) 최근 출시작 샘플 (신규 진입자의 현실적 기대치)
            supply, recent_ids, recent_titles = await _fetch_combo(
                session, (id_a, id_b), extra="&sort_by=Released_DESC",
            )
            await asyncio.sleep(REQUEST_DELAY)

            if supply < MIN_SUPPLY or supply > MAX_SUPPLY or not recent_ids:
                continue

            review_counts: list[int] = []
            ratios: list[float] = []
            for appid in recent_ids:
                cnt, ratio = await _fetch_review_stats(session, appid)
                if cnt > 0:
                    review_counts.append(cnt)
                    ratios.append(ratio)
                await asyncio.sleep(REQUEST_DELAY)

            if not review_counts:
                continue

            # 2) 장르 1등 (시장 상한 참고값 — 점수에는 미반영)
            _, top_ids, top_titles = await _fetch_combo(
                session, (id_a, id_b), extra="&sort_by=Reviews_DESC",
            )
            await asyncio.sleep(REQUEST_DELAY)
            top_reviews = 0
            top_title = ""
            if top_ids:
                top_reviews, _ = await _fetch_review_stats(session, top_ids[0])
                top_title = top_titles[0] if top_titles else ""
                await asyncio.sleep(REQUEST_DELAY)

            sorted_counts = sorted(review_counts)
            mid = len(sorted_counts) // 2
            median = (
                float(sorted_counts[mid])
                if len(sorted_counts) % 2 == 1
                else (sorted_counts[mid - 1] + sorted_counts[mid]) / 2.0
            )

            gap = GenreGap(
                tag_names=(name_a, name_b),
                tag_ids=(id_a, id_b),
                supply=supply,
                median_reviews=median,
                top_reviews=top_reviews,
                positive_ratio=sum(ratios) / len(ratios),
                recent_titles=recent_titles[:3],
                top_title=top_title,
            )
            gap.opportunity_score = _compute_opportunity_score(gap)
            results.append(gap)

    results.sort(key=lambda g: g.opportunity_score, reverse=True)
    logger.info(f"장르 공백 스캔 완료: {len(results)}개 유효 조합")
    _save_cache(results)
    return results


def _save_cache(gaps: list[GenreGap]) -> None:
    """결과 캐시 저장 (추이 비교용)"""
    try:
        GAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GAP_CACHE_PATH.write_text(
            json.dumps([g.to_dict() for g in gaps], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"장르 공백 캐시 저장 실패: {e}")
