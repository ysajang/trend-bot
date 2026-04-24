"""
TrendRadar Bot - 크롤러 테스트
실행: pytest tests/test_crawler.py -v
"""

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession

# 테스트 대상
from crawler.dcinside import (
    Post,
    _make_headers,
    _warm_up_session,
    fetch_page,
    parse_realtime_best,
    crawl_realtime_best,
    UA_POOL,
    BASE_URL,
)


# ────────────────────────────────────────────────────────────
# 픽스처
# ────────────────────────────────────────────────────────────

SAMPLE_HTML = """
<html><body>
<table>
<tr class="ub-content">
  <td class="gall_tit">
    <a href="/board/view/?id=baseball&no=12345">류현진 오늘 경기 진짜 레전드</a>
    <a class="reply_numbox">[42]</a>
  </td>
  <td class="gall_name"><a href="/board/lists/?id=baseball">야구 갤러리</a></td>
  <td class="gall_writer"><em class="nickname">익명</em></td>
  <td class="gall_count">9823</td>
  <td class="gall_recommend">315</td>
  <td class="gall_date" title="2026-04-24 05:58:00">05:58</td>
</tr>
<tr class="ub-content">
  <td class="gall_tit">
    <a href="/board/view/?id=soccer&no=67890">손흥민 골 모음</a>
  </td>
  <td class="gall_name"><a href="/board/lists/?id=soccer">축구 갤러리</a></td>
  <td class="gall_writer"><em class="nickname">팬123</em></td>
  <td class="gall_count">5412</td>
  <td class="gall_recommend">201</td>
  <td class="gall_date" title="2026-04-24 05:55:00">05:55</td>
</tr>
</table>
</body></html>
"""

EMPTY_HTML = "<html><body><table></table></body></html>"
BLOCKED_HTML = "<html><body>접근이 차단되었습니다</body></html>"  # 500bytes 미만


# ────────────────────────────────────────────────────────────
# 1. 헤더 생성
# ────────────────────────────────────────────────────────────

class TestMakeHeaders:
    def test_ua_is_from_pool(self):
        """User-Agent가 UA_POOL 중 하나인지 확인"""
        for _ in range(20):
            headers = _make_headers(page=1)
            assert headers["User-Agent"] in UA_POOL

    def test_page1_referer_is_main(self):
        """1페이지 Referer는 gall 메인"""
        headers = _make_headers(page=1)
        assert headers["Referer"] == BASE_URL + "/"

    def test_page2_referer_is_page1(self):
        """2페이지 Referer는 1페이지 URL"""
        headers = _make_headers(page=2)
        assert "page=1" in headers["Referer"]

    def test_required_sec_fetch_headers_present(self):
        """Sec-Fetch 헤더 포함 여부"""
        headers = _make_headers(page=1)
        assert "Sec-Fetch-Dest" in headers
        assert "Sec-Fetch-Mode" in headers
        assert "Sec-Fetch-Site" in headers


# ────────────────────────────────────────────────────────────
# 2. HTML 파싱
# ────────────────────────────────────────────────────────────

class TestParseRealtimeBest:
    def test_parses_two_posts(self):
        posts = parse_realtime_best(SAMPLE_HTML)
        assert len(posts) == 2

    def test_first_post_fields(self):
        posts = parse_realtime_best(SAMPLE_HTML)
        p = posts[0]
        assert p.title == "류현진 오늘 경기 진짜 레전드"
        assert p.post_id == "12345"
        assert p.gallery_id == "baseball"
        assert p.gallery_name == "야구 갤러리"
        assert p.view_count == 9823
        assert p.recommend_count == 315
        assert p.comment_count == 42
        assert p.timestamp == "2026-04-24 05:58:00"
        assert "12345" in p.url

    def test_second_post_no_comment(self):
        """댓글 없는 게시글 — comment_count 0"""
        posts = parse_realtime_best(SAMPLE_HTML)
        assert posts[1].comment_count == 0

    def test_url_absolute(self):
        """상대경로 href → 절대 URL 변환"""
        posts = parse_realtime_best(SAMPLE_HTML)
        for p in posts:
            assert p.url.startswith("http")

    def test_empty_html_returns_empty(self):
        posts = parse_realtime_best(EMPTY_HTML)
        assert posts == []

    def test_post_source_is_dcinside(self):
        posts = parse_realtime_best(SAMPLE_HTML)
        for p in posts:
            assert p.source == "dcinside"

    def test_to_dict_contains_all_fields(self):
        posts = parse_realtime_best(SAMPLE_HTML)
        d = posts[0].to_dict()
        expected_keys = {
            "post_id", "title", "author", "gallery_name", "gallery_id",
            "view_count", "recommend_count", "comment_count",
            "timestamp", "url", "source",
        }
        assert expected_keys.issubset(d.keys())


