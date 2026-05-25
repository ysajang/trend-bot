"""
사업 기회 시그널 생성기 (Sonnet 제한적 LLM)

리포트 최종 단계에서 Sonnet 1회 호출하여
TOP 10 키워드 + 대표 뉴스 + 추이 태그를 기반으로
구체적인 사업 기회 시그널 2~3개를 생성합니다.

비용: 하루 1회 호출 = 월 $1~3 수준
"""

import logging
import os

import anthropic

from analyzer.report_builder import ReportData

logger = logging.getLogger(__name__)

# Sonnet 모델 (비용 효율)
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1024

# 프롬프트 시스템 메시지
SYSTEM_PROMPT = """당신은 1인 SaaS 개발자의 사업 기회 탐색을 돕는 분석가입니다.

다음 규칙을 반드시 지키세요:
1. 이미 존재하는 서비스(예: "번역 앱", "뉴스 요약 서비스")를 제안하면 안 됩니다
2. "맞춤형 플랫폼", "AI 기반 솔루션" 같은 추상적 카테고리가 아닌 구체적 제품/기능 형태로 제안하세요
3. 1인 개발자가 2주 내에 MVP를 만들 수 있는 범위여야 합니다
4. 글로벌 시장 우선 (한국 전용은 명확한 사업기회일 때만)
5. 각 시그널은 "무엇을 만들지" + "왜 지금인지" + "수익 모델" 3가지를 포함하세요
6. 2~3개만 제안하세요 (많으면 집중력 분산)
7. 한국어로 응답하세요"""


def _build_user_prompt(report: ReportData) -> str:
    """ReportData에서 Sonnet 입력 프롬프트 생성"""
    lines: list[str] = []
    lines.append("오늘의 트렌드 데이터를 기반으로 사업 기회 시그널을 찾아주세요.")
    lines.append("")
    lines.append("## TOP 10 키워드 + 추이")
    for i, kw in enumerate(report.top_keywords, 1):
        source = f" [{kw.source_tag}]" if kw.source_tag else ""
        news = f" | 뉴스: {kw.representative_news_title}" if kw.representative_news_title else ""
        lines.append(f"{i}. {kw.keyword} ({kw.count}회) - {kw.trend_tag}{source}{news}")

    if report.trend_highlights:
        lines.append("")
        lines.append("## 7일 추이 요약")
        for tag, keywords in report.trend_highlights.items():
            lines.append(f"- {tag}: {', '.join(keywords)}")

    lines.append("")
    lines.append("위 데이터에서 1인 SaaS 개발자가 실행 가능한 사업 기회 시그널 2~3개를 제안해주세요.")

    return "\n".join(lines)


async def generate_opportunity_signals(report: ReportData) -> str:
    """
    Sonnet을 호출하여 사업 기회 시그널 생성

    Args:
        report: 규칙기반 리포트 데이터

    Returns:
        💡 사업 기회 시그널 텍스트 (실패 시 빈 문자열)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY가 설정되지 않았습니다 → 시그널 생성 생략")
        return ""

    if not report.top_keywords:
        logger.warning("TOP 키워드가 없습니다 → 시그널 생성 생략")
        return ""

    user_prompt = _build_user_prompt(report)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )

        # 텍스트 블록 추출
        result_text = ""
        for block in message.content:
            if block.type == "text":
                result_text += block.text

        if not result_text.strip():
            logger.warning("Sonnet 응답이 비어있습니다")
            return ""

        logger.info(
            f"사업 기회 시그널 생성 완료 "
            f"(입력 토큰: {message.usage.input_tokens} / "
            f"출력 토큰: {message.usage.output_tokens})"
        )
        return result_text.strip()

    except anthropic.APIError as e:
        logger.error(f"Anthropic API 오류: {e}")
        return ""
    except Exception as e:
        logger.exception(f"시그널 생성 예상치 못한 오류: {e}")
        return ""
