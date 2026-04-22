"""
AI 인사이트 생성기
- Haiku: 키워드 분류 + 노이즈 제거 (전처리)
- Sonnet: 정제된 데이터로 사업 기회 관점 인사이트 생성
"""

import logging

import anthropic

import config

logger = logging.getLogger(__name__)


def get_client() -> anthropic.Anthropic:
    """Anthropic 클라이언트 생성"""
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def preprocess_with_haiku(client: anthropic.Anthropic, raw_data: str) -> str:
    """
    Haiku로 1차 전처리
    - 무의미한 키워드 제거
    - 카테고리 분류
    - 핵심 트렌드 압축
    """
    prompt = f"""다음은 한국 온라인 커뮤니티(디시인사이드)에서 수집한 실시간 인기글 분석 데이터입니다.

{raw_data}

위 데이터를 아래 기준으로 정리해주세요:

1. 키워드를 카테고리별로 분류 (IT/기술, 경제/재테크, 사회/이슈, 엔터테인먼트, 생활/소비, 기타)
2. 각 카테고리에서 가장 주목할 만한 키워드 3개씩 선정
3. 단순 유머/밈/욕설 관련 키워드는 제외
4. 각 키워드가 왜 지금 뜨고 있는지 추정 (관련 제목 기반)

JSON이 아닌 자연어로 깔끔하게 정리해주세요."""

    try:
        response = client.messages.create(
            model=config.HAIKU_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text
        logger.info(f"Haiku 전처리 완료 (입력: {len(raw_data)}자 -> 출력: {len(result)}자)")
        return result
    except Exception as e:
        logger.error(f"Haiku API 호출 실패: {e}")
        return raw_data  # 실패 시 원본 반환


def generate_insight_with_sonnet(client: anthropic.Anthropic, preprocessed_data: str) -> str:
    """
    Sonnet으로 2차 인사이트 생성
    - 사업 기회 관점 분석
    - 1인 개발자/프리랜서 관점 액션 아이템
    """
    prompt = f"""당신은 한국 온라인 트렌드 분석 전문가입니다.

다음은 오늘 한국 커뮤니티에서 뜨고 있는 키워드와 트렌드를 정리한 데이터입니다:

{preprocessed_data}

위 데이터를 기반으로 아래 형식으로 인사이트를 작성해주세요:

🔥 오늘의 핵심 트렌드 TOP 5
(각 트렌드에 대해 1~2줄 설명)

💡 사업 기회 시그널
(1인 개발자/프리랜서가 활용할 수 있는 기회 2~3개)
(구체적으로: "이런 서비스를 만들면 수요가 있을 것" 수준)

⚡ 주목할 변화
(어제와 다른 점, 새롭게 등장한 키워드, 분위기 전환 등)

규칙:
- 한국어로 작성
- 각 항목은 간결하게 (1~2문장)
- 추측이면 "~가능성" "~추정"으로 표현
- 정치적 편향 없이 중립적으로"""

    try:
        response = client.messages.create(
            model=config.SONNET_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text
        logger.info(f"Sonnet 인사이트 생성 완료 ({len(result)}자)")
        return result
    except Exception as e:
        logger.error(f"Sonnet API 호출 실패: {e}")
        return "⚠️ AI 인사이트 생성 실패 - 수동 확인 필요"


def generate_full_insight(raw_data: str) -> str:
    """
    전체 인사이트 생성 파이프라인
    raw_data -> Haiku 전처리 -> Sonnet 인사이트
    """
    client = get_client()

    # 1차: Haiku 전처리
    preprocessed = preprocess_with_haiku(client, raw_data)

    # 2차: Sonnet 인사이트
    insight = generate_insight_with_sonnet(client, preprocessed)

    return insight
