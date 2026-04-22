"""
디시인사이드 실시간베스트 크롤러
- 실시간베스트 게시판에서 인기글 제목 + 본문 미리보기 수집
- IP 차단 방지를 위해 요청 간격 조절
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field, asdict

import aiohttp
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://gall.dcinside.com/",
}

BASE_URL = "https://gall.dcinside.com"


@dataclass
class Post:
    """수집된 게시글 데이터"""
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


async def fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    """URL에서 HTML을 가져옴"""
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                return await resp.text()
            logger.warning(f"HTTP {resp.status} for {url}")
            return None
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching {url}")
        return None
    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        return None


def parse_realtime_best(html: str) -> list[Post]:
    """실시간베스트 페이지 파싱"""
    posts: list[Post] = []
    soup = BeautifulSoup(html, "lxml")

    rows = soup.select("tr.ub-content")
    if not rows:
        # 대체 선택자
        rows = soup.select(".ub-content")

    for row in rows:
        try:
            # 제목
            title_el = row.select_one("td.gall_tit a") or row.select_one(".gall_tit a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")

            # post_id 추출
            post_id = ""
            if "no=" in href:
                post_id = href.split("no=")[-1].split("&")[0]

            # 갤러리 이름
            gall_el = row.select_one("td.gall_name a") or row.select_one(".gall_name")
            gallery_name = gall_el.get_text(strip=True) if gall_el else ""

            # 갤러리 ID
            gallery_id = ""
            if gall_el and gall_el.get("href"):
                gall_href = gall_el.get("href", "")
                if "id=" in gall_href:
                    gallery_id = gall_href.split("id=")[-1].split("&")[0]

            # 작성자
            author_el = row.select_one("td.gall_writer") or row.select_one(".gall_writer")
            author = ""
            if author_el:
                nick_el = author_el.select_one(".nickname") or author_el.select_one("em")
                author = nick_el.get_text(strip=True) if nick_el else author_el.get_text(strip=True)

            # 조회수
            view_el = row.select_one("td.gall_count") or row.select_one(".gall_count")
            view_count = 0
            if view_el:
                view_text = view_el.get_text(strip=True).replace(",", "")
                view_count = int(view_text) if view_text.isdigit() else 0

            # 추천수
            rec_el = row.select_one("td.gall_recommend") or row.select_one(".gall_recommend")
            recommend_count = 0
            if rec_el:
                rec_text = rec_el.get_text(strip=True).replace(",", "")
                recommend_count = int(rec_text) if rec_text.isdigit() else 0

            # 댓글수
            comment_count = 0
            reply_el = title_el.find_next_sibling("a", class_="reply_numbox") if title_el else None
            if not reply_el:
                reply_el = row.select_one(".reply_numbox")
            if reply_el:
                reply_text = reply_el.get_text(strip=True).strip("[]")
                comment_count = int(reply_text) if reply_text.isdigit() else 0

            # 시간
            time_el = row.select_one("td.gall_date") or row.select_one(".gall_date")
            timestamp = ""
            if time_el:
                timestamp = time_el.get("title", "") or time_el.get_text(strip=True)

            # URL 조합
            url = href if href.startswith("http") else f"{BASE_URL}{href}"

            post = Post(
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
            )
            posts.append(post)

        except Exception as e:
            logger.warning(f"게시글 파싱 오류: {e}")
            continue

    return posts


async def crawl_realtime_best(max_pages: int = 3) -> list[Post]:
    """
    디시인사이드 실시간베스트 크롤링

    Args:
        max_pages: 수집할 페이지 수 (1페이지 = 약 50개)

    Returns:
        수집된 Post 리스트
    """
    all_posts: list[Post] = []
    seen_ids: set[str] = set()

    async with aiohttp.ClientSession() as session:
        for page in range(1, max_pages + 1):
            url = f"{BASE_URL}/board/lists/?id=dcbest&page={page}"
            logger.info(f"크롤링 중: 실시간베스트 페이지 {page}")

            html = await fetch_page(session, url)
            if not html:
                logger.warning(f"페이지 {page} 가져오기 실패")
                continue

            posts = parse_realtime_best(html)
            for post in posts:
                if post.post_id and post.post_id not in seen_ids:
                    seen_ids.add(post.post_id)
                    all_posts.append(post)

            logger.info(f"페이지 {page}: {len(posts)}개 수집")

            # IP 차단 방지 딜레이
            if page < max_pages:
                await asyncio.sleep(config.CRAWL_DELAY_SEC)

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
