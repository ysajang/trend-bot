"""
디시인사이드 실시간베스트 크롤러
- 실시간베스트 게시판에서 인기글 제목 + 본문 미리보기 수집
- IP 차단 방지: UA 로테이션 + 쿠키 세션 유지 + 요청 간격 조절
"""

import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

import aiohttp
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# User-Agent 풀 — 로테이션으로 핑거프린트 분산
UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

BASE_URL = "https://gall.dcinside.com"
LIST_URL = f"{BASE_URL}/board/lists/?id=dcbest"

def _make_headers(page: int = 1) -> dict:
    """페이지마다 UA 로테이션 + Referer 체인 설정"""
    ua = random.choice(UA_POOL)
    referer = BASE_URL + "/" if page == 1 else f"{LIST_URL}&page={page - 1}"
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }


@dataclass
class Post:
    post_id: str = ""
    title: str = ""
    author: str = ""
    gallery_name: str = ""
    gallery_id: str = ""
    view_count: int = 0
    recommend_count: int = 0
    comment_count: int = 0
    timestamp: str = ""
    url: str = ""
    source: str = "dcinside"

    def to_dict(self) -> dict:
        return asdict(self)


async def _warm_up_session(session: aiohttp.ClientSession) -> bool:
    """
    메인 페이지 먼저 방문 → 쿠키 획득 후 실베 요청
    브라우저 동작 모방으로 차단 회피
    """
    try:
        warm_url = "https://www.dcinside.com/"
        async with session.get(
            warm_url,
            headers=_make_headers(1),
            timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                logger.info("세션 워밍업 완료 (쿠키 획득)")
                return True
            logger.warning(f"워밍업 실패: HTTP {resp.status}")
            return False
    except Exception as e:
        logger.warning(f"워밍업 예외: {e}")
        return False


async def fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    page: int = 1,
    retries: int = 2,
) -> str | None:
    """URL에서 HTML 가져오기 — 타임아웃 25초 + 재시도 2회"""
    for attempt in range(retries + 1):
        try:
            headers = _make_headers(page)
            timeout = aiohttp.ClientTimeout(total=25, connect=10)
            async with session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # 차단 감지: 본문이 너무 짧으면 차단 페이지 의심
                    if len(text) < 500:
                        logger.warning(f"응답 너무 짧음 ({len(text)}bytes) — 차단 의심: {url}")
                        return None
                    return text
                elif resp.status == 403:
                    logger.error(f"403 Forbidden — IP 차단 확정: {url}")
                    return None
                else:
                    logger.warning(f"HTTP {resp.status} for {url}")
                    return None
        except asyncio.TimeoutError:
            logger.error(f"Timeout (시도 {attempt+1}/{retries+1}): {url}")
            if attempt < retries:
                wait = 3 + attempt * 2  # 3초 → 5초
                logger.info(f"{wait}초 후 재시도...")
                await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    return None


