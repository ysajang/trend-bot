"""
장르 심층 검증기 (신호1+2 통합)

scan_genre_gaps가 찾은 후보 조합을 실제 진입 판단이 가능한 수준까지 파헤친다.

분석 항목:
  1. 최근 신작 추세  - 연도별 출시 수 (성장/정체/쇠퇴)
  2. 가격대 분포     - 이 장르가 얼마에 팔리는지
  3. 성과 분포       - 리뷰 수 사분위 (상위권 vs 중간층 격차)
  4. 소규모 성공사례 - 최근 2년 신작 중 리뷰 100~5000 구간 (재현 가능한 성공)
  5. 불만 패턴       - 부정 리뷰 반복 키워드 (빈틈)

검색 결과 HTML 한 번에 출시일/리뷰수/긍정률/가격이 모두 담겨 있어
게임당 API 호출 없이 대량 수집이 가능하다 (리뷰 본문만 별도 호출).
"""

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp

import config
from analyzer.keyword_extractor import extract_nouns

logger = logging.getLogger(__name__)

SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count=100&tags={tags}&category1=998"
    "&sort_by=Released_DESC&infinite=1&json=1&cc=KR&l=koreana"
)
REVIEWS_URL = (
    "https://store.steampowered.com/appreviews/{appid}"
    "?json=1&filter=recent&review_type=negative&num_per_page=40"
    "&language=koreana,english&purchase_type=all"
)

HEADERS = {"User-Agent": "Mozilla/5.0 (TrendRadar/2.4)"}
TIMEOUT = aiohttp.ClientTimeout(total=25)
DELAY = 0.5

MAX_PAGES = 3            # 조합당 최대 300개까지 수집
REVIEW_SAMPLE_GAMES = 6  # 불만 분석할 게임 수
MIN_REVIEW_LEN = 15      # 저품질 리뷰 배제 (욕설 단문 등)

# 불만 분석에서 제외할 일반어 (장르 무관 빈출)
COMPLAINT_STOPWORDS = {
    # 한국어 일반명사
    "게임", "정말", "그냥", "생각", "사람", "시간", "정도", "부분", "느낌",
    "가지", "때문", "경우", "자체", "이상", "문제", "내용", "필요", "가격",
    "추천", "구매", "플레이", "진행", "시작", "가능", "다른", "무엇", "조금",
    "이것", "저것", "여기", "거기", "지금", "다음", "이번", "정말로",
    # 영어 기능어 (kiwipiepy가 SL 태그로 잡아내므로 명시 제외 필요)
    "the", "and", "you", "for", "but", "with", "that", "this", "have",
    "not", "are", "was", "its", "it's", "get", "just", "like", "can",
    "game", "games", "play", "playing", "played", "really", "much",
    "is", "to", "of", "it", "in", "on", "be", "or", "at", "as", "an",
    "my", "me", "we", "he", "she", "they", "them", "his", "her", "your",
    "all", "any", "only", "some", "more", "most", "very", "too", "also",
    "if", "so", "no", "yes", "do", "does", "did", "has", "had", "been",
    "will", "would", "could", "should", "there", "then", "than", "when",
    "what", "who", "how", "why", "which", "one", "two", "out", "up",
    "from", "by", "about", "after", "before", "into", "over", "even",
    "make", "made", "take", "want", "need", "know", "think", "see",
    "way", "lot", "time", "thing", "things", "player", "players",
    "steam", "review", "reviews", "hour", "hours", "still", "back",
    "good", "bad", "great", "well", "better", "best", "worse", "worst",
    "am", "im", "ive", "dont", "doesnt", "cant", "isnt", "wasnt",
    # 2차 정제: 실제 출력에서 확인된 잔여 일반어
    "because", "every", "other", "others", "first", "last", "little",
    "where", "while", "each", "many", "few", "same", "own", "such",
    "feel", "feels", "felt", "seem", "seems", "look", "looks",
    "recommend", "recommended", "buy", "bought", "price", "money",
    "hard", "easy", "long", "short", "new", "old", "many", "less",
    "actually", "though", "however", "maybe", "probably", "definitely",
    "something", "anything", "everything", "nothing", "someone",
    "people", "person", "guys", "kind", "sort", "bit", "part",
    "point", "example", "problem", "issue", "reason", "case",
    "give", "gives", "put", "keep", "come", "comes", "went", "goes",
    "find", "found", "try", "tried", "start", "started", "end", "ends",
    "run", "runs", "use", "used", "using", "work", "works", "worked",
    "say", "says", "said", "tell", "told", "mean", "means",
}


