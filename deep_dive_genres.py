"""
장르 후보 심층 검증 실행 스크립트

scan_genre_gaps.py가 추린 후보를 진입 판단 가능한 수준까지 분석한다.

사용:
    python3 deep_dive_genres.py              # 기본 후보 3개 분석
    python3 deep_dive_genres.py --no-send    # 텔레그램 발송 생략
"""

import argparse
import asyncio
import logging
import sys

from collector.steam_deep_dive import deep_dive, GenreDeepDive
from collector.steam_genre_gap import SOLO_DEV_TAGS
from notifier.telegram import send_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# scan_genre_gaps 결과에서 판독 룰을 통과한 후보
# (경쟁 200 미만 / 신작 중앙값 50+ / 1등 리뷰 5000 미만)
CANDIDATES: list[tuple[str, str]] = [
    ("카드 게임", "추리"),
    ("경영", "비주얼 노벨"),
    ("로그라이크", "도시 건설"),
]


def format_deep_dive(d: GenreDeepDive) -> str:
    """심층 분석 결과를 텔레그램 메시지로 포맷"""
    lines: list[str] = []
    tags = f"{d.tag_names[0]}+{d.tag_names[1]}"
    lines.append(f"🔬 {tags}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"총 {d.total_games}개 | 무료 비중 {d.free_ratio:.0%}")

    # 출시 추세 (최근 5년)
    if d.yearly_releases:
        recent = sorted(d.yearly_releases.items())[-5:]
        trend = " / ".join(f"{y}:{c}" for y, c in recent)
        lines.append(f"📅 출시 추세 {trend}")

    # 가격
    if d.price_median_krw:
        lo, hi = d.price_range
        lines.append(
            f"💰 가격 중앙값 {d.price_median_krw:,}원 "
            f"(범위 {lo:,}~{hi:,})"
        )

    # 성과 분포
    q1, med, q3 = d.review_quartiles
    lines.append(f"📊 리뷰 분포 하위25% {q1} / 중앙 {med} / 상위25% {q3}")

    # 소규모 성공 사례
    if d.small_successes:
        lines.append("")
        lines.append("✅ 최근 2년 소규모 성공작")
        for g in d.small_successes[:5]:
            price = f"{g.price_krw:,}원" if g.price_krw else "무료"
            lines.append(
                f"  · {g.name[:26]} — {g.review_count}리뷰 "
                f"{g.positive_pct}% / {price}"
            )
    else:
        lines.append("")
        lines.append("⚠️ 최근 2년 소규모 성공작 없음")

    # 불만 패턴
    if d.complaints:
        lines.append("")
        top = [f"{w}({c})" for w, c in d.complaints[:8]]
        lines.append(f"😠 부정리뷰 빈출: {' / '.join(top)}")

    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-send", action="store_true")
    args = parser.parse_args()

    results: list[GenreDeepDive] = []
    for name_a, name_b in CANDIDATES:
        id_a = SOLO_DEV_TAGS.get(name_a)
        id_b = SOLO_DEV_TAGS.get(name_b)
        if id_a is None or id_b is None:
            logger.error(f"태그 ID 없음: {name_a} / {name_b}")
            continue
        result = await deep_dive((name_a, name_b), (id_a, id_b))
        results.append(result)

    if not results:
        logger.error("분석 결과 없음")
        return 1

    blocks = ["🔬 장르 후보 심층 검증", ""]
    for r in results:
        blocks.append(format_deep_dive(r))
        blocks.append("")
    report = "\n".join(blocks)
    print(report)

    if not args.no_send:
        sent = await send_message(report)
        logger.info(f"텔레그램 발송: {'성공' if sent else '실패'}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