def parse_realtime_best(html: str) -> list[Post]:
    """실시간베스트 페이지 파싱"""
    posts: list[Post] = []
    soup = BeautifulSoup(html, "lxml")

    rows = soup.select("tr.ub-content")
    if not rows:
        rows = soup.select(".ub-content")

    if not rows:
        # 파싱 실패 디버그용 — HTML 구조 변경 감지
        logger.warning(f"tr.ub-content 없음 — HTML 구조 변경 의심. body 앞 200자: {str(soup.body)[:200] if soup.body else 'body없음'}")

    for row in rows:
        try:
            title_el = row.select_one("td.gall_tit a") or row.select_one(".gall_tit a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            post_id = ""
            if "no=" in href:
                post_id = href.split("no=")[-1].split("&")[0]

            gall_el = row.select_one("td.gall_name a") or row.select_one(".gall_name")
            gallery_name = gall_el.get_text(strip=True) if gall_el else ""

            gallery_id = ""
            if gall_el and gall_el.get("href"):
                gall_href = gall_el.get("href", "")
                if "id=" in gall_href:
                    gallery_id = gall_href.split("id=")[-1].split("&")[0]

            author_el = row.select_one("td.gall_writer") or row.select_one(".gall_writer")
            author = ""
            if author_el:
                nick_el = author_el.select_one(".nickname") or author_el.select_one("em")
                author = nick_el.get_text(strip=True) if nick_el else author_el.get_text(strip=True)

            view_el = row.select_one("td.gall_count") or row.select_one(".gall_count")
            view_count = 0
            if view_el:
                view_text = view_el.get_text(strip=True).replace(",", "")
                view_count = int(view_text) if view_text.isdigit() else 0

            rec_el = row.select_one("td.gall_recommend") or row.select_one(".gall_recommend")
            recommend_count = 0
            if rec_el:
                rec_text = rec_el.get_text(strip=True).replace(",", "")
                recommend_count = int(rec_text) if rec_text.isdigit() else 0

            comment_count = 0
            reply_el = title_el.find_next_sibling("a", class_="reply_numbox") if title_el else None
            if not reply_el:
                reply_el = row.select_one(".reply_numbox")
            if reply_el:
                reply_text = reply_el.get_text(strip=True).strip("[]")
                comment_count = int(reply_text) if reply_text.isdigit() else 0

            time_el = row.select_one("td.gall_date") or row.select_one(".gall_date")
            timestamp = ""
            if time_el:
                timestamp = time_el.get("title", "") or time_el.get_text(strip=True)

            url = href if href.startswith("http") else f"{BASE_URL}{href}"

            posts.append(Post(
                post_id=post_id,
                title=title,
                author=author,
                gallery_name=gallery_name,
                gallery_id=gallery_id,
                view_count=view_count,
                recommend_count=recommend_count,
                comment_count=comment_count,
                timestamp=timestamp,
                url=url,
            ))

        except Exception as e:
            logger.warning(f"게시글 파싱 오류: {e}")
            continue

    return posts


async def crawl_realtime_best(max_pages: int = 3) -> list[Post]:
    """
    디시인사이드 실시간베스트 크롤링

    Args:
        max_pages: 수집할 페이지 수 (1페이지 = 약 50개)
    """
    all_posts: list[Post] = []
    seen_ids: set[str] = set()

    # 세션 레벨 쿠키 공유 (CookieJar 자동 관리)
    connector = aiohttp.TCPConnector(limit=5, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:

        # 워밍업: 메인 페이지 방문으로 쿠키 선취
        await _warm_up_session(session)
        await asyncio.sleep(random.uniform(1.5, 3.0))  # 사람처럼 잠깐 대기

        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}/board/lists/?id=dcbest&page={page}"
            logger.info(f"크롤링 중: 실시간베스트 페이지 {page}")

            html = await fetch_page(session, url, page=page, retries=2)
            if not html:
                logger.warning(f"페이지 {page} 가져오기 실패 (재시도 소진)")
                continue

            posts = parse_realtime_best(html)
            new_count = 0
            for post in posts:
                if post.post_id and post.post_id not in seen_ids:
                    seen_ids.add(post.post_id)
                    all_posts.append(post)
                    new_count += 1

            logger.info(f"페이지 {page}: {len(posts)}개 파싱 / {new_count}개 신규")

            if page < max_pages:
                delay = random.uniform(config.CRAWL_DELAY_SEC, config.CRAWL_DELAY_SEC + 2)
                logger.info(f"다음 페이지까지 {delay:.1f}초 대기")
                await asyncio.sleep(delay)

    logger.info(f"실시간베스트 총 {len(all_posts)}개 수집 완료")
    return all_posts


async def main():
    """테스트용 단독 실행"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    posts = await crawl_realtime_best(max_pages=2)
    for i, p in enumerate(posts[:10], 1):
        print(f"{i}. [{p.gallery_name}] {p.title} (조회:{p.view_count} 추천:{p.recommend_count})")
    print(f"\n총 {len(posts)}개 수집됨")


if __name__ == "__main__":
    asyncio.run(main())
