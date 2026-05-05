"""
TrendRadar 수집기 패키지

Layer 1 (RSS) / Layer 2 (Naver News) / Layer 3 (DataLab) 수집기가 모두
이 패키지 하위에 위치하며 공통 출력 형식인 TrendItem을 사용합니다.
"""

from collector.trend_item import TrendItem, SourceType, ALLOWED_SOURCE_TYPES

__all__ = ["TrendItem", "SourceType", "ALLOWED_SOURCE_TYPES"]
