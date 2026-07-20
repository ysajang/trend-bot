"""
Steam 선행지표 수집기 (공식 API 전용 — LegalByDesign)

수집 소스:
  1. ISteamChartsService/GetMostPlayedGames — 글로벌 동접 순위 (플레이 수요)
  2. store featuredcategories top_sellers (KR) — 한국 판매 순위 (구매 수요)

선행지표 핵심: 어제 순위 대비 급상승 게임 = "뜨기 시작한" 신호
appid → 게임명/장르는 appdetails API로 조회 후 로컬 캐시 (호출 최소화)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

import config

logger = logging.getLogger(__name__)

CHARTS_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"
FEATURED_URL = "https://store.steampowered.com/api/featuredcategories?cc=KR&l=korean"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=korean&cc=KR&filters=basic,genres"

NAME_CACHE_PATH = config.DATA_DIR / "steam_name_cache.json"
RANK_HISTORY_PATH = config.DATA_DIR / "steam_rank_history.json"

MOST_PLAYED_LIMIT = 15   # 동접 상위 N개
TOP_SELLERS_LIMIT = 10   # KR 톱셀러 상위 N개
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
HEADERS = {"User-Agent": "Mozilla/5.0 (TrendRadar/2.2)"}


@dataclass
class SteamGame:
    """Steam 차트 항목"""
    appid: int
    name: str = ""
    genres: list[str] = field(default_factory=list)
    rank: int = 0                 # 현재 순위 (1-base)
    peak_players: int = 0         # 동접 피크 (most_played만)
    chart: str = "most_played"    # "most_played" | "top_sellers_kr"
    rank_change: int | None = None  # 어제 대비 순위 변동 (+상승/-하락/None=신규

    def to_dict(self) -> dict:
        return {
            "appid": self.appid,
            "name": self.name,
            "genres": self.genres,
            "rank": self.rank,
            "peak_players": self.peak_players,
            "chart": self.chart,
            "rank_change": self.rank_change,
        }


# ──────────────────────────────────────────────
# appid → 이름/장르 캐시
# ──────────────────────────────────────────────

def _load_name_cache() -> dict:
    if NAME_CACHE_PATH.exists():
        try:
            return json.loads(NAME_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_name_cache(cache: dict) -> None:
    try:
        NAME_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        NAME_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"Steam 이름 캐시 저장 실패: {e}")


async def _fetch_app_detail(
    session: aiohttp.ClientSession, appid: int,
) -> tuple[str, list[str]]:
    """appdetails API로 이름/장르 조회 (실패 시 빈 값)"""
    url = APPDETAILS_URL.format(appid=appid)
    try:
        async with session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return "", []
            data = await resp.json()
            entry = data.get(str(appid), {})
            if not entry.get("success"):
                return "", []
            d = entry.get("data", {})
            name = d.get("name", "")
            genres = [g.get("description", "") for g in d.get("genres", [])]
            return name, [g for g in genres if g]
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"appdetails 실패 (appid={appid}): {e}")
        return "", []


async def _resolve_names(
    session: aiohttp.ClientSession, games: list[SteamGame],
) -> None:
    """appid → 이름/장르 채우기 (캐시 우선, 미스만 API 호출)"""
    cache = _load_name_cache()
    updated = False

    for game in games:
        key = str(game.appid)
        if key in cache:
            game.name = cache[key].get("name", "")
            game.genres = cache[key].get("genres", [])
            continue
        name, genres = await _fetch_app_detail(session, game.appid)
        if name:
            game.name = name
            game.genres = genres
            cache[key] = {"name": name, "genres": genres}
            updated = True
        await asyncio.sleep(0.3)  # appdetails rate limit 보호

    if updated:
        _save_name_cache(cache)


# ──────────────────────────────────────────────
# 순위 이력 (어제 대비 변동 계산)
# ──────────────────────────────────────────────

def _load_rank_history() -> dict:
    if RANK_HISTORY_PATH.exists():
        try:
            return json.loads(RANK_HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _apply_rank_changes(games: list[SteamGame], history: dict) -> None:
    """어제 순위와 비교해 rank_change 채우기 (신규는 None)"""
    for game in games:
        prev = history.get(game.chart, {}).get(str(game.appid))
        if prev is not None:
            game.rank_change = prev - game.rank  # 양수=상승
        else:
            game.rank_change = None  # 차트 신규 진입


def _save_rank_history(games: list[SteamGame]) -> None:
    """오늘 순위를 이력으로 저장 (다음 실행 시 비교용)"""
    history: dict = {}
    for game in games:
        history.setdefault(game.chart, {})[str(game.appid)] = game.rank
    try:
        RANK_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        RANK_HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False), encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"Steam 순위 이력 저장 실패: {e}")


# ──────────────────────────────────────────────
# 수집 메인
# ──────────────────────────────────────────────

async def _fetch_most_played(session: aiohttp.ClientSession) -> list[SteamGame]:
    """글로벌 동접 차트 상위 N개"""
    try:
        async with session.get(CHARTS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"Steam 차트 HTTP {resp.status}")
                return []
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.error(f"Steam 차트 수집 실패: {e}")
        return []

    ranks = data.get("response", {}).get("ranks", [])[:MOST_PLAYED_LIMIT]
    return [
        SteamGame(
            appid=r.get("appid", 0),
            rank=r.get("rank", 0),
            peak_players=r.get("peak_in_game", 0),
            chart="most_played",
        )
        for r in ranks
        if r.get("appid")
    ]


async def _fetch_top_sellers_kr(session: aiohttp.ClientSession) -> list[SteamGame]:
    """한국 스토어 톱셀러 상위 N개 (이름 포함 응답)"""
    try:
        async with session.get(FEATURED_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"Steam 스토어 HTTP {resp.status}")
                return []
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        logger.error(f"Steam 스토어 수집 실패: {e}")
        return []

    items = data.get("top_sellers", {}).get("items", [])[:TOP_SELLERS_LIMIT]
    games: list[SteamGame] = []
    for i, item in enumerate(items, 1):
        appid = item.get("id", 0)
        if not appid:
            continue
        games.append(SteamGame(
            appid=appid,
            name=item.get("name", ""),
            rank=i,
            chart="top_sellers_kr",
        ))
    return games


async def fetch_steam_signals() -> list[SteamGame]:
    """
    Steam 선행지표 수집 (동접 차트 + KR 톱셀러)

    Returns:
        SteamGame 리스트 (rank_change 포함) — 실패 시 빈 리스트
    """
    async with aiohttp.ClientSession() as session:
        most_played = await _fetch_most_played(session)
        top_sellers = await _fetch_top_sellers_kr(session)

        # 동접 차트는 appid만 오므로 이름 해석 (캐시 활용)
        await _resolve_names(session, most_played)

    games = most_played + top_sellers

    # 어제 대비 순위 변동
    history = _load_rank_history()
    _apply_rank_changes(games, history)
    _save_rank_history(games)

    logger.info(
        f"Steam 수집 완료: 동접 {len(most_played)} + KR톱셀러 {len(top_sellers)}"
    )
    return games
