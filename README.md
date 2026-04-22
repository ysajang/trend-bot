# TrendRadar Bot

커뮤니티 트렌드 자동 수집 + AI 분석 + 텔레그램 알림 봇

## 구조

```
trend-bot/
├── .env                    # 환경변수 (토큰, API키)
├── requirements.txt        # 의존성
├── config.py               # 설정 관리
├── crawler/
│   ├── __init__.py
│   ├── dcinside.py         # 디시인사이드 크롤러
│   └── fmkorea.py          # 에펨코리아 크롤러 (2단계)
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
├── main.py                 # 메인 실행 (cron용)
└── data/                   # 수집 데이터 저장 디렉토리
```

## 실행

```bash
# 1회 실행
python3 main.py

# cron 등록 (매일 06:00)
crontab -e
0 6 * * * cd /home/seungjae/trend-bot && /usr/bin/python3 main.py >> /home/seungjae/trend-bot/cron.log 2>&1
```