@dataclass
class GameRecord:
    """검색 결과에서 파싱한 게임 1건"""
    appid: str
    name: str
    release_date: str = ""
    release_year: int = 0
    review_count: int = 0
    positive_pct: int = 0
    price_krw: int = 0

    @property
    def is_free(self) -> bool:
        return self.price_krw == 0


@dataclass
class ComplaintExcerpt:
    """불만 키워드가 등장한 리뷰 원문 발췌"""
    keyword: str
    game_name: str
    sentence: str
    playtime_hours: float = 0.0

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "game": self.game_name,
            "sentence": self.sentence,
            "playtime_hours": round(self.playtime_hours, 1),
        }


@dataclass
class GenreDeepDive:
    """장르 조합 심층 분석 결과"""
    tag_names: tuple[str, str]
    total_games: int = 0
    yearly_releases: dict[int, int] = field(default_factory=dict)
    price_median_krw: int = 0
    price_range: tuple[int, int] = (0, 0)
    review_quartiles: tuple[int, int, int] = (0, 0, 0)  # Q1, 중앙값, Q3
    small_successes: list[GameRecord] = field(default_factory=list)
    complaints: list[tuple[str, int]] = field(default_factory=list)
    excerpts: list[ComplaintExcerpt] = field(default_factory=list)
    free_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "tags": list(self.tag_names),
            "total_games": self.total_games,
            "yearly_releases": self.yearly_releases,
            "price_median_krw": self.price_median_krw,
            "price_range": list(self.price_range),
            "review_quartiles": list(self.review_quartiles),
            "free_ratio": round(self.free_ratio, 3),
            "small_successes": [
                {
                    "name": g.name,
                    "reviews": g.review_count,
                    "positive": g.positive_pct,
                    "price": g.price_krw,
                    "released": g.release_date,
                }
                for g in self.small_successes
            ],
            "complaints": [{"word": w, "count": c} for w, c in self.complaints],
            "excerpts": [e.to_dict() for e in self.excerpts],
        }


# ──────────────────────────────────────────────
# 파싱
# ──────────────────────────────────────────────

ROW_SPLIT = re.compile(r'(?=<a href="https://store\.steampowered\.com/app/)')
RE_APPID = re.compile(r'data-ds-appid="(\d+)"')
RE_TITLE = re.compile(r'<span class="title">([^<]*)</span>')
RE_RELEASED = re.compile(r'search_released responsive_secondrow">\s*([^<]*?)\s*</div>')
RE_PRICE = re.compile(r'data-price-final="(\d+)"')
RE_REVIEW = re.compile(
    r'사용자 평가 ([\d,]+)개 중 (\d+)%|([\d,]+) user reviews.*?(\d+)% '
)
RE_REVIEW_KO = re.compile(r'평가 ([\d,]+)개 중 (\d+)%가 긍정')


def _parse_year(date_str: str) -> int:
    """'2026년 8월 21일' / '21 Aug, 2026' 등에서 연도 추출"""
    m = re.search(r"(19|20)\d{2}", date_str)
    return int(m.group(0)) if m else 0


