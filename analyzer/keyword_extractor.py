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

        for noun in set(nouns):  # 한 제목 내 중복 제거
            keyword_counter[noun] += 1
            if noun not in keyword_titles:
                keyword_titles[noun] = []
            keyword_titles[noun].append(title)

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


def format_keywords_for_ai(keywords: list[KeywordResult], posts: list[dict]) -> str:
    """Claude API에 넘길 텍스트 포맷 생성"""
    lines: list[str] = []
    lines.append(f"=== 커뮤니티 트렌드 데이터 ({len(posts)}개 게시글 분석) ===\n")

    lines.append("## TOP 키워드 (빈도순)")
    for i, kw in enumerate(keywords, 1):
        lines.append(f"{i}. {kw.keyword} ({kw.count}회)")
        for title in kw.related_titles[:3]:
            lines.append(f"   - {title}")
        lines.append("")

    lines.append("\n## 인기 게시글 TOP 10 (추천순)")
    sorted_posts = sorted(posts, key=lambda x: x.get("recommend_count", 0), reverse=True)
    for i, post in enumerate(sorted_posts[:10], 1):
        lines.append(
            f"{i}. [{post.get('gallery_name', '')}] {post.get('title', '')} "
            f"(추천:{post.get('recommend_count', 0)} 조회:{post.get('view_count', 0)} "
            f"댓글:{post.get('comment_count', 0)})"
        )

    return "\n".join(lines)
