# TrendRadar Bot v2

뉴스 기반 트렌드 자동 수집 + AI 분석 + 텔레그램 알림 봇

## 아키텍처 (3-Layer)

```
[Layer 1] RSS 피드 (트렌드 발견)
    ↓
[Layer 2] Naver News API (트렌드 심화)
    ↓
[Layer 3] Naver DataLab API (트렌드 검증)
    ↓
[analyzer] kiwipiepy 키워드 추출 + Claude Haiku/Sonnet 인사이트
    ↓
[notifier] Telegram 일일 리포트 (06:00 KST)
```

## 빌드 진행 상태

- [x] 단계 A: TrendItem 베이스 + 레거시 dcinside 크롤러 제거
- [ ] 단계 B: Layer 1 RSS 수집기 (`collector/rss_collector.py`)
- [ ] 단계 C: Layer 2 Naver News 수집기 (`collector/naver_news.py`)
- [ ] 단계 D: Layer 3 Naver DataLab 검증기 (`collector/naver_datalab.py`)
- [ ] 단계 E: analyzer 재배선 (Haiku/Sonnet 프롬프트 v2)
- [ ] 단계 F: notifier 리포트 포맷 v2

## 구조

```
trend-bot/
├── .env                    # 환경변수 (토큰, API키)
├── requirements.txt        # 의존성
├── config.py               # 설정 관리 (Naver API 한도 / 모델 / 불용어)
├── collector/
│   ├── __init__.py         # public API export
│   └── trend_item.py       # 통합 데이터 모델 (TrendItem)
├── analyzer/
│   ├── __init__.py
│   ├── keyword_extractor.py # 키워드 추출 (kiwipiepy)
│   └── ai_insight.py       # Claude API 인사이트 생성
├── notifier/
│   ├── __init__.py
│   └── telegram.py         # 텔레그램 알림
├── storage/
│   ├── __init__.py
│   └── json_store.py       # JSON 파일 기반 저장
├── tests/
│   └── test_trend_item.py  # TrendItem 단위 테스트
├── main.py                 # 메인 실행 (cron용)
└── data/                   # 수집 데이터 저장 디렉토리
```

## 환경변수 (.env)

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ANTHROPIC_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```

Naver Open API 키 발급: https://developers.naver.com/apps/#/register
필요 API: 검색(뉴스) + 데이터랩(검색어트렌드)

## 실행

```bash
# 1회 실행
python3 main.py

# 테스트
pytest tests/ -v

# cron 등록 (매일 06:00 KST)
crontab -e
0 6 * * * cd /home/seungjae/trend-bot && /usr/bin/python3 main.py >> /home/seungjae/trend-bot/cron.log 2>&1
```

## 법적 근거

- 커뮤니티 크롤링 전면 중단 (Legal by Design 원칙)
- RSS는 언론사가 공개 배포 목적으로 제공하는 서비스 / 개인 구독 용도
- Naver Open API는 공식 API 약관에 따른 호출
- 기사 본문 전체 스크래핑 금지 (헤드라인 + 요약만 사용)