def _parse_rows(html: str) -> list[GameRecord]:
    """검색 결과 HTML → GameRecord 리스트"""
    records: list[GameRecord] = []
    for chunk in ROW_SPLIT.split(html):
        m_id = RE_APPID.search(chunk)
        if not m_id:
            continue
        m_title = RE_TITLE.search(chunk)
        m_rel = RE_RELEASED.search(chunk)
        m_price = RE_PRICE.search(chunk)
        m_rev = RE_REVIEW_KO.search(chunk)

        release = m_rel.group(1).strip() if m_rel else ""
        rec = GameRecord(
            appid=m_id.group(1),
            name=m_title.group(1).strip() if m_title else "",
            release_date=release,
            release_year=_parse_year(release),
            price_krw=int(m_price.group(1)) // 100 if m_price else 0,
            review_count=int(m_rev.group(1).replace(",", "")) if m_rev else 0,
            positive_pct=int(m_rev.group(2)) if m_rev else 0,
        )
        if rec.name:
            records.append(rec)
    return records


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP {resp.status}: {url[:70]}")
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"요청 실패 ({str(e)[:40]})")
        return None


async def _collect_games(
    session: aiohttp.ClientSession, tag_ids: tuple[int, int],
) -> tuple[int, list[GameRecord]]:
    """태그 조합의 게임 목록 수집 (최신 출시순)"""
    all_records: list[GameRecord] = []
    total = 0
    for page in range(MAX_PAGES):
        url = SEARCH_URL.format(
            start=page * 100, tags=f"{tag_ids[0]},{tag_ids[1]}",
        )
        data = await _fetch_json(session, url)
        if not data:
            break
        if page == 0:
            total = data.get("total_count", 0) or 0
        rows = _parse_rows(data.get("results_html", ""))
        if not rows:
            break
        all_records.extend(rows)
        await asyncio.sleep(DELAY)
        if len(all_records) >= total:
            break
    return total, all_records


SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")
MAX_SENTENCE_LEN = 220
EXCERPTS_PER_KEYWORD = 3


def _extract_keywords(text: str) -> set[str]:
    """리뷰 본문에서 불만 후보 키워드 추출 (불용어/단문 배제)"""
    words: set[str] = set()
    for word in set(extract_nouns(text)):
        w = word.lower()
        if w in COMPLAINT_STOPWORDS:
            continue
        if w.isascii() and len(w) < 4:
            continue
        if len(w) < 2:
            continue
        words.add(word)
    return words


