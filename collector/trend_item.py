"""
TrendRadar 통합 데이터 모델
- Layer 1 (RSS) / Layer 2 (Naver News API) / Layer 3 (DataLab) 모든 수집기는
  TrendItem 형식으로 출력
- analyzer/notifier는 source_type 분기로 안전하게 처리
"""

from dataclasses import dataclass, field, asdict
from typing import Literal, get_args

# 허용된 소스 타입 (오타 방지용 화이트리스트)
SourceType = Literal["rss", "naver_news", "datalab"]
ALLOWED_SOURCE_TYPES: tuple[str, ...] = get_args(SourceType)


@dataclass
class TrendItem:
    """
    수집 파이프라인 통합 데이터 모델

    Fields:
        source: 세부 소스 식별자 (예: "rss:hani", "naver_news", "datalab")
        source_type: 소스 분류 ("rss" | "naver_news" | "datalab")
        title: 제목 (필수, 비어있으면 ValueError)
        summary: 요약/본문 미리보기 (HTML 태그 정제 후)
        url: 원본 URL
        published_at: ISO8601 KST 형식 (예: "2026-05-05T14:30:00+09:00")
        category: 카테고리 라벨 ("종합" | "경제" | "IT" | "정책" | "")
        signal_score: 신호 강도 (소스별 의미 다름)
            - rss: 1.0 고정
            - naver_news: 키워드 발행 빈도 정규화 값
            - datalab: 검색량 변화율
        keywords: DataLab 전용 키워드 리스트 (다른 소스는 빈 리스트)
        raw_meta: 소스별 원본 메타 (디버깅/확장용)
    """
    source: str
    source_type: str
    title: str
    summary: str = ""
    url: str = ""
    published_at: str = ""
    category: str = ""
    signal_score: float = 0.0
    keywords: list[str] = field(default_factory=list)
    raw_meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 입력값 검증 (Legal by Design + 데이터 무결성)
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("TrendItem.title은 비어있지 않은 문자열이어야 합니다")
        if self.source_type not in ALLOWED_SOURCE_TYPES:
            raise ValueError(
                f"TrendItem.source_type은 {ALLOWED_SOURCE_TYPES} 중 하나여야 합니다 "
                f"(받은 값: {self.source_type!r})"
            )
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("TrendItem.source는 비어있지 않은 문자열이어야 합니다")
        # 정규화
        self.title = self.title.strip()
        self.source = self.source.strip()
        self.summary = (self.summary or "").strip()
        self.url = (self.url or "").strip()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrendItem":
        """dict → TrendItem 복원 (저장소 로드용)"""
        return cls(
            source=data.get("source", ""),
            source_type=data.get("source_type", ""),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            url=data.get("url", ""),
            published_at=data.get("published_at", ""),
            category=data.get("category", ""),
            signal_score=float(data.get("signal_score", 0.0)),
            keywords=list(data.get("keywords", [])),
            raw_meta=dict(data.get("raw_meta", {})),
        )