# ────────────────────────────────────────────────────────────
# 3. fetch_page (네트워크 mock)
# ────────────────────────────────────────────────────────────

class TestFetchPage:
    @pytest.mark.asyncio
    async def test_returns_html_on_200(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=SAMPLE_HTML)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await fetch_page(mock_session, "https://gall.dcinside.com/board/lists/?id=dcbest&page=1", page=1, retries=0)
        assert result == SAMPLE_HTML

    @pytest.mark.asyncio
    async def test_returns_none_on_403(self):
        mock_resp = AsyncMock()
        mock_resp.status = 403
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await fetch_page(mock_session, "https://gall.dcinside.com/test", page=1, retries=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_short_response(self):
        """500bytes 미만 응답 = 차단 페이지 판정"""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=BLOCKED_HTML)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await fetch_page(mock_session, "https://gall.dcinside.com/test", page=1, retries=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        """타임아웃 시 retries 횟수만큼 재시도"""
        call_count = 0

        async def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = fake_get
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_ctx)

        with patch("crawler.dcinside.asyncio.sleep", new_callable=AsyncMock):
            result = await fetch_page(mock_session, "https://gall.dcinside.com/test", page=1, retries=2)

        assert result is None
        assert call_count == 3  # 최초 1 + 재시도 2


# ────────────────────────────────────────────────────────────
# 4. warm_up_session (네트워크 mock)
# ────────────────────────────────────────────────────────────

class TestWarmUpSession:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await _warm_up_session(mock_session)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=Exception("connection error"))

        result = await _warm_up_session(mock_session)
        assert result is False


# ────────────────────────────────────────────────────────────
# 5. crawl_realtime_best 통합 (full mock)
# ────────────────────────────────────────────────────────────

class TestCrawlRealtimeBest:
    @pytest.mark.asyncio
    async def test_deduplicates_posts(self):
        """동일 post_id 중복 제거 확인"""
        duplicate_html = SAMPLE_HTML + SAMPLE_HTML  # 같은 글 2번

        with patch("crawler.dcinside._warm_up_session", new_callable=AsyncMock, return_value=True), \
             patch("crawler.dcinside.fetch_page", new_callable=AsyncMock, return_value=duplicate_html), \
             patch("crawler.dcinside.asyncio.sleep", new_callable=AsyncMock):
            posts = await crawl_realtime_best(max_pages=1)

        assert len(posts) == 2  # 중복 제거 후 2개

    @pytest.mark.asyncio
    async def test_returns_empty_on_all_page_fail(self):
        """모든 페이지 fetch 실패 시 빈 리스트"""
        with patch("crawler.dcinside._warm_up_session", new_callable=AsyncMock, return_value=False), \
             patch("crawler.dcinside.fetch_page", new_callable=AsyncMock, return_value=None), \
             patch("crawler.dcinside.asyncio.sleep", new_callable=AsyncMock):
            posts = await crawl_realtime_best(max_pages=3)

        assert posts == []

    @pytest.mark.asyncio
    async def test_collects_from_multiple_pages(self):
        """여러 페이지 게시글 합산"""
        page_html = {1: SAMPLE_HTML, 2: SAMPLE_HTML.replace("12345", "99991").replace("67890", "99992")}
        call_order = [1, 2]

        async def fake_fetch(session, url, page=1, retries=2):
            return page_html.get(page, EMPTY_HTML)

        with patch("crawler.dcinside._warm_up_session", new_callable=AsyncMock, return_value=True), \
             patch("crawler.dcinside.fetch_page", side_effect=fake_fetch), \
             patch("crawler.dcinside.asyncio.sleep", new_callable=AsyncMock):
            posts = await crawl_realtime_best(max_pages=2)

        assert len(posts) == 4  # 페이지당 2개 × 2페이지


# ────────────────────────────────────────────────────────────
# 6. Post 데이터 클래스
# ────────────────────────────────────────────────────────────

class TestPost:
    def test_default_values(self):
        p = Post()
        assert p.source == "dcinside"
        assert p.view_count == 0
        assert p.recommend_count == 0
        assert p.comment_count == 0

    def test_to_dict_serializable(self):
        import json
        p = Post(post_id="1", title="테스트", view_count=100)
        d = p.to_dict()
        # JSON 직렬화 가능한지 확인
        json_str = json.dumps(d, ensure_ascii=False)
        assert "테스트" in json_str