def _find_sentences(text: str, keyword: str) -> list[str]:
    """키워드가 포함된 문장만 추출 (길이 제한 적용)"""
    kw = keyword.lower()
    hits: list[str] = []
    for raw in SENTENCE_SPLIT.split(text):
        s = " ".join(raw.split())
        if not s or kw not in s.lower():
            continue
        if len(s) > MAX_SENTENCE_LEN:
            # 키워드 주변만 잘라냄
            idx = s.lower().index(kw)
            start = max(0, idx - MAX_SENTENCE_LEN // 2)
            s = ("..." if start > 0 else "") + s[start:start + MAX_SENTENCE_LEN] + "..."
        hits.append(s)
    return hits


async def _collect_complaints(
    session: aiohttp.ClientSession, games: list[GameRecord],
) -> tuple[list[tuple[str, int]], list[ComplaintExcerpt]]:
    """
    상위 게임들의 부정 리뷰에서 반복 불만 키워드 + 원문 발췌 추출

    Returns:
        (키워드 빈도 상위 목록, 대표 원문 발췌 목록)
    """
    counter: Counter = Counter()
    # 키워드별 (게임명, 문장, 플레이시간) 후보 보관
    pool: dict[str, list[tuple[str, str, float]]] = {}

    for game in games[:REVIEW_SAMPLE_GAMES]:
        data = await _fetch_json(session, REVIEWS_URL.format(appid=game.appid))
        await asyncio.sleep(DELAY)
        if not data or not data.get("success"):
            continue

        for review in data.get("reviews", []):
            text = (review.get("review") or "").strip()
            # 저품질 리뷰 배제: 너무 짧거나 플레이 시간이 거의 없는 경우
            if len(text) < MIN_REVIEW_LEN:
                continue
            playtime = review.get("author", {}).get("playtime_forever", 0)
            if playtime < 30:  # 30분 미만은 판단 근거 약함
                continue

            hours = playtime / 60.0
            for word in _extract_keywords(text):
                counter[word] += 1
                for sentence in _find_sentences(text, word)[:1]:
                    pool.setdefault(word, []).append((game.name, sentence, hours))

    top_words = counter.most_common(12)

    # 상위 키워드에 대해 플레이시간이 긴 리뷰 우선으로 발췌 선정
    excerpts: list[ComplaintExcerpt] = []
    for word, _count in top_words[:6]:
        candidates = pool.get(word, [])
        candidates.sort(key=lambda c: c[2], reverse=True)
        seen_games: set[str] = set()
        picked = 0
        for game_name, sentence, hours in candidates:
            if game_name in seen_games:  # 게임 편중 방지
                continue
            seen_games.add(game_name)
            excerpts.append(ComplaintExcerpt(
                keyword=word,
                game_name=game_name,
                sentence=sentence,
                playtime_hours=hours,
            ))
            picked += 1
            if picked >= EXCERPTS_PER_KEYWORD:
                break

    return top_words, excerpts


def _quartiles(values: list[int]) -> tuple[int, int, int]:
    """Q1, 중앙값, Q3 계산"""
    if not values:
        return (0, 0, 0)
    s = sorted(values)
    n = len(s)
    return (s[n // 4], s[n // 2], s[(3 * n) // 4])


async def deep_dive(
    tag_names: tuple[str, str], tag_ids: tuple[int, int],
) -> GenreDeepDive:
    """단일 태그 조합 심층 분석"""
    logger.info(f"심층 분석 시작: {tag_names[0]}+{tag_names[1]}")

    async with aiohttp.ClientSession() as session:
        total, games = await _collect_games(session, tag_ids)
        if not games:
            return GenreDeepDive(tag_names=tag_names, total_games=total)

        # 연도별 출시 수
        yearly: Counter = Counter(g.release_year for g in games if g.release_year)

        # 가격 (유료작만)
        paid_prices = [g.price_krw for g in games if g.price_krw > 0]
        paid_prices.sort()
        price_median = paid_prices[len(paid_prices) // 2] if paid_prices else 0
        price_range = (paid_prices[0], paid_prices[-1]) if paid_prices else (0, 0)
        free_ratio = 1 - (len(paid_prices) / len(games)) if games else 0.0

        # 리뷰 분포 (리뷰가 있는 게임만)
        reviewed = [g.review_count for g in games if g.review_count > 0]
        quartiles = _quartiles(reviewed)

        # 소규모 성공: 최근 2년 + 리뷰 100~5000 + 긍정 70% 이상
        this_year = datetime.now().year
        small = [
            g for g in games
            if g.release_year >= this_year - 2
            and 100 <= g.review_count <= 5000
            and g.positive_pct >= 70
        ]
        small.sort(key=lambda g: g.review_count, reverse=True)

        # 불만 패턴: 성과 상위 게임 기준
        top_games = sorted(games, key=lambda g: g.review_count, reverse=True)
        complaints, excerpts = await _collect_complaints(session, top_games)

    result = GenreDeepDive(
        tag_names=tag_names,
        total_games=total,
        yearly_releases=dict(sorted(yearly.items())),
        price_median_krw=price_median,
        price_range=price_range,
        review_quartiles=quartiles,
        small_successes=small[:8],
        complaints=complaints,
        excerpts=excerpts,
        free_ratio=free_ratio,
    )
    logger.info(
        f"심층 분석 완료: {tag_names[0]}+{tag_names[1]} "
        f"(수집 {len(games)}개 / 소규모성공 {len(small)}개)"
    )
    return result
