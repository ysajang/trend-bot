"""
규칙기반 리포트 생성기 (v2 — LLM 제거)

입력: Layer 1/2/3 TrendItem[] + KeywordResult[]
출력: ReportData 구조체 (섹션별 데이터)

처리 규칙:
  1. 키워드 TOP 10 (빈도순) + DataLab 트렌드 태그 매핑
  2. 뉴스 클러스터링: 키워드별 대표 뉴스 1건 (최신 + 가장 긴 summary)
  3. 카테고리별 키워드 분포 (Layer 1 RSS 기준)
  4. 7일 추이 요약: 급상승/신규 키워드 하이라이트
  5. 어제 대비 변화: 신규/이탈 키워드
"""

import logging
from dataclasses import dataclass, field

from collector.trend_item import TrendItem
from analyzer.keyword_extractor import KeywordResult

logger = logging.getLogger(__name__)


@dataclass
class TopKeyword:
    """TOP 키워드 + DataLab 트렌드 태그 + 대표 뉴스"""
    keyword: str
    count: int
    trend_tag: str = "—"
    source_tag: str = ""  # 📰미디어주도 / 🔍대중관심 / 🔥진짜트렌드 / 빈 문자열
    representative_news_title: str = ""
    representative_news_url: str = ""

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "count": self.count,
            "trend_tag": self.trend_tag,
            "source_tag": self.source_tag,
            "news_title": self.representative_news_title,
            "news_url": self.representative_news_url,
        }


@dataclass
class TrendChange:
    """어제 대비 키워드 변화"""
    new_keywords: list[str] = field(default_factory=list)
    dropped_keywords: list[str] = field(default_factory=list)


@dataclass
class ReportData:
    """리포트 전체 데이터 구조체"""
    generated_at: str = ""
    total_rss: int = 0
    total_news: int = 0
    total_datalab: int = 0

    # 섹션 1: TOP 키워드
    top_keywords: list[TopKeyword] = field(default_factory=list)

    # 섹션 2: 카테고리별 분포 (RSS 기준)
    category_distribution: dict[str, int] = field(default_factory=dict)

    # 섹션 3: 트렌드 하이라이트 (태그별 키워드 리스트)
    trend_highlights: dict[str, list[str]] = field(default_factory=dict)

    # 섹션 4: 어제 대비 변화
    yesterday_diff: TrendChange | None = None

    # 섹션 5: 엔터/문화/게임 트렌드 TOP (엔터 카테고리 전용)
    entertainment_keywords: list[TopKeyword] = field(default_factory=list)


def _map_datalab_tags(
    layer3_items: list[TrendItem],
) -> dict[str, str]:
    """
    Layer 3 TrendItem에서 keyword → trend_tag 매핑 추출

    Returns:
        {"삼성전자": "급상승", "비트코인": "지속상승", ...}
    """
    tag_map: dict[str, str] = {}
    for item in layer3_items:
        if item.source_type != "datalab":
            continue
        tag = item.raw_meta.get("trend_tag", "유지")
        for kw in item.keywords:
            tag_map[kw] = tag
    return tag_map


def _map_datalab_ratios(
    layer3_items: list[TrendItem],
) -> dict[str, float]:
    """
    Layer 3 TrendItem에서 keyword → change_ratio 매핑 추출

    Returns:
        {"삼성전자": 2.5, "비트코인": 1.1, ...}
    """
    ratio_map: dict[str, float] = {}
    for item in layer3_items:
        if item.source_type != "datalab":
            continue
        change_ratio = item.raw_meta.get("change_ratio", 0.0)
        for kw in item.keywords:
            ratio_map[kw] = change_ratio
    return ratio_map


def _compute_source_tag(
    keyword_count: int,
    median_count: float,
    change_ratio: float,
) -> str:
    """
    기사빈도 vs DataLab 검색량 크로스체크 → source_tag 결정

    규칙:
      기사빈도 높음 (중위수 이상) + 검색량 높음 (change_ratio >= 1.5) → 🔥 진짜트렌드
      기사빈도 높음 + 검색량 낮음 (change_ratio < 0.8) → 📰 미디어주도
      기사빈도 낮음 + 검색량 높음 → 🔍 대중관심
      그 외 → 빈 문자열

    Args:
        keyword_count: 해당 키워드의 기사 출현 빈도
        median_count: TOP 10 키워드 기사빈도의 중위수
        change_ratio: DataLab 최근값/평균 비율
    """
    is_high_media = keyword_count >= median_count
    is_high_search = change_ratio >= 1.5
    is_low_search = change_ratio < 0.8

    if is_high_media and is_high_search:
        return "🔥 진짜트렌드"
    elif is_high_media and is_low_search:
        return "📰 미디어주도"
    elif not is_high_media and is_high_search:
        return "🔍 대중관심"
    return ""


