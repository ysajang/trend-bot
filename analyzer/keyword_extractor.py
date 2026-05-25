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
    """텍스트에서 명사 추출"""
    nouns: list[str] = []
    tokens = kiwi.tokenize(text)
    for token in tokens:
        # NNG: 일반명사, NNP: 고유명사, SL: 외국어
        if token.tag in ("NNG", "NNP", "SL"):
            word = token.form.strip()
            word_lower = word.lower()
            if (
                len(word) >= config.MIN_KEYWORD_LENGTH
                and word not in config.STOPWORDS
                and word_lower not in {s.lower() for s in config.STOPWORDS}
                and not word.isdigit()
            ):
                nouns.append(word)
    return nouns


def analyze_keywords(posts: list[dict]) -> list[KeywordResult]:
    """
    게시글 리스트에서 TOP 키워드 추출

    Args:
        posts: Post.to_dict() 리스트

    Returns:
        빈도순 KeywordResult 리스트
    """
    keyword_counter: Counter = Counter()
    keyword_titles: dict[str, list[str]] = {}

    for post in posts:
        title = post.get("title", "")
        cleaned = clean_title(title)
        nouns = extract_nouns(cleaned)
        noun_set = set(nouns)

        for noun in set(nouns):  # 한 제목 내 중복 제거
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
