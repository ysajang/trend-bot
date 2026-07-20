"""
키워드 추출기 (kiwipiepy 기반)
- 게시글 제목에서 명사 추출
- 빈도 기반 TOP 키워드 도출
- 불용어 필터링
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass

from kiwipiepy import Kiwi

import config

logger = logging.getLogger(__name__)

kiwi = Kiwi()

# ── 약칭 → 정식명 매핑 (동일 대상 통합) ──
ALIAS_MAP: dict[str, str] = {
    "스벅": "스타벅스",
    "삼전": "삼성전자",
    "현차": "현대차",
    "기아차": "기아",
    "국힘": "국민의힘",
    "민주": "더불어민주당",
    "조국당": "조국혁신당",
    "한동훈": "한동훈",
}

# ── 동음이의어 사전 (P1: 문맥 기반 분기) ──
# 키: 동음이의어 / 값: {의미: 동반출현 키워드 세트}
# 동반 키워드가 같은 제목에 존재하면 해당 의미로 분류
# 어느 쪽에도 매칭 안 되면 빈도에서 제외 (ambiguous)
HOMONYM_DICT: dict[str, dict[str, set[str]]] = {
    "구속": {
        "야구": {"투수", "이닝", "야구", "타자", "마운드", "직구", "변화구", "삼진", "볼넷",
                 "선발", "중계", "마무리", "승리투수", "패전투수", "KBO", "MLB", "타석"},
        "법률": {"검찰", "법원", "재판", "혐의", "피의자", "영장", "수사", "경찰", "체포",
                 "기소", "구치소", "보석", "감옥", "징역", "형사", "피고인", "공판"},
    },
    "시장": {
        "경제": {"주식", "증시", "코스피", "나스닥", "투자", "매출", "경제", "금리", "환율",
                 "수출", "무역", "거래", "상장", "펀드", "채권", "원자재", "유가"},
        "행정": {"시청", "구청", "지자체", "선거", "후보", "당선", "지방", "행정", "도지사",
                 "군수", "구청장", "공약", "지역구", "의회"},
    },
    "전투": {
        "군사": {"군대", "전쟁", "미사일", "폭격", "병력", "작전", "사령관", "국방"},
        "게임": {"게임", "캐릭터", "레벨", "아이템", "던전", "보스", "스킬", "RPG"},
    },
}


def _resolve_homonym(word: str, title_nouns: set[str]) -> str | None:
    """
    동음이의어의 맥락 분류

    Args:
        word: 동음이의어
        title_nouns: 같은 제목에서 추출된 전체 명사 세트

    Returns:
        분류된 의미 라벨 (예: "야구") 또는 None (분류 불가 → 빈도에서 제외)
    """
    if word not in HOMONYM_DICT:
        return word  # 동음이의어가 아니면 그대로 반환

    senses = HOMONYM_DICT[word]
    best_sense = None
    best_overlap = 0

    for sense_label, context_words in senses.items():
        overlap = len(title_nouns & context_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_sense = sense_label

    if best_sense is None:
        return None  # 분류 불가 → 빈도에서 제외

    return f"{word}({best_sense})"

# 제목 전처리 패턴
# [싱갤], [유갤], [디시人터뷰] 등 대괄호 태그 제거
GALLERY_TAG_PATTERN = re.compile(r"^\[.*?\]\s*")
# 파일 확장자 제거 (.jpg, .JPG, .png, .gif, .webp, .mp4, .jpgif)
FILE_EXT_PATTERN = re.compile(r"\.(jpg|jpeg|png|gif|webp|mp4|jpgif|JPG|JPEG|PNG|GIF|WEBP)\b", re.IGNORECASE)
# (17pic), (스압) 등 괄호 부가정보 제거
PAREN_INFO_PATTERN = re.compile(r"\(\d+pic\)|\(스압\)|\(후방\)", re.IGNORECASE)


@dataclass
class KeywordResult:
    """키워드 분석 결과"""
    keyword: str
    count: int
    related_titles: list[str]

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "count": self.count,
            "related_titles": self.related_titles[:5],  # 최대 5개만
        }


def clean_title(title: str) -> str:
    """제목 전처리 - 갤러리 태그/확장자/부가정보 제거"""
    title = GALLERY_TAG_PATTERN.sub("", title)
    title = FILE_EXT_PATTERN.sub("", title)
    title = PAREN_INFO_PATTERN.sub("", title)
    return title.strip()


def extract_nouns(text: str) -> list[str]:
    """
    텍스트에서 명사 추출

    개선사항:
      - 인접 NNP 토큰 병합 ("장동" + "혁" → "장동혁")
      - 약칭 → 정식명 통합 ("스벅" → "스타벅스")
    """
    tokens = kiwi.tokenize(text)

    # 1단계: 인접 NNP 토큰 병합
    merged_tokens: list[tuple[str, str]] = []  # (form, tag)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.tag == "NNP":
            # 연속 NNP 탐색 (end == 다음 start)
            combined = tok.form
            end_pos = tok.end
            j = i + 1
            while j < len(tokens) and tokens[j].tag == "NNP" and tokens[j].start == end_pos:
                combined += tokens[j].form
                end_pos = tokens[j].end
                j += 1
            merged_tokens.append((combined, "NNP"))
            i = j
        else:
            merged_tokens.append((tok.form, tok.tag))
            i += 1

    # 2단계: 명사 필터링 + 약칭 통합
    stopwords_lower = {s.lower() for s in config.STOPWORDS}
    nouns: list[str] = []
    for form, tag in merged_tokens:
        if tag not in ("NNG", "NNP", "SL"):
            continue
        word = form.strip()
        word = word.strip(".·,'\"!?")  # 끝 특수문자 정규화 (DK. → DK)
        if not word or len(word) < config.MIN_KEYWORD_LENGTH or word.isdigit():
            continue

        # 약칭 → 정식명 변환
        word = ALIAS_MAP.get(word, word)

        if word in config.STOPWORDS or word.lower() in stopwords_lower:
            continue

        nouns.append(word)

    return nouns


def analyze_keywords(
    posts: list[dict],
    include_categories: set[str] | None = None,
    exclude_categories: set[str] | None = None,
    extra_stopwords: set[str] | None = None,
) -> list[KeywordResult]:
    """
    게시글 리스트에서 TOP 키워드 추출

    Args:
        posts: Post.to_dict() 리스트
        include_categories: 지정 시 해당 category 게시글만 집계 (예: {"게임","문화","연예"})
        exclude_categories: 지정 시 해당 category 게시글 제외
        extra_stopwords: 이 호출에서만 추가로 제외할 불용어 (전역 STOPWORDS 미변경)

    Returns:
        빈도순 KeywordResult 리스트
    """
    keyword_counter: Counter = Counter()
    keyword_titles: dict[str, list[str]] = {}
    extra_stop = {s.lower() for s in (extra_stopwords or set())}

    for post in posts:
        category = post.get("category", "")
        if include_categories is not None and category not in include_categories:
            continue
        if exclude_categories is not None and category in exclude_categories:
            continue

        title = post.get("title", "")
        cleaned = clean_title(title)
        nouns = extract_nouns(cleaned)
        noun_set = set(nouns)

        for noun in set(nouns):  # 한 제목 내 중복 제거
            if noun.lower() in extra_stop:
                continue
            # P1: 동음이의어 분기
            resolved = _resolve_homonym(noun, noun_set)
            if resolved is None:
                continue  # 분류 불가 → 빈도에서 제외
            keyword_counter[resolved] += 1
            if resolved not in keyword_titles:
                keyword_titles[resolved] = []
            keyword_titles[resolved].append(title)

    # TOP N 추출
    top_keywords = keyword_counter.most_common(config.TOP_KEYWORD_COUNT)

    results: list[KeywordResult] = []
    for keyword, count in top_keywords:
        results.append(KeywordResult(
            keyword=keyword,
            count=count,
            related_titles=keyword_titles.get(keyword, []),
        ))

    logger.info(f"키워드 {len(results)}개 추출 완료")
    return results