def _compute_relevance_score(
    keyword: str,
    item: TrendItem,
    all_top_keywords: list[str] | None = None,
) -> float:
    """
    키워드-뉴스 맥락 일치도 점수 계산

    점수 구성:
      - 제목 내 키워드 위치 (앞쪽일수록 높음): 0~30점
      - 제목 내 키워드 비중 (제목 대비 키워드 길이): 0~20점
      - 검색 키워드 직접 매치 (L2 search_keyword): 15점
      - 동일 클러스터 내 다른 TOP 키워드 동시 출현: 키워드당 10점 (최대 30점)
      - summary 정보량: 0~5점

    Returns:
        0.0 이상의 relevance 점수 (높을수록 관련성 높음)
    """
    score = 0.0
    kw_lower = keyword.lower()
    title_lower = item.title.lower()
    summary_lower = item.summary.lower()
    top_kws = all_top_keywords or []

    title_has_kw = kw_lower in title_lower
    summary_has_kw = kw_lower in summary_lower
    search_kw_match = any(kw_lower == k.lower() for k in item.keywords)

    # 제목에도 summary에도 없고 search_keyword도 아니면 매칭 불가
    if not title_has_kw and not summary_has_kw and not search_kw_match:
        return 0.0

    # ── 1. 제목 내 키워드 위치 가중치 (0~30점) ──
    if title_has_kw:
        pos = title_lower.index(kw_lower)
        title_len = max(len(title_lower), 1)
        # 앞쪽 등장 = 높은 점수 (위치비율 0.0~1.0 → 30~0점)
        position_ratio = pos / title_len
        score += 30.0 * (1.0 - position_ratio)
    elif summary_has_kw:
        # summary에만 있으면 약한 매칭 (5점)
        score += 5.0

    # ── 2. 제목 내 키워드 비중 (0~20점) ──
    if title_has_kw:
        coverage = len(keyword) / max(len(item.title), 1)
        score += min(coverage * 100, 20.0)

    # ── 3. search_keyword 직접 매치 보너스 (15점) ──
    if search_kw_match:
        score += 15.0

    # ── 4. 다른 TOP 키워드 동시 출현 (키워드당 10점 최대 30점) ──
    co_occurrence_bonus = 0.0
    combined_text = title_lower + " " + summary_lower
    for other_kw in top_kws:
        if other_kw.lower() == kw_lower:
            continue
        if other_kw.lower() in combined_text:
            co_occurrence_bonus += 10.0
            if co_occurrence_bonus >= 30.0:
                break
    score += co_occurrence_bonus

    # ── 5. summary 정보량 보너스 (0~5점) ──
    summary_len = len(item.summary)
    score += min(summary_len / 100.0, 5.0)

    return score


def _find_best_news(
    keyword: str,
    candidate_items: list[TrendItem],
    used_urls: set[str] | None = None,
    all_top_keywords: list[str] | None = None,
    allowed_source_types: tuple[str, ...] = ("naver_news",),
) -> TrendItem | None:
    """
    키워드에 매칭되는 뉴스 중 대표 1건 선정

    선정 기준: relevance score (맥락 일치도) 최고점
    동점 시: published_at 최신순

    Args:
        used_urls: 이미 선정된 뉴스 URL (중복 배제용)
        all_top_keywords: 전체 TOP 키워드 리스트 (동시 출현 보너스 계산용)
        allowed_source_types: 대표뉴스로 허용할 source_type
            (종합=naver_news / 엔터=rss)
    """
    exclude = used_urls or set()
    scored: list[tuple[float, TrendItem]] = []

    for item in candidate_items:
        if item.source_type not in allowed_source_types:
            continue
        if item.url in exclude:
            continue

        rel_score = _compute_relevance_score(keyword, item, all_top_keywords)
        if rel_score > 0.0:
            scored.append((rel_score, item))

    if not scored:
        return None

    # relevance score 내림차순 → published_at 내림차순 (동점 시)
    scored.sort(
        key=lambda x: (x[0], x[1].published_at or ""),
        reverse=True,
    )
    return scored[0][1]


def _compute_category_distribution(
    layer1_items: list[TrendItem],
) -> dict[str, int]:
    """Layer 1 RSS 아이템의 카테고리별 분포"""
    dist: dict[str, int] = {}
    for item in layer1_items:
        cat = item.category or "기타"
        dist[cat] = dist.get(cat, 0) + 1
    # 건수 내림차순 정렬
    return dict(sorted(dist.items(), key=lambda x: x[1], reverse=True))


def _compute_trend_highlights(
    tag_map: dict[str, str],
) -> dict[str, list[str]]:
    """
    DataLab 태그별 키워드 그룹핑

    Returns:
        {"급상승": ["삼성전자", ...], "신규": [...], ...}
    """
    highlights: dict[str, list[str]] = {}
    for kw, tag in tag_map.items():
        if tag not in highlights:
            highlights[tag] = []
        highlights[tag].append(kw)
    return highlights


def compute_yesterday_diff(
    today_keywords: list[str],
    yesterday_keywords: list[str],
) -> TrendChange:
    """
    어제 대비 키워드 변화 계산

    Args:
        today_keywords: 오늘 TOP 키워드 리스트
        yesterday_keywords: 어제 TOP 키워드 리스트

    Returns:
        TrendChange (신규 키워드 / 이탈 키워드)
    """
    today_set = set(today_keywords)
    yesterday_set = set(yesterday_keywords)

    return TrendChange(
        new_keywords=sorted(today_set - yesterday_set),
        dropped_keywords=sorted(yesterday_set - today_set),
    )


