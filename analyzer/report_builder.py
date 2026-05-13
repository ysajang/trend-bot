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
    representative_news_title: str = ""
    representative_news_url: str = ""

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "count": self.count,
            "trend_tag": self.trend_tag,
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


def _find_best_news(
    keyword: str,
    layer2_items: list[TrendItem],
    used_urls: set[str] | None = None,
) -> TrendItem | None:
    """
    키워드에 매칭되는 L2 뉴스 중 대표 1건 선정

    선정 기준:
      1차: summary 길이 (정보량)
      2차: published_at 최신순 (동률 시)

    Args:
        used_urls: 이미 선정된 뉴스 URL (중복 배제용)
    """
    candidates: list[TrendItem] = []
    kw_lower = keyword.lower()
    exclude = used_urls or set()

    for item in layer2_items:
        if item.source_type != "naver_news":
            continue
        # 이미 다른 키워드에 배정된 뉴스 제외
        if item.url in exclude:
            continue
        # 키워드가 제목 또는 summary에 포함
        if kw_lower in item.title.lower() or kw_lower in item.summary.lower():
            candidates.append(item)
        # 또는 item.keywords에 포함 (L2 수집 시 search_keyword 저장됨)
        elif any(kw_lower == k.lower() for k in item.keywords):
            candidates.append(item)

    if not candidates:
        return None

    # summary 길이 내림차순 → published_at 내림차순
    candidates.sort(
        key=lambda x: (len(x.summary), x.published_at or ""),
        reverse=True,
    )
    return candidates[0]


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


def build_report(
    layer1_items: list[TrendItem],
    layer2_items: list[TrendItem],
    layer3_items: list[TrendItem],
    keyword_results: list[KeywordResult],
    yesterday_keywords: list[str] | None = None,
) -> ReportData:
    """
    규칙기반 리포트 생성 (메인 함수)

    Args:
        layer1_items: RSS 수집 결과
        layer2_items: Naver News 수집 결과
        layer3_items: DataLab 검증 결과
        keyword_results: 키워드 추출 결과 (빈도순)
        yesterday_keywords: 어제 TOP 키워드 (없으면 diff 생략)

    Returns:
        ReportData 구조체
    """
    logger.info("규칙기반 리포트 생성 시작")

    # 1. DataLab 태그 매핑
    tag_map = _map_datalab_tags(layer3_items)

    # 2. TOP 10 키워드 + 태그 + 대표뉴스 (중복 배제)
    top_keywords: list[TopKeyword] = []
    used_news_urls: set[str] = set()
    for kw_result in keyword_results[:10]:
        best_news = _find_best_news(kw_result.keyword, layer2_items, used_news_urls)
        if best_news and best_news.url:
            used_news_urls.add(best_news.url)
        top_keywords.append(TopKeyword(
            keyword=kw_result.keyword,
            count=kw_result.count,
            trend_tag=tag_map.get(kw_result.keyword, "—"),
            representative_news_title=best_news.title if best_news else "",
            representative_news_url=best_news.url if best_news else "",
        ))

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
    )

    logger.info(
        f"리포트 생성 완료: "
        f"TOP {len(top_keywords)} 키워드 / "
        f"카테고리 {len(category_dist)}종 / "
        f"트렌드 태그 {len(trend_highlights)}종"
    )
    return report