def _build_top_keywords(
    keyword_results: list[KeywordResult],
    candidate_items: list[TrendItem],
    tag_map: dict[str, str],
    ratio_map: dict[str, float],
    limit: int,
    used_news_urls: set[str],
    allowed_source_types: tuple[str, ...] = ("naver_news",),
) -> list[TopKeyword]:
    """
    KeywordResult 리스트 → TopKeyword 리스트 변환
    (대표뉴스 매칭 + DataLab source_tag 계산 포함)
    """
    results: list[TopKeyword] = []
    all_kw_names = [kw.keyword for kw in keyword_results[:limit]]

    counts = [kw.count for kw in keyword_results[:limit]]
    if counts:
        sorted_counts = sorted(counts)
        mid = len(sorted_counts) // 2
        median_count = (
            sorted_counts[mid]
            if len(sorted_counts) % 2 == 1
            else (sorted_counts[mid - 1] + sorted_counts[mid]) / 2.0
        )
    else:
        median_count = 0.0

    for kw_result in keyword_results[:limit]:
        best_news = _find_best_news(
            kw_result.keyword, candidate_items, used_news_urls, all_kw_names,
            allowed_source_types=allowed_source_types,
        )
        if best_news and best_news.url:
            used_news_urls.add(best_news.url)

        change_ratio = ratio_map.get(kw_result.keyword, 0.0)
        source_tag = _compute_source_tag(kw_result.count, median_count, change_ratio)

        results.append(TopKeyword(
            keyword=kw_result.keyword,
            count=kw_result.count,
            trend_tag=tag_map.get(kw_result.keyword, "—"),
            source_tag=source_tag,
            representative_news_title=best_news.title if best_news else "",
            representative_news_url=best_news.url if best_news else "",
        ))
    return results


def build_report(
    layer1_items: list[TrendItem],
    layer2_items: list[TrendItem],
    layer3_items: list[TrendItem],
    keyword_results: list[KeywordResult],
    yesterday_keywords: list[str] | None = None,
    entertainment_results: list[KeywordResult] | None = None,
) -> ReportData:
    """
    규칙기반 리포트 생성 (메인 함수)

    Args:
        layer1_items: RSS 수집 결과
        layer2_items: Naver News 수집 결과
        layer3_items: DataLab 검증 결과
        keyword_results: 종합 키워드 추출 결과 (빈도순, 엔터 제외)
        yesterday_keywords: 어제 TOP 키워드 (없으면 diff 생략)
        entertainment_results: 엔터/문화/게임 키워드 추출 결과 (빈도순)

    Returns:
        ReportData 구조체
    """
    logger.info("규칙기반 리포트 생성 시작")

    # 1. DataLab 태그 매핑 + change_ratio 매핑
    tag_map = _map_datalab_tags(layer3_items)
    ratio_map = _map_datalab_ratios(layer3_items)

    # 2. 종합 TOP 10 키워드 (대표뉴스 중복 배제)
    used_news_urls: set[str] = set()
    top_keywords = _build_top_keywords(
        keyword_results, layer2_items, tag_map, ratio_map, 10, used_news_urls,
    )

    # 2-1. 엔터/문화/게임 TOP 5 키워드 (대표뉴스는 RSS 엔터 기사에서)
    entertainment_keywords: list[TopKeyword] = []
    if entertainment_results:
        ent_source_items = [
            it for it in layer1_items
            if it.category in ("게임", "문화", "연예")
        ]
        entertainment_keywords = _build_top_keywords(
            entertainment_results, ent_source_items, tag_map, ratio_map, 5,
            used_news_urls, allowed_source_types=("rss",),
        )

    # 3. 카테고리별 분포
    category_dist = _compute_category_distribution(layer1_items)

    # 4. 트렌드 하이라이트 (TOP 10 키워드만 포함)
    top_kw_set = {kw.keyword for kw in top_keywords}
    filtered_tag_map = {k: v for k, v in tag_map.items() if k in top_kw_set}
    trend_highlights = _compute_trend_highlights(filtered_tag_map)

    # 5. 어제 대비 변화
    yesterday_diff = None
    if yesterday_keywords is not None:
        today_kws = [kw.keyword for kw in top_keywords]
        yesterday_diff = compute_yesterday_diff(today_kws, yesterday_keywords)

    report = ReportData(
        total_rss=len(layer1_items),
        total_news=len(layer2_items),
        total_datalab=len(layer3_items),
        top_keywords=top_keywords,
        category_distribution=category_dist,
        trend_highlights=trend_highlights,
        yesterday_diff=yesterday_diff,
        entertainment_keywords=entertainment_keywords,
    )

    logger.info(
        f"리포트 생성 완료: "
        f"TOP {len(top_keywords)} 키워드 / "
        f"엔터 {len(entertainment_keywords)} 키워드 / "
        f"카테고리 {len(category_dist)}종 / "
        f"트렌드 태그 {len(trend_highlights)}종"
    )
    return report
